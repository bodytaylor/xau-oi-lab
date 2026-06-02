"""Shared utilities used by collector.py and oi_collector.py."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR            = Path(__file__).parent
QUIKSTRIKE_URL_FILE = BASE_DIR / "quikstrike_url.json"


def load_quikstrike_url() -> str:
    """Return the user-saved QuikStrike chart URL, or empty string if not set."""
    if not QUIKSTRIKE_URL_FILE.exists():
        return ""
    try:
        return json.loads(QUIKSTRIKE_URL_FILE.read_text()).get("url", "")
    except Exception:
        return ""


def is_login_page(page) -> bool:
    """Return True if the page appears to be a CME login/sign-in wall."""
    url   = page.url.lower()
    title = page.title().lower()
    return any(k in url or k in title for k in ["login", "signin", "sign-in", "log-in"])


def utc7_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=7)))


def target_exp_date() -> str:
    """Return target expiration date as 'DD Mon YYYY' (e.g. '06 May 2026').
    Uses today on weekdays; falls back to last Friday on weekends."""
    now = utc7_now()
    wd = now.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    if wd == 5:
        target = now - timedelta(days=1)
    elif wd == 6:
        target = now - timedelta(days=2)
    else:
        target = now
    return target.strftime("%d %b %Y")
