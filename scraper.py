import json
import os
import re
import urllib.parse
from bs4 import BeautifulSoup
import feedparser
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "seen_jobs.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

HTML_TARGETS = [
    {
        "name": "Town of Gibsons",
        "url": "https://gibsons.ca/town-hall/employment-opportunities/",
        "selector": "main a, article a, .entry-content a",
    },
    {
        "name": "SCRD",
        "url": "https://www.scrd.ca/careers/",
        "selector": "main a, .entry-content a, a[href*='career']",
    },
    {
        "name": "District of Sechelt",
        "url": "https://www.sechelt.ca/en/town-hall/employment.aspx",
        "selector": "main a, #maincontent a, .content a",
    },
]

RSS_TARGETS = [
    {
        "name": "CivicJobs BC (Sunshine Coast)",
        "url": "https://www.civicjobs.ca/rss?region=Sunshine+Coast",
    },
    {
        "name": "Indeed (Union - Sunshine Coast)",
        "url": "https://ca.indeed.com/rss?q=union&l=Sunshine+Coast%2C+BC",
    },
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


def clean_snippet(raw_html_or_text, max_len=300):
    """Очищает HTML-теги, лишние пробелы и обрезает до аккуратного сниппета."""
    if not raw_html_or_text:
        return ""
    soup = BeautifulSoup(raw_html_or_text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def fetch_page_description(url):
    """Если вакансия ведет на HTML-страницу, переходит внутрь и забирает текст описания."""
    # Если это прямой PDF-файл, извлечение HTML пропускаем
    if url.lower().endswith(".pdf"):
        return "📄 <i>Описание находится внутри прикрепленного PDF-документа</i>"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            # Ищем основной текстовый блок страницы
            body = (
                soup.find("article")
                or soup.find("main")
                or soup.find(class_=re.compile(r"content|job|entry", re.I))
            )
            if body:
                p_tags = body.find_all("p")
                joined_p = " ".join([p.get_text(strip=True) for p in p_tags if len(p.get_text(strip=True)) > 20])
                if joined_p:
                    return clean_snippet(joined_p, 320)
                return clean_snippet(body.get_text(), 320)
    except Exception:
        pass
    return ""


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


def process_item(source_name, title, link, description, seen, new_seen):
    if not link or not title or len(title) < 4:
        return
    skip_words = [
        "home", "contact", "privacy", "facebook", "twitter", "instagram",
        "youtube", "menu", "sitemap", "read more", "search", "back to"
    ]
    if any(w in title.lower() for w in skip_words):
        return

    job_id = f"{source_name}::{title}::{link}"
    if job_id not in seen:
        new_seen.add(job_id)

        # Если описания еще нет (HTML-сайт), переходим по ссылке и забираем
        if not description:
            description = fetch_page_description(link)

        desc_block = f"\n\n📝 {description}" if description else ""

        msg = (
            f"⚡ <b>Новая вакансия: {source_name}</b>\n\n"
            f"📌 <b>{title}</b>"
            f"{desc_block}\n\n"
            f"🔗 <a href='{link}'>Открыть вакансию</a>"
        )
        send_telegram(msg)


def main():
    seen = load_seen()
    new_seen = set(seen)

    # 1. HTML сайты
    for target in HTML_TARGETS:
        try:
            resp = requests.get(target["url"], headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select(target["selector"]):
                    title = a.get_text(strip=True)
                    href = a.get("href", "")
                    if href and not href.startswith("mailto:"):
                        full_url = urllib.parse.urljoin(target["url"], href)
                        process_item(target["name"], title, full_url, "", seen, new_seen)
        except Exception as e:
            print(f"Ошибка {target['name']}: {e}")

    # 2. RSS фиды
    for feed in RSS_TARGETS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = clean_snippet(entry.get("summary", entry.get("description", "")))
                process_item(feed["name"], title, link, summary, seen, new_seen)
        except Exception as e:
            print(f"Ошибка {feed['name']}: {e}")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
