"""
Oktoberfest Augustiner Festhalle — reservation availability checker.

Monitors www.oktoberfest-booking.com and alerts via WhatsApp (Callmebot)
as soon as a reservation link for the Augustiner tent appears.

Required env vars:
  WA_PHONE          – WhatsApp number with country code, e.g. +393331234567
  CALLMEBOT_APIKEY  – API key received from Callmebot
"""

import os
import json
import time
import random
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

WA_PHONE         = os.environ["WA_PHONE"]
CALLMEBOT_APIKEY = os.environ["CALLMEBOT_APIKEY"]
TARGET_URL       = os.environ.get("OKTOBERFEST_URL", "https://www.oktoberfest-booking.com")

# The script flags success when it finds any link whose href or text contains
# at least one of these strings (case-insensitive).
AUGUSTINER_KEYWORDS = ["augustiner", "augustiner-festhalle", "augustinerfesthalle"]

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
    "Accept-Encoding": "gzip, deflate, br",
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
    return {"notified": False, "last_check": None, "errors": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── Page fetching ─────────────────────────────────────────────────────────────

def fetch_with_requests(url: str) -> str:
    """Fetch page HTML using requests + realistic headers. Raises on non-200."""
    session = requests.Session()
    # First visit the homepage to get cookies (mimics a real browser)
    session.get("https://www.oktoberfest-booking.com", headers=HEADERS, timeout=30)
    time.sleep(random.uniform(1.5, 3.5))
    resp = session.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
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
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(random.uniform(2, 4))  # let JS render
        html = page.content()
        browser.close()
    return html

# ── Availability check ────────────────────────────────────────────────────────

def find_augustiner_link(html: str) -> str | None:
    """
    Parses the page and looks for any <a> whose href or visible text
    contains an Augustiner keyword.
    Returns the href of the first match, or None if not found.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("a", href=True):
        href = tag["href"].lower()
        text = tag.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in AUGUSTINER_KEYWORDS):
            return tag["href"]  # return original (non-lowercased) href
    return None


def check_availability() -> tuple[bool, str | None]:
    """
    Returns (is_available, booking_url_or_None).
    Tries requests first; falls back to Playwright on 403/blocked.
    """
    time.sleep(random.uniform(1.0, 3.0))  # polite delay

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

    link = find_augustiner_link(html)
    return (link is not None), link

# ── WhatsApp notification ─────────────────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    url = "https://api.callmebot.com/whatsapp.php"
    resp = requests.get(
        url,
        params={"phone": WA_PHONE, "text": message, "apikey": CALLMEBOT_APIKEY},
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
        is_available, booking_url = check_availability()
        state["errors"] = 0

        print(f"  Augustiner link found: {is_available}  →  {booking_url}")
        print(f"  Previously notified: {state['notified']}")

        if is_available and not state["notified"]:
            msg = (
                "OKTOBERFEST AUGUSTINER - PRENOTAZIONI APERTE!\n"
                f"Prenota subito: {booking_url}"
            )
            print("  Sending WhatsApp notification...")
            send_whatsapp(msg)
            state["notified"] = True

        elif not is_available and state["notified"]:
            # Link disappeared (unlikely, but reset so we notify again if it reappears)
            print("  Link no longer present — resetting notification flag.")
            state["notified"] = False

        state["last_check"] = now

    except Exception as e:
        state["errors"] = state.get("errors", 0) + 1
        print(f"  Error: {e}  (consecutive errors: {state['errors']})", file=sys.stderr)

        # Warn via WhatsApp every 10 consecutive errors (possible persistent block)
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
