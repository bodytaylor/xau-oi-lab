# tests/test_db_sync.py
from unittest.mock import MagicMock, patch
import pytest


def _mock_client():
    """Build a mock Supabase client that chains .table().upsert().execute() etc."""
    client = MagicMock()
    table  = MagicMock()
    client.table.return_value = table
    table.upsert.return_value = table
    table.insert.return_value = table
    table.select.return_value = table
    table.order.return_value  = table
    table.limit.return_value  = table
    table.execute.return_value = MagicMock(data=[{"date": "2026-05-02"}])
    return client


@patch("db_sync._client")
def test_upsert_session_calls_table(mock_client_fn, sample_session):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    result = db_sync.upsert_session(sample_session)
    assert result is True
    mock_client_fn.return_value.table.assert_called_with("sessions")


@patch("db_sync._client")
def test_upsert_session_returns_false_when_supabase_not_configured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    result = db_sync.upsert_session({"date": "2026-05-02"})
    assert result is False


@patch("db_sync._client")
def test_insert_signal_calls_table(mock_client_fn):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    signal = {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
    }
    result = db_sync.insert_signal("2026-05-02", signal, 4740.0)
    assert result is True
    mock_client_fn.return_value.table.assert_called_with("signals")


@patch("db_sync._client")
def test_get_history_returns_list(mock_client_fn):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    history = db_sync.get_history(n=5)
    assert isinstance(history, list)


@patch("db_sync._client")
def test_get_history_returns_empty_when_unconfigured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    assert db_sync.get_history() == []
