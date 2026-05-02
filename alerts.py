# alerts.py
"""
Discord webhook alert dispatcher.

Alert types: phase1_complete, phase2_complete, zone_alert, recovery_alert,
             gamma_alert, phase_failed.

Deduplication: each alert type fires at most once per day (reset at midnight UTC+7).
Uses httpx for HTTP; webhook URL from config.settings.
"""
import logging
from datetime import datetime, timezone, timedelta

import httpx

from config import settings

UTC7 = timezone(timedelta(hours=7))
log  = logging.getLogger("alerts")

# In-memory deduplication — reset on new session date
_fired: set[str] = set()
_fired_date: str = ""


def _dedup(key: str) -> bool:
    """Returns True if this alert should fire (not yet fired today)."""
    global _fired_date, _fired
    today = datetime.now(UTC7).strftime("%Y-%m-%d")
    if today != _fired_date:
        _fired_date = today
        _fired      = set()
    if key in _fired:
        return False
    _fired.add(key)
    return True


def _post(embed: dict) -> None:
    if not settings.discord_webhook_url:
        return
    try:
        httpx.post(
            settings.discord_webhook_url,
            json={"embeds": [embed]},
            timeout=10,
        )
    except Exception as e:
        log.debug(f"Discord post failed: {e}")


def phase1_complete(session: dict) -> None:
    if not _dedup(f"phase1_{session['date']}"):
        return
    sd = session["sd_zones"]
    zones_text = "\n".join(f"{k}: {v:.2f}" for k, v in sd["zones"].items())
    _post({
        "title": "✅ Phase 1 Complete — XAUUSD Zone Map Locked",
        "color": 0x00FF88,
        "fields": [
            {"name": "Open",   "value": f"{session['open_price']:.2f}", "inline": True},
            {"name": "IV%",    "value": str(session["iv_pct"]),         "inline": True},
            {"name": "1SD ±",  "value": f"{sd['sd1_pts']:.2f} pts",    "inline": True},
            {"name": "SD Zones", "value": zones_text, "inline": False},
        ],
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def phase2_complete(session: dict) -> None:
    if not _dedup(f"phase2_{session['date']}"):
        return
    oi     = session.get("oi_analysis") or {}
    magnets = ", ".join(f"{m:.0f}" for m in oi.get("magnets", [])) or "None"
    gamma   = ", ".join(str(g["strike"]) for g in oi.get("gamma_levels", [])) or "None"
    _post({
        "title": "📊 Phase 2 Complete — OI Analysis Ready",
        "color": 0x0099FF,
        "fields": [
            {"name": "Skew",    "value": oi.get("skew_verdict", "N/A"), "inline": True},
            {"name": "Calls",   "value": f"{oi.get('call_pct', 0):.1f}%", "inline": True},
            {"name": "Puts",    "value": f"{oi.get('put_pct', 0):.1f}%",  "inline": True},
            {"name": "Magnets", "value": magnets, "inline": False},
            {"name": "Gamma Risk", "value": gamma, "inline": False},
        ],
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def zone_alert(price: float, signal: dict) -> None:
    zone  = signal.get("zone", "")
    key   = f"zone_{zone}_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    is_3sd  = "3SD" in zone
    color   = 0xFF0000 if is_3sd else 0xFF9900
    emoji   = "🔴" if is_3sd else "⚠️"
    entry   = signal.get("entry")
    fields  = [
        {"name": "Zone",       "value": zone,                         "inline": True},
        {"name": "Direction",  "value": signal.get("direction", ""), "inline": True},
        {"name": "Confidence", "value": signal.get("confidence", ""), "inline": True},
    ]
    if entry is not None:
        fields += [
            {"name": "Entry", "value": str(entry),                  "inline": True},
            {"name": "TP1",   "value": str(signal.get("tp1")),      "inline": True},
            {"name": "TP2/SL","value": f"{signal.get('tp2')} / {signal.get('sl')}", "inline": True},
        ]
    _post({
        "title": f"{emoji} XAUUSD at {zone} — {price:.2f}",
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def recovery_alert(price: float, signal: dict) -> None:
    key = f"recovery_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    _post({
        "title": "⚡ RECOVERY SIGNAL — Cut and Follow Trend",
        "color": 0xFF0000,
        "description": (
            f"Price **{price:.2f}** broke through 3SD without reversing.\n"
            f"Cut opposing position — follow **{signal.get('direction')}**."
        ),
        "fields": [
            {"name": "Follow", "value": signal.get("direction", ""), "inline": True},
            {"name": "Entry",  "value": str(signal.get("entry", "")), "inline": True},
        ],
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def gamma_alert(strike: float, opt_type: str) -> None:
    key = f"gamma_{strike:.0f}_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    _post({
        "title": "⚡ GAMMA RISK — Market Maker Hedging Expected",
        "color": 0xFF6600,
        "description": (
            f"Price approaching {opt_type.upper()} gamma level at **{strike:.0f}**.\n"
            "Delta < 5% — MM hedging may cause rapid move."
        ),
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def phase_failed(phase: int, error: str) -> None:
    _post({
        "title": f"❌ Phase {phase} FAILED",
        "color": 0xFF0000,
        "description": f"```{error[:500]}```",
        "timestamp": datetime.now(UTC7).isoformat(),
    })
