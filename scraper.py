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
    """Извлекает ставку в $/час или годовую зарплату даже из табличных колонок."""
    # 1. Почасовая со словами per hour / hr / hourly: $27.98 per hour
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

    # 3. Поиск по строке заголовка Wage/Salary
    table_wage = re.search(
        r"(?:wage|rate|salary)[^\$\n\r]{0,30}(\$\s*\d{2,3}(?:\.\d{2}|,\d{3})?(?:\s*(?:-|to)\s*\$?\s*\d{2,3}(?:\.\d{2}|,\d{3})?)?)",
        text,
        re.IGNORECASE,
    )
    if table_wage:
        val = table_wage.group(1).strip()
        if len(val) > 2 and not val.endswith("."):
            return f"{val}/hr" if "." in val else val

    # 4. Годовой оклад: $86,312 to $109,614
    salary_annual = re.search(
        r"(\$\s*\d{2,3},\d{3}\s*(?:-|to)\s*\$?\s*\d{2,3},\d{3})", text
    )
    if salary_annual:
        return f"{salary_annual.group(1).strip()} per year"

    return None


def clean_meaningful_text(text, max_len=360):
    """Полностью срезает любые вариации рекламного туристского текста SCRD."""
    # Жесткий срез всех вариантов промо-текстов региона
    patterns_to_remove = [
        r"The Sunshine Coast.*?Hike the trails.*?(attend|cross - country skiing|culture)[,\.]?",
        r"The Sunshine Coast A natural paradise.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Bordered by rugged mountains.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Whatever hobby or interest you might enjoy.*?attend[,\.]?",
    ]
    for pattern in patterns_to_remove:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    # Принудительно стартуем с маркеров обязанностей/описания роли
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
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:
                full_text += " " + (page.extract_text() or "")

            wage = extract_wage(full_text)
            body = clean_meaningful_text(full_text)

            wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
            return f"{wage_line}{body}"
    except Exception:
        pass
    return "📄 <i>Подробности и требования указаны в прикрепленном документе.</i>"


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
    # Очищаем заголовки от лишних знаков
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
    """Сбор открытых позиций SCRD."""
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
                    ]
                )
            ):
                continue

            link_tag = h.find_next("a", href=re.compile(r"\.pdf", re.I))
            if link_tag:
                pdf_link = urllib.parse.urljoin(url, link_tag.get("href"))
                snippet = extract_pdf_snippet(pdf_link)
                notify_job("SCRD", title, pdf_link, snippet, seen, new_seen)
    except Exception as e:
        print(f"Ошибка SCRD: {e}")


def parse_gibsons(seen, new_seen):
    """Сбор вакансий Town of Gibsons по всем доступным ссылкам и кнопкам."""
    url = "https://gibsons.ca/town-hall/employment-opportunities/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        soup = BeautifulSoup(r.text, "html.parser")

        # Ищем все ссылки на PDF или внутренние страницы вакансий
        for a in soup.select("a[href*='.pdf'], .entry-content a, main a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not href or len(title) < 5:
                continue

            # Отсекаем мусор и навигацию сайта
            if any(
                w in title.lower()
                for w in [
                    "council",
                    "meeting",
                    "hearing",
                    "bylaw",
                    "form",
                    "policy",
                    "guide",
                    "map",
                    "contact",
                ]
            ):
                continue

            full_url = urllib.parse.urljoin(url, href)
            if full_url.lower().endswith(".pdf"):
                snippet = extract_pdf_snippet(full_url)
            else:
                snippet = "📄 <i>Официальное объявление муниципалитета Gibsons.</i>"

            notify_job(
                "Town of Gibsons", title, full_url, snippet, seen, new_seen
            )
    except Exception as e:
        print(f"Ошибка Gibsons: {e}")


def parse_civicjobs_rss(seen, new_seen):
    """Парсинг через стабильный официальный RSS-поток CivicJobs BC."""
    url = "https://www.civicjobs.ca/rss"
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            full_text = f"{title} {summary}"

            # Фильтруем позиции только для нашего региона
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
                BeautifulSoup(
                    entry.get("summary", ""), "html.parser"
                ).get_text()
            )
            notify_job(
                "SD46 (School District)", title, link, summary, seen, new_seen
            )
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
