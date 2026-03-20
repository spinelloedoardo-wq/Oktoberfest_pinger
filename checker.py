"""
Oktoberfest Augustiner Festhalle — reservation availability checker.
Sends a WhatsApp notification via Callmebot when reservations open.

Required env vars:
  WA_PHONE          – your WhatsApp number with country code, e.g. +393331234567
  CALLMEBOT_APIKEY  – the API key you received from Callmebot
  OKTOBERFEST_URL   – URL of the Augustiner tent reservation page to monitor
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

WA_PHONE = os.environ["WA_PHONE"]
CALLMEBOT_APIKEY = os.environ["CALLMEBOT_APIKEY"]
OKTOBERFEST_URL = os.environ["OKTOBERFEST_URL"]

STATE_FILE = "state.json"

# Keywords that signal reservations are open (checked in the page text, case-insensitive)
OPEN_KEYWORDS = [
    "prenota", "prenotazione", "reservierung", "reservieren",
    "buchen", "buchung", "book now", "reserve", "reservation",
    "tischreservierung", "reserve a table",
]

# Keywords that signal the page is explicitly closed/not-yet-open (reduces false positives)
CLOSED_KEYWORDS = [
    "nicht verfügbar", "not available", "non disponibile",
    "coming soon", "demnächst", "prossimamente",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,de;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ── State helpers ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"available": False, "notified": False, "last_check": None, "errors": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── Availability check ────────────────────────────────────────────────────────

def check_availability() -> tuple[bool, list[str]]:
    """
    Fetches the target URL and looks for reservation signals.
    Returns (is_available, list_of_found_open_keywords).
    Raises on HTTP errors or timeouts.
    """
    # Polite random delay before each request (1–4 seconds)
    time.sleep(random.uniform(1.0, 4.0))

    session = requests.Session()
    session.headers.update(HEADERS)

    resp = session.get(OKTOBERFEST_URL, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove script and style noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    page_text = soup.get_text(separator=" ").lower()

    found_open = [kw for kw in OPEN_KEYWORDS if kw in page_text]
    found_closed = [kw for kw in CLOSED_KEYWORDS if kw in page_text]

    # Also check for booking-related links/buttons
    booking_elements = soup.find_all(
        ["a", "button"],
        string=lambda s: s and any(kw in s.lower() for kw in OPEN_KEYWORDS),
    )

    # Available = at least one open keyword AND no strong closed signal
    # OR a booking link/button is present
    is_available = (len(found_open) > 0 and len(found_closed) == 0) or len(booking_elements) > 0

    return is_available, found_open

# ── WhatsApp notification ─────────────────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    """Sends a WhatsApp message via Callmebot API."""
    url = "https://api.callmebot.com/whatsapp.php"
    params = {
        "phone": WA_PHONE,
        "text": message,
        "apikey": CALLMEBOT_APIKEY,
    }
    # Callmebot is sometimes slow — generous timeout
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    print(f"  WhatsApp sent → HTTP {resp.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Checking: {OKTOBERFEST_URL}")

    state = load_state()

    try:
        is_available, found_keywords = check_availability()
        state["errors"] = 0  # reset error counter on success

        print(f"  Available: {is_available}  |  Keywords found: {found_keywords}")
        print(f"  Previously notified: {state['notified']}")

        if is_available and not state["notified"]:
            msg = (
                "OKTOBERFEST AUGUSTINER ALERT!\n"
                "Le prenotazioni per il tendone Augustiner sono APERTE!\n"
                f"Vai subito su: {OKTOBERFEST_URL}"
            )
            print("  Sending WhatsApp notification...")
            send_whatsapp(msg)
            state["notified"] = True

        elif not is_available and state["notified"]:
            # Reservations seem to have closed again — reset so we can notify next time
            print("  Page no longer shows availability — resetting notification flag.")
            state["notified"] = False

        state["available"] = is_available
        state["last_check"] = now

    except requests.HTTPError as e:
        state["errors"] = state.get("errors", 0) + 1
        print(f"  HTTP error: {e}  (consecutive errors: {state['errors']})", file=sys.stderr)
        # If blocked repeatedly, warn via WhatsApp (once every 10 consecutive errors)
        if state["errors"] % 10 == 0:
            try:
                send_whatsapp(
                    f"Oktoberfest Pinger: {state['errors']} errori consecutivi "
                    f"({e}). Potrebbe esserci un blocco. Controlla manualmente."
                )
            except Exception:
                pass

    except Exception as e:
        state["errors"] = state.get("errors", 0) + 1
        print(f"  Unexpected error: {e}  (consecutive errors: {state['errors']})", file=sys.stderr)

    finally:
        save_state(state)

    print(f"  State saved. Done.")


if __name__ == "__main__":
    main()
