# tests/test_signal_engine.py
import math
import pytest
from tests.conftest import SAMPLE_SESSION


def test_wait_when_inside_1sd(sample_session):
    from signal_engine import compute_signal
    # 4650 is 28 pts from open — well inside 1SD (82 pts)
    sig = compute_signal(sample_session, 4650.0)
    assert sig["direction"] == "WAIT"
    assert sig["zone"] == "INSIDE_1SD"
    assert sig["entry"] is None
    assert sig["tp1"] is None
    assert sig["sl"] is None
    assert sig["recovery"] is False


def test_short_signal_at_plus2sd(sample_session):
    from signal_engine import compute_signal
    # 4740 is 118.22 pts above open — in 2SD zone (82–164 pts)
    sig = compute_signal(sample_session, 4740.0)
    assert sig["direction"] == "SHORT"
    assert sig["zone"] == "+2SD"
    # box_top = ceil(4740/50)*50 = 4750
    assert sig["entry"] == 4750.0
    assert sig["tp1"] == 4725.0   # 4750 - 25
    assert sig["tp2"] == 4700.0   # 4750 - 50
    assert sig["sl"] == 4775.0    # 4750 + 25


def test_long_signal_at_minus2sd(sample_session):
    from signal_engine import compute_signal
    # 4480 is 141.78 pts below open — in 2SD zone
    sig = compute_signal(sample_session, 4480.0)
    assert sig["direction"] == "LONG"
    assert sig["zone"] == "-2SD"
    # box_bot = floor(4480/50)*50 = 4450
    assert sig["entry"] == 4450.0
    assert sig["tp1"] == 4475.0   # 4450 + 25
    assert sig["tp2"] == 4500.0   # 4450 + 50
    assert sig["sl"] == 4425.0    # 4450 - 25


def test_3sd_confidence_high_with_skew_and_magnet(sample_session):
    from signal_engine import compute_signal
    # Modify session: PUT heavy confirms SHORT at +3SD, magnet at 4700 (closer to open)
    session = sample_session.copy()
    session["oi_analysis"] = {
        **SAMPLE_SESSION["oi_analysis"],
        "call_pct": 35.0,
        "put_pct": 65.0,
        "skew_verdict": "PUT heavy — bearish",
        "magnets": [4700.0],  # 78 pts from open — closer than the 3SD price
    }
    # price = 4860 is 238 pts above open — in 3SD zone (246 pts threshold)
    sig = compute_signal(session, 4860.0)
    assert sig["zone"] == "+3SD"
    assert sig["direction"] == "SHORT"
    assert sig["confidence"] == "HIGH"


def test_3sd_confidence_medium_when_skew_contradicts(sample_session):
    from signal_engine import compute_signal
    # sample_session has CALL heavy (bullish) — contradicts SHORT at +3SD
    sig = compute_signal(sample_session, 4860.0)
    assert sig["zone"] == "+3SD"
    assert sig["confidence"] == "MEDIUM"


def test_recovery_signal_price_above(sample_session):
    from signal_engine import compute_signal
    # 3SD = 246.11 pts. recovery triggers at 3SD + 25 = 271.11 pts from open
    # open = 4621.78 + 271.11 = 4892.89 — use 4900 to clearly trigger
    sig = compute_signal(sample_session, 4900.0)
    assert sig["recovery"] is True
    # Follow trend: price above open → follow LONG (trend is UP)
    assert sig["direction"] == "LONG"


def test_recovery_signal_price_below(sample_session):
    from signal_engine import compute_signal
    # open = 4621.78 - 271.11 = 4350.67 — use 4340 to clearly trigger
    sig = compute_signal(sample_session, 4340.0)
    assert sig["recovery"] is True
    assert sig["direction"] == "SHORT"  # follow DOWN trend


def test_signal_at_returns_datetime_string(sample_session):
    from signal_engine import compute_signal
    sig = compute_signal(sample_session, 4740.0)
    assert "signal_at" in sig
    assert "T" in sig["signal_at"]  # ISO format
