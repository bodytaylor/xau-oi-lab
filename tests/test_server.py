# tests/test_server.py
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, sample_session):
    """TestClient with session_data.json pre-written and all external calls mocked."""
    session_file = tmp_path / "session_data.json"
    session_file.write_text(json.dumps(sample_session))

    with (
        patch("server.SESSION_FILE", session_file),
        patch("server.tv_client") as mock_tv,
        patch("server.db_sync"),
        patch("server.alerts"),
        patch("server.export_pine", return_value="//@version=5"),
        patch("server.scheduler"),
    ):
        mock_tv.get_quote.return_value = 4740.0
        mock_tv.is_connected.return_value = True

        from server import app
        yield TestClient(app, raise_server_exceptions=True)


def test_get_session_returns_200(client):
    resp = client.get("/api/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["open_price"] == 4621.78


def test_get_session_404_when_no_file(tmp_path):
    with (
        patch("server.SESSION_FILE", tmp_path / "missing.json"),
        patch("server.tv_client"),
        patch("server.db_sync"),
        patch("server.alerts"),
        patch("server.export_pine", return_value=""),
        patch("server.scheduler"),
    ):
        from server import app
        c = TestClient(app)
        resp = c.get("/api/session")
        assert resp.status_code == 404


def test_get_signal_returns_signal_dict(client):
    resp = client.get("/api/signal")
    assert resp.status_code == 200
    data = resp.json()
    assert "direction" in data
    assert "zone" in data


def test_get_history_returns_list(client):
    with patch("server.db_sync.get_history", return_value=[{"date": "2026-05-01"}]):
        resp = client.get("/api/history?n=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_refresh_phase1_returns_triggered(client):
    resp = client.post("/api/refresh/phase1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "triggered"


def test_refresh_phase2_returns_triggered(client):
    resp = client.post("/api/refresh/phase2")
    assert resp.status_code == 200
    assert resp.json()["status"] == "triggered"
