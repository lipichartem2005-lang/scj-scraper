import json
import os
import urllib.parse
from bs4 import BeautifulSoup
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "seen_jobs.json"

TARGETS = [
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


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Ошибка: не заданы переменные TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    requests.post(url, json=payload, timeout=10)


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


def main():
    seen = load_seen()
    new_seen = set(seen)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for target in TARGETS:
        try:
            resp = requests.get(target["url"], headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            elements = soup.select(target["selector"])

            for el in elements:
                title = el.get_text(strip=True)
                raw_link = el.get("href", "")

                # Игнорируем пустые или нерелевантные пункты меню
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
                job_id = f"{target['name']}::{title}::{full_link}"

                if job_id not in seen:
                    new_seen.add(job_id)
                    message = (
                        f"⚡ <b>Новая вакансия: {target['name']}</b>\n\n"
                        f"📌 {title}\n"
                        f"🔗 <a href='{full_link}'>Открыть объявление</a>"
                    )
                    send_telegram(message)
        except Exception as e:
            print(f"Ошибка при обработке {target['name']}: {e}")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
