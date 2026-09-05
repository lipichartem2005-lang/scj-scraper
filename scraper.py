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
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
})


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[-] Ошибка: TELEGRAM_TOKEN или CHAT_ID не настроены!")
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
    hourly_explicit = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?\s*(?:per\s*hour|\/\s*hr|hourly))",
        text,
        re.IGNORECASE,
    )
    if hourly_explicit:
        return hourly_explicit.group(1).strip()

    hourly_range = re.search(
        r"(\$\s*\d{2}(?:\.\d{2})?\s*(?:-|to)\s*\$?\s*\d{2}(?:\.\d{2})?(?:\s*(?:per\s*hour|\/\s*hr|hourly))?)",
        text,
        re.IGNORECASE,
    )
    if hourly_range and any(
        w in hourly_range.group(0).lower() for w in ["per", "/", "to", "-"]
    ):
        return hourly_range.group(1).strip()

    table_wage = re.search(
        r"(?:wage|rate|salary)[^\$\n\r]{0,30}(\$\s*\d{2,3}(?:\.\d{2}|,\d{3})?(?:\s*(?:-|to)\s*\$?\s*\d{2,3}(?:\.\d{2}|,\d{3})?)?)",
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
        return f"{salary_annual.group(1).strip()} per year"

    return None


def clean_meaningful_text(text, max_len=360):
    patterns_to_remove = [
        r"The Sunshine Coast.*?Hike the trails.*?(attend|cross - country skiing|culture)[,\.]?",
        r"The Sunshine Coast A natural paradise.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Bordered by rugged mountains.*?Skwxw[uú]7mesh.*?Nations?[,\.]?",
        r"Whatever hobby or interest you might enjoy.*?attend[,\.]?",
    ]
    for p in patterns_to_remove:
        text = re.sub(p, "", text, flags=re.IGNORECASE | re.DOTALL)

    markers = [
        "The Opportunity",
        "Position Overview",
        "About the Role",
        "Job Summary",
        "Duties",
        "Key Responsibilities",
        "Summary",
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
        r = SESSION.get(pdf_url, timeout=12)
        if r.status_code == 200:
            reader = PdfReader(io.BytesIO(r.content))
            full_text = ""
            for page in reader.pages[:2]:
                full_text += " " + (page.extract_text() or "")

            job_keywords = [
                "duties",
                "qualifications",
                "experience",
                "hourly",
                "wage",
                "salary",
                "apply",
            ]
            if not any(k in full_text.lower() for k in job_keywords):
                return None

            wage = extract_wage(full_text)
            body = clean_meaningful_text(full_text)
            wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
            return f"{wage_line}{body}"
    except Exception as e:
        print(f"[-] Ошибка PDF {pdf_url}: {e}")
    return None


def load_seen():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                print(f"[*] Загружено из кэша: {len(data)} позиций")
                return set(data)
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
        return False

    new_seen.add(job_id)
    print(f"[+] НАЙДЕНА НОВАЯ: [{source}] {clean_title}")
    msg = (
        f"⚡ <b>Новая вакансия: {source}</b>\n\n"
        f"📌 <b>{clean_title}</b>\n\n"
        f"📝 {description}\n\n"
        f"🔗 <a href='{link}'>Открыть вакансию</a>"
    )
    send_telegram(msg)
    return True


def parse_scrd(seen, new_seen):
    url = "https://www.scrd.ca/careers/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for h in soup.find_all(re.compile(r"h[2-5]")):
                title = h.get_text(strip=True)
                if not ("#" in title or any(w in title for w in ["Lifeguard", "Coordinator", "Operator", "Manager", "Assistant", "Driver", "Tech", "Attendant"])):
                    continue
                link_tag = h.find_next("a", href=re.compile(r"\.pdf", re.I))
                if link_tag:
                    pdf_link = urllib.parse.urljoin(url, link_tag.get("href"))
                    snippet = extract_pdf_snippet(pdf_link)
                    if snippet and notify_job("SCRD", title, pdf_link, snippet, seen, new_seen):
                        sent += 1
    except Exception as e:
        print(f"[-] SCRD: {e}")
    return sent


def parse_gibsons(seen, new_seen):
    url = "https://gibsons.ca/town-hall/employment-opportunities/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            curr_opps = soup.find(lambda tag: tag.name in ["h2", "h3", "h4"] and "current opportunit" in tag.get_text().lower())
            container = curr_opps.parent if curr_opps else soup
            for a in container.find_all("a", href=True):
                href = a.get("href", "").strip()
                title = a.get_text(strip=True)
                full_url = urllib.parse.urljoin(url, href)
                if not (full_url.lower().endswith(".pdf") or "/careers/" in full_url.lower()):
                    continue
                if any(s in title.lower() or s in href.lower() for s in ["application", "volunteer", "guide", "form", "policy"]):
                    continue
                if len(title) < 5 or any(j in title.lower() for j in ["click here", "pdf", "download"]):
                    prev_h = a.find_previous(["h3", "h4", "h5", "p", "strong"])
                    if prev_h and len(prev_h.get_text(strip=True)) > 5:
                        title = prev_h.get_text(strip=True)
                    else:
                        continue
                snippet = extract_pdf_snippet(full_url)
                if snippet and notify_job("Town of Gibsons", title, full_url, snippet, seen, new_seen):
                    sent += 1
    except Exception as e:
        print(f"[-] Gibsons: {e}")
    return sent


def parse_sechelt(seen, new_seen):
    url = "https://www.sechelt.ca/en/town-hall/employment.aspx"
    sent = 0
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href*='.pdf'], .content a"):
                href = a.get("href", "")
                title = a.get_text(strip=True)
                if not href.lower().endswith(".pdf") or len(title) < 6:
                    continue
                if any(s in title.lower() for s in ["form", "benefit", "bylaw", "handbook", "agreement"]):
                    continue
                full_url = urllib.parse.urljoin(url, href)
                snippet = extract_pdf_snippet(full_url)
                if snippet and notify_job("District of Sechelt", title, full_url, snippet, seen, new_seen):
                    sent += 1
    except Exception as e:
        print(f"[-] Sechelt: {e}")
    return sent


def parse_bc_liquor(seen, new_seen):
    url = "https://bcliquorstores.prevueaps.ca/jobs/"
    sent = 0
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href*='/jobs/']"):
                text = a.get_text(separator=" ", strip=True)
                href = a.get("href", "")
                if any(loc in text.lower() for loc in ["gibsons", "sechelt", "sunshine coast"]):
                    link = urllib.parse.urljoin(url, href)
                    wage = extract_wage(text) or "$29.94/hr (BCGEU)"
                    prefix = f"💰 <b>Ставка:</b> {wage}\n\n"
                    if notify_job("BC Liquor Stores", text, link, f"{prefix}Розничная должность в государственной сети BCLDB.", seen, new_seen):
                        sent += 1
    except Exception as e:
        print(f"[-] BC Liquor: {e}")
    return sent


