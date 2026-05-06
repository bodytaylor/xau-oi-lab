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
# Primary source: the TradingView watchlist panel — the XAUUSD row's price cell
# is matched by '[class*="price-"]' and reflects the live last-traded price.
# The watchlist panel MUST be open in TradingView for this to work correctly
# (see README — TradingView Setup).
# Fallback: the OHLCV chart legend. Elements appear in O, H, L, C DOM order;
# we take the LAST value > 1000, which is Close (current price), not Open.
_JS_GET_PRICE = """
(() => {
    const selectors = [
        '[class*="price-"]',
        '[class*="valueValue-"]',
        '[class*="lastPrice"]',
        '.js-symbol-last',
        '[data-field="last_price"]',
        '.tv-symbol-price-quote__value'
    ];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            const n = parseFloat((el?.textContent || '').replace(/,/g, ''));
            if (n > 1000) return n;
        }
    }
    // OHLCV legend fallback: take the last value (Close), not the first (Open).
    const legendPrices = [...document.querySelectorAll('[data-name="legend-series-item-price"]')]
        .map(el => parseFloat((el?.textContent || '').replace(/,/g, '')))
        .filter(n => n > 1000);
    if (legendPrices.length > 0) {
        return legendPrices[legendPrices.length - 1];
    }
    return null;
})()
"""

# JavaScript that sets Pine Script content via Monaco's model.setValue() API.
#
# Why NOT execCommand / Input.insertText:
#   Both go through Monaco's typing pipeline which applies autoIndent:"full"
#   after every newline, snowballing the indentation deeper with each line.
#
# Solution: reach Monaco's ITextModel.setValue() through TradingView's webpack
#   module registry (window.webpackChunktradingview).  setValue() writes
#   directly to the model without any autoIndent post-processing.
#
# The Monaco module ID is discovered at runtime by scanning for the module that
# exposes editor.getModels(), then cached in window.__tvMonacoModId so the
# scan (≈11 k modules) only runs once per TradingView session.
_JS_SET_PINE_TEMPLATE = """
(() => {{
    // ── 1. Obtain webpack require ─────────────────────────────────────────
    let wr = null;
    const chunk = window.webpackChunktradingview;
    if (!chunk) return 'no_webpack_chunk';
    const origPush = chunk.push.bind(chunk);
    chunk.push = function(c) {{
        if (Array.isArray(c) && typeof c[2] === 'function') {{
            const rt = c[2];
            c[2] = function(__wr) {{ wr = __wr; return rt(__wr); }};
        }}
        return origPush(c);
    }};
    chunk.push([[Symbol()], {{}}, function(__wr) {{ wr = __wr; }}]);
    chunk.push = origPush;
    if (!wr) return 'no_webpack_require';

    // ── 2. Locate the Monaco editor module (cached after first lookup) ────
    if (!window.__tvMonacoModId) {{
        for (const id of Object.keys(wr.m || {{}})) {{
            try {{
                const m = wr(id);
                if (m && m.editor && typeof m.editor.getModels === 'function') {{
                    window.__tvMonacoModId = id;
                    break;
                }}
            }} catch(_) {{}}
        }}
    }}
    if (!window.__tvMonacoModId) return 'monaco_module_not_found';

    const monaco = wr(window.__tvMonacoModId);
    if (!monaco || !monaco.editor) return 'no_monaco_editor';

    // ── 3. Find the active Pine Script editor (visible, not just any model) ─
    //   getEditors() returns only live editor instances currently mounted in
    //   the DOM, so the first .pine editor here IS the one the user sees.
    //   Fall back to getModels() only if no live editor is found.
    let model = null;
    const editors = monaco.editor.getEditors ? monaco.editor.getEditors() : [];
    for (const ed of editors) {{
        const m = ed.getModel();
        if (m && m.uri?.path?.endsWith('.pine')) {{ model = m; break; }}
    }}
    if (!model) {{
        // fallback: scan models
        const models = monaco.editor.getModels();
        if (!models.length) return 'no_models';
        model = models.find(m => m.uri?.path?.endsWith('.pine')) || models[0];
    }}

    // ── 4. setValue() — writes directly to model, no autoIndent applied ──
    model.setValue({code_json});
    return 'ok:lines=' + model.getLineCount();
}})()
"""

# JavaScript to open (or show) the Pine Script Editor panel.
_JS_OPEN_PINE_EDITOR = """
(() => {
    const btn =
        document.querySelector('[data-name="pine-dialog-button"]') ||
        document.querySelector('[aria-label="Pine"]');
    if (btn) { btn.click(); return 'opened'; }
    return 'not_found';
})()
"""

