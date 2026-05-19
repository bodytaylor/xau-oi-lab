"""
XAUUSD Framework — Phase 1 Collector
=====================================
Collects open price from investing.com, then IV from CME Vol2Vol.

CME navigation strategy (from DOM analysis):
  - Navigate to cmegroup.com page — uses browser_profile persistent session
  - The Vol2Vol tool is embedded in an iframe from cmegroup-tools.quikstrike.net
  - iframe loads with default pid — navigate frame URL to pid=40 for Gold
  - Product selector arrow: #ctl11_hlProductArrow (fallback UI path)
  - Expiration popup: #ctl00_ucSelector_hlExpiration → first link in popup
  - EOD tab: #MainContent_ucViewControl_IntegratedV2VExpectedRange_lbEODVolume

Run:  python collector.py
Prereq: python login_setup.py  (once, to save CME session in browser_profile/)
"""

import json, re, sys, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from utils import utc7_now, target_exp_date

BASE_DIR       = Path(__file__).parent
OUTPUT_FILE    = BASE_DIR / "session_data.json"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
CME_SESSION    = BASE_DIR / "cme_session.json"
PROFILE_DIR    = BASE_DIR / "browser_profile"

# ── CME Vol2Vol — navigate via the public CME page (iframe loads quikstrike) ──
CME_PAGE_URL = (
    "https://www.cmegroup.com/tools-information/quikstrike/vol2vol-expected-range.html"
)

HEADLESS = False
SLOW_MO  = 150
TIMEOUT  = 45_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "collector.log"),
    ]
)
log = logging.getLogger("collector")



def ss(page, name):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    p = SCREENSHOT_DIR / f"{utc7_now().strftime('%H%M%S')}_{name}.png"
    page.screenshot(path=str(p), full_page=False)
    log.info(f"Screenshot → {p.name}")


def parse_float(text: str) -> float | None:
    m = re.search(r"[\d]+(?:\.\d+)?", str(text).replace(",", ""))
    return float(m.group()) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def make_context(pw, load_session=False):
    """Fresh browser context — used for investing.com (no auth needed)."""
    browser = pw.chromium.launch(
        headless=HEADLESS,
        slow_mo=SLOW_MO,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    opts = dict(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="Asia/Bangkok",
    )
    if load_session and CME_SESSION.exists():
        opts["storage_state"] = json.loads(CME_SESSION.read_text())
        log.info("CME session loaded ✓")
    ctx = browser.new_context(**opts)
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        "window.chrome={runtime:{}};"
    )
    return browser, ctx


def make_persistent_context(pw):
    """
    Persistent browser context using the saved browser_profile directory.
    Required for CME — the quikstrike iframe uses CME's authenticated session
    which is stored in the full browser profile (not just cookies/localStorage).
    Run login_setup.py once first to populate browser_profile/.
    """
    if not PROFILE_DIR.exists():
        raise RuntimeError(
            "browser_profile/ missing — run login_setup.py first to save your CME session"
        )
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=HEADLESS,
        slow_mo=SLOW_MO,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — investing.com open price
# ─────────────────────────────────────────────────────────────────────────────

