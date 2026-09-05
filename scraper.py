import io
import json
import os
import re
import urllib.parse
from bs4 import BeautifulSoup
import feedparser
from pypdf import PdfReader
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "seen_jobs.json"

SESSION = requests.Session()
retries = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
SESSION.mount("https://", HTTPAdapter(max_retries=retries))
SESSION.mount("http://", HTTPAdapter(max_retries=retries))

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
})


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[-] TELEGRAM_TOKEN или CHAT_ID не настроены.")
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
        print(f"[-] Ошибка отправки Telegram: {e}")


def extract_wage(text):
    if not text:
        return None

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
    if hourly_range and any(w in hourly_range.group(0).lower() for w in ["per", "/", "to", "-"]):
        return hourly_range.group(1).strip()

    table_wage = re.search(
        r"(?:wage|rate|salary)[^\$\n\r]{0,25}(\$\s*\d{2,3}(?:\.\d{2}|,\d{3})?(?:\s*(?:-|to)\s*\$?\s*\d{2,3}(?:\.\d{2}|,\d{3})?)?)",
        text,
        re.IGNORECASE,
    )
    if table_wage:
        val = table_wage.group(1).strip()
        if len(val) > 2 and not val.endswith("."):
            return f"{val}/hr" if "." in val else val

    salary_annual = re.search(
        r"(\$\s*\d{2,3},\d{3}\s*(?:-|to)\s*\$?\s*\d{2,3},\d{3})", text
    )
    if salary_annual:
        return f"{salary_annual.group(1).strip()} / год"

    return None


def get_pdf_wage(pdf_url):
    try:
        r = SESSION.get(pdf_url, timeout=10, verify=False)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:
                full_text += " " + (page.extract_text() or "")
            return extract_wage(full_text)
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


def notify(source, title, link, wage, seen, new_seen):
    clean_title = re.sub(r"\s+", " ", title).strip()
    clean_link = link.strip()
    job_id = f"{source}::{clean_title}::{clean_link}"
    if job_id in seen:
        return False

    new_seen.add(job_id)
    wage_str = f"💰 <b>Ставка:</b> {wage}" if wage else "💰 <b>Ставка:</b> указана в объявлении"

    msg = (
        f"⚡ <b>Новая вакансия: {source}</b>\n\n"
        f"📌 <b>{clean_title}</b>\n"
        f"{wage_str}\n\n"
        f"🔗 <a href='{clean_link}'>Открыть вакансию</a>"
    )
    send_telegram(msg)
    return True


