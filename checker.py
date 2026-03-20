"""
Oktoberfest Augustiner Festhalle — reservation availability checker.

Monitors www.oktoberfest-booking.com and alerts via WhatsApp (Twilio)
as soon as a reservation link for the Augustiner tent appears.
Also monitors booking sites for a specific target date (26.09.2026).

Required env vars:
  TWILIO_ACCOUNT_SID  – Twilio Account SID (starts with AC...)
  TWILIO_AUTH_TOKEN   – Twilio Auth Token
  TWILIO_FROM         – Twilio WhatsApp number, e.g. whatsapp:+14155238886
  WA_TO               – Your WhatsApp number, e.g. whatsapp:+393407480234

Optional env vars:
  SIMULATE_DATE       – Set to "1" to force a date availability notification (testing)
"""

import os
import json
import time
import random
import sys
from datetime import datetime, timezone

import requests
import cloudscraper
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM        = os.environ["TWILIO_FROM"]
WA_TO              = os.environ["WA_TO"]

TARGET_URL = os.environ.get("OKTOBERFEST_URL", "https://www.oktoberfest-booking.com")
SIMULATE_DATE = os.environ.get("SIMULATE_DATE", "0") == "1"

AUGUSTINER_KEYWORDS = ["augustiner", "augustiner-festhalle", "augustinerfesthalle"]
BOOKING_UTM = "utm_source=newsbanner_oktobook"

# Date to monitor across all booking sites
TARGET_DATE = "26.09"

# Sites that show dates as visible text (confirmed working)
DATE_CHECK_SITES = [
    ("Löwenbräu",     "https://reservierung.loewenbraeuzelt.de/reservierung"),
    ("Hofbräu",       "https://reservierung.hb-festzelt.de/reservierung"),
    ("Himmel Bayern", "https://reservierung.derhimmelderbayern.de/reservierung/"),
    ("Paulaner",      "https://reservierung.paulanerfestzelt.de/reservierung/"),
]

STATE_FILE = "state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.9,de-DE;q=0.8,de;q=0.7,en-US;q=0.6,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma":         "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "notified": False,
        "new_link_notified": False,
        "known_links": [],
        "date_notified": [],   # list of tent names already notified for target date
        "last_check": None,
        "errors": 0,
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── Page fetching ─────────────────────────────────────────────────────────────