def parse_feed(source_name, query, seen, new_seen):
    """Парсер RSS-потоков с обходом блокировок Cloudflare."""
    url = f"https://ca.indeed.com/rss?q={urllib.parse.quote(query)}&l={urllib.parse.quote('Sunshine Coast, BC')}"
    sent = 0
    try:
        resp = SESSION.get(url, timeout=12)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                wage = extract_wage(summary)
                desc = clean_meaningful_text(BeautifulSoup(summary, "html.parser").get_text())
                prefix = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
                if notify_job(source_name, title, link, f"{prefix}{desc}", seen, new_seen):
                    sent += 1
        else:
            print(f"[-] {source_name} feed HTTP status {resp.status_code}")
    except Exception as e:
        print(f"[-] Ошибка фида {source_name}: {e}")
    return sent


def parse_civicjobs(seen, new_seen):
    url = "https://www.civicjobs.ca/rss"
    sent = 0
    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "")
                full = f"{title} {summary}".lower()
                if any(loc in full for loc in ["sunshine coast", "gibsons", "sechelt", "pender harbour"]):
                    link = entry.get("link", "").strip()
                    wage = extract_wage(summary)
                    desc = clean_meaningful_text(BeautifulSoup(summary, "html.parser").get_text())
                    wage_line = f"💰 <b>Ставка:</b> {wage}\n\n" if wage else ""
                    if notify_job("CivicJobs BC", title, link, f"{wage_line}{desc}", seen, new_seen):
                        sent += 1
    except Exception as e:
        print(f"[-] CivicJobs: {e}")
    return sent


def parse_sd46(seen, new_seen):
    url = "https://www.makeafuture.ca/bc-schools-and-districts/sunshine-coast-school-district-no-46/feed/"
    sent = 0
    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = clean_meaningful_text(BeautifulSoup(entry.get("summary", ""), "html.parser").get_text())
                if notify_job("SD46 (School District)", title, link, summary, seen, new_seen):
                    sent += 1
    except Exception as e:
        print(f"[-] SD46: {e}")
    return sent


def main():
    seen = load_seen()
    new_seen = set(seen)
    total_new = 0

    print("[*] Старт сканирования всех работодателей Sunshine Coast...")

    total_new += parse_scrd(seen, new_seen)
    total_new += parse_gibsons(seen, new_seen)
    total_new += parse_sechelt(seen, new_seen)
    total_new += parse_bc_liquor(seen, new_seen)

    total_new += parse_feed("Canada Post", "Canada Post", seen, new_seen)
    total_new += parse_feed("Vancouver Coastal Health", "Vancouver Coastal Health", seen, new_seen)
    total_new += parse_feed("BC Ferries", "BC Ferries", seen, new_seen)
    total_new += parse_feed("RainCity Housing", "RainCity Housing", seen, new_seen)
    total_new += parse_feed("SCACL", "Sunshine Coast Association for Community Living", seen, new_seen)
    total_new += parse_feed("SCCSS", "Sunshine Coast Community Services", seen, new_seen)

    total_new += parse_civicjobs(seen, new_seen)
    total_new += parse_sd46(seen, new_seen)

    # Отчет, если ничего нового нет
    if total_new == 0:
        print("[*] Новых вакансий не обнаружено.")
        send_telegram("ℹ️ <b>Проверка завершена:</b> новых вакансий не найдено.")
    else:
        print(f"[+] Всего отправлено новых вакансий: {total_new}")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