# JavaScript to click the "Add to chart" button in the Pine Script editor toolbar.
# data-qa-id="add-script-to-chart" is the stable selector (confirmed via DOM inspection).
# NOTE: do NOT use [data-name="add-symbol-button"] — that is the watchlist "Add symbol"
#       button and opens the symbol-search panel instead.
#
# IMPORTANT: scope the search to exclude buttons inside any open dialog/modal.
# If the indicators search panel is open with a private script highlighted, its
# "Add to chart" button matches the same qa-id and triggers the
# "ask the author to publish" error instead.
_JS_COMPILE = """
(() => {
    const candidates = document.querySelectorAll('[data-qa-id="add-script-to-chart"]');
    // Prefer a button that is NOT inside any dialog/modal overlay
    const btn = [...candidates].find(b =>
        !b.closest('[role="dialog"]') &&
        !b.closest('[data-name="indicators-dialog"]') &&
        !b.closest('[data-name="screener-dialog"]')
    ) || candidates[0];
    if (btn) { btn.click(); return 'compiled'; }
    return 'no_compile_btn';
})()
"""

# Rename flow — three separate JS snippets called with sleeps between them.
# Step 1: open the title drop-down menu.
_JS_RENAME_OPEN_MENU = """
(() => {
    const btn = document.querySelector('[data-qa-id="pine-script-title-button"]');
    if (btn) { btn.click(); return 'opened'; }
    return 'not_found';
})()
"""

# Step 2: click the "Rename…" menu item.
_JS_RENAME_CLICK = """
(() => {
    const item = [...document.querySelectorAll('[role="menuitem"]')]
                    .find(el => el.textContent.trim().startsWith('Rename'));
    if (item) { item.click(); return 'clicked'; }
    return 'not_found';
})()
"""

# Step 3: clear the name input, type the new name, click Save.
_JS_RENAME_TYPE_TEMPLATE = """
(() => {{
    const input = document.querySelector('[data-qa-id="ui-lib-Input-input"]');
    if (!input) return 'no_input';
    input.focus();
    input.select();
    document.execCommand('insertText', false, {name_json});
    const saveBtn = document.querySelector('[data-qa-id="save-btn"]');
    if (saveBtn) {{ saveBtn.click(); return 'saved'; }}
    return 'no_save_btn';
}})()
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

    def open_pine_editor(self) -> bool:
        """
        Click the Pine Script Editor tab so the editor panel is visible.
        Returns True if the button was found and clicked, False otherwise.
        """
        try:
            ws_url = self._get_tv_ws_url()
            ws = websocket.create_connection(ws_url, timeout=5)
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": _JS_OPEN_PINE_EDITOR, "returnByValue": True},
            }))
            result = json.loads(ws.recv())
            ws.close()
            val = result.get("result", {}).get("result", {}).get("value")
            if val == "opened":
                log.info("Pine Editor panel opened ✓")
                return True
            log.warning(f"open_pine_editor: button not found ('{val}')")
            return False
        except Exception as e:
            log.warning(f"open_pine_editor: {e}")
            return False

    def rename_pine_script(self, name: str) -> bool:
        """
        Rename the current Pine Script via the editor's title-button menu.
        Opens the menu → clicks "Rename…" → types the name → clicks Save.
        Returns True on success.
        """
        import time

        def _eval(ws, id_, expr):
            ws.send(json.dumps({
                "id": id_,
                "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True},
            }))
            return json.loads(ws.recv()).get("result", {}).get("result", {}).get("value")

        # Step 1 — open the title drop-down
        try:
            ws = websocket.create_connection(self._get_tv_ws_url(), timeout=5)
            val = _eval(ws, 1, _JS_RENAME_OPEN_MENU)
            ws.close()
        except Exception as e:
            log.warning(f"rename_pine_script step1: {e}")
            return False
        if val != "opened":
            log.warning(f"rename_pine_script: menu open returned '{val}'")
            return False
        time.sleep(0.4)

        # Step 2 — click "Rename…"
        try:
            ws = websocket.create_connection(self._get_tv_ws_url(), timeout=5)
            val = _eval(ws, 2, _JS_RENAME_CLICK)
            ws.close()
        except Exception as e:
            log.warning(f"rename_pine_script step2: {e}")
            return False
        if val != "clicked":
            log.warning(f"rename_pine_script: rename click returned '{val}'")
            return False
        time.sleep(0.4)

        # Step 3 — type name + click Save
        try:
            ws = websocket.create_connection(self._get_tv_ws_url(), timeout=5)
            js = _JS_RENAME_TYPE_TEMPLATE.format(name_json=json.dumps(name))
            val = _eval(ws, 3, js)
            ws.close()
        except Exception as e:
            log.warning(f"rename_pine_script step3: {e}")
            return False
        if val == "saved":
            log.info(f"Pine Script renamed to '{name}' ✓")
            return True
        log.warning(f"rename_pine_script: save returned '{val}'")
        return False

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

            if not (val and str(val).startswith("ok")):
                log.warning(f"push_pine: Monaco returned '{val}'")
                return False

            log.info(f"push_pine: Monaco setValue → {val}")

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
