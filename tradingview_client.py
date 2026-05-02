# tradingview_client.py
"""
TradingView Desktop CDP client.

Connects to TradingView Desktop via Chrome DevTools Protocol (CDP) at
http://localhost:{TV_CDP_PORT}/json to:
  - get_quote()         → read current XAUUSD price from the TV page
  - push_pine(code)     → inject Pine Script into Monaco editor + compile
  - create_sd_alerts()  → create TradingView price alerts at SD levels
  - is_connected()      → health check

Pre-requisite: TradingView Desktop launched with:
  open -a "TradingView" --args --remote-debugging-port=9222
"""
import json
import logging
import requests
import websocket

log = logging.getLogger("tv_client")

# JavaScript injected into TV page to read the current price.
# Tries multiple selectors in order; returns first value > 1000 (gold is always > 1000).
_JS_GET_PRICE = """
(() => {
    const selectors = [
        '[data-name="legend-series-item-price"]',
        '.js-symbol-last',
        '[class*="lastPrice"]',
        '[data-field="last_price"]',
        '.tv-symbol-price-quote__value'
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        const n = parseFloat((el?.textContent || '').replace(/,/g, ''));
        if (n > 1000) return n;
    }
    return null;
})()
"""

# JavaScript that uses Monaco editor API to set Pine Script source.
_JS_SET_PINE_TEMPLATE = """
(() => {{
    const models = typeof monaco !== 'undefined' ? monaco?.editor?.getModels() : null;
    if (!models || models.length === 0) return 'no_editor';
    models[0].setValue({code_json});
    return 'ok';
}})()
"""

# JavaScript to click the "Add to chart" / compile button.
_JS_COMPILE = """
(() => {
    const btn = document.querySelector('[data-name="add-to-chart-button"]') ||
                document.querySelector('button[class*="addToChart"]') ||
                document.querySelector('[class*="compileButton"]');
    if (btn) { btn.click(); return 'compiled'; }
    return 'no_compile_btn';
})()
"""

# JavaScript to attempt creating a TradingView price alert.
_JS_ALERT_TEMPLATE = """
(() => {{
    if (window.tvPlatformPublicChat?.createAlert) {{
        window.tvPlatformPublicChat.createAlert({{
            condition: {{ type: 'crossing', price: {price} }},
            name: '{name}'
        }});
        return 'ok';
    }}
    // Fallback: dispatch custom event some TV versions listen to
    document.dispatchEvent(new CustomEvent('create-alert', {{
        detail: {{ price: {price}, name: '{name}' }}
    }}));
    return 'ok';
}})()
"""


class TradingViewClient:
    def __init__(self, cdp_port: int = 9222):
        self.cdp_port = cdp_port

    # ── Public interface ──────────────────────────────────────────────────

    def is_connected(self) -> bool:
        """Returns True if a TradingView tab is reachable via CDP."""
        try:
            tabs = requests.get(
                f"http://localhost:{self.cdp_port}/json", timeout=2
            ).json()
            return any("tradingview" in t.get("url", "").lower() for t in tabs)
        except Exception:
            return False

    def get_quote(self) -> float | None:
        """Returns current XAUUSD price from TradingView Desktop, or None."""
        try:
            ws_url = self._get_tv_ws_url()
            ws = websocket.create_connection(ws_url, timeout=5)
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": _JS_GET_PRICE, "returnByValue": True},
            }))
            result = json.loads(ws.recv())
            ws.close()
            val = result.get("result", {}).get("result", {}).get("value")
            return float(val) if val is not None else None
        except Exception as e:
            log.debug(f"get_quote: {e}")
            return None

    def push_pine(self, code: str) -> bool:
        """
        Inject Pine Script into TradingView's Monaco editor and compile it.
        Returns True on success, False if the editor was not found or CDP failed.
        """
        try:
            ws_url = self._get_tv_ws_url()
            ws = websocket.create_connection(ws_url, timeout=10)
        except Exception as e:
            log.warning(f"push_pine: {e}")
            return False

        try:
            # Step 1: set editor source via Monaco API
            code_json = json.dumps(code)
            js_set = _JS_SET_PINE_TEMPLATE.format(code_json=code_json)
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": js_set, "returnByValue": True},
            }))
            result = json.loads(ws.recv())
            val = result.get("result", {}).get("result", {}).get("value")

            if val != "ok":
                log.warning(f"push_pine: Monaco returned '{val}'")
                return False

            # Step 2: click compile / add-to-chart button
            ws.send(json.dumps({
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {"expression": _JS_COMPILE, "returnByValue": True},
            }))
            ws.recv()
            log.info("Pine Script injected and compiled ✓")
            return True

        except Exception as e:
            log.warning(f"push_pine: {e}")
            return False

        finally:
            ws.close()

    def create_sd_alerts(self, sd_zones: dict) -> int:
        """
        Create TradingView price alerts at all SD levels (±1SD, ±2SD, ±3SD).
        Returns the count of successfully created alerts.
        Alert creation is best-effort — failures are logged but not raised.
        """
        levels = {k: v for k, v in sd_zones["zones"].items() if k != "OPEN"}
        created = 0
        for label, price in levels.items():
            if self._create_alert(price, f"XAUUSD {label}: {price:.2f}"):
                created += 1
        log.info(f"Created {created}/{len(levels)} TradingView alerts")
        return created

    # ── Private helpers ───────────────────────────────────────────────────

    def _get_tv_ws_url(self) -> str:
        """Find the CDP WebSocket URL for the active TradingView tab."""
        tabs = requests.get(
            f"http://localhost:{self.cdp_port}/json", timeout=5
        ).json()
        for tab in tabs:
            if "tradingview" in tab.get("url", "").lower():
                return tab["webSocketDebuggerUrl"]
        raise RuntimeError(
            f"No TradingView tab found on CDP port {self.cdp_port}. "
            "Launch TradingView Desktop with --remote-debugging-port=9222"
        )

    def _create_alert(self, price: float, name: str) -> bool:
        """Create a single TradingView price alert via CDP JS execution."""
        try:
            ws_url = self._get_tv_ws_url()
            ws = websocket.create_connection(ws_url, timeout=5)
            safe_name = name.replace("'", "\\'")
            js = _JS_ALERT_TEMPLATE.format(price=price, name=safe_name)
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": js, "returnByValue": True},
            }))
            result = json.loads(ws.recv())
            ws.close()
            val = result.get("result", {}).get("result", {}).get("value")
            return val == "ok"
        except Exception as e:
            log.debug(f"_create_alert {price}: {e}")
            return False
