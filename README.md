# XAUUSD OI Trading Framework

Automated XAUUSD options-driven trading assistant. Scrapes CME open interest and implied volatility data, calculates standard deviation zones, generates Pine Script indicators, pushes them to TradingView Desktop, fires Discord alerts on zone entry, and stores session history in Supabase — all driven by a FastAPI server with a live HTML dashboard.

---

## How It Works

| Phase | Time (UTC+7) | What happens |
|---|---|---|
| **Phase 1** | 01:30 Mon–Fri | Scrapes investing.com open price + CME Vol2Vol EOD IV → calculates ±1/2/3SD zones → injects Pine Script into TradingView → sends Discord alert |
| **Phase 2** | 08:30 Mon–Fri | Scrapes CME intraday OI bars → calculates call/put skew, magnets, gamma levels → updates Pine Script → sends Discord alert |
| **Price polling** | Every 15s | Reads live price from TradingView Desktop via CDP → computes signal → broadcasts to dashboard via WebSocket → fires zone/recovery alerts |

---

## System Requirements

### Operating System

| OS | Support |
|---|---|
| macOS | Fully supported |
| Windows | Fully supported |
| Linux | Supported with workaround — see note below |

> **Linux note:** TradingView Desktop does not have a Linux release. Substitute it with Chromium or Chrome launched with CDP enabled:
> ```bash
> chromium-browser --remote-debugging-port=9222 https://www.tradingview.com/chart/
> ```
> Open a XAUUSD chart, verify with `curl http://localhost:9222/json`, and the rest of the project works identically.

---

### Runtime

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or higher | Required |
| pip | Any current version | For installing dependencies |
| Chromium or Chrome | Any current version | Used by Playwright for scraping CME and investing.com; installed automatically via `python -m playwright install chromium` |

---

### TradingView

- **TradingView Desktop** (macOS / Windows) — or any Chromium/Chrome instance on Linux — must be running with the Chrome DevTools Protocol enabled on port 9222 (configurable via `TV_CDP_PORT` in `.env`)
- A **XAUUSD chart** must be open and visible in TradingView when the server is running
- The **Watchlist panel must be open** and XAUUSD must be listed in it — the live price is read from the watchlist's price cell via CDP. If the watchlist is closed or XAUUSD is not in it, price polling will fall back to the chart's OHLCV legend (less reliable) or return `None`

> **Watchlist setup:** Open the watchlist panel (default left sidebar in TradingView Desktop). Add XAUUSD if it is not already listed. Keep the panel open while the server is running. Do not collapse or hide it.

---

### Network

- Outbound HTTPS access to:
  - `investing.com` — XAUUSD open price (Phase 1)
  - `cmegroup.com` / `vol2vol.cmegroup.com` — IV and OI data (Phase 1 & 2)
  - `supabase.co` — session/signal persistence (optional)
  - `discord.com` — webhook alerts (optional)
- Local port **8000** — FastAPI server and dashboard
- Local port **9222** (or `TV_CDP_PORT`) — TradingView CDP

---

### Optional Services

| Service | Purpose | Required |
|---|---|---|
| Supabase (free tier) | Stores session history and signals; powers `/api/history` | No |
| Discord webhook | Zone entry, recovery, and phase-completion alerts | No |

Both services are optional. The server starts and runs without them; alerts and history are silently skipped if credentials are absent.

---

## Installation

### 1. Clone and install

```bash
git clone https://github.com/bodytaylor/xauusd-automation.git
cd xauusd-automation
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```ini
# Discord webhook (Server Settings → Integrations → Webhooks)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN

# Supabase (Project Settings → API)
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# TradingView Desktop CDP port (default 9222)
TV_CDP_PORT=9222
```

Discord and Supabase are both optional — the server runs without them, alerts and history are just skipped.

### 3. Set up Supabase schema (if using Supabase)

Open the Supabase SQL editor for your project and run the contents of:

```
migrations/001_initial_schema.sql
```

This creates the `sessions` and `signals` tables.

### 4. Set up CME session (first run only)

The CME Vol2Vol scraper needs a saved browser session to bypass login:

```bash
python login_setup.py
```

A Chromium browser window opens. Log in to CME Group manually, then press Enter in the terminal. The session cookies are saved to `cme_session.json`.

### 5. Launch TradingView Desktop with CDP enabled

Use the included helper script (macOS):

```bash
bash launch_tradingview.sh
```

This kills any existing TradingView process and relaunches it with both required flags:

```bash
/Applications/TradingView.app/Contents/MacOS/TradingView \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* &
```

> **Important:** Both flags are required. `--remote-debugging-port=9222` opens the CDP port; `--remote-allow-origins=*` allows WebSocket connections to it. Without the second flag, `push_pine()` will silently fail with a 403 error every time.

Open a XAUUSD chart in TradingView and keep the app running. The server connects to it via Chrome DevTools Protocol on port 9222.

To verify CDP is working:

```bash
curl http://localhost:9222/json
```

You should see a JSON list containing a tab with `"url": "https://www.tradingview.com/..."`.

To verify the WebSocket connection specifically (tests that `push_pine()` can connect):

```bash
python3 -c "from tradingview_client import TradingViewClient; tv = TradingViewClient(); print('CDP:', tv.is_connected()); print('Quote:', tv.get_quote())"
```

If `get_quote()` returns `None`, TradingView was launched without `--remote-allow-origins=*`. Quit and relaunch with the full command above.

---

## Running the Server

```bash
python server.py
```

The server starts on `http://localhost:8000`. On startup it logs:

