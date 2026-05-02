# signal_engine.py
import math
from datetime import datetime, timezone, timedelta

UTC7 = timezone(timedelta(hours=7))


def compute_signal(session: dict, price: float) -> dict:
    """
    Compute the current box-trading signal given session data and live price.

    Returns a dict with: zone, direction, confidence, entry, tp1, tp2, sl,
    recovery (bool), signal_at (ISO timestamp).
    """
    open_p = session["open_price"]
    sd     = session["sd_zones"]
    sd1    = sd["sd1_pts"]
    sd2    = sd["sd2_pts"]
    sd3    = sd["sd3_pts"]
    oi     = session.get("oi_analysis") or {}

    diff = abs(price - open_p)
    above = price >= open_p  # True = price is above open

    # ── Zone classification ───────────────────────────────────────────────
    if diff < sd1:
        raw_zone = "INSIDE_1SD"
    elif diff < sd2:
        raw_zone = "2SD"
    elif diff < sd3:
        raw_zone = "3SD"
    else:
        raw_zone = "BEYOND_3SD"

    zone_label = (
        raw_zone if raw_zone == "INSIDE_1SD"
        else f"+{raw_zone}" if above
        else f"-{raw_zone}"
    )

    # ── WAIT — inside 1SD ─────────────────────────────────────────────────
    if raw_zone == "INSIDE_1SD":
        return {
            "zone": "INSIDE_1SD",
            "direction": "WAIT",
            "confidence": "LOW",
            "entry": None, "tp1": None, "tp2": None, "sl": None,
            "recovery": False,
            "signal_at": datetime.now(UTC7).isoformat(),
        }

    # ── Recovery — trend broke through 3SD ───────────────────────────────
    recovery = diff > sd3 + 25

    box_size = 50

    if recovery:
        # Follow the trend, not fade it
        follow_dir = "LONG" if above else "SHORT"
        if follow_dir == "LONG":
            bot = math.floor(price / box_size) * box_size
            entry, tp1, tp2, sl = bot, bot + 25, bot + 50, bot - 25
        else:
            top = math.ceil(price / box_size) * box_size
            entry, tp1, tp2, sl = top, top - 25, top - 50, top + 25
        return {
            "zone": zone_label,
            "direction": follow_dir,
            "confidence": "HIGH",
            "entry": float(entry),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "sl": float(sl),
            "recovery": True,
            "signal_at": datetime.now(UTC7).isoformat(),
        }

    # ── Normal fade signal at 2SD or 3SD ─────────────────────────────────
    fade_dir = "SHORT" if above else "LONG"

    if fade_dir == "SHORT":
        top   = math.ceil(price / box_size) * box_size
        entry, tp1, tp2, sl = top, top - 25, top - 50, top + 25
    else:
        bot   = math.floor(price / box_size) * box_size
        entry, tp1, tp2, sl = bot, bot + 25, bot + 50, bot - 25

    # ── Confidence ────────────────────────────────────────────────────────
    call_pct = oi.get("call_pct", 50.0)
    put_pct  = oi.get("put_pct", 50.0)
    magnets  = oi.get("magnets", [])

    # Skew confirms if OI pressure aligns with the fade direction
    skew_confirms = (
        (fade_dir == "SHORT" and put_pct > 55) or
        (fade_dir == "LONG"  and call_pct > 55)
    )

    # Magnet target exists closer to open than the current price
    has_magnet_target = any(abs(m - open_p) < diff for m in magnets)

    confidence = (
        "HIGH" if raw_zone == "3SD" and skew_confirms and has_magnet_target
        else "MEDIUM"
    )

    return {
        "zone": zone_label,
        "direction": fade_dir,
        "confidence": confidence,
        "entry": float(entry),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "sl": float(sl),
        "recovery": False,
        "signal_at": datetime.now(UTC7).isoformat(),
    }
