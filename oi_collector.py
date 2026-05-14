"""
XAUUSD Framework — Phase 2 OI Collector
=========================================
Navigates CME Vol2Vol (inside iframe), switches to INTRADAY,
extracts per-strike Call/Put OI bar chart data.

From DOM analysis:
  - Tool is inside <iframe> on quikstrike.net
  - Product links use: a[productid="40"] for Gold
  - Expiration link ID: ctl00_ucSelector_hlExpiration
  - Chart is Highcharts or similar — bars keyed by delta/strike
  - Legend shows: Put (orange), Call (blue), Vol Settle (dashed), Ranges (gray)
  - X-axis shows delta labels: 45∆P, 45∆C, 35∆C, 25∆C, 15∆C, 5∆C

Run:  python oi_collector.py
"""

import json, re, sys, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright
from utils import utc7_now, target_exp_date

BASE_DIR       = Path(__file__).parent
SESSION_FILE   = BASE_DIR / "session_data.json"
CME_SESSION    = BASE_DIR / "cme_session.json"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
PROFILE_DIR    = BASE_DIR / "browser_profile"

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
        logging.FileHandler(BASE_DIR / "oi_collector.log"),
    ]
)
log = logging.getLogger("oi")


def ss(page, name):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    p = SCREENSHOT_DIR / f"{utc7_now().strftime('%H%M%S')}_{name}.png"
    page.screenshot(path=str(p), full_page=False)
    log.info(f"Screenshot → {p.name}")


# ─── Browser ─────────────────────────────────────────────────────────────────

def make_context(pw):
    """Persistent browser context using browser_profile/ — same as Phase 1."""
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


# ─── CME Navigation ───────────────────────────────────────────────────────────