```
01:23:45  INFO     Server started. Dashboard → http://localhost:8000
01:23:45  INFO     TradingView CDP: CONNECTED
01:23:45  INFO       Scheduled: Phase 1 — Open price + IV  next=2026-05-05 01:30:00+07:00
01:23:45  INFO       Scheduled: Phase 2 — OI sentiment     next=2026-05-05 08:30:00+07:00
```

Open `http://localhost:8000` to see the live dashboard.

---

## Dashboard

Three-panel dark dashboard served at `http://localhost:8000`:

- **Left — Zone Map:** Visual ±3SD ladder with live price dot, OI magnet lines
- **Center — Signal:** Current direction (LONG / SHORT / WAIT), confidence, entry / TP1 / TP2 / SL, recovery banner
- **Right — OI Analysis:** Call/put skew verdict, percentage gauges, magnet prices, gamma risk levels
- **Bottom bar:** Live price, TradingView connection status, last update time, Pine Script export link

The dashboard updates every 15 seconds via WebSocket and reconnects automatically if the connection drops.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/session` | Current session data (open price, SD zones, OI analysis) |
| `GET` | `/api/signal` | Current signal computed from live TradingView price |
| `GET` | `/api/history?n=30` | Last N sessions from Supabase |
| `POST` | `/api/refresh/phase1` | Trigger Phase 1 manually (runs in background) |
| `POST` | `/api/refresh/phase2` | Trigger Phase 2 manually (runs in background) |
| `WS` | `/ws/price` | WebSocket — broadcasts price/signal/zone every 15s |

---

## Running Phases Manually

Without the scheduler, you can run each phase independently:

```bash
# Phase 1 — open price + IV + SD zones
python collector.py

# Phase 2 — OI sentiment
python oi_collector.py
```

Or trigger via API while the server is running:

```bash
curl -X POST http://localhost:8000/api/refresh/phase1
curl -X POST http://localhost:8000/api/refresh/phase2
```

---

## Signal Logic

| Zone | Distance from open | Signal |
|---|---|---|
| INSIDE_1SD | < 1SD | WAIT |
| ±2SD | 1SD – 2SD | Fade (SHORT if above, LONG if below) |
| ±3SD | 2SD – 3SD | Fade with confidence scoring |
| BEYOND_3SD + 25pt | > 3SD + 25pt | Recovery — follow trend |

**Box grid:** 50-point entries. SHORT: entry = ceil(price/50)×50, TP1=−25pt, TP2=−50pt, SL=+25pt. LONG: entry = floor(price/50)×50, TP1=+25pt, TP2=+50pt, SL=−25pt.

**Confidence HIGH** requires: price at 3SD + OI skew confirms direction + magnet target exists closer to open than current price.

---

## Discord Alerts

Six alert types sent to your webhook:

| Alert | Trigger | Color |
|---|---|---|
| Phase 1 Complete | Phase 1 finishes | Green |
| Phase 2 Complete | Phase 2 finishes | Blue |
| Zone Alert | Price enters ±2SD or ±3SD | Amber / Red |
| Recovery Signal | Price breaks 3SD+25pt | Red |
| Gamma Risk | Price within 10pt of gamma level | Orange |
| Phase Failed | Collector subprocess exits non-zero | Red |

Each alert fires at most once per trading day (reset at midnight UTC+7).

---

## Pine Script

After each phase, a Pine Script v5 indicator is generated and injected into TradingView Desktop:

- **Phase 1:** SD zone backgrounds, ±1/2/3SD `hline()` levels, 50-point box grid
- **Phase 2:** Adds OI magnet `hline()` levels in blue

The `.pine` files are also saved to `exports/session_YYYY-MM-DD.pine` and linked from the dashboard bottom bar.

### Manual push

To push a Pine Script file to TradingView without running the full server:

```bash
# Push today's session file (exports/session_YYYY-MM-DD.pine)
python push_pine.py

# Push the most recently generated file regardless of date
python push_pine.py --latest

