import io
import json
import os
import re
import urllib.parse
from bs4 import BeautifulSoup
import feedparser
from pypdf import PdfReader
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "seen_jobs.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка отправки Telegram: {e}")


def extract_wage(text):
    """Точный поиск почасовой ставки ($XX.XX/hr) или диапазона годового оклада."""
    # 1. Почасовая ставка: $28.50/hour, $31.45 per hour, $25.00 - $29.00/hr
    hourly = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?(?:\s*(?:-|to)\s*\$?\s*\d{2}(?:\.\d{2})?)?\s*(?:per\s*hour|\/\s*hr|hourly))",
        text,
        re.IGNORECASE,
    )
    if hourly:
        return hourly.group(1).strip()

    # 2. Годовой оклад: $86,312 to $109,614 per year / annually
    salary = re.search(
        r"(\$\s*\d{2,3}(?:,\d{3})+(?:\s*(?:-|to)\s*\$?\s*\d{2,3}(?:,\d{3})+)?\s*(?:per\s*annum|per\s*year|\/\s*year|annually)?)",
        text,
        re.IGNORECASE,
    )
    if salary and any(w in salary.group(0).lower() for w in ["to", "year", "annum", "annually"]):
        return salary.group(1).strip()

    # 3. Блок с ключевыми словами Wage / Salary / Rate
    keyword_wage = re.search(
        r"(?:wage|salary|rate)\s*[:\-]?\s*(\$\s*\d{2,3}(?:\.\d{2}|,\d{3})?(?:\s*(?:-|to)\s*\$?\s*\d{2,3}(?:\.\d{2}|,\d{3})?)?(?:\s*(?:per\s*hour|\/\s*hr|hourly|annually))?)",
        text,
        re.IGNORECASE,
    )
    if keyword_wage:
        val = keyword_wage.group(1).strip()
        if len(val) > 2:
            return val

    return None


def extract_meaningful_snippet(text, max_len=360):
    """Очищает текст от шаблонных преамбул SCRD и оставляет суть должности."""
    # Убираем шаблонные преамбулы про горы и земли First Nations
    text = re.sub(
        r"The Sunshine Coast.*?ancestral lands? of the.*?(First Nations|Nation)[,\.]?",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"Bordered by rugged mountains.*?(First Nations|Nation)[,\.]?",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Ищем фактическое начало обязанностей
    markers = [
        "The Opportunity",
        "About the Role",
        "Position Overview",
        "Job Summary",
        "Duties",
        "Key Responsibilities",
    ]
    for m in markers:
        idx = text.find(m)
        if idx != -1:
            text = text[idx:]
            break

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def extract_pdf_snippet(pdf_url):
    """Скачивает PDF, извлекает валидную ставку и содержательный блок."""
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:
                full_text += " " + (page.extract_text() or "")

            wage = extract_wage(full_text)
            body = extract_meaningful_snippet(full_text)

            wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
            return f"{wage_line}{body}"
    except Exception:
        pass
    return "📄 <i>Подробное описание и требования доступны в PDF по ссылке.</i>"


def load_seen():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def notify_job(source, title, link, description, seen, new_seen):
    job_id = f"{source}::{title}::{link}"
    if job_id in seen:
        return

    new_seen.add(job_id)
    msg = (
        f"⚡ <b>Новая вакансия: {source}</b>\n\n"
        f"📌 <b>{title}</b>\n\n"
        f"📝 {description}\n\n"
        f"🔗 <a href='{link}'>Открыть вакансию</a>"
    )
    send_telegram(msg)


def parse_scrd(seen, new_seen):
    """Парсер вакансий SCRD с прикрепленными PDF-файлами."""
    url = "https://www.scrd.ca/careers/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        headers = soup.find_all(re.compile(r"h[2-5]"))
        for h in headers:
            title = h.get_text(strip=True)
            if not ("#" in title or any(w in title for w in ["Lifeguard", "Coordinator", "Operator", "Manager", "Assistant"])):
                continue

            next_node = h.find_next("a", href=re.compile(r"\.pdf", re.I))
            if next_node:
                pdf_link = urllib.parse.urljoin(url, next_node.get("href"))
                snippet = extract_pdf_snippet(pdf_link)
                notify_job("SCRD", title, pdf_link, snippet, seen, new_seen)
    except Exception as e:
        print(f"Ошибка SCRD: {e}")


def parse_gibsons(seen, new_seen):
    """Парсер Town of Gibsons."""
    url = "https://gibsons.ca/town-hall/employment-opportunities/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        main_content = soup.find("article") or soup.find("main") or soup
        for a in main_content.find_all("a", href=re.compile(r"\.pdf", re.I)):
            title = a.get_text(strip=True)
            if len(title) > 6 and not any(w in title.lower() for w in ["form", "policy", "bylaw", "agenda"]):
                full_url = urllib.parse.urljoin(url, a.get("href"))
                snippet = extract_pdf_snippet(full_url)
                notify_job("Town of Gibsons", title, full_url, snippet, seen, new_seen)
    except Exception as e:
        print(f"Ошибка Gibsons: {e}")


def parse_civicjobs(seen, new_seen):
    """Парсер CivicJobs BC (муниципальные вакансии региона)."""
    search_url = "https://www.civicjobs.ca/jobs?region=Sunshine+Coast"
    try:
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            job_rows = soup.select(".job-list-item, .job-item, tr")
            for row in job_rows:
                link_tag = row.find("a", href=re.compile(r"/jobs\?id="))
                if link_tag:
                    title = link_tag.get_text(strip=True)
                    if len(title) > 4:
                        link = urllib.parse.urljoin("https://www.civicjobs.ca", link_tag.get("href"))
                        row_text = row.get_text(separator=" ", strip=True)
                        wage = extract_wage(row_text)
                        clean_desc = extract_meaningful_snippet(row_text)
                        prefix = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
                        notify_job("CivicJobs BC", title, link, f"{prefix}{clean_desc}", seen, new_seen)
    except Exception as e:
        print(f"Ошибка CivicJobs: {e}")


def main():
    seen = load_seen()
    new_seen = set(seen)

    parse_scrd(seen, new_seen)
    parse_gibsons(seen, new_seen)
    parse_civicjobs(seen, new_seen)

    save_seen(new_seen)


if __name__ == "__main__":
    main()
