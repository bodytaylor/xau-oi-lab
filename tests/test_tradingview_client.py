# tests/test_tradingview_client.py
import json
from unittest.mock import MagicMock, patch
import pytest

TV_TABS_RESPONSE = [
    {
        "id": "abc123",
        "url": "https://www.tradingview.com/chart/XAUUSD/",
        "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/abc123",
    }
]

CDP_PRICE_RESPONSE = json.dumps({
    "result": {"result": {"type": "number", "value": 4703.50}}
})

CDP_OK_RESPONSE = json.dumps({
    "result": {"result": {"type": "string", "value": "ok"}}
})

CDP_NULL_RESPONSE = json.dumps({
    "result": {"result": {"type": "null", "value": None}}
})


@patch("tradingview_client.requests.get")
def test_is_connected_true_when_tv_running(mock_get):
    mock_get.return_value.json.return_value = TV_TABS_RESPONSE
    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    assert client.is_connected() is True


@patch("tradingview_client.requests.get", side_effect=ConnectionRefusedError)
def test_is_connected_false_when_tv_not_running(mock_get):
    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    assert client.is_connected() is False


@patch("tradingview_client.websocket.create_connection")
@patch("tradingview_client.requests.get")
def test_get_quote_returns_price(mock_get, mock_ws):
    mock_get.return_value.json.return_value = TV_TABS_RESPONSE
    ws_inst = MagicMock()
    ws_inst.recv.return_value = CDP_PRICE_RESPONSE
    mock_ws.return_value = ws_inst

    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    price = client.get_quote()
    assert price == 4703.50


@patch("tradingview_client.requests.get", side_effect=ConnectionRefusedError)
def test_get_quote_returns_none_when_offline(mock_get):
    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    assert client.get_quote() is None


@patch("tradingview_client.websocket.create_connection")
@patch("tradingview_client.requests.get")
def test_push_pine_returns_true_on_success(mock_get, mock_ws):
    mock_get.return_value.json.return_value = TV_TABS_RESPONSE
    ws_inst = MagicMock()
    # First recv: monaco setValue → ok; second recv: compile click
    ws_inst.recv.side_effect = [CDP_OK_RESPONSE, CDP_OK_RESPONSE]
    mock_ws.return_value = ws_inst

    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    result = client.push_pine("//@version=5\nindicator('test', overlay=true)")
    assert result is True


@patch("tradingview_client.websocket.create_connection")
@patch("tradingview_client.requests.get")
def test_push_pine_returns_false_when_no_editor(mock_get, mock_ws):
    mock_get.return_value.json.return_value = TV_TABS_RESPONSE
    ws_inst = MagicMock()
    ws_inst.recv.return_value = json.dumps({
        "result": {"result": {"type": "string", "value": "no_editor"}}
    })
    mock_ws.return_value = ws_inst

    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    result = client.push_pine("//@version=5")
    assert result is False


@patch("tradingview_client.websocket.create_connection")
@patch("tradingview_client.requests.get")
def test_create_sd_alerts_returns_count(mock_get, mock_ws, sample_session):
    mock_get.return_value.json.return_value = TV_TABS_RESPONSE
    ws_inst = MagicMock()
    ws_inst.recv.return_value = CDP_OK_RESPONSE
    mock_ws.return_value = ws_inst

    from tradingview_client import TradingViewClient
    client = TradingViewClient()
    # sd_zones has 6 non-OPEN entries: ±1SD, ±2SD, ±3SD
    created = client.create_sd_alerts(sample_session["sd_zones"])
    assert created == 6
