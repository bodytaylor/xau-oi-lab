"""Shared utilities used by collector.py and oi_collector.py."""

from datetime import datetime, timezone, timedelta


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