def parse_scrd(seen, new_seen):
    url = "https://www.scrd.ca/careers/"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=12, verify=False)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.find_all(re.compile(r"h[2-5]")):
            title = h.get_text(strip=True)
            if "#" in title or any(w in title for w in ["Lifeguard", "Coordinator", "Operator", "Manager", "Driver", "Tech", "Attendant"]):
                a = h.find_next("a", href=re.compile(r"\.pdf", re.I))
                if a:
                    total_found += 1
                    pdf_url = urllib.parse.urljoin(url, a.get("href"))
                    wage = get_pdf_wage(pdf_url)
                    if notify("SCRD", title, pdf_url, wage, seen, new_seen):
                        sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_gibsons(seen, new_seen):
    url = "https://gibsons.ca/town-hall/employment-opportunities/"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=12, verify=False)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent
        soup = BeautifulSoup(r.text, "html.parser")
        opps = soup.find(lambda t: t.name in ["h2", "h3", "h4"] and "current opportunit" in t.get_text().lower())
        box = opps.parent if opps else soup
        for a in box.find_all("a", href=True):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href.lower().endswith(".pdf"):
                continue
            if any(s in title.lower() or s in href.lower() for s in ["form", "policy", "benefit", "handbook", "guide"]):
                continue
            total_found += 1
            full_url = urllib.parse.urljoin("https://gibsons.ca", href)
            wage = get_pdf_wage(full_url)
            if notify("Town of Gibsons", title, full_url, wage, seen, new_seen):
                sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_sechelt(seen, new_seen):
    url = "https://www.sechelt.ca/en/town-hall/employment.aspx"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=15, verify=False, allow_redirects=True)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("main a, #maincontent a, .content a, a[href*='.pdf']"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or len(title) < 5:
                continue
            if any(s in title.lower() for s in ["bylaw", "benefit", "form", "handbook", "policy"]):
                continue

            full_url = urllib.parse.urljoin(url, href)
            if full_url.lower().endswith(".pdf"):
                total_found += 1
                wage = get_pdf_wage(full_url)
                if notify("District of Sechelt", title, full_url, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_bc_ferries(seen, new_seen):
    url = "https://careers.bcferries.com/rss"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=15, verify=False)
        feed = feedparser.parse(r.text) if r.status_code == 200 else feedparser.parse(url)
        for e in feed.entries:
            title = e.get("title", "")
            summary = e.get("summary", "")
            full = f"{title} {summary}".lower()
            if any(loc in full for loc in ["langdale", "sunshine coast", "earls cove", "gibson", "sechelt"]):
                total_found += 1
                link = e.get("link", "")
                wage = extract_wage(summary)
                if notify("BC Ferries", title, link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_bc_liquor(seen, new_seen):
    url = "https://bcliquorstores.prevueaps.ca/jobs/"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=12, verify=False)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/jobs/']"):
            txt = a.get_text(separator=" ", strip=True)
            if any(l in txt.lower() for l in ["gibsons", "sechelt", "sunshine coast"]):
                total_found += 1
                link = urllib.parse.urljoin(url, a.get("href"))
                wage = extract_wage(txt) or "$29.94/hr (BCGEU Grid)"
                if notify("BC Liquor Stores", txt, link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_ywca(seen, new_seen):
    url = "https://ywcabc.org/careers"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=12, verify=False)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/careers/'], a[href*='/job']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if len(title) < 5 or any(w in title.lower() for w in ["learn more", "apply", "view all", "contact", "about"]):
                continue
            total_found += 1
            full_link = urllib.parse.urljoin(url, href)
            parent_text = a.find_parent("div").get_text(separator=" ", strip=True) if a.find_parent("div") else ""
            wage = extract_wage(parent_text)
            if notify("YWCA Careers", title, full_link, wage, seen, new_seen):
                sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_civicjobs(seen, new_seen):
    url = "https://www.civicjobs.ca/rss"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=12, verify=False)
        feed = feedparser.parse(r.text) if r.status_code == 200 else feedparser.parse(url)
        for e in feed.entries:
            t = e.get("title", "")
            s = e.get("summary", "")
            if any(l in f"{t} {s}".lower() for l in ["sunshine coast", "gibsons", "sechelt", "pender harbour"]):
                total_found += 1
                link = e.get("link", "")
                wage = extract_wage(s)
                if notify("CivicJobs BC", t, link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def parse_sd46(seen, new_seen):
    url = "https://www.makeafuture.ca/regions-districts/bc-public-school-districts/sunshine-coast/sunshine-coast-sd46/"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=15, verify=False, allow_redirects=True)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='job'], a[href*='posting']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if len(title) > 6 and not any(w in title.lower() for w in ["apply", "login", "register"]):
                total_found += 1
                full_link = urllib.parse.urljoin(url, href)
                parent_text = a.find_parent("tr").get_text(separator=" ", strip=True) if a.find_parent("tr") else ""
                wage = extract_wage(parent_text)
                if notify("SD46 School District", title, full_link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


def main():
    seen = load_seen()
    new_seen = set(seen)

    sources = [
        ("SCRD (Regional District)", parse_scrd),
        ("Town of Gibsons", parse_gibsons),
        ("District of Sechelt", parse_sechelt),
        ("BC Ferries", parse_bc_ferries),
        ("BC Liquor Stores", parse_bc_liquor),
        ("YWCA Careers", parse_ywca),
        ("CivicJobs BC", parse_civicjobs),
        ("SD46 School District", parse_sd46),
    ]

    report_lines = []
    total_checked = 0
    total_new = 0

    try:
        for name, func in sources:
            status, checked, sent = func(seen, new_seen)
            total_checked += checked
            total_new += sent
            icon = "✅" if "успешно" in status else "⚠️"
            report_lines.append(f"{icon} <b>{name}</b>: проверено {checked} | новых +{sent}")
    finally:
        save_seen(new_seen)

        status_header = (
            f"🔔 <b>Отчет проверки вакансий</b>\n"
            f"Всего проверено: <b>{total_checked}</b>\n"
            f"Новых за цикл: <b>{total_new}</b>\n\n"
            f"<b>Статус по источникам:</b>\n"
        )
        report_text = status_header + "\n".join(report_lines)
        send_telegram(report_text)


if __name__ == "__main__":
    main()