# Push a specific file
python push_pine.py --file exports/session_2026-05-03.pine
```

`push_pine.py` automates the full sequence over CDP:

1. Opens the Pine Script Editor panel (`data-name="pine-dialog-button"`)
2. Renames the script to **"OI data"** via the title-button menu
3. Injects the Pine Script code into Monaco — using `model.setValue()` reached through TradingView's webpack module registry (`window.webpackChunktradingview`) to bypass Monaco's auto-indent pipeline entirely
4. Clicks **Add to Chart** (`data-qa-id="add-script-to-chart"`)

> **Note:** TradingView must be running with CDP enabled (`bash launch_tradingview.sh`) and a XAUUSD chart must be open before running `push_pine.py`.

---

## Project Structure

```
xauusd-automation/
├── collector.py          # Phase 1 — investing.com + CME EOD IV scraper
├── oi_collector.py       # Phase 2 — CME intraday OI scraper
├── server.py             # FastAPI server (scheduler + WebSocket + REST)
├── signal_engine.py      # Box trading signal logic
├── pine_exporter.py      # Pine Script v5 generator
├── tradingview_client.py # TradingView Desktop CDP client (push_pine, rename, open editor)
├── push_pine.py          # CLI: push today's / latest .pine file to TradingView
├── launch_tradingview.sh # macOS helper: relaunch TradingView with CDP on port 9222
├── alerts.py             # Discord webhook dispatcher
├── db_sync.py            # Supabase persistence (sessions + signals)
├── config.py             # Settings from .env
├── scheduler.py          # Legacy standalone scheduler (pre-server)
├── login_setup.py        # CME session cookie saver
├── dashboard/            # HTML/CSS/JS live dashboard
│   ├── index.html
│   ├── style.css
│   └── app.js
├── exports/              # Generated .pine files (gitignored)
├── logs/                 # Log files (gitignored)
├── migrations/
│   └── 001_initial_schema.sql   # Supabase table definitions
├── tests/                # pytest test suite (39 tests)
├── .env.example          # Environment variable template
├── requirements.txt
└── pytest.ini
```

---

## Running Tests

```bash
pytest tests/ -v
```

Expected: 39 tests, all passing.

---

## Troubleshooting

**Dashboard shows wrong price / price does not update**
- The live price is read from the **TradingView watchlist panel** price cell
- Ensure the watchlist panel is **open** (not collapsed) in TradingView Desktop
- Ensure **XAUUSD is listed** in the watchlist — add it if missing
- If another symbol sits above XAUUSD in the watchlist and its price is also > 1000, the scraper will return that symbol's price instead; move XAUUSD to the top of the watchlist or remove other symbols with prices above 1000
- Verify with: `python3 -c "from tradingview_client import TradingViewClient; print(TradingViewClient().get_quote())"`

**TradingView CDP not connecting / Pine Script not appearing after Phase 1**
- TradingView Desktop must be launched with **both** flags: `--remote-debugging-port=9222 --remote-allow-origins=*`
- The easiest way: `bash launch_tradingview.sh` — this handles stopping and relaunching with the correct flags
- Without `--remote-allow-origins=*`, the WebSocket connection is rejected with 403 Forbidden — `push_pine()` returns False silently and no indicator appears
- Run `curl http://localhost:9222/json` — you should see a JSON list with a tradingview.com URL
- Test the full connection: `python3 -c "from tradingview_client import TradingViewClient; tv = TradingViewClient(); print(tv.get_quote())"`
  - If this returns `None`, relaunch TradingView with the correct flags
- Only TradingView Desktop supports CDP; the browser version does not

**Pine Script injected but indentation is wrong / "line continuation" errors in TradingView**
- This was caused by `execCommand('insertText')` routing through Monaco's typing pipeline, which applies `autoIndent: "full"` after every newline — snowballing the indentation with each line inside `if` blocks
- Fixed in `tradingview_client.py`: the injector now calls `model.setValue()` via Monaco's internal API, reached through TradingView's webpack module registry (`window.webpackChunktradingview`). `setValue()` writes directly to the model with no auto-indent post-processing
- If TradingView updates and `push_pine()` returns `monaco_module_not_found`, clear `window.__tvMonacoModId` in the browser console — the module ID will be rediscovered automatically on the next run

**Pine Editor opens symbol-search panel instead of compiling**
- Fixed: the old `_JS_COMPILE` selector `[data-name="add-symbol-button"]` was hitting the watchlist "Add symbol" button
- Now uses `[data-qa-id="add-script-to-chart"]` which is the Pine Editor toolbar's "Add to Chart" button

**CME scraper fails / bot detection**
- Run `python login_setup.py` again to refresh the saved session
- Check `screenshots/` to see what the browser was seeing when it failed
- The scraper falls back to manual CLI input if auto-extraction fails

**investing.com layout changes**
- Edit the `strategies` list in `fetch_open_price()` inside `collector.py`
- Use browser DevTools (F12) to find the updated element selector

**Phase 2 OI bars not found**
- CME Vol2Vol is JavaScript-heavy; the scraper may need selector updates
- Check `screenshots/` for the last known browser state

**Discord alerts not sending**
- Verify `DISCORD_WEBHOOK_URL` in `.env` is the full webhook URL
- Test directly: `curl -X POST $DISCORD_WEBHOOK_URL -H "Content-Type: application/json" -d '{"content":"test"}'`

**Supabase upsert failing**
- Ensure `migrations/001_initial_schema.sql` has been run in the Supabase SQL editor
- Verify `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (use the **service role** key, not the anon key)
