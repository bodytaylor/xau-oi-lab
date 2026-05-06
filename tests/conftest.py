# tests/conftest.py
import copy
import pytest

SAMPLE_SESSION = {
    "version": "1.2",
    "date": "2026-05-02",
    "locked_at": "2026-05-02T01:30:00+07:00",
    "open_price": 4621.78,
    "iv_pct": 28.4,
    "iv_source": {"iv_pct": 28.4, "strike": 4625, "source": "chart_hover"},
    "sd_zones": {
        "daily_pct": 1.775,
        "sd1_pts": 82.04,
        "sd2_pts": 164.07,
        "sd3_pts": 246.11,
        "zones": {
            "+3SD": 4867.89,
            "+2SD": 4785.85,
            "+1SD": 4703.82,
            "OPEN": 4621.78,
            "-1SD": 4539.74,
            "-2SD": 4457.71,
            "-3SD": 4375.67,
        },
    },
    "phase1_complete": True,
    "phase2_complete": True,
    "phase2_at": "2026-05-02T08:30:00+07:00",
    "oi_data": [],
    "oi_analysis": {
        "call_vol": 6500.0,
        "put_vol": 3500.0,
        "call_pct": 65.0,
        "put_pct": 35.0,
        "skew_verdict": "CALL heavy — bullish",
        "magnets": [4700.0, 4500.0],
        "gamma_levels": [{"strike": 4650.0, "type": "call", "delta": 0.04}],
    },
    "vol_skew_analysis": {
        "atm_strike": 4625.0,
        "atm_iv": 28.4,
        "left_slope": 0.0012,
        "right_slope": 0.0008,
        "slope_ratio": 1.5,
        "verdict": "LEFT heavy — bearish vol skew",
        "point_count": 8,
    },
}


@pytest.fixture
def sample_session():
    return copy.deepcopy(SAMPLE_SESSION)
