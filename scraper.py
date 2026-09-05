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
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
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


# 1. SCRD
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


# 2. Town of Gibsons
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


# 3. District of Sechelt (исправлен URL и парсинг внутренней таблицы)
def parse_sechelt(seen, new_seen):
    url = "https://www.sechelt.ca/en/work-and-business/careers-with-sechelt.aspx"
    fallback_url = "https://www.sechelt.ca/en/town-hall/employment.aspx"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=15, verify=False, allow_redirects=True)
        if r.status_code != 200:
            r = SESSION.get(fallback_url, timeout=15, verify=False, allow_redirects=True)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent

        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select("table a, .content a, main a, a[href*='.pdf']")
        for a in links:
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or len(title) < 4:
                continue
            if any(s in title.lower() for s in ["bylaw", "benefit", "form", "handbook", "policy", "home"]):
                continue

            full_url = urllib.parse.urljoin("https://www.sechelt.ca", href)
            if full_url.lower().endswith(".pdf") or "job" in full_url.lower():
                total_found += 1
                wage = get_pdf_wage(full_url) if full_url.lower().endswith(".pdf") else None
                if notify("District of Sechelt", title, full_url, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


# 4. BC Ferries (парсинг открытого поискового эндпоинта)
def parse_bc_ferries(seen, new_seen):
    # Прямой поисковый запрос без блокировок
    url = "https://careers.bcferries.com/search/?q=Langdale&locationsearch="
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=15, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select(".job-tile, tr.data-row, .searchResults tr")
            for row in rows:
                link_tag = row.find("a", href=re.compile(r"/job/"))
                if link_tag:
                    title = link_tag.get_text(strip=True)
                    link = urllib.parse.urljoin("https://careers.bcferries.com", link_tag.get("href"))
                    total_found += 1
                    row_txt = row.get_text(separator=" ", strip=True)
                    wage = extract_wage(row_txt)
                    if notify("BC Ferries", title, link, wage, seen, new_seen):
                        sent += 1
            return "успешно", total_found, sent
        return f"статус {r.status_code}", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


# 5. BC Liquor Stores (сканирование портала вакансий)
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
            total_found += 1
            # Если есть в нашем регионе — отправляем
            if any(l in txt.lower() for l in ["gibsons", "sechelt", "sunshine coast"]):
                link = urllib.parse.urljoin(url, a.get("href"))
                wage = extract_wage(txt) or "$29.94/hr (BCGEU)"
                if notify("BC Liquor Stores", txt, link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


# 6. YWCA (Metro Vancouver & Sunshine Coast)
def parse_ywca(seen, new_seen):
    url = "https://ywcabc.org/careers"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=15, verify=False)
        if r.status_code != 200:
            return f"статус {r.status_code}", total_found, sent
        soup = BeautifulSoup(r.text, "html.parser")
        # Парсим карточки и ссылки внутри карьерного раздела
        items = soup.select(".view-content .views-row, article, a[href*='careers']")
        for item in items:
            link_tag = item.find("a") if item.name != "a" else item
            if not link_tag:
                continue
            title = link_tag.get_text(strip=True)
            href = link_tag.get("href", "")
            if len(title) < 6 or any(w in title.lower() for w in ["learn more", "apply", "contact", "about"]):
                continue
            total_found += 1
            full_link = urllib.parse.urljoin(url, href)
            wage = extract_wage(item.get_text())
            # Отбираем позиции для нашего региона или общие
            if any(l in item.get_text().lower() for l in ["sunshine coast", "sechelt", "gibsons", "transition house"]):
                if notify("YWCA (Sunshine Coast)", title, full_link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


# 7. CivicJobs BC
def parse_civicjobs(seen, new_seen):
    url = "https://www.civicjobs.ca/rss"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(url, timeout=12, verify=False)
        feed = feedparser.parse(r.text) if r.status_code == 200 else feedparser.parse(url)
        for e in feed.entries:
            t = e.get("title", "")
            s = e.get("summary", "")
            total_found += 1
            if any(l in f"{t} {s}".lower() for l in ["sunshine coast", "gibsons", "sechelt", "pender harbour"]):
                link = e.get("link", "")
                wage = extract_wage(s)
                if notify("CivicJobs BC", t, link, wage, seen, new_seen):
                    sent += 1
        return "успешно", total_found, sent
    except Exception as e:
        return f"ошибка ({e.__class__.__name__})", total_found, sent


# 8. SD46 School District (Make A Future API)
def parse_sd46(seen, new_seen):
    # Прямой эндпоинт MakeAFuture для SD46
    url = "https://makeafuture.applytoeducation.com/JobSearch/JobSearchEngine.aspx?region=23&employer=140"
    fallback_url = "https://www.sd46.bc.ca/employment/"
    total_found, sent = 0, 0
    try:
        r = SESSION.get(fallback_url, timeout=15, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href*='posting'], a[href*='job'], a[href*='makeafuture']"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if len(title) > 5 and not any(w in title.lower() for w in ["apply", "login"]):
                    total_found += 1
                    full_link = urllib.parse.urljoin(fallback_url, href)
                    wage = extract_wage(title)
                    if notify("SD46 School District", title, full_link, wage, seen, new_seen):
                        sent += 1
            return "успешно", total_found, sent
        return f"статус {r.status_code}", total_found, sent
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