def navigate_to_intraday(page, open_price: float) -> tuple[list, list, list, str | None]:
    """Phase 2 navigation: extract Intraday volume and Open Interest OI for Gold.
    EOD volume is extracted by Phase 1 (collector.py) — not repeated here.
    Returns (intraday_rows, oi_rows, vol_pts, series_name)
    """
    log.info("→ CME Vol2Vol (Gold, INTRADAY + OI)")
    page.goto(CME_PAGE_URL, timeout=TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    if "login" in page.url.lower() or "sign" in page.url.lower():
        ss(page, "cme_login_wall")
        raise RuntimeError("CME session expired — run login_setup.py")

    ss(page, "cme_p2_loaded")

    # Find the cmegroup-tools.quikstrike.net iframe (same as Phase 1)
    frame = _get_frame(page)
    if frame is None:
        ss(page, "cme_no_iframe")
        raise RuntimeError("QuikStrike iframe not found — check CME page loaded correctly")
    log.info(f"Tool iframe URL: {frame.url}")

    # Ensure Gold is selected (pid=40 in URL should handle this)
    _ensure_gold(frame, page)

    # Select nearest expiration (front month = default)
    series_name = _select_expiration(frame, page)
    page.wait_for_timeout(2000)

    # ── 1. Intraday Volume ────────────────────────────────────────────────
    _set_intraday(frame, page)
    page.wait_for_timeout(4000)
    ss(page, "cme_p2_intraday_set")
    intraday_rows = _extract_bar_data(frame, page, open_price)
    log.info(f"Intraday rows extracted: {len(intraday_rows)}")

    # Extract volatility curve while on Intraday tab (Vol Settle tooltips active)
    vol_pts = _extract_vol_curve(frame, page, open_price)
    log.info(f"Vol curve points extracted: {len(vol_pts)}")

    # ── 2. Open Interest OI ───────────────────────────────────────────────
    _set_oi(frame, page)
    page.wait_for_timeout(4000)
    ss(page, "cme_p2_oi_set")
    oi_rows = _extract_bar_data(frame, page, open_price)
    log.info(f"OI rows extracted: {len(oi_rows)}")

    ss(page, "cme_p2_extracted")
    return intraday_rows, oi_rows, vol_pts, series_name


def _get_frame(page):
    """
    Find the cmegroup-tools.quikstrike.net iframe (same as Phase 1).
    The main CME page URL also contains 'quikstrike' so match subdomain specifically.
    Waits up to 15 extra seconds if not found immediately.
    """
    page.wait_for_timeout(2000)

    def _find(frames):
        for f in frames:
            if "cmegroup-tools.quikstrike.net" in f.url:
                return f
        return None

    frame = _find(page.frames)
    if frame:
        log.info("Tool iframe found ✓")
        return frame

    log.info("Iframe not yet loaded — waiting 15s more...")
    page.wait_for_timeout(15000)
    return _find(page.frames)


def _ensure_gold(frame, page):
    """Navigate iframe to Gold (pid=40) — same approach as Phase 1."""
    current_url = frame.url
    log.info(f"Current iframe URL: {current_url[:80]}")

    if "pid=40" in current_url:
        log.info("Gold (pid=40) already selected ✓")
        return

    # Replace pid in the existing frame URL (preserves session tokens)
    gold_url = re.sub(r'\bpid=\d+', 'pid=40', current_url)
    log.info("Navigating iframe to Gold (pid=40)...")
    try:
        frame.goto(gold_url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        log.info("Gold loaded via URL navigation ✓")
        return
    except Exception as e:
        log.warning(f"Frame URL navigation failed ({e}), trying UI product selector...")

    # Fallback: click product selector UI
    try:
        for sel in ["#ctl11_hlProductText", "#ctl11_hlProductArrow"]:
            btn = frame.locator(sel).first
            if btn.count() > 0:
                btn.click(force=True)
                page.wait_for_timeout(1000)
                break
        frame.locator('a[groupid="6"]').first.click(timeout=10000)
        page.wait_for_timeout(1000)
        precious = frame.locator('a[familyid]').filter(has_text=re.compile("precious", re.I)).first
        precious.click(timeout=10000)
        page.wait_for_timeout(1000)
        frame.locator('a[productid="40"]').first.click(timeout=10000)
        page.wait_for_timeout(4000)
        log.info("Gold selected via UI ✓")
    except Exception as e:
        log.warning(f"_ensure_gold UI fallback: {e}")


def _select_expiration(frame, page):
    """
    Select the expiration matching today's date (or last Friday on weekends).
    Date format in the popup is 'dd MMM yyyy' (e.g. '06 May 2026').
    Always opens the popup — the site auto-selects Friday's contract, which is wrong
    on days when a same-day expiry exists.
    Returns the label text of the selected expiration, or None on failure.
    """
    target = target_exp_date()
    log.info(f"Target expiration date: {target}")
    selected_label = None
    try:
        exp_link = frame.locator("#ctl00_ucSelector_hlExpiration").first
        if exp_link.count() == 0:
            log.warning("Expiration link not found")
            return selected_label
        exp_link.click(timeout=10000)
        page.wait_for_timeout(1500)
        ss(page, "exp_popup")

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
            selected_label = " ".join(found.split())   # collapse internal whitespace
            log.info(f"Expiration matched by date '{target}': {selected_label} ✓")
            page.wait_for_timeout(2000)
        else:
            log.warning(f"No expiration found for '{target}' — falling back to first link")
            first_exp = frame.locator("#ctl00_ucSelector_pnlExpirations a").first
            if first_exp.count() > 0:
                label = " ".join(first_exp.inner_text(timeout=3000).split())
                log.info(f"Selecting fallback expiration: {label}")
                first_exp.click(timeout=10000)
                page.wait_for_timeout(2000)
                log.info(f"Expiration {label} selected ✓")
                selected_label = label
            else:
                page.keyboard.press("Escape")
                log.warning("No expiration links found in popup")

    except Exception as e:
        log.warning(f"_select_expiration: {e}")
    return selected_label


def _set_oi(frame, page):
    """
    Switch to the Open Interest → OI view on CME Vol2Vol.
    Attempts in order:
      1. Direct ID click (naming pattern from EOD/Intraday tabs)
      2. Two-step: click 'Open Interest' category link, then 'OI' sub-tab
      3. Text-only match for an element whose full text is 'OI'
    """
    log.info("Switching to Open Interest OI...")

    # Attempt 1: direct ID guesses (follow the lbEODVolume / lbIntradayVolume pattern)
    for target_id in [
        "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbOpenInterest",
        "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbOI",
        "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbOpenInterestOI",
    ]:
        try:
            btn = frame.locator(f"#{target_id}").first
            if btn.count() > 0:
                btn.click(timeout=8000)
                log.info(f"OI tab clicked via ID ✓  ({target_id})")
                page.wait_for_timeout(3000)
                return
        except Exception as e:
            log.debug(f"ID {target_id}: {e}")

    # Attempt 2: two-step — first click the 'Open Interest' category, then 'OI' sub-tab
    clicked_cat = frame.evaluate("""() => {
        for (const el of document.querySelectorAll('a,button,li,span,div,label')) {
            const txt = (el.innerText || '').trim();
            if (txt === 'Open Interest') { el.click(); return txt; }
        }
        return null;
    }""")
    if clicked_cat:
        log.info(f"Clicked category: '{clicked_cat}'")
        page.wait_for_timeout(1500)
        clicked_tab = frame.evaluate("""() => {
            for (const el of document.querySelectorAll('a,button,li,span,div')) {
                const txt = (el.innerText || '').trim();
                if (txt === 'OI' || txt === 'Open Interest') { el.click(); return txt; }
            }
            return null;
        }""")
        if clicked_tab:
            log.info(f"Clicked OI sub-tab: '{clicked_tab}'")
            page.wait_for_timeout(3000)
            return

    # Attempt 3: bare text match for 'OI'
    result = frame.evaluate("""() => {
        for (const el of document.querySelectorAll('a,button,li,span,div')) {
            if (el.children.length === 0 && (el.innerText || '').trim() === 'OI') {
                el.click();
                return el.id || el.className || 'clicked';
            }
        }
        return null;
    }""")
    if result:
        log.info(f"OI set via text match: {result}")
        page.wait_for_timeout(3000)
    else:
        log.warning("Could not locate Open Interest OI tab — proceeding without OI data")


def _set_eod(frame, page):
    """
    Switch to EOD volume tab.
    Element ID from DOM inspection (same tool as Phase 1):
      #MainContent_ucViewControl_IntegratedV2VExpectedRange_lbEODVolume
    """
    log.info("Switching to EOD volume...")
    target_id = "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbEODVolume"

    try:
        btn = frame.locator(f"#{target_id}").first
        if btn.count() > 0:
            btn.click(timeout=10000)
            log.info("EOD volume tab clicked ✓")
            page.wait_for_timeout(3000)
            return
    except Exception as e:
        log.debug(f"Direct ID click failed: {e}")

    # Fallback: text match
    result = frame.evaluate("""() => {
        const all = document.querySelectorAll('a,button,li,span,div');
        for (const el of all) {
            if (el.children.length === 0 &&
                el.innerText.trim().toLowerCase() === 'eod') {
                el.click();
                return el.id || el.className || 'clicked';
            }
        }
        return null;
    }""")
    if result:
        log.info(f"EOD set via text match: {result}")
        page.wait_for_timeout(3000)
    else:
        log.warning("Could not locate EOD tab — proceeding (may be Intraday)")


def _set_intraday(frame, page):
    """
    Switch to Intraday volume tab.
    Confirmed element ID from DOM inspection (same tool as Phase 1):
      #MainContent_ucViewControl_IntegratedV2VExpectedRange_lbIntradayVolume
    """
    log.info("Switching to INTRADAY volume...")
    target_id = "MainContent_ucViewControl_IntegratedV2VExpectedRange_lbIntradayVolume"

    try:
        btn = frame.locator(f"#{target_id}").first
        if btn.count() > 0:
            btn.click(timeout=10000)
            log.info("Intraday volume tab clicked ✓")
            page.wait_for_timeout(3000)
            return
    except Exception as e:
        log.debug(f"Direct ID click failed: {e}")

    # Fallback: text match
    result = frame.evaluate("""() => {
        const all = document.querySelectorAll('a,button,li,span,div');
        for (const el of all) {
            if (el.children.length === 0 &&
                el.innerText.trim().toLowerCase() === 'intraday') {
                el.click();
                return el.id || el.className || 'clicked';
            }
        }
        return null;
    }""")
    if result:
        log.info(f"Intraday set via text match: {result}")
        page.wait_for_timeout(3000)
    else:
        log.warning("Could not locate Intraday tab — proceeding (may be EOD)")


def _read_info_bar(frame) -> dict | None:
    """
    Read summary stats from the Vol2Vol info bar and title bar.
    Info bar shows: 'Put: X  Call: X  Vol: XX.XX  Vol Chg: X  Future Chg: X'
    Title bar shows: '...vs XXXX.X (+0) - Intraday Volume'
    """
    try:
        data = frame.evaluate("""() => {
            // Info bar — smallest element that contains all three keywords
            let info_text = null;
            for (const el of document.querySelectorAll('*')) {
                const txt = (el.innerText || '').trim();
                if (!txt) continue;
                if (txt.indexOf('Put:') >= 0 && txt.indexOf('Call:') >= 0 &&
                    txt.indexOf('Vol:') >= 0 && txt.length < 500) {
                    if (!info_text || txt.length < info_text.length) info_text = txt;
                }
            }
            // Title bar — element containing 'DTE' and ' vs '
            let title_text = null;
            for (const el of document.querySelectorAll('*')) {
                const txt = (el.innerText || '').trim();
                if (!txt) continue;
                if (txt.indexOf('DTE') >= 0 && txt.indexOf(' vs ') >= 0 && txt.length < 200) {
                    if (!title_text || txt.length < title_text.length) title_text = txt;
                }
            }
            return { info: info_text, title: title_text };
        }""")

        if not data or not data.get('info'):
            return None

        info_text  = data['info']
        title_text = data.get('title') or ''
        log.info(f"Info bar  : {info_text}")
        log.info(f"Title bar : {title_text}")

        put_m  = re.search(r'Put:\s*([\d.]+)',   info_text)
        call_m = re.search(r'Call:\s*([\d.]+)',  info_text)
        vol_m  = re.search(r'\bVol:\s*([\d.]+)', info_text)

        if not vol_m:
            return None

        put_vol    = float(put_m.group(1))  if put_m  else 0.0
        call_vol   = float(call_m.group(1)) if call_m else 0.0
        vol_settle = float(vol_m.group(1))

        # Futures price from title: "Gold (OG|GC) G1MK6 (1.28 DTE) vs 4644.5 (+0)"
        price_m      = re.search(r'\bvs\s+([\d.]+)', title_text)
        futures_price = float(price_m.group(1)) if price_m else None

        log.info(f"Confirmed → Put:{put_vol}  Call:{call_vol}  "
                 f"Vol Settle:{vol_settle}  Future:{futures_price}")
        return {
            "put_vol":       put_vol,
            "call_vol":      call_vol,
            "vol_settle":    vol_settle,
            "futures_price": futures_price,
        }
    except Exception as e:
        log.debug(f"_read_info_bar: {e}")
    return None


def _extract_bar_data(frame, page, open_price: float) -> list:
    """
    Extract per-strike OI bar data from the Vol2Vol chart.

    From screenshot analysis:
    - Chart is a bar chart with Call (blue) and Put (orange) bars
    - X-axis: delta labels (45∆P, 45∆C, 35∆C, 25∆C, 15∆C, 5∆C)
    - Each bar represents volume at that delta strike
    - Highcharts is the likely library (common on financial sites)
    """
    log.info("Extracting OI bar chart data...")

    # ── A: Info bar — always read first ────────────────────────────────────
    info = _read_info_bar(frame)

    # If no intraday volume traded yet (Sunday / pre-market), return directly
    if info and info["put_vol"] == 0 and info["call_vol"] == 0:
        log.info("No intraday volume (Put=0, Call=0) — returning info bar summary")
        fp = info["futures_price"] or open_price
        return [
            {"strike": fp, "volume": 0, "type": "call",
             "vol_settle": info["vol_settle"], "futures_price": fp,
             "delta": None, "source": "info_bar"},
            {"strike": fp, "volume": 0, "type": "put",
             "vol_settle": info["vol_settle"], "futures_price": fp,
             "delta": None, "source": "info_bar"},
        ]

    # ── B: Highcharts series data ──────────────────────────────────────────
    hc_data = _eval(frame, """() => {
        if (!window.Highcharts?.charts) return null;
        const chart = window.Highcharts.charts.find(c => c && c.series?.length > 0);
        if (!chart) return null;

        const out = { series: [], xAxis: [] };

        const xCats = chart.xAxis?.[0]?.categories || [];
        out.xAxis = xCats;

        chart.series.forEach(s => {
            const pts = (s.data || []).map((pt, i) => ({
                x: pt.x ?? i,
                y: pt.y,
                category: xCats[pt.x ?? i] || pt.category,
                name: pt.name,
                options: {
                    strike: pt.options?.strike || pt.options?.custom?.strike,
                    delta:  pt.options?.delta  || pt.options?.custom?.delta,
                },
            }));
            out.series.push({ name: s.name, color: s.color, type: s.type, data: pts });
        });
        return out;
    }""")

    if hc_data:
        log.info(f"Highcharts data: {len(hc_data.get('series',[]))} series, "
                 f"x-cats: {hc_data.get('xAxis',[])[:5]}")
        rows = _parse_hc_data(hc_data, open_price)
        if rows:
            log.info(f"Parsed {len(rows)} OI rows from Highcharts")
            if info:
                for r in rows:
                    r.setdefault("vol_settle", info["vol_settle"])
                    r.setdefault("futures_price", info["futures_price"])
            return rows

    # ── C: SVG element inspection ──────────────────────────────────────────
    log.info("Trying SVG bar inspection...")
    svg_rows = _eval(frame, """() => {
        const out = [];
        const barGroups = document.querySelectorAll(
            '.highcharts-series rect, svg rect[fill], g.bar rect'
        );
        barGroups.forEach((rect, i) => {
            const h = parseFloat(rect.getAttribute('height') || '0');
            if (h < 2) return;
            const fill = rect.getAttribute('fill') || rect.style.fill || '';
            const type = fill.includes('orange') || fill.includes('#f5a6') || fill.includes('e6') ?
                         'put' : 'call';
            const parent = rect.closest('g');
            const title = parent?.querySelector('title')?.textContent;
            out.push({ index: i, height: parseFloat(h.toFixed(1)), fill, type, title: title || null });
        });
        return out;
    }""")

    if svg_rows:
        log.info(f"SVG bars found: {len(svg_rows)}")
        rows = _parse_svg_bars(svg_rows, open_price)
        if rows:
            if info:
                for r in rows:
                    r.setdefault("vol_settle", info["vol_settle"])
                    r.setdefault("futures_price", info["futures_price"])
            return rows

    # ── D: Tooltip sweep ───────────────────────────────────────────────────
    log.info("Trying tooltip hover sweep...")
    tooltip_rows = _sweep_tooltips(frame, page, open_price)
    if tooltip_rows:
        return tooltip_rows

    # ── E: Manual entry ────────────────────────────────────────────────────
    log.warning("All auto-extraction failed — manual entry")
    return _manual_entry()


def _parse_hc_data(hc: dict, open_price: float) -> list:
    """Convert Highcharts series data to our OI row format."""
    rows = []
    x_cats = hc.get("xAxis", [])

    for series in hc.get("series", []):
        name  = (series.get("name") or "").lower()
        color = (series.get("color") or "").lower()

        # Determine call vs put from series name or color
        if "put" in name or "orange" in color or "#f5a623" in color:
            opt_type = "put"
        elif "call" in name or "blue" in color or "#2196" in color or "#0" in color[:3]:
            opt_type = "call"
        else:
            continue  # Skip non-OI series (vol settle, ranges)

        for pt in series.get("data", []):
            y = pt.get("y")
            if not y or y <= 0:
                continue

            # Get strike from category label (e.g. "3200" or "45∆C")
            cat = pt.get("category") or (x_cats[pt.get("x", 0)] if pt.get("x", 0) < len(x_cats) else None)
            strike = None
            if cat:
                m = re.search(r"(\d{3,})", str(cat))
                if m:
                    strike = float(m.group(1))

            # Get strike from options if available
            if not strike and pt.get("options", {}).get("strike"):
                strike = float(pt["options"]["strike"])

            delta = pt.get("options", {}).get("delta")

            rows.append({
                "strike":    strike or 0,
                "volume":    float(y),
                "delta":     float(delta) if delta else None,
                "type":      opt_type,
                "category":  cat,
                "source":    "highcharts",
            })

    return [r for r in rows if r["strike"] > 0]


def _parse_svg_bars(bars: list, open_price: float) -> list:
    """Convert SVG bar heights to relative volume rows."""
    if not bars:
        return []
    max_h = max(b["height"] for b in bars) or 1
    rows = []
    for i, bar in enumerate(bars):
        vol = round(bar["height"] / max_h * 1000)  # normalize to 0-1000
        rows.append({
            "strike":  open_price + (i - len(bars) // 2) * 50,  # estimated
            "volume":  vol,
            "delta":   None,
            "type":    bar.get("type", "call"),
            "source":  "svg_bars",
        })
    return rows


def _sweep_tooltips(frame, page, open_price: float) -> list:
    """Hover across the chart to collect tooltip data per strike."""
    rows = []
    try:
        chart_loc = (frame if hasattr(frame,'locator') else page).locator(
            "canvas, .highcharts-plot-background, svg.highcharts-root, [class*='chart-wrap']"
        ).first
        if chart_loc.count() == 0:
            return []

        bbox = chart_loc.bounding_box()
        if not bbox:
            return []

        seen = set()
        for i in range(30):
            cx = bbox["x"] + (i / 29.0) * bbox["width"]
            cy = bbox["y"] + bbox["height"] * 0.4
            page.mouse.move(cx, cy)
            page.wait_for_timeout(350)

            tip = _eval(frame, """() =>
                [...document.querySelectorAll(
                    '.highcharts-tooltip, [class*="tooltip" i], [role="tooltip"]'
                )].map(t=>t.innerText.trim()).join('|||')
            """)
            if not tip:
                continue

            # Parse: look for strike + call/put volumes + deltas
            strike_m = re.search(r"(\d{3,}(?:\.\d+)?)", tip)
            call_m   = re.search(r"Call[\s:]+(\d+(?:\.\d+)?)", tip, re.I)
            put_m    = re.search(r"Put[\s:]+(\d+(?:\.\d+)?)", tip, re.I)
            delta_m  = re.search(r"Delta[\s:]+(\d+(?:\.\d+)?)", tip, re.I)

            if not strike_m:
                continue
            strike = float(strike_m.group(1))
            if strike in seen:
                continue
            seen.add(strike)

            delta = float(delta_m.group(1)) if delta_m else None

            if call_m:
                rows.append({"strike": strike, "volume": float(call_m.group(1)),
                             "delta": delta, "type": "call", "source": "tooltip"})
            if put_m:
                rows.append({"strike": strike, "volume": float(put_m.group(1)),
                             "delta": abs(delta)*-1 if delta else None,
                             "type": "put", "source": "tooltip"})

    except Exception as e:
        log.debug(f"Tooltip sweep: {e}")
    return rows


def _manual_entry() -> list:
    print("\n─── MANUAL OI ENTRY ─────────────────────")
    rows = []
    while True:
        s = input("Strike (blank to finish): ").strip()
        if not s: break
        try:
            strike = float(s)
            for t in ["call", "put"]:
                v = input(f"  {t.upper()} volume: ").strip()
                d = input(f"  {t.upper()} delta (Enter=skip): ").strip()
                if v:
                    rows.append({"strike": strike, "volume": float(v.replace(",","")),
                                 "delta": float(d) if d else None, "type": t, "source": "manual"})
        except ValueError:
            print("  Invalid — skipping")
    return rows


# ─── Frame evaluation helpers ─────────────────────────────────────────────────

def _eval(frame, script: str):
    try:
        if hasattr(frame, 'evaluate'):
            return frame.evaluate(script)
    except Exception as e:
        log.debug(f"_eval: {e}")
    return None


def _click(frame, page, selector: str) -> bool:
    """Click an element in frame context."""
    result = _eval(frame, f"""() => {{
        const el = document.querySelector('{selector}');
        if (el) {{ el.click(); return true; }}
        return false;
    }}""")
    return bool(result)


# ─── Vol curve extraction & skew analysis ────────────────────────────────────

def _sweep_vol_curve(frame, page) -> list:
    """
    Primary vol curve method: dispatch synthetic mousemove events left→right
    across the Highcharts plot area inside the frame.

    page.mouse.move() does NOT fire Highcharts pointer events across the
    cross-origin iframe boundary.  Synthetic MouseEvents dispatched directly
    inside the frame context (via frame.evaluate) work correctly.

    After the full sweep, discard any reading that does not contain
    'Vol Settle:' then parse the remainder into {strike, iv} points.
    Returns list of {strike, iv} sorted by strike, empty list if unavailable.
    """
    raw_tips: list[str] = []
    try:
        # Get Highcharts plot-area geometry from inside the frame.
        # getBoundingClientRect() in iframe context returns frame-relative
        # coordinates, which match clientX/clientY for dispatched MouseEvents.
        geo = _eval(frame, """() => {
            const chart = window.Highcharts?.charts?.find(c => c);
            if (!chart) return null;
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
            };
        }""")

        if not geo:
            log.info("Vol sweep: Highcharts geometry unavailable")
            return []

        log.info(f"Vol sweep: geo={geo}")

        steps = 60
        for i in range(steps):
            frac = i / (steps - 1)
            cx = geo["svgLeft"] + geo["plotLeft"] + frac * geo["plotWidth"]
            cy = geo["svgTop"]  + geo["plotTop"]  + geo["plotHeight"] * 0.5

            # Dispatch directly inside the frame — works across iframe boundary
            _eval(frame, f"""() => {{
                const svg = document.querySelector('svg.highcharts-root');
                if (svg) svg.dispatchEvent(new MouseEvent('mousemove', {{
                    bubbles: true, cancelable: true,
                    clientX: {cx}, clientY: {cy},
                    view: window,
                }}));
            }}""")
            page.wait_for_timeout(250)

            tip = _eval(frame, """() =>
                [...document.querySelectorAll(
                    '.highcharts-tooltip, [class*="tooltip" i], [role="tooltip"]'
                )].map(t => t.textContent.trim()).join('|||')
            """)
            if not tip:
                continue

            log.info(f"[vol_sweep {i}] {repr(tip[:300])}")
            raw_tips.append(tip)

    except Exception as e:
        log.info(f"Vol sweep error: {e}")

    # --- post-sweep: keep only readings that contain "Vol Settle:" ---
    tips = [t for t in raw_tips if "Vol Settle:" in t]
    log.info(f"Vol sweep: {len(raw_tips)} reads → {len(tips)} contain Vol Settle:")

    pts: dict = {}
    for tip in tips:
        vol_m    = re.search(r"Vol Settle:\s*(\d+(?:\.\d+)?)", tip, re.I)
        strike_m = re.search(r"(\d{3,}(?:\.\d+)?)", tip)
        if not vol_m or not strike_m:
            continue
        iv     = float(vol_m.group(1))
        strike = float(strike_m.group(1))
        if not (5 <= iv <= 200) or strike <= 0:
            continue
        if strike not in pts or iv > pts[strike]:
            pts[strike] = iv

    result = sorted([{"strike": s, "iv": v} for s, v in pts.items()],
                    key=lambda x: x["strike"])
    if result:
        preview = [f'{p["strike"]:.0f}={p["iv"]:.2f}' for p in result[:6]]
        log.info(f"Vol curve (sweep): {len(result)} points — {preview}")
    else:
        log.info("Vol curve sweep: 0 points")
    return result


def _extract_vol_curve(frame, page, ref_price: float) -> list:
    """
    Extract per-strike implied volatility from the Highcharts chart.
    Primary: hover sweep reading 'Vol Settle:' from tooltip textContent.
    Fallback: Highcharts JS series data.
    Returns list of {strike, iv} sorted by strike, empty list if unavailable.
    """
    # Primary: tooltip hover sweep
    pts = _sweep_vol_curve(frame, page)
    if pts:
        return pts
    log.info("Vol sweep returned 0 points — trying Highcharts JS fallback")

    hc_data = _eval(frame, """() => {
        if (!window.Highcharts?.charts) return null;
        const chart = window.Highcharts.charts.find(c => c && c.series?.length > 0);
        if (!chart) return null;

        const xCats = chart.xAxis?.[0]?.categories || [];
        const out = [];

        chart.series.forEach(s => {
            const stype = (s.type || '').toLowerCase();
            const sname = (s.name || '').toLowerCase();
            if (stype === 'column' || stype === 'bar') return;
            const is_vol = sname.includes('vol') || sname.includes('iv') ||
                           sname.includes('settle') || sname.includes('range') ||
                           stype === 'spline' || stype === 'line';
            if (!is_vol) return;

            (s.data || []).forEach((pt, i) => {
                const y = pt.y;
                if (y == null || y <= 0) return;
                const cat = xCats.length ? (xCats[pt.x ?? i] || pt.category || '') : '';
                // When x-axis has no categories, pt.x IS the strike price
                const rawX = (xCats.length === 0 && pt.x > 100) ? pt.x : null;
                out.push({
                    y:        parseFloat(y.toFixed(4)),
                    category: cat,
                    x:        pt.x,
                    rawX:     rawX,
                    strike:   pt.options?.strike || pt.options?.custom?.strike || null,
                    series:   s.name,
                });
            });
        });
        const seriesInfo = chart.series.map(s => ({name: s.name, type: s.type, pts: (s.data||[]).length}));
        return { points: out, xAxis: xCats, seriesInfo };
    }""")

    if not hc_data or not hc_data.get("points"):
        return []

    log.info(f"Vol curve series: {hc_data.get('seriesInfo')}")
    x_cats = hc_data.get("xAxis", [])
    by_strike: dict = {}

    for pt in hc_data["points"]:
        y = pt.get("y")
        if not y or not (5 <= y <= 200):   # IV must be plausible (5–200%)
            continue

        strike = pt.get("strike")
        if not strike:
            cat = pt.get("category") or ""
            m = re.search(r"(\d{3,})", str(cat))
            if m:
                strike = float(m.group(1))
        if not strike and pt.get("rawX"):
            strike = float(pt["rawX"])

        if strike and float(strike) > 0:
            s = float(strike)
            # Keep highest IV per strike (outermost point of the smile)
            if s not in by_strike or y > by_strike[s]:
                by_strike[s] = float(y)

    pts = sorted([{"strike": s, "iv": v} for s, v in by_strike.items()],
                 key=lambda x: x["strike"])
    preview = [f'{p["strike"]:.0f}={p["iv"]:.2f}' for p in pts[:6]]
    log.info(f"Vol curve: {len(pts)} points — {preview}")

    return pts


def analyse_vol_curve(vol_points: list, ref_price: float) -> dict | None:
    """
    Compute implied-vol surface skew from per-strike IV points.

    LEFT heavy  = OTM puts steeper than OTM calls → bearish vol skew
    RIGHT heavy = OTM calls steeper than OTM puts → bullish vol skew
    Neutral     = roughly symmetric

    slope = (OTM_iv - ATM_iv) / (strike_distance)  normalised by ATM_iv.
    A ratio of left_slope / right_slope >= 1.25 = directional skew.
    """
    if len(vol_points) < 3:
        return None

    pts = sorted(vol_points, key=lambda x: x["strike"])

    atm       = min(pts, key=lambda x: abs(x["strike"] - ref_price))
    atm_iv    = atm["iv"]
    atm_strike = atm["strike"]

    left_pts  = [p for p in pts if p["strike"] < atm_strike]
    right_pts = [p for p in pts if p["strike"] > atm_strike]

    if not left_pts or not right_pts:
        return None

    # Raw slopes (IV rise per point of distance from ATM)
    left_raw  = (left_pts[0]["iv"]   - atm_iv) / max(1, atm_strike - left_pts[0]["strike"])
    right_raw = (right_pts[-1]["iv"] - atm_iv) / max(1, right_pts[-1]["strike"] - atm_strike)

    # Normalise by ATM IV so comparison is dimensionless
    left_norm  = left_raw  / atm_iv if atm_iv else 0.0
    right_norm = right_raw / atm_iv if atm_iv else 0.0

    if right_norm > 0:
        ratio = left_norm / right_norm
    elif left_norm > 0:
        ratio = 999.0
    else:
        ratio = 1.0

    THRESHOLD = 1.25

    if ratio >= THRESHOLD:
        verdict = "LEFT heavy — bearish vol skew"
    elif ratio <= (1.0 / THRESHOLD):
        verdict = "RIGHT heavy — bullish vol skew"
    else:
        verdict = "Neutral"

    log.info(f"Vol curve skew → left_norm={left_norm:.4f}  right_norm={right_norm:.4f}  "
             f"ratio={ratio:.3f}  verdict={verdict}")

    return {
        "atm_strike":   atm_strike,
        "atm_iv":       round(atm_iv, 4),
        "left_slope":   round(left_norm, 6),
        "right_slope":  round(right_norm, 6),
        "slope_ratio":  round(min(ratio, 999), 3),
        "verdict":      verdict,
        "point_count":  len(pts),
    }


# ─── Tag and analyse OI rows ──────────────────────────────────────────────────

def tag_rows(rows: list) -> list:
    if not rows: return rows
    vol_by_strike: dict = {}
    for r in rows:
        vol_by_strike[r["strike"]] = vol_by_strike.get(r["strike"], 0) + r["volume"]
    top2 = set(sorted(vol_by_strike, key=vol_by_strike.get, reverse=True)[:2])
    for r in rows:
        r["is_magnet"]  = r["strike"] in top2
        d = r.get("delta")
        r["gamma_risk"] = (abs(d) < 0.05) if d is not None else False
    return rows


def analyse(rows: list) -> dict:
    cv = sum(r["volume"] for r in rows if r["type"]=="call")
    pv = sum(r["volume"] for r in rows if r["type"]=="put")
    t  = cv + pv or 1
    cp, pp = round(cv/t*100,1), round(pv/t*100,1)
    skew = ("PUT heavy — bearish" if pp>60 else
            "CALL heavy — bullish" if cp>60 else "Neutral")
    return {
        "call_vol": cv, "put_vol": pv, "call_pct": cp, "put_pct": pp,
        "skew_verdict": skew,
        "magnets": sorted({r["strike"] for r in rows if r.get("is_magnet")}),
        "gamma_levels": [{"strike":r["strike"],"type":r["type"],"delta":r["delta"]}
                         for r in rows if r.get("gamma_risk")],
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log.info("═"*52)
    log.info(f"Phase 2  |  {utc7_now().strftime('%Y-%m-%d %H:%M UTC+7')}")
    log.info("═"*52)
    SCREENSHOT_DIR.mkdir(exist_ok=True)

    if not SESSION_FILE.exists():
        log.error("session_data.json not found — run Phase 1 first")
        sys.exit(1)

    session    = json.loads(SESSION_FILE.read_text())
    open_price = session["open_price"]
    log.info(f"Open price: {open_price}")

    with sync_playwright() as pw:
        ctx = make_context(pw)
        page = ctx.new_page()
        try:
            intraday_rows, oi_rows, vol_pts, series_name = navigate_to_intraday(page, open_price)
        except RuntimeError as e:
            log.error(str(e))
            intraday_rows = _manual_entry()
            oi_rows = []
            vol_pts = []
            series_name = None
        finally:
            ctx.close()

    intraday_rows = tag_rows(intraday_rows)
    oi_rows       = tag_rows(oi_rows)
    analysis      = analyse(intraday_rows)   # oi_analysis is based on intraday volume
    vol_pts       = [p for p in vol_pts if p.get("iv") is not None]
    vol_skew      = analyse_vol_curve(vol_pts, open_price) if vol_pts else None

    # Preserve eod_data written by Phase 1 — Phase 2 does not overwrite it
    session.update({
        "exp_series_name":   series_name or session.get("date", ""),
        "vol_curve_points":  [{"strike": p["strike"], "iv": p["iv"]} for p in vol_pts],
        "oi_data":           intraday_rows,      # Intraday Volume
        "oi_interest_data":  oi_rows,            # Open Interest OI
        "oi_analysis":       analysis,
        "vol_skew_analysis": vol_skew,
        "phase2_complete":   True,
        "phase2_at":         utc7_now().isoformat(),
    })
    SESSION_FILE.write_text(json.dumps(session, indent=2))

    # Pull vol_settle / futures_price from intraday rows (vol curve sweep runs on intraday tab)
    vol_settle    = next((r["vol_settle"]    for r in intraday_rows if r.get("vol_settle")),    None)
    futures_price = next((r["futures_price"] for r in intraday_rows if r.get("futures_price")), None)

    print("\n" + "═"*52)
    print("  PHASE 2 LOCKED")
    print("═"*52)
    print(f"  {len(intraday_rows)} intraday rows  |  {len(oi_rows)} OI interest rows")
    if vol_settle is not None:
        print(f"  Vol Settle: {vol_settle}%")
    if futures_price is not None:
        print(f"  Future    : {futures_price}")
    print(f"  OI Skew : {analysis['skew_verdict']}")
    print(f"  Calls   : {analysis['call_pct']}%   Puts: {analysis['put_pct']}%")
    if vol_skew:
        print(f"  Vol Skew: {vol_skew['verdict']}  "
              f"(L={vol_skew['left_slope']:.4f}  R={vol_skew['right_slope']:.4f}  "
              f"ratio={vol_skew['slope_ratio']:.3f})")
    else:
        print("  Vol Skew: not available (no per-strike IV extracted)")
    print(f"  Magnets : {', '.join(str(m) for m in analysis['magnets']) or 'None'}")
    if analysis["gamma_levels"]:
        gl = [g["strike"] for g in analysis["gamma_levels"]]
        print(f"  ⚠ Gamma : {gl}")
    print("═"*52 + "\n")


if __name__ == "__main__":
    main()
