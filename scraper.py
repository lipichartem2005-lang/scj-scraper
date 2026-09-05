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
        " like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

# Ключевые слова, подтверждающие, что это реальная вакансия
JOB_INDICATORS = [
    "wage",
    "salary",
    "per hour",
    "hourly",
    "union",
    "bcgeu",
    "cupe",
    "unifor",
    "duties",
    "qualifications",
    "closing date",
    "position",
    "employment opportunity",
    "job description",
    "apply by",
]

# Слова меню и мусора, которые надо сразу отсекать
SKIP_TITLES = [
    "council",
    "meeting",
    "hearing",
    "bylaw",
    "delegation",
    "advocacy",
    "contact",
    "mayor",
    "budget",
    "garbage",
    "utility",
    "water",
    "tax",
    "calendar",
    "minutes",
    "agendas",
    "privacy",
    "accessibility",
    "home",
    "about",
    "news",
    "alert",
    "notice",
]


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
        print(f"Ошибка Telegram: {e}")


def clean_text(text, max_len=380):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def extract_pdf_snippet(pdf_url):
    """Скачивает PDF с вакансией и извлекает первые абзацы сути."""
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:  # читаем первые 2 страницы
                txt = page.extract_text() or ""
                full_text += " " + txt

            # Проверяем, что это не пустой бланк или отчет совета
            lower_text = full_text.lower()
            if not any(word in lower_text for word in JOB_INDICATORS):
                return None

            # Ищем блок с зарплатой, если указана
            wage_match = re.search(
                r"(\$\s*\d+[\d\.,]*\s*(?:-|to)\s*\$?\s*\d+[\d\.,]*|\$\s*\d+[\d\.,]*(?:\s*(?:per|\/)\s*hr|\s*hourly)?)",
                full_text,
                re.IGNORECASE,
            )
            prefix = (
                f"💰 <b>Ставка:</b> {wage_match.group(0).strip()}\n\n"
                if wage_match
                else ""
            )

            # Берем вводную часть с описанием обязанностей
            return prefix + clean_text(full_text)
    except Exception:
        pass
    return None


def extract_html_snippet(url):
    """Извлекает текст со страниц вакансий."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            body = soup.find("article") or soup.find("main") or soup.body
            if not body:
                return None

            raw_text = body.get_text(separator=" ", strip=True)
            if not any(w in raw_text.lower() for w in JOB_INDICATORS):
                return None

            paragraphs = [
                p.get_text(strip=True)
                for p in body.find_all(["p", "li"])
                if len(p.get_text(strip=True)) > 25
            ]
            if paragraphs:
                return clean_text(" ".join(paragraphs))
            return clean_text(raw_text)
    except Exception:
        pass
    return None


def is_junk_link(title, url):
    t = title.lower()
    u = url.lower()
    if any(skip in t for skip in SKIP_TITLES):
        return True
    if not (
        u.endswith(".pdf")
        or "career" in u
        or "job" in u
        or "employment" in u
        or "position" in u
    ):
        return True
    return False


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


def process_candidate(source_name, title, link, seen, new_seen, feed_desc=None):
    if not link or not title or len(title) < 5:
        return

    job_id = f"{source_name}::{title}::{link}"
    if job_id in seen:
        return

    # Извлекаем и валидируем описание
    snippet = None
    if feed_desc:
        snippet = clean_text(feed_desc)
    elif link.lower().endswith(".pdf"):
        snippet = extract_pdf_snippet(link)
    else:
        snippet = extract_html_snippet(link)

    # Если текст не похож на вакансию — отбрасываем
    if not snippet:
        return

    new_seen.add(job_id)
    msg = (
        f"⚡ <b>Новая вакансия: {source_name}</b>\n\n"
        f"📌 <b>{title}</b>\n\n"
        f"📝 {snippet}\n\n"
        f"🔗 <a href='{link}'>Открыть объявление</a>"
    )
    send_telegram(msg)


def main():
    seen = load_seen()
    new_seen = set(seen)

    # 1. Город Гибсонс (ищет реальные PDF и ссылки объявлений)
    try:
        r = requests.get(
            "https://gibsons.ca/town-hall/employment-opportunities/",
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select(".entry-content a, main a"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if href and not is_junk_link(title, href):
                    full_url = urllib.parse.urljoin(
                        "https://gibsons.ca/town-hall/employment-opportunities/",
                        href,
                    )
                    process_candidate(
                        "Town of Gibsons", title, full_url, seen, new_seen
                    )
    except Exception as e:
        print(f"Ошибка Gibsons: {e}")

    # 2. Региональный округ SCRD
    try:
        r = requests.get(
            "https://www.scrd.ca/careers/", headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href*='.pdf'], a[href*='career']"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if (
                    href
                    and len(title) > 5
                    and not any(s in title.lower() for s in SKIP_TITLES)
                ):
                    full_url = urllib.parse.urljoin(
                        "https://www.scrd.ca/careers/", href
                    )
                    process_candidate("SCRD", title, full_url, seen, new_seen)
    except Exception as e:
        print(f"Ошибка SCRD: {e}")

    # 3. CivicJobs BC (RSS-фид официальных вакансий Sunshine Coast)
    try:
        feed = feedparser.parse(
            "https://www.civicjobs.ca/rss?region=Sunshine+Coast"
        )
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", entry.get("description", ""))
            process_candidate(
                "CivicJobs BC",
                title,
                link,
                seen,
                new_seen,
                feed_desc=clean_text(
                    BeautifulSoup(summary, "html.parser").get_text()
                ),
            )
    except Exception as e:
        print(f"Ошибка CivicJobs: {e}")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
