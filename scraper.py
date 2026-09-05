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
    """Точный поиск почасовой ставки ($XX.XX/hr) или годового оклада."""
    # 1. Почасовая со словами per hour / hr / hourly
    hourly_explicit = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?\s*(?:per\s*hour|\/\s*hr|hourly))",
        text,
        re.IGNORECASE,
    )
    if hourly_explicit:
        return hourly_explicit.group(1).strip()

    # 2. Почасовой диапазон: $25.00 - $32.50 / $25.00 to $32.50
    hourly_range = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?\s*(?:-|to)\s*\$?\s*\d{2}(?:\.\d{2})?(?:\s*(?:per\s*hour|\/\s*hr|hourly))?)",
        text,
        re.IGNORECASE,
    )
    if hourly_range and any(
        w in hourly_range.group(0).lower() for w in ["per", "/", "to", "-"]
    ):
        return hourly_range.group(1).strip()

    # 3. Строка с маркером Wage/Rate/Salary
    table_wage = re.search(
        r"(?:wage|rate|salary)[^\$\n\r]{0,30}(\$\s*\d{2,3}(?:\.\d{2}|,\d{3})?(?:\s*(?:-|to)\s*\$?\s*\d{2,3}(?:\.\d{2}|,\d{3})?)?)",
        text,
        re.IGNORECASE,
    )
    if table_wage:
        val = table_wage.group(1).strip()
        if len(val) > 2 and not val.endswith("."):
            return f"{val}/hr" if "." in val else val

    # 4. Годовой оклад
    salary_annual = re.search(
        r"(\$\s*\d{2,3},\d{3}\s*(?:-|to)\s*\$?\s*\d{2,3},\d{3})", text
    )
    if salary_annual:
        return f"{salary_annual.group(1).strip()} per year"

    return None


def clean_meaningful_text(text, max_len=360):
    """Срезает рекламные преамбулы и находит фактическое описание должности."""
    patterns_to_remove = [
        r"The Sunshine Coast.*?Hike the trails.*?(attend|cross - country skiing|culture)[,\.]?",
        r"The Sunshine Coast A natural paradise.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Bordered by rugged mountains.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Whatever hobby or interest you might enjoy.*?attend[,\.]?",
    ]
    for pattern in patterns_to_remove:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    markers = [
        "The Opportunity",
        "Position Overview",
        "About the Role",
        "Job Summary",
        "Duties",
        "Key Responsibilities",
        "Duties & Responsibilities",
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
    """Скачивает PDF и извлекает проверенный сниппет с зарплатой."""
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:
                full_text += " " + (page.extract_text() or "")

            wage = extract_wage(full_text)
            body = clean_meaningful_text(full_text)

            # Проверка: если в тексте нет признаков реальной вакансии — возвращаем None
            job_keywords = ["duties", "qualifications", "experience", "hourly", "wage", "salary", "hours of work", "apply"]
            if not any(k in full_text.lower() for k in job_keywords):
                return None

            wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
            return f"{wage_line}{body}"
    except Exception:
        pass
    return None


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
    clean_title = re.sub(r"\s+", " ", title).strip()
    job_id = f"{source}::{clean_title}::{link}"
    if job_id in seen:
        return

    new_seen.add(job_id)
    msg = (
        f"⚡ <b>Новая вакансия: {source}</b>\n\n"
        f"📌 <b>{clean_title}</b>\n\n"
        f"📝 {description}\n\n"
        f"🔗 <a href='{link}'>Открыть вакансию</a>"
    )
    send_telegram(msg)


def parse_scrd(seen, new_seen):
    """Парсер вакансий SCRD с прикрепленными PDF."""
    url = "https://www.scrd.ca/careers/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        headers = soup.find_all(re.compile(r"h[2-5]"))
        for h in headers:
            title = h.get_text(strip=True)
            if not (
                "#" in title
                or any(
                    w in title
                    for w in [
                        "Lifeguard",
                        "Coordinator",
                        "Operator",
                        "Manager",
                        "Assistant",
                        "Driver",
                        "Tech",
                        "Attendant",
                    ]
                )
            ):
                continue

            link_tag = h.find_next("a", href=re.compile(r"\.pdf", re.I))
            if link_tag:
                pdf_link = urllib.parse.urljoin(url, link_tag.get("href"))
                snippet = extract_pdf_snippet(pdf_link)
                if snippet:
                    notify_job("SCRD", title, pdf_link, snippet, seen, new_seen)
    except Exception as e:
        print(f"Ошибка SCRD: {e}")


def parse_gibsons(seen, new_seen):
    """Строгий парсер Town of Gibsons: только блок вакансий и валидные PDF."""
    url = "https://gibsons.ca/town-hall/employment-opportunities/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")

        curr_opps_header = soup.find(
            lambda tag: tag.name in ["h2", "h3", "h4"]
            and "current opportunit" in tag.get_text().lower()
        )
        container = curr_opps_header.parent if curr_opps_header else soup

        for a in container.find_all("a", href=True):
            href = a.get("href", "").strip()
            title = a.get_text(strip=True)
            full_url = urllib.parse.urljoin(url, href)

            if not (full_url.lower().endswith(".pdf") or "/careers/" in full_url.lower()):
                continue

            skip_docs = ["application", "volunteer", "guide", "form", "policy", "benefit", "handbook", "agreement"]
            if any(skip in title.lower() or skip in href.lower() for skip in skip_docs):
                continue

            junk_anchors = ["click here", "learn more", "download", "pdf", "view", "link"]
            if len(title) < 5 or any(j in title.lower() for j in junk_anchors):
                prev_h = a.find_previous(["h3", "h4", "h5", "p", "strong"])
                if prev_h and len(prev_h.get_text(strip=True)) > 5:
                    title = prev_h.get_text(strip=True)
                else:
                    continue

            snippet = extract_pdf_snippet(full_url)
            if not snippet:
                continue

            notify_job("Town of Gibsons", title, full_url, snippet, seen, new_seen)
    except Exception as e:
        print(f"Ошибка Gibsons: {e}")


def parse_civicjobs_rss(seen, new_seen):
    """Парсер официального RSS-потока CivicJobs BC."""
    url = "https://www.civicjobs.ca/rss"
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            full_text = f"{title} {summary}"

            if any(
                loc in full_text.lower()
                for loc in [
                    "sunshine coast",
                    "gibsons",
                    "sechelt",
                    "pender harbour",
                ]
            ):
                link = entry.get("link", "").strip()
                wage = extract_wage(summary)
                clean_desc = clean_meaningful_text(
                    BeautifulSoup(summary, "html.parser").get_text()
                )
                wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
                notify_job(
                    "CivicJobs BC",
                    title,
                    link,
                    f"{wage_line}{clean_desc}",
                    seen,
                    new_seen,
                )
    except Exception as e:
        print(f"Ошибка CivicJobs RSS: {e}")


def parse_sd46(seen, new_seen):
    """Школьный округ SD46 (Make A Future RSS)."""
    url = "https://www.makeafuture.ca/bc-schools-and-districts/sunshine-coast-school-district-no-46/feed/"
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = clean_meaningful_text(
                BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            )
            notify_job("SD46 (School District)", title, link, summary, seen, new_seen)
    except Exception as e:
        print(f"Ошибка SD46: {e}")


def main():
    seen = load_seen()
    new_seen = set(seen)

    parse_scrd(seen, new_seen)
    parse_gibsons(seen, new_seen)
    parse_civicjobs_rss(seen, new_seen)
    parse_sd46(seen, new_seen)

    save_seen(new_seen)


if __name__ == "__main__":
    main()
