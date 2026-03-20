"""
Oktoberfest Augustiner Festhalle — reservation availability checker.

Monitors www.oktoberfest-booking.com and alerts via WhatsApp (Twilio)
as soon as a reservation link for the Augustiner tent appears.

Required env vars:
  TWILIO_ACCOUNT_SID  – Twilio Account SID (starts with AC...)
  TWILIO_AUTH_TOKEN   – Twilio Auth Token
  TWILIO_FROM         – Twilio WhatsApp number, e.g. whatsapp:+14155238886
  WA_TO               – Your WhatsApp number, e.g. whatsapp:+393407480234
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

# ── Config ────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM        = os.environ["TWILIO_FROM"]   # e.g. whatsapp:+14155238886
WA_TO              = os.environ["WA_TO"]          # e.g. whatsapp:+393407480234

TARGET_URL = os.environ.get("OKTOBERFEST_URL", "https://www.oktoberfest-booking.com")

# Primary: link href or text contains one of these (case-insensitive)
AUGUSTINER_KEYWORDS = ["augustiner", "augustiner-festhalle", "augustinerfesthalle"]

# Secondary safety net: signature shared by ALL oktoberfest-booking.com tent links.
# Any NEW link with this UTM parameter that wasn't there last run gets flagged too,
# in case Augustiner uses an unexpected domain name.
BOOKING_UTM = "utm_source=newsbanner_oktobook"

STATE_FILE = "state.json"

# Realistic browser headers — the site returns 403 to plain requests
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
    return {"notified": False, "new_link_notified": False, "known_links": [], "last_check": None, "errors": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── Page fetching ─────────────────────────────────────────────────────────────

def fetch_with_requests(url: str) -> str:
    """
    Fetch page HTML using cloudscraper (handles Cloudflare JS challenge automatically).
    Falls through to plain requests on import error.
    """
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update(HEADERS)
    # Warm-up request to get cookies
    scraper.get("https://www.oktoberfest-booking.com", timeout=30)
    time.sleep(random.uniform(1.5, 3.0))
    resp = scraper.get(url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_with_playwright(url: str) -> str:
    """
    Fallback: use a real Chromium browser (Playwright).
    Only attempted if requests returns 403/blocked.
    Requires: pip install playwright && playwright install chromium
    """
    from playwright.sync_api import sync_playwright  # lazy import

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="it-IT",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        # Block images/fonts to speed up loading
        page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda r: r.abort())
        page.goto(url, wait_until="networkidle", timeout=60000)
        time.sleep(random.uniform(3, 6))  # extra wait for JS-rendered content
        html = page.content()
        # Debug: print page title and snippet to diagnose Cloudflare/blocking issues
        title = page.title()
        print(f"  [DEBUG] Page title: {title!r}")
        print(f"  [DEBUG] HTML snippet: {html[:300]!r}")
        browser.close()
    return html

# ── Availability check ────────────────────────────────────────────────────────

def extract_booking_links(html: str) -> dict[str, str]:
    """
    Returns all tent booking links found on the page as {href: anchor_text}.
    A booking link is any <a> whose href contains the shared UTM signature.
    """
    soup = BeautifulSoup(html, "lxml")
    links = {}
    for tag in soup.find_all("a", href=True):
        if BOOKING_UTM in tag["href"]:
            links[tag["href"]] = tag.get_text(strip=True)
    return links


def find_augustiner_link(html: str) -> str | None:
    """
    Primary check: any <a> whose href or visible text contains an Augustiner keyword.
    Returns the href of the first match, or None.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("a", href=True):
        href = tag["href"].lower()
        text = tag.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in AUGUSTINER_KEYWORDS):
            return tag["href"]
    return None


def check_availability(known_links: list) -> tuple[bool, str | None, list, list]:
    """
    Returns:
      is_available      – Augustiner keyword found
      booking_url       – the Augustiner link (or None)
      all_booking_links – all tent links currently on the page (for state persistence)
      new_links         – tent links that weren't there last run (secondary alert)

    Tries requests first; falls back to Playwright on 403/blocked.
    """
    time.sleep(random.uniform(1.0, 3.0))

    html = None
    try:
        html = fetch_with_requests(TARGET_URL)
        print("  Fetched with requests.")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 429, 503):
            print(f"  Blocked ({e.response.status_code}) — trying Playwright fallback...")
            try:
                html = fetch_with_playwright(TARGET_URL)
                print("  Fetched with Playwright.")
            except ImportError:
                print(
                    "  Playwright not installed. "
                    "Add 'playwright' to requirements.txt and run 'playwright install chromium'.",
                    file=sys.stderr,
                )
                raise
        else:
            raise

    # Debug: show page title and first 400 chars regardless of fetch method
    soup_debug = BeautifulSoup(html, "lxml")
    print(f"  [DEBUG] Title: {soup_debug.title.string if soup_debug.title else 'N/A'!r}")
    print(f"  [DEBUG] HTML snippet: {html[:400]!r}")

    augustiner_link = find_augustiner_link(html)
    booking_links   = extract_booking_links(html)
    new_links       = [url for url in booking_links if url not in known_links]

    return (augustiner_link is not None), augustiner_link, list(booking_links.keys()), new_links

# ── WhatsApp notification (Twilio) ────────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    resp = requests.post(
        url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_FROM, "To": WA_TO, "Body": message},
        timeout=60,
    )
    resp.raise_for_status()
    print(f"  WhatsApp sent → HTTP {resp.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Checking: {TARGET_URL}")

    state = load_state()

    try:
        known_links = state.get("known_links", [])
        is_available, booking_url, all_links, new_links = check_availability(known_links)
        state["errors"] = 0

        print(f"  Augustiner found: {is_available}  →  {booking_url}")
        print(f"  All booking links on page: {all_links}")
        print(f"  New links since last run:  {new_links}")
        print(f"  Previously notified: {state['notified']}")

        # ── Primary alert: Augustiner keyword in link ──────────────────────
        if is_available and not state["notified"]:
            msg = (
                "OKTOBERFEST AUGUSTINER - PRENOTAZIONI APERTE!\n"
                f"Prenota subito: {booking_url}"
            )
            print("  Sending WhatsApp notification (Augustiner found)...")
            send_whatsapp(msg)
            state["notified"] = True

        elif not is_available and state["notified"]:
            print("  Augustiner link gone — resetting notification flag.")
            state["notified"] = False

        # ── Secondary alert: any unexpected new tent link appeared ─────────
        if new_links and not is_available and not state.get("new_link_notified"):
            names = ", ".join(new_links)
            msg = (
                "Oktoberfest Booking: nuova tenda aggiunta al sito!\n"
                f"Controlla se è Augustiner: {names}\n"
                f"Sito: {TARGET_URL}"
            )
            print("  Sending WhatsApp notification (new unknown link)...")
            send_whatsapp(msg)
            state["new_link_notified"] = True

        state["known_links"] = all_links
        state["last_check"]  = now

    except Exception as e:
        state["errors"] = state.get("errors", 0) + 1
        print(f"  Error: {e}  (consecutive errors: {state['errors']})", file=sys.stderr)

        if state["errors"] % 10 == 0:
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
