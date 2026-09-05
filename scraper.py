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


def clean_text(text, max_len=350):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def extract_pdf_snippet(pdf_url):
    """Скачивает PDF и достает выжимку обязанностей или ставки."""
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:
                txt = page.extract_text() or ""
                full_text += " " + txt

            wage = re.search(
                r"(\$\s*\d+[\d\.,]*\s*(?:-|to)\s*\$?\s*\d+[\d\.,]*|\$\s*\d+[\d\.,]*(?:\s*(?:per|\/)\s*hr|\s*hourly)?)",
                full_text,
                re.IGNORECASE,
            )
            prefix = f"💰 <b>Ставка:</b> {wage.group(0).strip()}\n\n" if wage else ""
            return prefix + clean_text(full_text)
    except Exception:
        pass
    return "📄 <i>Подробное описание внутри PDF по ссылке ниже.</i>"


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
    """Специальный парсер структуры карьеры SCRD (Sechelt / Gibsons)."""
    url = "https://www.scrd.ca/careers/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")

        # Вакансии на SCRD идут заголовками h3 или h4, а под ними ссылки на PDF
        headers = soup.find_all(re.compile(r"h[2-5]"))
        for h in headers:
            title = h.get_text(strip=True)
            if not ("#" in title or "Lifeguard" in title or "Coordinator" in title or "Operator" in title or "Manager" in title):
                continue

            # Ищем ссылку на PDF под заголовком
            next_node = h.find_next("a", href=re.compile(r"\.pdf", re.I))
            if next_node:
                pdf_link = urllib.parse.urljoin(url, next_node.get("href"))
                snippet = extract_pdf_snippet(pdf_link)
                notify_job("SCRD", title, pdf_link, snippet, seen, new_seen)
    except Exception as e:
        print(f"Ошибка SCRD: {e}")


def parse_gibsons(seen, new_seen):
    """Парсер вакансий Town of Gibsons."""
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
    # Ищем напрямую через поиск портала по региону
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
                        desc = clean_text(row.get_text(separator=" ", strip=True))
                        notify_job("CivicJobs BC", title, link, desc, seen, new_seen)
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
