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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# 1. Сайты для обычного HTML-парсинга
HTML_TARGETS = [
    {
        "name": "Town of Gibsons",
        "url": "https://gibsons.ca/town-hall/employment-opportunities/",
        "selector": "a[href*='pdf'], .entry-content a",
    },
    {
        "name": "SCRD (Regional District)",
        "url": "https://www.scrd.ca/careers/",
        "selector": "a[href*='career'], a[href*='job'], .entry-content a",
    },
    {
        "name": "District of Sechelt",
        "url": "https://www.sechelt.ca/en/town-hall/employment.aspx",
        "selector": "a[href*='pdf'], .main-content a",
    },
]

# 2. RSS-ленты (CivicJobs и MakeAFuture / SD46)
RSS_TARGETS = [
    {
        "name": "CivicJobs BC (Sunshine Coast)",
        "url": (
            "https://www.civicjobs.ca/rss?region=Sunshine+Coast"
        ),  # агрегатор всех муниципалитетов региона
    },
    {
        "name": "SD46 (School District 46)",
        "url": (
            "https://www.makeafuture.ca/bc-schools-and-districts/sunshine-coast-school-district-no-46/feed/"
        ),
    },
]


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Ошибка: не заданы TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
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
        print(f"Ошибка отправки сообщения: {e}")


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
    job_id = f"{source_name}::{title}::{link}"
    if job_id not in seen:
        new_seen.add(job_id)
        msg = (
            f"⚡ <b>Новая вакансия: {source_name}</b>\n\n"
            f"📌 {title}\n"
            f"🔗 <a href='{link}'>Открыть вакансию</a>"
        )
        send_telegram(msg)


def scrape_html(seen, new_seen):
    for target in HTML_TARGETS:
        try:
            resp = requests.get(target["url"], headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for el in soup.select(target["selector"]):
                title = el.get_text(strip=True)
                raw_link = el.get("href", "")
                if not title or len(title) < 5 or not raw_link:
                    continue
                if any(
                    skip in title.lower()
                    for skip in [
                        "home",
                        "contact",
                        "privacy",
                        "accessibility",
                        "read more",
                    ]
                ):
                    continue
                full_link = urllib.parse.urljoin(target["url"], raw_link)
                process_item(target["name"], title, full_link, seen, new_seen)
        except Exception as e:
            print(f"Ошибка HTML-парсинга {target['name']}: {e}")


def scrape_rss(seen, new_seen):
    for feed in RSS_TARGETS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if title and link:
                    process_item(feed["name"], title, link, seen, new_seen)
        except Exception as e:
            print(f"Ошибка RSS {feed['name']}: {e}")


def scrape_vch(seen, new_seen):
    # Vancouver Coastal Health: фильтр по Sechelt и Gibsons
    url = "https://careers.vch.ca/api/jobs"
    params = {"keywords": "Gibsons OR Sechelt", "sortBy": "relevance", "page": 1}
    try:
        resp = requests.get(
            url,
            params=params,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            for job in data.get("jobs", []):
                title = job.get("data", {}).get("title")
                slug = job.get("data", {}).get("slug")
                if title and slug:
                    link = f"https://careers.vch.ca/jobs/{slug}"
                    process_item(
                        "VCH (Health Care)", title, link, seen, new_seen
                    )
    except Exception as e:
        print(f"Ошибка VCH: {e}")


def main():
    seen = load_seen()
    new_seen = set(seen)

    scrape_html(seen, new_seen)
    scrape_rss(seen, new_seen)
    scrape_vch(seen, new_seen)

    save_seen(new_seen)


if __name__ == "__main__":
    main()
