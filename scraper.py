import json
import os
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
        print("ОШИБКА: Секреты TELEGRAM_TOKEN или CHAT_ID не найдены в ENV!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"Telegram вернул ошибку {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Сбой сети Telegram: {e}")


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


def process_item(source_name, title, link, seen, new_seen):
    if not link or not title or len(title) < 4:
        return
    # Исключаем служебные ссылки сайтов
    skip_words = [
        "home",
        "contact",
        "privacy",
        "facebook",
        "twitter",
        "instagram",
        "youtube",
        "menu",
        "sitemap",
    ]
    if any(w in title.lower() for w in skip_words):
        return

    job_id = f"{source_name}::{title}::{link}"
    if job_id not in seen:
        new_seen.add(job_id)
        msg = (
            f"⚡ <b>Новая вакансия: {source_name}</b>\n\n"
            f"📌 {title}\n"
            f"🔗 <a href='{link}'>Открыть ссылку</a>"
        )
        send_telegram(msg)


def main():
    seen = load_seen()
    new_seen = set(seen)

    # Проверочный сигнал при первом старте (если база вакансий пустая)
    if not seen:
        send_telegram("🤖 Бот успешно запущен и начинает сбор вакансий...")

    for target in HTML_TARGETS:
        try:
            resp = requests.get(target["url"], headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.select(target["selector"])
                for a in links:
                    title = a.get_text(strip=True)
                    href = a.get("href", "")
                    if href and not href.startswith("mailto:"):
                        full_url = urllib.parse.urljoin(target["url"], href)
                        process_item(
                            target["name"], title, full_url, seen, new_seen
                        )
        except Exception as e:
            print(f"Ошибка сайта {target['name']}: {e}")

    for feed in RSS_TARGETS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                process_item(feed["name"], title, link, seen, new_seen)
        except Exception as e:
            print(f"Ошибка ленты {feed['name']}: {e}")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
