# tests/test_alerts.py
from unittest.mock import patch, MagicMock
import pytest


def _reset_alerts():
    """Clear deduplication state between tests."""
    import alerts
    alerts._fired.clear()
    alerts._fired_date = ""


@patch("alerts.httpx.post")
@patch("alerts.settings")
def test_phase1_complete_posts_embed(mock_settings, mock_post, sample_session):
    _reset_alerts()
    mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test/test"
    import alerts
    alerts.phase1_complete(sample_session)
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert "Phase 1" in embed["title"]
    assert embed["color"] == 0x00FF88


@patch("alerts.httpx.post")
@patch("alerts.settings")
def test_phase1_complete_deduplicates(mock_settings, mock_post, sample_session):
    _reset_alerts()
    mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test/test"
    import alerts
    alerts.phase1_complete(sample_session)
    alerts.phase1_complete(sample_session)  # second call same session date
    assert mock_post.call_count == 1  # only fired once


@patch("alerts.httpx.post")
@patch("alerts.settings")
def test_zone_alert_posts_red_embed_for_3sd(mock_settings, mock_post):
    _reset_alerts()
    mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test/test"
    import alerts
    signal = {
        "zone": "+3SD", "direction": "SHORT", "confidence": "HIGH",
        "entry": 4900.0, "tp1": 4875.0, "tp2": 4850.0, "sl": 4925.0,
        "recovery": False, "signal_at": "2026-05-02T10:00:00+07:00",
    }
    alerts.zone_alert(4867.89, signal)
    payload = mock_post.call_args.kwargs["json"]
    embed = payload["embeds"][0]
    assert embed["color"] == 0xFF0000
    assert "+3SD" in embed["title"]


@patch("alerts.httpx.post")
@patch("alerts.settings")
def test_zone_alert_posts_amber_for_2sd(mock_settings, mock_post):
    _reset_alerts()
    mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test/test"
    import alerts
    signal = {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "entry": 4800.0, "tp1": 4775.0, "tp2": 4750.0, "sl": 4825.0,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
    }
    alerts.zone_alert(4785.85, signal)
    embed = mock_post.call_args.kwargs["json"]["embeds"][0]
    assert embed["color"] == 0xFF9900


@patch("alerts.httpx.post")
@patch("alerts.settings")
def test_recovery_alert_fires_once(mock_settings, mock_post):
    _reset_alerts()
    mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test/test"
    import alerts
    signal = {"direction": "LONG", "entry": 4875.0, "signal_at": "2026-05-02T10:00:00+07:00"}
    alerts.recovery_alert(4900.0, signal)
    alerts.recovery_alert(4910.0, signal)  # second call — should be deduplicated
    assert mock_post.call_count == 1


@patch("alerts.httpx.post")
def test_no_post_when_webhook_not_configured(mock_post, sample_session):
    _reset_alerts()
    import alerts
    with patch("alerts.settings") as mock_settings:
        mock_settings.discord_webhook_url = ""
        alerts.phase1_complete(sample_session)
    mock_post.assert_not_called()