def fetch_with_requests(url: str) -> str:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update(HEADERS)
    scraper.get("https://www.oktoberfest-booking.com", timeout=30)
    time.sleep(random.uniform(1.5, 3.0))
    resp = scraper.get(url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_with_playwright(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="it-IT",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda r: r.abort())
        page.goto(url, wait_until="networkidle", timeout=60000)
        time.sleep(random.uniform(3, 6))
        html = page.content()
        browser.close()
    return html


def fetch_booking_site(url: str) -> str:
    """Fetch a tent booking site with Playwright (they don't have Cloudflare issues)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="de-DE",
        ).new_page()
        page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda r: r.abort())
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(2)
        text = page.inner_text("body")
        browser.close()
    return text

# ── Availability checks ────────────────────────────────────────────────────────

def extract_booking_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    links = {}
    for tag in soup.find_all("a", href=True):
        if BOOKING_UTM in tag["href"]:
            links[tag["href"]] = tag.get_text(strip=True)
    return links


def find_augustiner_link(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        href_lower = href.lower()
        if (href_lower.startswith("http")
                and any(kw in href_lower for kw in AUGUSTINER_KEYWORDS)
                and BOOKING_UTM in href_lower):
            return href
    return None


def check_main_site(known_links: list) -> tuple[bool, str | None, list, list]:
    time.sleep(random.uniform(1.0, 3.0))
    html = None
    try:
        html = fetch_with_requests(TARGET_URL)
        print("  Fetched with requests.")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 429, 503):
            print(f"  Blocked ({e.response.status_code}) — trying Playwright fallback...")
            html = fetch_with_playwright(TARGET_URL)
            print("  Fetched with Playwright.")
        else:
            raise

    augustiner_link = find_augustiner_link(html)
    booking_links   = extract_booking_links(html)
    new_links       = [url for url in booking_links if url not in known_links]
    return (augustiner_link is not None), augustiner_link, list(booking_links.keys()), new_links


def check_date_availability(already_notified: list) -> list[tuple[str, str]]:
    """
    Checks each booking site for TARGET_DATE.
    Returns list of (tent_name, url) where date is newly available.
    """
    if SIMULATE_DATE:
        print("  [SIMULATE] Forcing date found on Paulaner")
        name = "Paulaner"
        url  = "https://reservierung.paulanerfestzelt.de/reservierung/"
        return [(name, url)] if name not in already_notified else []

    found = []
    for name, url in DATE_CHECK_SITES:
        if name in already_notified:
            continue
        try:
            time.sleep(random.uniform(1, 2))
            text = fetch_booking_site(url)
            if TARGET_DATE in text:
                print(f"  Date {TARGET_DATE} found on {name}!")
                found.append((name, url))
            else:
                print(f"  Date {TARGET_DATE} NOT on {name}")
        except Exception as e:
            print(f"  Error checking {name}: {e}", file=sys.stderr)
    return found

# ── WhatsApp notification ─────────────────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    resp = requests.post(
        url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_FROM, "To": WA_TO, "Body": message},
        timeout=60,
    )
    if not resp.ok:
        print(f"  Twilio error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    print(f"  WhatsApp sent → HTTP {resp.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Checking...")

    state = load_state()

    try:
        # ── 1. Check main site for Augustiner ─────────────────────────────
        print(f"  Checking {TARGET_URL} for Augustiner...")
        known_links = state.get("known_links", [])
        is_available, booking_url, all_links, new_links = check_main_site(known_links)
        state["errors"] = 0

        print(f"  Augustiner found: {is_available}  →  {booking_url}")

        if is_available and not state["notified"]:
            send_whatsapp(
                "OKTOBERFEST AUGUSTINER - PRENOTAZIONI APERTE!\n"
                f"Prenota subito: {booking_url}"
            )
            state["notified"] = True
        elif not is_available and state["notified"]:
            state["notified"] = False

        if new_links and not is_available and not state.get("new_link_notified"):
            send_whatsapp(
                "Oktoberfest Booking: nuova tenda aggiunta al sito!\n"
                f"Controlla se è Augustiner: {', '.join(new_links)}\n"
                f"Sito: {TARGET_URL}"
            )
            state["new_link_notified"] = True

        state["known_links"] = all_links

        # ── 2. Check booking sites for target date ─────────────────────────
        print(f"\n  Checking booking sites for {TARGET_DATE}.2026...")
        already_notified = state.get("date_notified", [])
        new_date_sites = check_date_availability(already_notified)

        for tent_name, tent_url in new_date_sites:
            send_whatsapp(
                f"OKTOBERFEST {TARGET_DATE}.2026 DISPONIBILE!\n"
                f"Tenda: {tent_name}\n"
                f"Prenota subito: {tent_url}"
            )
            already_notified.append(tent_name)

        state["date_notified"] = already_notified
        state["last_check"] = now

    except Exception as e:
        state["errors"] = state.get("errors", 0) + 1
        print(f"  Error: {e}  (consecutive errors: {state['errors']})", file=sys.stderr)

        if state["errors"] % 3 == 0:
            try:
                send_whatsapp(
                    f"Oktoberfest Pinger: {state['errors']} errori consecutivi "
                    f"({type(e).__name__}: {e}). "
                    "Potrebbe esserci un blocco. Controlla manualmente."
                )
            except Exception:
                pass

    finally:
        save_state(state)
        print("  State saved. Done.")


if __name__ == "__main__":
    main()
