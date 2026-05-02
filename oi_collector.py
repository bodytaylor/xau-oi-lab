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

BASE_DIR       = Path(__file__).parent
SESSION_FILE   = BASE_DIR / "session_data.json"
CME_SESSION    = BASE_DIR / "cme_session.json"
SCREENSHOT_DIR = BASE_DIR / "screenshots"

CME_GOLD_URL = (
    "https://quikstrike.net/User/QuikStrikeView.aspx"
    "?pid=40&pf=6&viewitemid=IntegratedV2VExpectedRange"
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


def utc7_now():
    return datetime.now(timezone(timedelta(hours=7)))


def ss(page, name):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    p = SCREENSHOT_DIR / f"{utc7_now().strftime('%H%M%S')}_{name}.png"
    page.screenshot(path=str(p), full_page=False)
    log.info(f"Screenshot → {p.name}")


# ─── Browser ─────────────────────────────────────────────────────────────────

def make_context(pw):
    browser = pw.chromium.launch(
        headless=HEADLESS, slow_mo=SLOW_MO,
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
    if CME_SESSION.exists():
        opts["storage_state"] = json.loads(CME_SESSION.read_text())
        log.info("CME session loaded ✓")
    ctx = browser.new_context(**opts)
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    return browser, ctx


# ─── CME Navigation ───────────────────────────────────────────────────────────

def navigate_to_intraday(page, open_price: float) -> list:
    """Full navigation sequence to get Intraday OI data for Gold."""
    log.info("→ CME Vol2Vol (Gold, INTRADAY)")
    page.goto(CME_GOLD_URL, timeout=TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    if "login" in page.url.lower() or "sign" in page.url.lower():
        ss(page, "cme_login_wall")
        raise RuntimeError("CME session expired — run login_setup.py")

    ss(page, "cme_p2_loaded")

    # Find the iframe that holds the Vol2Vol tool
    frame = _get_frame(page)
    log.info(f"Working in: {'iframe' if frame != page else 'main page'}")

    # Ensure Gold is selected (pid=40 in URL should handle this)
    _ensure_gold(frame, page)

    # Select nearest expiration (front month = default)
    _select_expiration(frame, page)
    page.wait_for_timeout(2000)

    # Switch to INTRADAY mode
    _set_intraday(frame, page)
    page.wait_for_timeout(4000)

    ss(page, "cme_p2_intraday_set")

    # Extract bar chart data
    oi_rows = _extract_bar_data(frame, page, open_price)

    ss(page, "cme_p2_extracted")
    return oi_rows


def _get_frame(page):
    """Return the frame containing the Vol2Vol tool, or the page itself."""
    page.wait_for_timeout(1500)
    for frame in page.frames:
        url = frame.url.lower()
        if any(k in url for k in ["quikstrike", "vol2vol", "viewitemid"]):
            log.info(f"iframe: {frame.url}")
            return frame
    # Try frame locators
    for sel in ["iframe[src*='quikstrike']", "iframe[src*='QuikStrike']", "iframe"]:
        try:
            fl = page.frame_locator(sel).first
            if fl.locator(".navbar-selector, #ctl11_hlProductText").count() > 0:
                log.info(f"frame_locator matched: {sel}")
                return fl
        except Exception:
            pass
    return page


def _ensure_gold(frame, page):
    """Verify Gold is selected; navigate via direct link if not."""
    try:
        ev = _eval(frame, """() => {
            const s = document.querySelector('.navbar-selector .text span');
            return s ? s.innerText.trim() : '';
        }""")
        if ev and "gold" not in ev.lower():
            log.info(f"Wrong product ({ev}) — switching to Gold")
            gold_href = _eval(frame, """() => {
                const a = document.querySelector('a[productid="40"]');
                return a ? a.href : null;
            }""")
            if gold_href:
                page.goto(gold_href, timeout=TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
        else:
            log.info(f"Product: {ev or 'Gold (assumed)'} ✓")
    except Exception as e:
        log.debug(f"_ensure_gold: {e}")


def _select_expiration(frame, page):
    """
    Click the expiration link (ctl00_ucSelector_hlExpiration) and
    pick the nearest-dated expiration from the popup.
    """
    try:
        # Read what's currently shown
        current = _eval(frame, """() => {
            const el = document.querySelector('#ctl00_ucSelector_hlExpiration strong,
                                               a[id*="hlExpiration"] strong');
            return el ? el.nextSibling?.textContent?.trim() || el.innerText : null;
        }""")
        log.info(f"Current expiration: {current}")

        # Click the expiration anchor to open the popup
        clicked = _click(frame, page,
                         "#ctl00_ucSelector_hlExpiration, a[id*='ucSelector_hlExpiration']")
        if not clicked:
            log.debug("Expiration link not clicked — using current default")
            return

        page.wait_for_timeout(1200)
        ss(page, "exp_popup")

        # The popup lists expirations — pick the first one (front month)
        first = _eval(frame, """() => {
            const popup = document.querySelector('#ctl00_ucSelector_pnlExpirations');
            if (!popup) return null;
            const links = popup.querySelectorAll('a');
            if (links.length > 0) { links[0].click(); return links[0].innerText.trim(); }
            return null;
        }""")

        if first:
            log.info(f"Selected expiration: {first} ✓")
            page.wait_for_timeout(2000)
        else:
            # Press Escape to close popup if nothing selected
            page.keyboard.press("Escape")

    except Exception as e:
        log.debug(f"_select_expiration: {e}")


def _set_intraday(frame, page):
    """
    Switch the volume display from EOD → Intraday.
    The Vol2Vol tool uses various patterns — try all of them.
    """
    log.info("Switching to INTRADAY volume...")

    # Strategy 1: Find radio/button labeled "Intraday"
    result = _eval(frame, """() => {
        // Radio buttons
        const inputs = document.querySelectorAll('input[type="radio"],input[type="checkbox"]');
        for (const inp of inputs) {
            const lbl = document.querySelector(`label[for="${inp.id}"]`);
            if ((lbl?.innerText || inp.value || '').toLowerCase().includes('intraday')) {
                inp.click();
                return 'radio:' + (lbl?.innerText || inp.value);
            }
        }

        // Select dropdowns
        for (const sel of document.querySelectorAll('select')) {
            for (const opt of sel.options) {
                if (opt.text.toLowerCase().includes('intraday')) {
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    return 'select:' + opt.text;
                }
            }
        }

        // Any clickable element with text "intraday"
        const all = [...document.querySelectorAll('a,button,li,span,div,td')];
        for (const el of all) {
            if (el.children.length === 0 &&
                el.innerText.trim().toLowerCase() === 'intraday') {
                el.click();
                return 'click:' + el.tagName + ' ' + el.innerText;
            }
        }
        return null;
    }""")

    if result:
        log.info(f"Intraday set via: {result}")
        page.wait_for_timeout(3000)
        return

    # Strategy 2: Look for a toggle/tab at top of chart section
    log.debug("Trying Intraday tab/button click via Playwright locator...")
    for sel in [
        'text="Intraday"', 'text="INTRADAY"',
        '[value="Intraday"]', '[data-mode="intraday"]',
    ]:
        try:
            loc = (frame if hasattr(frame, 'locator') else page).locator(sel).first
            if loc.count() > 0:
                loc.click()
                log.info(f"Intraday clicked via: {sel}")
                page.wait_for_timeout(3000)
                return
        except Exception:
            pass

    log.warning("Could not locate Intraday toggle — proceeding (may be EOD)")


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

    # ── A: Highcharts series data ──────────────────────────────────────────
    hc_data = _eval(frame, """() => {
        if (!window.Highcharts?.charts) return null;
        const chart = window.Highcharts.charts.find(c => c && c.series?.length > 0);
        if (!chart) return null;

        const out = { series: [], xAxis: [] };

        // Get x-axis categories (strike/delta labels)
        const xCats = chart.xAxis?.[0]?.categories || [];
        out.xAxis = xCats;

        // Get series data
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
            out.series.push({
                name: s.name,
                color: s.color,
                type: s.type,
                data: pts,
            });
        });
        return out;
    }""")

    if hc_data:
        log.info(f"Highcharts data: {len(hc_data.get('series',[]))} series, "
                 f"x-cats: {hc_data.get('xAxis',[])[:5]}")
        rows = _parse_hc_data(hc_data, open_price)
        if rows:
            log.info(f"Parsed {len(rows)} OI rows from Highcharts")
            return rows

    # ── B: SVG element inspection ──────────────────────────────────────────
    log.info("Trying SVG bar inspection...")
    svg_rows = _eval(frame, """() => {
        const out = [];
        // Each bar group typically has a series class
        const barGroups = document.querySelectorAll(
            '.highcharts-series rect, svg rect[fill], g.bar rect'
        );
        barGroups.forEach((rect, i) => {
            const h = parseFloat(rect.getAttribute('height') || '0');
            if (h < 2) return;  // skip invisible bars
            const fill = rect.getAttribute('fill') || rect.style.fill || '';
            const type = fill.includes('orange') || fill.includes('#f5a6') || fill.includes('e6') ?
                         'put' : 'call';
            // Try to get the associated x-label
            const parent = rect.closest('g');
            const title = parent?.querySelector('title')?.textContent;
            out.push({
                index: i,
                height: parseFloat(h.toFixed(1)),
                fill,
                type,
                title: title || null,
            });
        });
        return out;
    }""")

    if svg_rows:
        log.info(f"SVG bars found: {len(svg_rows)}")
        # SVG heights represent volume — map to relative volumes
        rows = _parse_svg_bars(svg_rows, open_price)
        if rows:
            return rows

    # ── C: Tooltip sweep ──────────────────────────────────────────────────
    log.info("Trying tooltip hover sweep...")
    tooltip_rows = _sweep_tooltips(frame, page, open_price)
    if tooltip_rows:
        return tooltip_rows

    # ── D: Manual entry ───────────────────────────────────────────────────
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
        browser, ctx = make_context(pw)
        page = ctx.new_page()
        try:
            oi_rows = navigate_to_intraday(page, open_price)
        except RuntimeError as e:
            log.error(str(e))
            oi_rows = _manual_entry()
        finally:
            browser.close()

    oi_rows  = tag_rows(oi_rows)
    analysis = analyse(oi_rows)

    session.update({
        "oi_data": oi_rows,
        "oi_analysis": analysis,
        "phase2_complete": True,
        "phase2_at": utc7_now().isoformat(),
    })
    SESSION_FILE.write_text(json.dumps(session, indent=2))

    print("\n" + "═"*52)
    print("  PHASE 2 LOCKED")
    print("═"*52)
    print(f"  {len(oi_rows)} OI rows collected")
    print(f"  Skew    : {analysis['skew_verdict']}")
    print(f"  Calls   : {analysis['call_pct']}%   Puts: {analysis['put_pct']}%")
    print(f"  Magnets : {', '.join(str(m) for m in analysis['magnets']) or 'None'}")
    if analysis["gamma_levels"]:
        gl = [g["strike"] for g in analysis["gamma_levels"]]
        print(f"  ⚠ Gamma : {gl}")
    print("═"*52 + "\n")


if __name__ == "__main__":
    main()
