"""
Gold navigation test — user provides the quikstrike iframe URL directly.
Flow: navigate to URL → arrow → Metals → Gold (OC|GC)
"""
import logging, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as PWError

BASE_DIR    = Path(__file__).parent
PROFILE_DIR = BASE_DIR / "browser_profile"
TIMEOUT     = 45_000
HEADLESS    = False
SLOW_MO     = 150
STEP_DELAY  = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("gold_nav_test")


def js_wait_visible(frame, selector, max_tries=6, interval_s=0.5):
    for _ in range(max_tries):
        try:
            result = frame.evaluate(f"""
                (() => {{
                    const el = document.querySelector('{selector}');
                    if (!el) return 'missing';
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 && r.height === 0) return 'zero_size';
                    const s = window.getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0')
                        return 'hidden';
                    return 'visible';
                }})()
            """)
            if result == 'visible':
                return True
        except PWError as e:
            if "detached" in str(e).lower() or "closed" in str(e).lower():
                return False
        time.sleep(interval_s)
    return False


def js_click(frame, selector):
    frame.evaluate(f"document.querySelector('{selector}').click()")


def js_click_attr(frame, attr, value):
    frame.evaluate(f"document.querySelector('[{attr}=\"{value}\"]').click()")


def js_innertext(frame, selector):
    try:
        return frame.evaluate(f"document.querySelector('{selector}')?.innerText?.trim()")
    except Exception:
        return None


def dump_visible_links(frame):
    try:
        links = frame.evaluate("""
            (() => {
                const popup = document.querySelector(
                    '#ctl11_ucProductSelectorPopup_pnlProductSelectorPopup'
                ) || document.body;
                return [...popup.querySelectorAll('a')].map(a => ({
                    text: a.innerText.trim().slice(0, 40),
                    pid: a.getAttribute('productid'),
                    fid: a.getAttribute('familyid'),
                    gid: a.getAttribute('groupid'),
                    vis: a.offsetParent !== null
                })).filter(x => x.vis && (x.pid || x.fid || x.gid));
            })()
        """)
        log.info(f"Visible product links: {links}")
    except Exception as e:
        log.warning(f"Dump failed: {e}")


def test_gold_navigation(url):
    if not PROFILE_DIR.exists():
        log.error("browser_profile/ missing — run login_setup.py first")
        sys.exit(1)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        log.info(f"Navigating to: {url[:100]}")
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        time.sleep(5)
        log.info("Page loaded ✓")

        # The page IS the quikstrike tool — use the main frame directly
        frame = page.main_frame

        if "pid=40" in frame.url:
            log.info("Already on Gold (pid=40) ✓")
        else:
            log.info(f"Current URL: {frame.url[:100]}")
            log.info("Starting navigation: arrow → Metals → Gold...")

            # Step 1: click the ⌄ arrow
            log.info("Step 1: clicking product arrow...")
            if not js_wait_visible(frame, "#ctl11_hlProductArrow", max_tries=10):
                log.error("Arrow not visible — aborting")
                ctx.close()
                return
            js_click(frame, "#ctl11_hlProductArrow")
            log.info(f"Arrow clicked ✓  (waiting {STEP_DELAY}s)")
            time.sleep(STEP_DELAY)

            # Step 2: confirm popup
            log.info("Step 2: confirming popup...")
            popup_sel = "#ctl11_ucProductSelectorPopup_pnlProductSelectorPopup"
            if not js_wait_visible(frame, popup_sel, max_tries=6):
                log.error("Popup did not open")
                ctx.close()
                return
            log.info(f"Popup visible ✓  (waiting {STEP_DELAY}s)")
            time.sleep(STEP_DELAY)

            # Step 3: click Metals
            log.info("Step 3: clicking Metals...")
            if not js_wait_visible(frame, 'a[groupid="6"]', max_tries=6):
                log.error("Metals link not visible")
                ctx.close()
                return
            js_click_attr(frame, "groupid", "6")
            log.info(f"Metals clicked ✓  (waiting {STEP_DELAY + 1}s for products to load)")
            time.sleep(STEP_DELAY + 1)

            dump_visible_links(frame)

            # Step 4: click Gold
            log.info("Step 4: clicking Gold (productid=40)...")
            if not js_wait_visible(frame, 'a[productid="40"]', max_tries=6):
                log.error("Gold link not visible — see dump above")
                ctx.close()
                return
            gold_text = js_innertext(frame, 'a[productid="40"]')
            log.info(f"Gold text: '{gold_text}'")
            js_click_attr(frame, "productid", "40")
            log.info(f"Gold clicked ✓  (waiting 6s for chart to load)")
            time.sleep(6)

        log.info(f"Final URL: {page.url[:120]}")
        if "pid=40" in page.url:
            log.info("PASS: Gold (pid=40) confirmed ✓")
        else:
            log.warning("FAIL: pid=40 not in final URL")

        ctx.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        url = input("Paste the quikstrike chart URL: ").strip()
    else:
        url = sys.argv[1]
    test_gold_navigation(url)
