"""
XAUUSD Framework — CME Login Setup (Run Once)
=============================================
Run this script ONCE to save your CME login session to disk.
After that, collector.py and oi_collector.py will reuse the
saved session automatically — no credentials stored in code.

Run:  python login_setup.py
"""

import json
import sys
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
# Directory where the browser profile / cookies are saved
PROFILE_DIR = Path(__file__).parent / "browser_profile"
SESSION_FILE = Path(__file__).parent / "cme_session.json"

CME_LOGIN_URL  = "https://www.cmegroup.com/tools-information/quikstrike/vol2vol-expected-range.html"
CME_CHECK_URL  = "https://www.cmegroup.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("login_setup")


def main():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "═" * 55)
    print("  CME LOGIN SETUP — Run this once to save your session")
    print("═" * 55)
    print()
    print("  A browser window will open.")
    print("  1. Log in to your CME account in the browser.")
    print("  2. Navigate to the Vol2Vol tool to confirm access.")
    print("  3. Come back here and press ENTER when ready.")
    print()

    with sync_playwright() as pw:
        # Launch with a PERSISTENT context so cookies/localStorage are saved
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            slow_mo=100,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()
        log.info(f"Opening CME → {CME_LOGIN_URL}")
        page.goto(CME_LOGIN_URL, timeout=30_000, wait_until="domcontentloaded")

        print("\n  Browser is open. Log in to your CME account now.")
        print("  After logging in and seeing the Vol2Vol tool, come back here.\n")
        input("  Press ENTER when you are logged in and the tool is visible: ")

        # Verify session works by checking the page title / URL
        current_url = page.url
        current_title = page.title()
        log.info(f"Current page: {current_title} | {current_url}")

        if "login" in current_url.lower() or "sign" in current_url.lower():
            log.warning("Still looks like a login page — make sure you completed login.")
        else:
            log.info("✓ Session looks valid — saving cookies and storage state.")

        # Save full storage state (cookies + localStorage + sessionStorage)
        storage_state = context.storage_state()
        SESSION_FILE.write_text(json.dumps(storage_state, indent=2))
        log.info(f"Session saved → {SESSION_FILE}")

        # Save a list of cookies summary for inspection
        cookies = storage_state.get("cookies", [])
        cme_cookies = [c for c in cookies if "cmegroup" in c.get("domain", "")]
        log.info(f"Saved {len(cme_cookies)} CME cookies.")

        context.close()

    print()
    print("═" * 55)
    print("  SETUP COMPLETE")
    print(f"  Session file: {SESSION_FILE}")
    print(f"  Profile dir : {PROFILE_DIR}")
    print()
    print("  You can now run:")
    print("    python collector.py      ← Phase 1 (01:30 UTC+7)")
    print("    python oi_collector.py   ← Phase 2 (08:30 UTC+7)")
    print("═" * 55)
    print()
    print("  NOTE: If CME logs you out (sessions expire after a few days),")
    print("  just re-run this script to refresh the saved session.")
    print()


if __name__ == "__main__":
    main()