def fetch_open_price(page) -> float:
    log.info("→ investing.com XAU/USD")
    page.goto("https://www.investing.com/currencies/xau-usd",
              timeout=TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    for desc, fn in [
        ("data-test attr",   lambda: page.locator('[data-test="instrument-price-open"]').first.inner_text(timeout=5000)),
        ("dl Open label",    lambda: page.evaluate("""() => {
            for (const dt of document.querySelectorAll('dt'))
                if (dt.innerText.trim().toLowerCase()==='open') {
                    const dd=dt.nextElementSibling;
                    return dd?dd.innerText.trim():null;
                }
            return null;
        }""")),
    ]:
        try:
            raw = fn()
            if raw:
                p = parse_float(raw)
                if p and p > 500:
                    log.info(f"Open price [{desc}]: {p}")
                    try:
                        ss(page, "investing_open")
                    except Exception as sse:
                        log.debug(f"Screenshot failed (non-fatal): {sse}")
                    return p
        except Exception as e:
            log.debug(f"[{desc}] {e}")

    try:
        ss(page, "investing_FAILED")
    except Exception:
        pass
    raise RuntimeError("Could not extract open price — check screenshots/")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — CME Vol2Vol: navigate CME page → iframe → Gold → expiration → EOD
# ─────────────────────────────────────────────────────────────────────────────

def fetch_iv_from_cme(page, open_price: float) -> dict:
    """
    CME architecture (confirmed via DOM inspection):
      - Outer page : cmegroup.com/tools-information/quikstrike/vol2vol-expected-range.html
      - Tool iframe : cmegroup-tools.quikstrike.net (class="cmeIframe")
      - iframe loads with default pid (Soybeans=25); navigate frame to pid=40 for Gold
      - Expiration popup : #ctl00_ucSelector_hlExpiration → first link = nearest
      - EOD tab : #MainContent_ucViewControl_IntegratedV2VExpectedRange_lbEODVolume
    """
    log.info("→ CME Vol2Vol (Gold, EOD)")
    for _attempt in range(3):
        try:
            page.goto(CME_PAGE_URL, timeout=TIMEOUT, wait_until="domcontentloaded")
            break
        except Exception as e:
            if _attempt == 2:
                raise RuntimeError(f"CME page failed to load after 3 attempts: {e}") from e
            log.warning(f"CME page.goto attempt {_attempt + 1} failed ({e}), retrying…")
            page.wait_for_timeout(5000)
    page.wait_for_timeout(8000)

    # ── Login guard ────────────────────────────────────────────────────────
    if _is_login_page(page):
        ss(page, "cme_login_wall")
        raise RuntimeError("CME session expired — run login_setup.py")

    ss(page, "cme_loaded")

    # ── Locate the quikstrike tool iframe ──────────────────────────────────
    frame = _get_tool_frame(page)
    if frame is None:
        ss(page, "cme_no_iframe")
        raise RuntimeError("QuikStrike iframe not found — check CME page loaded correctly")

    log.info(f"Tool iframe URL: {frame.url}")

    # ── Step A: Navigate iframe to Gold (pid=40) ───────────────────────────
    _navigate_to_gold(frame, page)
    page.wait_for_timeout(4000)

    # ── Step B: Select expiration = today (nearest front-month) ───────────
    _select_today_expiration(frame, page)
    page.wait_for_timeout(3000)

    # ── Step C: Set volume mode = EOD ─────────────────────────────────────
    _set_volume_mode(frame, page, "EOD")
    page.wait_for_timeout(3000)

    ss(page, "cme_eod_configured")

    # ── Step D: Extract IV at open_price strike ────────────────────────────
    result = _extract_iv(frame, page, open_price)
    ss(page, "cme_iv_done")
    result["dte"] = _read_dte(frame)

    # ── Step E: Extract per-strike EOD bar data (while still on EOD tab) ──
    eod_bars = _extract_eod_bars(frame, page, open_price)
    ss(page, "cme_eod_bars_done")

    # ── Step F: Full vol curve sweep (while still on EOD tab) ─────────────
    eod_vol_curve = _extract_eod_vol_curve(frame, page)

    return result, eod_bars, eod_vol_curve


def _is_login_page(page) -> bool:
    url   = page.url.lower()
    title = page.title().lower()
    return any(k in url or k in title for k in ["login", "signin", "sign-in", "log-in"])


def _get_tool_frame(page):
    """
    Find the cmegroup-tools.quikstrike.net iframe that contains the Vol2Vol chart.
    The main CME page URL also contains 'quikstrike' so we must match the subdomain
    specifically. Waits up to 15 extra seconds if not found immediately.
    """
    page.wait_for_timeout(2000)

    def _find(frames):
        for f in frames:
            if "cmegroup-tools.quikstrike.net" in f.url:
                return f
        return None

    frame = _find(page.frames)
    if frame:
        log.info(f"Tool iframe found ✓")
        return frame

    log.info("Iframe not yet loaded — waiting 15s more...")
    page.wait_for_timeout(15000)
    frame = _find(page.frames)
    if frame:
        log.info(f"Tool iframe found after extra wait ✓")
    return frame


def _navigate_to_gold(frame, page):
    """
    Navigate the quikstrike iframe to Gold (pid=40) by modifying the frame URL.
    Primary approach: replace pid in the existing frame URL (preserves all session tokens).
    Fallback: use the product selector UI.
    """
    current_url = frame.url
    log.info(f"Current iframe pid: {re.search(r'pid=\\d+', current_url)}")

    if "pid=40" in current_url:
        log.info("Gold (pid=40) already selected ✓")
        return

    # Primary: navigate frame URL with pid=40
    gold_url = re.sub(r'\bpid=\d+', 'pid=40', current_url)
    log.info(f"Navigating iframe to Gold URL (pid=40)...")
    try:
        frame.goto(gold_url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        log.info("Gold loaded via URL navigation ✓")
        return
    except Exception as e:
        log.warning(f"Frame URL navigation failed ({e}), trying UI product selector...")

    # Fallback: UI product selector
    _select_gold_via_ui(frame, page)


def _select_gold_via_ui(frame, page):
    """
    Open the product selector popup and navigate: Metals → Precious Metals → Gold.
    Uses confirmed IDs from DOM inspection.
    """
    try:
        # Open the product popup by clicking the product text/arrow
        for sel in ["#ctl11_hlProductText", "#ctl11_hlProductArrow"]:
            try:
                btn = frame.locator(sel).first
                if btn.count() > 0:
                    btn.click(force=True)
                    page.wait_for_timeout(1000)
                    log.info(f"Product popup opened via {sel}")
                    break
            except Exception:
                pass

        # Click Metals (groupid=6) — now visible after popup opened
        metals = frame.locator('a[groupid="6"]').first
        metals.click(timeout=10000)
        page.wait_for_timeout(1000)
        log.info("Metals selected ✓")

        # Click Precious Metals family — familyid=6 based on original pf=6 param
        # Try familyid=6 first, then scan visible family links for "Precious"
        precious = None
        for fid in ["6", "7", "8"]:
            loc = frame.locator(f'a[familyid="{fid}"]').first
            if loc.count() > 0:
                txt = loc.inner_text(timeout=2000)
                if "precious" in txt.lower():
                    precious = loc
                    break
        if precious is None:
            # Scan all visible family links
            precious = frame.locator('a[familyid]').filter(has_text=re.compile("precious", re.I)).first
        precious.click(timeout=10000)
        page.wait_for_timeout(1000)
        log.info("Precious Metals selected ✓")

        # Click Gold (productid=40)
        gold = frame.locator('a[productid="40"]').first
        gold.click(timeout=10000)
        page.wait_for_timeout(4000)
        log.info("Gold selected via UI ✓")

    except Exception as e:
        log.warning(f"_select_gold_via_ui: {e}")


def _select_today_expiration(frame, page):
    """
    Select the expiration matching today's date (or last Friday on weekends).
    Date format in the popup is 'dd MMM yyyy' (e.g. '06 May 2026').
    Always opens the popup — the site auto-selects Friday's contract, which is wrong
    on days when a same-day expiry exists.
    """
    target = target_exp_date()
    log.info(f"Target expiration date: {target}")
    try:
        exp_link = frame.locator("#ctl00_ucSelector_hlExpiration").first
        if exp_link.count() == 0:
            log.warning("Expiration link not found")
            return
        exp_link.click(timeout=10000)
        page.wait_for_timeout(1500)

        # Search for a link whose text contains today's date.
        # Pass target as an argument to avoid embedding it in a JS string literal.
        js_fn = """(target) => {
            const links = document.querySelectorAll('#ctl00_ucSelector_pnlExpirations a');
            for (const a of links) {
                if (a.innerText.trim().includes(target)) {
                    a.click();
                    return a.innerText.trim();
                }
            }
            return null;
        }"""
        found = frame.evaluate(js_fn, target)

        if found:
            log.info(f"Expiration matched by date '{target}': {found} ✓")
            page.wait_for_timeout(2000)
        else:
            log.warning(f"No expiration found for '{target}' — falling back to first link")
            first_exp = frame.locator("#ctl00_ucSelector_pnlExpirations a").first
            if first_exp.count() == 0:
                # Try legacy selector
                first_exp = frame.locator(
                    '[id*="lvGroupsExpirations_ctrl0_lvExpirations_ctrl0_lbExpiration"]'
                ).first
            if first_exp.count() > 0:
                exp_label = first_exp.inner_text(timeout=3000).strip()
                log.info(f"Selecting fallback expiration: {exp_label}")
                first_exp.click(timeout=10000)
                page.wait_for_timeout(2000)
                log.info("Fallback expiration selected ✓")
            else:
                log.warning("No expiration links found in popup")

    except Exception as e:
        log.warning(f"_select_today_expiration: {e}")


def _set_volume_mode(frame, page, mode: str):
    """
    Switch to EOD or Intraday volume tab.
    Confirmed element IDs from DOM inspection:
      EOD      : #MainContent_ucViewControl_IntegratedV2VExpectedRange_lbEODVolume
      Intraday : #MainContent_ucViewControl_IntegratedV2VExpectedRange_lbIntradayVolume
    """
    log.info(f"Setting volume mode → {mode}")
    id_map = {
        "EOD":      "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbEODVolume",
        "Intraday": "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbIntradayVolume",
    }
    target_id = id_map.get(mode.upper())
    if not target_id:
        log.warning(f"Unknown mode '{mode}'")
        return

    try:
        btn = frame.locator(f"#{target_id}").first
        if btn.count() > 0:
            btn.click(timeout=10000)
            log.info(f"Volume mode set to {mode} ✓")
            return
    except Exception as e:
        log.debug(f"Direct ID click failed: {e}")

    # Fallback: text match on leaf elements
    try:
        result = frame.evaluate(f"""() => {{
            const all = document.querySelectorAll('a,button,li,span,div');
            for (const el of all) {{
                if (el.children.length === 0 &&
                    el.innerText.trim().toLowerCase() === '{mode.lower()}') {{
                    el.click();
                    return el.id || el.className || 'clicked';
                }}
            }}
            return null;
        }}""")
        if result:
            log.info(f"Volume mode set via text match: {result}")
        else:
            log.warning(f"Could not find volume mode toggle for '{mode}'")
    except Exception as e:
        log.debug(f"_set_volume_mode fallback: {e}")


def _extract_iv(frame, page, open_price: float) -> dict:
    """
    Extract implied volatility from the Vol2Vol chart at the strike closest to open_price.

    Strategy:
    1. Hover sweep — moves mouse across the chart, reads Highcharts hoverPoint.x and
       the info bar Vol: value at each bar; stops at the bar nearest open_price.
       Gold bars are on a 5-point grid, so target = round(open_price/5)*5.
    2. Vol info line — reads whatever 'Vol: XX.XX' the chart currently displays
       (default/ATM bar, not necessarily at open_price).
    3. Highcharts JS volatility series lookup.
    4. SVG data attributes.
    5. Table extraction.
    6. Manual fallback.
    """
    # ── A: Hover sweep to find the exact strike nearest open_price ─────────
    log.info("Trying chart hover sweep (primary)...")
    hover_result = _hover_for_tooltip(frame, page, open_price)
    if hover_result:
        return hover_result

    # ── B: Vol Settle line (fallback — shows default/ATM bar, not open_price) ─
    log.info("Trying Vol Settle line extraction (fallback)...")
    vol_info = frame.evaluate("""() => {
        // The chart info bar shows "Put: x  Call: y  Vol Settle: xx.xx" on hover
        let best = null;
        for (const el of document.querySelectorAll('*')) {
            const txt = (el.innerText || '').trim();
            if (!txt) continue;
            if (/Vol Settle:\\s*[\\d.]+/.test(txt) && txt.length < 500) {
                if (best === null || txt.length < best.length) best = txt;
            }
        }
        return best;
    }""")
    if vol_info:
        m = re.search(r'Vol Settle:\s*([\d.]+)', vol_info)
        if m:
            iv = float(m.group(1))
            log.info(f"IV from Vol Settle fallback: {iv}%  (text: {vol_info[:120]})")
            return {"iv_pct": iv, "strike": open_price, "source": "vol_settle_line"}

    # ── C: JS Highcharts — volatility series only ──────────────────────────
    log.info("Trying Highcharts JS data (volatility series)...")
    js_data = frame.evaluate("""() => {
        if (window.Highcharts?.charts?.length > 0) {
            const chart = window.Highcharts.charts.find(c => c);
            if (chart?.series) {
                // Log all series names for debugging
                const names = chart.series.map(s => s.name);
                console.log('[collector] Highcharts series:', JSON.stringify(names));

                const out = [];
                chart.series.forEach(s => {
                    const n = (s.name || '').toLowerCase();
                    // Include only volatility-related series; exclude pure volume/OI bars
                    const isVol = /vol|volatility|iv|implied/.test(n);
                    const notVol = /^(put|call|open.interest|^oi$|churn)$/i.test(n.trim());
                    if (isVol && !notVol) {
                        s.data?.forEach(pt => {
                            if (pt.x && pt.y) {
                                out.push({ x: pt.x, y: pt.y, name: s.name });
                            }
                        });
                    }
                });

                if (out.length > 0) return JSON.stringify(out.slice(0, 200));

                // Fallback: return ALL series with names for manual diagnosis
                const all = [];
                chart.series.forEach(s => {
                    s.data?.slice(0,5).forEach(pt => {
                        all.push({ x: pt.x, y: pt.y, name: s.name });
                    });
                });
                return JSON.stringify({_all_series: names, sample: all});
            }
        }
        for (const k of ['chartData','volData','seriesData']) {
            if (window[k]) {
                try { return JSON.stringify(window[k]).slice(0,3000); } catch(e) {}
            }
        }
        return null;
    }""")

    if js_data:
        log.info(f"Highcharts data found ({len(js_data)} chars)")
        parsed = _parse_js_chart_data(js_data, open_price)
        if parsed:
            return parsed

    # ── D: SVG data ────────────────────────────────────────────────────────
    log.info("Trying SVG data extraction...")
    svg_data = frame.evaluate("""() => {
        const results = [];
        document.querySelectorAll('rect[data-strike],rect[title],g[data-strike]').forEach(el => {
            const strike = el.getAttribute('data-strike') || el.getAttribute('data-x');
            const vol    = el.getAttribute('data-vol') || el.getAttribute('data-y') ||
                           el.getAttribute('title');
            if (strike) results.push({strike: parseFloat(strike), vol});
        });
        return results;
    }""")
    if svg_data:
        log.info(f"SVG data points: {svg_data[:3]}")
        candidates = [r for r in svg_data if r.get("vol")]
        if candidates:
            best = min(candidates, key=lambda r: abs(r["strike"] - open_price))
            iv = parse_float(str(best["vol"]))
            if iv:
                log.info(f"IV from SVG: strike={best['strike']} iv={iv}%")
                return {"iv_pct": iv, "strike": best["strike"], "source": "svg"}

    # ── E: Table extraction ────────────────────────────────────────────────
    log.info("Trying table extraction...")
    table_data = frame.evaluate("""() => {
        const out = [];
        for (const tbl of document.querySelectorAll('table')) {
            const hdrs = Array.from(tbl.querySelectorAll('th'))
                .map(h => h.innerText.trim().toLowerCase());
            if (!hdrs.some(h => h.includes('strike') || h.includes('price'))) continue;
            const si = hdrs.findIndex(h => h.includes('strike') || h.includes('price'));
            const vi = hdrs.findIndex(h => h.includes('iv') || h.includes('vol') || h.includes('implied'));
            if (si < 0 || vi < 0) continue;
            for (const tr of tbl.querySelectorAll('tbody tr')) {
                const cells = Array.from(tr.querySelectorAll('td')).map(c => c.innerText.trim());
                const s = parseFloat(cells[si]?.replace(/,/g,''));
                const v = parseFloat(cells[vi]?.replace(/[,%]/g,''));
                if (!isNaN(s) && !isNaN(v)) out.push({strike: s, iv: v});
            }
        }
        return out;
    }""")
    if table_data:
        best = min(table_data, key=lambda r: abs(r["strike"] - open_price))
        log.info(f"IV from table: strike={best['strike']} iv={best['iv']}%")
        return {"iv_pct": best["iv"], "strike": best["strike"], "source": "table"}

    # ── F: Manual fallback ─────────────────────────────────────────────────
    ss(page, "cme_manual_needed")
    log.warning("All auto-extraction failed — requesting manual input")
    print(f"\n>>> CME browser is open. Find the IV% at strike {open_price:.0f} in the Vol2Vol chart.")
    iv_str = input(f"    Enter the EOD Implied Volatility % at {open_price:.0f}: ").strip()
    return {"iv_pct": float(iv_str), "strike": open_price, "source": "manual"}


def _parse_js_chart_data(raw_json: str, open_price: float) -> dict | None:
    """Parse Highcharts volatility series data to find IV at open_price."""
    try:
        data = json.loads(raw_json)

        # Diagnostic fallback shape from extraction — log and bail
        if isinstance(data, dict) and "_all_series" in data:
            log.info(f"Highcharts series names: {data['_all_series']}")
            log.info(f"Sample points: {data.get('sample', [])[:5]}")
            return None

        if not isinstance(data, list):
            return None

        candidates = []
        for point in data:
            if not isinstance(point, dict):
                continue
            x = point.get("x") or point.get("strike") or point.get("category")
            y = point.get("y") or point.get("iv") or point.get("vol")
            name = (point.get("name") or "").lower()
            if x and y and isinstance(x, (int, float)) and x > 500:
                # y values for volatility are typically 5–150%
                # Skip points that look like raw volume counts (large integers)
                if isinstance(y, (int, float)) and 1.0 <= float(y) <= 200.0:
                    candidates.append({"strike": float(x), "iv": float(y), "series": name})

        if candidates:
            log.info(f"Vol series candidates (n={len(candidates)}): {candidates[:5]}")
            best = min(candidates, key=lambda c: abs(c["strike"] - open_price))
            log.info(f"IV from JS data: {best}")
            return {"iv_pct": best["iv"], "strike": best["strike"], "source": "highcharts"}

    except Exception as e:
        log.debug(f"JS chart parse failed: {e}")
    return None


def _extract_eod_bars(frame, page, open_price: float) -> list:
    """
    Extract per-strike EOD volume bars from the Highcharts chart.
    Must be called while the tool is already on the EOD tab.
    Returns tagged rows identical in structure to oi_collector's oi_data rows.
    """
    log.info("Extracting EOD bar chart data...")
    hc = None
    try:
        hc = frame.evaluate("""() => {
            if (!window.Highcharts?.charts) return null;
            const chart = window.Highcharts.charts.find(c => c && c.series?.length > 0);
            if (!chart) return null;
            const xCats = chart.xAxis?.[0]?.categories || [];
            const out = { series: [], xAxis: xCats };
            chart.series.forEach(s => {
                const pts = (s.data || []).map((pt, i) => ({
                    x: pt.x ?? i,
                    y: pt.y,
                    category: xCats[pt.x ?? i] || pt.category,
                    options: {
                        strike: pt.options?.strike || pt.options?.custom?.strike,
                        delta:  pt.options?.delta  || pt.options?.custom?.delta,
                    },
                }));
                out.series.push({ name: s.name, color: s.color, data: pts });
            });
            return out;
        }""")
    except Exception as e:
        log.debug(f"_extract_eod_bars eval: {e}")

    if not hc:
        log.warning("No Highcharts data on EOD tab — eod_data will be empty")
        return []

    rows = []
    x_cats = hc.get("xAxis", [])
    for series in hc.get("series", []):
        name  = (series.get("name") or "").lower()
        color = (series.get("color") or "").lower()
        if "put" in name or "orange" in color or "#f5a623" in color:
            opt_type = "put"
        elif "call" in name or "blue" in color or "#2196" in color or color.startswith("#0"):
            opt_type = "call"
        else:
            continue
        for pt in series.get("data", []):
            y = pt.get("y")
            if not y or y <= 0:
                continue
            cat = pt.get("category") or (
                x_cats[pt.get("x", 0)] if pt.get("x", 0) < len(x_cats) else None
            )
            strike = None
            if cat:
                m = re.search(r"(\d{3,})", str(cat))
                if m:
                    strike = float(m.group(1))
            if not strike and pt.get("options", {}).get("strike"):
                strike = float(pt["options"]["strike"])
            if strike and strike > 0:
                rows.append({
                    "strike":     strike,
                    "volume":     float(y),
                    "type":       opt_type,
                    "delta":      None,
                    "source":     "highcharts_eod",
                    "is_magnet":  False,
                    "gamma_risk": False,
                })

    # Tag top-2 strikes by total volume as magnets
    if rows:
        vol_by_strike: dict = {}
        for r in rows:
            vol_by_strike[r["strike"]] = vol_by_strike.get(r["strike"], 0) + r["volume"]
        top2 = set(sorted(vol_by_strike, key=vol_by_strike.get, reverse=True)[:2])
        for r in rows:
            r["is_magnet"] = r["strike"] in top2

    log.info(f"EOD bars: {len(rows)} rows extracted")
    return rows


def _extract_eod_vol_curve(frame, page) -> list:
    """
    Full hover sweep across the EOD chart to collect per-strike IV.
    Must be called while on the EOD tab.
    Does not stop early — sweeps the entire chart width to capture all strikes.
    Returns list of {strike, iv} sorted by strike, empty if unavailable.
    """
    log.info("Extracting EOD vol curve (full sweep)...")
    try:
        geo = frame.evaluate("""() => {
            const chart = window.Highcharts?.charts?.find(c => c);
            if (!chart) return null;
            const ax  = chart.xAxis[0];
            const svg = document.querySelector('svg.highcharts-root');
            if (!svg) return null;
            const r = svg.getBoundingClientRect();
            return {
                svgLeft:    r.left,
                svgTop:     r.top,
                plotLeft:   chart.plotLeft,
                plotWidth:  chart.plotWidth,
                plotTop:    chart.plotTop,
                plotHeight: chart.plotHeight,
                xMin:       ax.min,
                xMax:       ax.max,
            };
        }""")
        if not geo or geo.get("xMin") is None:
            log.debug("EOD vol curve: Highcharts geometry unavailable")
            return []

        pts: dict = {}
        steps = 60
        for i in range(steps):
            frac = i / (steps - 1)
            cx = geo["svgLeft"] + geo["plotLeft"] + frac * geo["plotWidth"]
            cy = geo["svgTop"]  + geo["plotTop"]  + geo["plotHeight"] * 0.40

            frame.evaluate(f"""() => {{
                const svg = document.querySelector('svg.highcharts-root');
                if (svg) svg.dispatchEvent(new MouseEvent('mousemove', {{
                    bubbles: true, cancelable: true,
                    clientX: {cx}, clientY: {cy},
                    view: window,
                }}));
            }}""")
            page.wait_for_timeout(150)

            hover_x = frame.evaluate("""() =>
                window.Highcharts?.charts?.find(c => c)?.hoverPoint?.x ?? null
            """)
            vol = frame.evaluate("""() => {
                let best = null;
                for (const el of document.querySelectorAll('*')) {
                    const txt = (el.innerText || '').trim();
                    const m = txt.match(/Vol Settle:\\s*([\\d.]+)/);
                    if (m && txt.length < 500) {
                        if (best === null || txt.length < best.len)
                            best = { val: parseFloat(m[1]), len: txt.length };
                    }
                }
                return best ? best.val : null;
            }""")

            if hover_x is not None and vol is not None and 5.0 <= vol <= 200.0:
                if hover_x not in pts or vol > pts[hover_x]:
                    pts[hover_x] = vol
                log.debug(f"  eod_curve {i:02d}: strike={hover_x}  vol={vol}%")

        result = sorted(
            [{"strike": s, "iv": v} for s, v in pts.items()],
            key=lambda x: x["strike"],
        )
        preview = [f'{p["strike"]:.0f}={p["iv"]:.2f}' for p in result[:6]]
        log.info(f"EOD vol curve: {len(result)} points — {preview}")
        return result

    except Exception as e:
        log.debug(f"_extract_eod_vol_curve: {e}")
        return []


def _read_dte(frame) -> float | None:
    """
    Read DTE from the Vol2Vol chart title bar.
    Example: 'Gold (OG|GC) G1MK6 (1.28 DTE) vs 4644.5 (+0)' → 1.28
    """
    try:
        title_text = frame.evaluate("""() => {
            let best = null;
            for (const el of document.querySelectorAll('*')) {
                const txt = (el.innerText || '').trim();
                if (!txt) continue;
                if (txt.indexOf('DTE') >= 0 && txt.indexOf(' vs ') >= 0 && txt.length < 200) {
                    if (!best || txt.length < best.length) best = txt;
                }
            }
            return best;
        }""")
        if title_text:
            m = re.search(r'\(([0-9.]+)\s+DTE\)', title_text)
            if m:
                dte = float(m.group(1))
                log.info(f"DTE from title: {dte}  (text: {title_text})")
                return dte
        log.warning("DTE not found in chart title bar")
    except Exception as e:
        log.debug(f"_read_dte: {e}")
    return None


def _hover_for_tooltip(frame, page, open_price: float) -> dict | None:
    """
    Read the implied volatility at the strike nearest to open_price from the
    Vol2Vol chart info bar.

    Gold x-axis steps by 5 → target = round(open_price / 5) * 5.

    page.mouse.move() does not fire Highcharts pointer events across the
    cross-origin iframe boundary.  Instead we dispatch a synthetic MouseEvent
    directly inside the frame's JS context at the correct clientX/clientY
    (derived from the SVG element's getBoundingClientRect + the Highcharts
    plot-area geometry).

    Fallback: sweep 60 synthetic events left→right and track the info-bar
    Vol: value; pick the step with hoverPoint.x closest to the target.
    """
    target_strike = round(open_price / 5) * 5
    log.info(f"Hover — target strike: {target_strike}  (open={open_price:.2f})")

    try:
        # ── Gather Highcharts plot-area geometry from inside the frame ──────
        geo = frame.evaluate("""() => {
            const chart = window.Highcharts?.charts?.find(c => c);
            if (!chart) return null;
            const ax  = chart.xAxis[0];
            const svg = document.querySelector('svg.highcharts-root');
            if (!svg) return null;
            const r   = svg.getBoundingClientRect();
            return {
                svgLeft:   r.left,
                svgTop:    r.top,
                plotLeft:  chart.plotLeft,
                plotWidth: chart.plotWidth,
                plotTop:   chart.plotTop,
                plotHeight:chart.plotHeight,
                xMin:      ax.min,
                xMax:      ax.max,
            };
        }""")

        if not geo or geo.get("xMin") is None:
            log.debug("Highcharts geometry unavailable — skipping hover")
            return None

        log.info(
            f"xAxis {geo['xMin']:.1f}–{geo['xMax']:.1f}  "
            f"plot x={geo['plotLeft']:.0f}+{geo['plotWidth']:.0f}"
        )

        x_min, x_max = geo["xMin"], geo["xMax"]

        def _dispatch(target_x: float):
            """Fire a synthetic mousemove at the pixel for target_x and return immediately."""
            frac = (target_x - x_min) / (x_max - x_min)
            frac = max(0.0, min(1.0, frac))
            cx = geo["svgLeft"] + geo["plotLeft"] + frac * geo["plotWidth"]
            cy = geo["svgTop"]  + geo["plotTop"]  + geo["plotHeight"] * 0.40
            frame.evaluate(f"""() => {{
                const svg = document.querySelector('svg.highcharts-root');
                if (svg) svg.dispatchEvent(new MouseEvent('mousemove', {{
                    bubbles: true, cancelable: true,
                    clientX: {cx}, clientY: {cy},
                    view: window,
                }}));
            }}""")

        def _read_vol() -> float | None:
            """
            Read Vol Settle: from the chart info bar shown in the top-left corner
            when hovering over a bar: 'Put: x  Call: y  Vol Settle: xx.xx'
            """
            return frame.evaluate("""() => {
                let best = null;
                for (const el of document.querySelectorAll('*')) {
                    const txt = (el.innerText || '').trim();
                    const m   = txt.match(/Vol Settle:\\s*([\\d.]+)/);
                    if (m && txt.length < 500) {
                        if (best === null || txt.length < best.len)
                            best = { val: parseFloat(m[1]), len: txt.length };
                    }
                }
                return best ? best.val : null;
            }""")

        def _read_hover_x() -> float | None:
            """Return the x-value of the bar Highcharts currently considers hovered."""
            return frame.evaluate("""() =>
                window.Highcharts?.charts?.find(c => c)?.hoverPoint?.x ?? null
            """)

        # ── Strategy A: dispatch at exact target pixel, wait, then read ─────
        _dispatch(target_strike)
        page.wait_for_timeout(400)          # let Highcharts callbacks fire
        vol_a   = _read_vol()
        hover_a = _read_hover_x()
        log.info(f"Strategy A — hoverPoint.x={hover_a}  vol={vol_a}%")

        if vol_a is not None and hover_a is not None and abs(hover_a - target_strike) < 5:
            log.info(f"Hover A confirmed: strike={hover_a}  vol={vol_a}%")
            return {"iv_pct": vol_a, "strike": float(hover_a), "source": "chart_hover"}

        # ── Strategy B: sweep left→right, read hoverPoint.x after each step ─
        log.info("Strategy A did not confirm target — running sweep")
        steps = 60
        best  = None

        for i in range(steps):
            sweep_x = x_min + (i / (steps - 1)) * (x_max - x_min)
            _dispatch(sweep_x)
            page.wait_for_timeout(120)      # let callbacks fire between steps

            hover_x = _read_hover_x()
            vol_b   = _read_vol()

            if vol_b is None or hover_x is None:
                continue

            diff = abs(hover_x - open_price)
            log.debug(f"  sweep {i:02d}: strike={hover_x}  vol={vol_b}%  diff={diff:.1f}")

            if best is None or diff < best["diff"]:
                best = {"iv_pct": vol_b, "strike": hover_x, "diff": diff}

            if diff < 2.5:
                log.info(f"Sweep hit target: strike={hover_x}  vol={vol_b}%")
                break

        if best:
            log.info(
                f"Sweep best: strike={best['strike']}  iv={best['iv_pct']}%"
                f"  diff={best['diff']:.1f}"
            )
            return {"iv_pct": best["iv_pct"], "strike": best["strike"], "source": "chart_hover"}

    except Exception as e:
        log.debug(f"Hover extraction error: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — SD Zones
# ─────────────────────────────────────────────────────────────────────────────

def calc_sd_zones(open_price: float, iv_pct: float, dte: float) -> dict:
    daily = iv_pct / 16.0
    sd1   = open_price * (daily / 100.0) * dte
    return {
        "daily_pct": round(daily, 4),
        "dte":       round(dte, 4),
        "sd1_pts":   round(sd1, 2),
        "sd2_pts":   round(sd1 * 2, 2),
        "sd3_pts":   round(sd1 * 3, 2),
        "zones": {
            "+3SD": round(open_price + sd1 * 3, 2),
            "+2SD": round(open_price + sd1 * 2, 2),
            "+1SD": round(open_price + sd1, 2),
            "OPEN": round(open_price, 2),
            "-1SD": round(open_price - sd1, 2),
            "-2SD": round(open_price - sd1 * 2, 2),
            "-3SD": round(open_price - sd1 * 3, 2),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("═" * 52)
    log.info(f"Phase 1  |  {utc7_now().strftime('%Y-%m-%d %H:%M UTC+7')}")
    log.info("═" * 52)
    SCREENSHOT_DIR.mkdir(exist_ok=True)

    if not PROFILE_DIR.exists():
        log.warning("browser_profile/ missing — run login_setup.py first.")

    with sync_playwright() as pw:

        # ── Open price (fresh context — no auth needed) ────────────────────
        b1, ctx1 = make_context(pw, load_session=False)
        try:
            open_price = fetch_open_price(ctx1.new_page())
        except RuntimeError as e:
            log.error(str(e))
            open_price = float(input(">>> Manual — enter open price: ").strip())
        finally:
            b1.close()

        # ── IV + EOD bars from CME (persistent browser_profile session) ────
        ctx2 = make_persistent_context(pw)
        try:
            iv_result, eod_bars, eod_vol_curve = fetch_iv_from_cme(ctx2.new_page(), open_price)
        except Exception as e:
            log.error(str(e))
            v = float(input(f">>> Manual IV% at {open_price:.0f}: ").strip())
            iv_result = {"iv_pct": v, "strike": open_price, "source": "manual"}
            eod_bars = []
            eod_vol_curve = []
        finally:
            ctx2.close()

    dte = iv_result.get("dte")
    if dte is None:
        dte = float(input(">>> Manual — enter DTE (e.g. 0.25): ").strip())
    sd = calc_sd_zones(open_price, iv_result["iv_pct"], dte)
    payload = {
        "version": "1.2",
        "locked_at": utc7_now().isoformat(),
        "date": utc7_now().strftime("%Y-%m-%d"),
        "open_price": open_price,
        "iv_pct": iv_result["iv_pct"],
        "dte": dte,
        "iv_source": iv_result,
        "sd_zones": sd,
        "phase1_complete": True,
        "phase2_complete": False,
        "eod_data": eod_bars,               # from CME EOD tab — populated by Phase 1
        "eod_vol_curve_points": eod_vol_curve,  # per-strike IV from EOD tab — Phase 1
        "oi_data": [],                  # intraday volume — populated by Phase 2
        "oi_interest_data": [],         # open interest  — populated by Phase 2
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2))

    print("\n" + "═" * 52)
    print("  PHASE 1 LOCKED")
    print("═" * 52)
    print(f"  Open  : {open_price}")
    print(f"  IV    : {iv_result['iv_pct']}%  [{iv_result['source']}]")
    print(f"  DTE   : {dte}")
    print(f"  Daily%: {sd['daily_pct']}%   1SD: ±{sd['sd1_pts']} pts")
    print("─" * 52)
    for label, price in sd["zones"].items():
        print(f"  {label:6s}  {price:>10.2f}{'  ◄' if label=='OPEN' else ''}")
    print("═" * 52 + "\n")


if __name__ == "__main__":
    main()
