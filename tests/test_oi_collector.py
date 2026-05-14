# tests/test_oi_collector.py
"""Unit tests for session key logic in oi_collector (no browser required)."""


def _apply_session_update(session: dict, series_name, vol_pts: list) -> dict:
    """Mirrors the session.update() logic in oi_collector.run()."""
    session.update({
        "exp_series_name":  series_name or session.get("date", ""),
        "vol_curve_points": [{"strike": p["strike"], "iv": p["iv"]} for p in vol_pts],
    })
    return session


def test_exp_series_name_stored_when_found():
    session = {"date": "2026-05-14"}
    result = _apply_session_update(session, "06 May 2026", [])
    assert result["exp_series_name"] == "06 May 2026"


def test_exp_series_name_falls_back_to_date_when_none():
    session = {"date": "2026-05-14"}
    result = _apply_session_update(session, None, [])
    assert result["exp_series_name"] == "2026-05-14"


def test_exp_series_name_falls_back_to_empty_when_no_date():
    session = {}
    result = _apply_session_update(session, None, [])
    assert result["exp_series_name"] == ""


def test_vol_curve_points_stored():
    vol_pts = [
        {"strike": 4700.0, "iv": 27.2, "extra": "ignored"},
        {"strike": 4750.0, "iv": 28.1, "extra": "ignored"},
    ]
    session = {"date": "2026-05-14"}
    result = _apply_session_update(session, "06 May 2026", vol_pts)
    assert result["vol_curve_points"] == [
        {"strike": 4700.0, "iv": 27.2},
        {"strike": 4750.0, "iv": 28.1},
    ]


def test_vol_curve_points_empty_when_no_vol_pts():
    session = {"date": "2026-05-14"}
    result = _apply_session_update(session, "06 May 2026", [])
    assert result["vol_curve_points"] == []
