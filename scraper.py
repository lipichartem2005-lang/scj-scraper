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

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
})


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[-] Ошибка: переменные TELEGRAM_TOKEN или CHAT_ID не заданы.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = SESSION.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[-] Telegram API Error ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"[-] Сетевой сбой при отправке в Telegram: {e}")


def extract_wage(text):
    hourly = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?\s*(?:per\s*hour|\/\s*hr|hourly))",
        text,
        re.IGNORECASE,
    )
    if hourly:
        return hourly.group(1).strip()

    hourly_range = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?\s*(?:-|to)\s*\$?\s*\d{2}(?:\.\d{2})?(?:\s*(?:per\s*hour|\/\s*hr|hourly))?)",
        text,
        re.IGNORECASE,
    )
    if hourly_range and any(
        w in hourly_range.group(0).lower() for w in ["per", "/", "to", "-"]
    ):
        return hourly_range.group(1).strip()

    salary = re.search(
        r"(\$\s*\d{2,3},\d{3}\s*(?:-|to)\s*\$?\s*\d{2,3},\d{3})", text
    )
    if salary:
        return f"{salary.group(1).strip()} per year"

    return None


def clean_text(text, max_len=350):
    patterns = [
        (
            r"The Sunshine Coast.*?Hike the trails.*?(attend|cross - country"
            r" skiing|culture)[,\.]?"
        ),
        r"The Sunshine Coast A natural paradise.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Bordered by rugged mountains.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.IGNORECASE | re.DOTALL)

    markers = [
        "The Opportunity",
        "Position Overview",
        "About the Role",
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


def extract_pdf(url):
    try:
        r = SESSION.get(url, timeout=8)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            txt = ""
            for page in reader.pages[:2]:
                txt += " " + (page.extract_text() or "")
            if any(
                k in txt.lower()
                for k in ["duties", "qualifications", "wage", "salary", "apply"]
            ):
                wage = extract_wage(txt)
                body = clean_text(txt)
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
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[-] Ошибка записи кэша: {e}")


def notify(source, title, link, desc, seen, new_seen):
    clean_title = re.sub(r"\s+", " ", title).strip()
    job_id = f"{source}::{clean_title}::{link}"
    if job_id in seen:
        return False

    new_seen.add(job_id)
    msg = (
        f"⚡ <b>Новая вакансия: {source}</b>\n\n"
        f"📌 <b>{clean_title}</b>\n\n"
        f"📝 {desc}\n\n"
        f"🔗 <a href='{link}'>Открыть вакансию</a>"
    )
    send_telegram(msg)
    return True


def parse_scrd(seen, new_seen):
    url = "https://www.scrd.ca/careers/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.find_all(re.compile(r"h[2-5]")):
            title = h.get_text(strip=True)
            if "#" in title or any(
                w in title
                for w in [
                    "Lifeguard",
                    "Coordinator",
                    "Operator",
                    "Manager",
                    "Driver",
                    "Tech",
                    "Attendant",
                ]
            ):
                a = h.find_next("a", href=re.compile(r"\.pdf", re.I))
                if a:
                    pdf_url = urllib.parse.urljoin(url, a.get("href"))
                    desc = extract_pdf(pdf_url)
                    if desc and notify(
                        "SCRD", title, pdf_url, desc, seen, new_seen
                    ):
                        sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def parse_gibsons(seen, new_seen):
    url = "https://gibsons.ca/town-hall/employment-opportunities/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        soup = BeautifulSoup(r.text, "html.parser")
        opps = soup.find(
            lambda t: t.name in ["h2", "h3", "h4"]
            and "current opportunit" in t.get_text().lower()
        )
        box = opps.parent if opps else soup
        for a in box.find_all("a", href=True):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href.lower().endswith(".pdf"):
                continue
            if any(
                s in title.lower() or s in href.lower()
                for s in ["form", "policy", "benefit", "handbook", "guide"]
            ):
                continue
            full_url = urllib.parse.urljoin(url, href)
            desc = extract_pdf(full_url)
            if desc and notify(
                "Town of Gibsons", title, full_url, desc, seen, new_seen
            ):
                sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def parse_sechelt(seen, new_seen):
    url = "https://www.sechelt.ca/en/town-hall/employment.aspx"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='.pdf']"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if len(title) < 5 or any(
                s in title.lower() for s in ["bylaw", "benefit", "form"]
            ):
                continue
            full_url = urllib.parse.urljoin(url, href)
            desc = extract_pdf(full_url)
            if desc and notify(
                "District of Sechelt", title, full_url, desc, seen, new_seen
            ):
                sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def parse_bc_liquor(seen, new_seen):
    url = "https://bcliquorstores.prevueaps.ca/jobs/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/jobs/']"):
            txt = a.get_text(separator=" ", strip=True)
            if any(
                l in txt.lower() for l in ["gibsons", "sechelt", "sunshine coast"]
            ):
                link = urllib.parse.urljoin(
                    "https://bcliquorstores.prevueaps.ca", a.get("href")
                )
                wage = extract_wage(txt) or "$29.94/hr (BCGEU)"
                if notify(
                    "BC Liquor Stores",
                    txt,
                    link,
                    f"💰 <b>Ставка:</b> {wage}\n\nРозничная вакансия BCLDB.",
                    seen,
                    new_seen,
                ):
                    sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def parse_ywca(seen, new_seen):
    """Парсер карьерного портала YWCA (Metro Vancouver / Sunshine Coast)."""
    url = "https://ywcabc.org/careers"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        soup = BeautifulSoup(r.text, "html.parser")

        # Ищем карточки и строки вакансий
        for a in soup.select("a[href*='/careers/'], a[href*='/job']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if len(title) < 5 or any(
                w in title.lower()
                for w in ["learn more", "apply", "view all", "contact", "about"]
            ):
                continue

            full_link = urllib.parse.urljoin(url, href)
            # Извлекаем краткий сниппет из родительского блока
            parent_text = (
                a.find_parent("div").get_text(separator=" ", strip=True)
                if a.find_parent("div")
                else ""
            )

            # Проверяем привязку к Sunshine Coast, если указано
            is_sc = any(
                w in parent_text.lower()
                for w in [
                    "sunshine coast",
                    "gibsons",
                    "sechelt",
                    "transition house",
                ]
            )
            prefix_info = (
                "📍 <i>Sunshine Coast / Regional</i>\n" if is_sc else ""
            )

            wage = extract_wage(parent_text)
            wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
            desc = clean_text(parent_text) or "Карьерная позиция в сети YWCA."

            if notify(
                "YWCA Metro Vancouver",
                title,
                full_link,
                f"{prefix_info}{wage_line}{desc}",
                seen,
                new_seen,
            ):
                sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def parse_civicjobs(seen, new_seen):
    url = "https://www.civicjobs.ca/rss"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            t = e.get("title", "")
            s = e.get("summary", "")
            if any(
                l in f"{t} {s}".lower()
                for l in [
                    "sunshine coast",
                    "gibsons",
                    "sechelt",
                    "pender harbour",
                ]
            ):
                link = e.get("link", "")
                wage = extract_wage(s)
                desc = clean_text(BeautifulSoup(s, "html.parser").get_text())
                wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
                if notify(
                    "CivicJobs BC",
                    t,
                    link,
                    f"{wage_line}{desc}",
                    seen,
                    new_seen,
                ):
                    sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def parse_sd46(seen, new_seen):
    url = "https://www.makeafuture.ca/bc-schools-and-districts/sunshine-coast-school-district-no-46/feed/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return "ошибка сети", sent
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            t = e.get("title", "")
            link = e.get("link", "")
            desc = clean_text(
                BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            )
            if notify("SD46 (School District)", t, link, desc, seen, new_seen):
                sent += 1
        return "успешно", sent
    except Exception as e:
        return f"ошибка ({e})", 0


def main():
    seen = load_seen()
    new_seen = set(seen)

    sources = [
        ("SCRD (Regional District)", parse_scrd),
        ("Town of Gibsons", parse_gibsons),
        ("District of Sechelt", parse_sechelt),
        ("BC Liquor Stores", parse_bc_liquor),
        ("YWCA Careers", parse_ywca),
        ("CivicJobs BC", parse_civicjobs),
        ("SD46 School District", parse_sd46),
    ]

    report_lines = []
    total_new = 0

    print("[*] Старт проверки вакансий на Sunshine Coast...")

    try:
        for name, func in sources:
            status, sent = func(seen, new_seen)
            total_new += sent
            icon = "✅" if "успешно" in status else "⚠️"
            report_lines.append(f"{icon} <b>{name}</b>: новых +{sent}")
    finally:
        save_seen(new_seen)

        # Формирование и отправка итогового отчета
        status_header = (
            f"🔔 <b>Отчет проверки вакансий</b>\n"
            f"Всего новых за цикл: <b>{total_new}</b>\n\n"
            f"<b>Статус по источникам:</b>\n"
        )
        report_text = status_header + "\n".join(report_lines)
        send_telegram(report_text)


if __name__ == "__main__":
    main()
