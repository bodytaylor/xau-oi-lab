# XAUUSD OI Trading Framework — Full System Design

**Date:** 2026-05-02
**Status:** Approved for implementation
**Revision:** 2 — TradingView CDP replaces MetaApi; Pine Script pushed directly to chart

---

## 1. Overview

Extend the existing two-phase data collection pipeline (collector.py + oi_collector.py) with a
FastAPI backend, live price feed from TradingView Desktop via CDP, box-trading signal engine,
Discord alerts, Supabase persistence, HTML dashboard, and Pine Script auto-injection into the
TradingView chart.

The full automated flow, morning to close:

```
Phase 1 (01:30 UTC+7)
  collector.py
    → investing.com: XAUUSD open price
    → CME Vol2Vol (EOD): Implied Volatility %
    → calc SD zones ±1/2/3 SD from open
    → session_data.json (phase1_complete)
  pine_exporter.py
    → generates Pine Script (SD zones + box grid + labels)
  tradingview_client.py
    → CDP: inject Pine Script into TV editor → compile → zones live on chart
    → CDP: create 6 TradingView price alerts (±1SD, ±2SD, ±3SD)
  db_sync.py    → Supabase sessions upsert
  alerts.py     → Discord: Phase 1 summary embed

Phase 2 (08:30 UTC+7)
  oi_collector.py
    → CME Vol2Vol (Intraday): call/put bars, skew, magnets, gamma levels
    → session_data.json (phase2_complete)
  pine_exporter.py
    → rewrites Pine Script: adds OI magnet lines + gamma level markers
  tradingview_client.py
    → CDP: re-inject updated Pine Script → recompile
  db_sync.py    → Supabase sessions upsert
  alerts.py     → Discord: Phase 2 OI analysis embed

Continuous (while TradingView Desktop is open)
  tradingview_client.py polls CDP every 15s
    → quote_get: current XAUUSD bid price
    → broadcast to all dashboard WebSocket clients
    → signal_engine.py: recompute box trade plan
    → check SD proximity thresholds → Discord alert if zone entered
```

---

## 2. Existing System (Unchanged)

| File | Purpose |
|---|---|
| `collector.py` | Phase 1 — scrapes investing.com open price + CME EOD IV; calculates ±1/2/3 SD zones; writes `session_data.json` |
| `oi_collector.py` | Phase 2 — scrapes CME intraday OI; extracts call/put bars, skew, magnets, gamma levels; merges into `session_data.json` |
| `login_setup.py` | One-time CME persistent browser session saver |
| `session_data.json` | Central session state, read by all new components |

Existing collector files are **not modified**. The new `server.py` embeds APScheduler and calls
these collectors via subprocess. `scheduler.py` remains usable as a standalone fallback but is no
longer the primary orchestrator.

---

## 3. New Files and Responsibilities

```
xauusd_automation/
├── server.py               FastAPI app — WebSocket, REST, APScheduler
├── signal_engine.py        Box trading signal calculator
├── pine_exporter.py        Pine Script generator
├── tradingview_client.py   TradingView Desktop CDP client (price + Pine push)
├── alerts.py               Discord webhook dispatcher
├── db_sync.py              Supabase read/write
├── config.py               Loads .env secrets into typed Settings
├── dashboard/
│   ├── index.html          Trading dashboard (single-page app)
│   ├── style.css           Dark theme styles
│   └── app.js              WebSocket client + zone map rendering
├── exports/                Generated .pine files (one per session date)
├── logs/                   phase1.log, phase2.log, server.log
└── .env                    Secrets — never committed to version control
```

---

## 4. Prerequisites

### TradingView Desktop with CDP enabled

The `tradingview_client.py` connects to TradingView Desktop via Chrome DevTools Protocol (CDP)
on `localhost:9222`. TradingView Desktop must be launched with the debugging flag:

**macOS:**
```bash
open -a "TradingView" --args --remote-debugging-port=9222
```

Or use the `tradingview-mcp-jackson` launch script (which sets this flag automatically):
```bash
# From the tradingview-mcp-jackson repo
node src/launch.js
```

**Note:** Requires TradingView Desktop (not the web app) with a Pro+ account for Pine Script
indicators and price alerts.

### tradingview-mcp-jackson (optional, for interactive Claude Code sessions)

The MCP server can be used alongside this framework for interactive chart work with Claude Code.
Install once:
```bash
git clone https://github.com/LewisWJackson/tradingview-mcp-jackson ~/tradingview-mcp-jackson
cd ~/tradingview-mcp-jackson && npm install
```

Add to `~/.claude/.mcp.json` to use MCP tools in Claude Code sessions:
```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["/Users/YOUR_USERNAME/tradingview-mcp-jackson/src/server.js"]
    }
  }
}
```

---

## 5. FastAPI Server (`server.py`)

### Startup

`python server.py` starts everything in one process:
- Mounts `dashboard/` as static files at `/`
- Registers APScheduler jobs (Phase 1 @ 01:30 UTC+7, Phase 2 @ 08:30 UTC+7)
- Starts `tradingview_client.py` background polling task (every 15s)
- Listens on `localhost:8000`

### REST Endpoints

| Method | Path | Response |
|---|---|---|
| GET | `/api/session` | Today's `session_data.json` content |
| GET | `/api/signal` | Current trade signal from signal_engine |
| GET | `/api/history?n=30` | Last N sessions from Supabase |
| POST | `/api/refresh/phase1` | Manually trigger Phase 1 |
| POST | `/api/refresh/phase2` | Manually trigger Phase 2 |

### WebSocket

`WS /ws/price`

Broadcasts a JSON message on every CDP price poll (~15s interval):

```json
{
  "price": 4703.50,
  "ts": "2026-05-02T08:35:00+07:00",
  "signal": { "zone": "+1SD", "direction": "WAIT", ... },
  "zone": "+1SD"
}
```

### Phase Hooks

After each phase subprocess exits with code 0:

- **Phase 1:** `pine_exporter.run()` → `tradingview_client.push_pine()` → `tradingview_client.create_sd_alerts()` → `db_sync.upsert_session()` → `alerts.phase1_complete()`
- **Phase 2:** `pine_exporter.run()` → `tradingview_client.push_pine()` → `db_sync.upsert_session()` → `alerts.phase2_complete()`

---

## 6. TradingView Client (`tradingview_client.py`)

Connects to TradingView Desktop via CDP on `ws://localhost:{TV_CDP_PORT}` (default 9222).

### Connection Pattern

```python
import websocket, json, requests

def _get_tv_tab():
    """Find the TradingView tab via CDP /json endpoint."""
    tabs = requests.get(f"http://localhost:{TV_CDP_PORT}/json").json()
    for tab in tabs:
        if "tradingview" in tab.get("url", "").lower():
            return tab["webSocketDebuggerUrl"]
    raise RuntimeError("TradingView tab not found — is TV Desktop running on port 9222?")

def _eval_js(ws, js: str):
    """Execute JavaScript in TradingView page via CDP Runtime.evaluate."""
    ws.send(json.dumps({
        "id": 1, "method": "Runtime.evaluate",
        "params": {"expression": js, "returnByValue": True}
    }))
    return json.loads(ws.recv())["result"]["result"].get("value")
```

### Public Interface

| Method | What it does |
|---|---|
| `get_quote() → float` | Returns current XAUUSD bid price from TradingView page |
| `push_pine(code: str)` | Injects Pine Script into TV editor, compiles, applies to chart |
| `create_sd_alerts(zones: dict)` | Creates 6 TradingView price alerts at ±1/2/3 SD levels |
| `is_connected() → bool` | Health check — returns False if TV Desktop is not reachable |

### Fallback Behaviour

If TradingView Desktop is not running (e.g. overnight), `get_quote()` returns `None` and
the WebSocket broadcast is skipped. The price display on the dashboard shows "TradingView
offline". The scheduled Phase 1/2 collectors continue to run regardless.

---

## 7. Signal Engine (`signal_engine.py`)

Inputs: `session_data.json` (SD zones, OI analysis) + current price from TradingView CDP.

### Zone Classification

```
if abs(price - open) < sd1:      zone = "INSIDE_1SD"
elif abs(price - open) < sd2:    zone = "2SD"
elif abs(price - open) < sd3:    zone = "3SD"
else:                             zone = "BEYOND_3SD"

direction = "SHORT" if price > open else "LONG"
```

### Box Grid

50-point boundaries:
```python
box_size = 50
box_top  = math.ceil(price / box_size) * box_size
box_bot  = math.floor(price / box_size) * box_size
```

### Trade Plan (generated when zone == "2SD" or "3SD")

```
entry = box_top (SHORT) or box_bot (LONG)
tp1   = entry ∓ 25   # half-box — always lock some profit here
tp2   = entry ∓ 50   # full box
sl    = entry ± 25   # cut if box fails as midpoint
```

### Confidence Tier

| Condition | Confidence |
|---|---|
| Zone == 3SD AND OI skew confirms direction AND magnet on opposite side | HIGH |
| Zone == 2SD or 3SD without full skew confirmation | MEDIUM |
| Zone == INSIDE_1SD | LOW / WAIT |

### Recovery Signal

Triggered when price is beyond (sd3 + 25 pts) without reversing. Flips direction to "follow
the trend" and fires a Discord recovery alert (once per session).

### Output

```json
{
  "zone": "+2SD",
  "direction": "SHORT",
  "confidence": "MEDIUM",
  "entry": 4750.0,
  "tp1": 4725.0,
  "tp2": 4700.0,
  "sl": 4775.0,
  "recovery": false,
  "signal_at": "2026-05-02T09:15:00+07:00"
}
```

---

## 8. Pine Script Exporter (`pine_exporter.py`)

Reads `session_data.json`. Called after Phase 1 (SD zones only) and again after Phase 2
(adds OI magnet lines + gamma markers).

Writes `exports/session_YYYY-MM-DD.pine`, then calls `tradingview_client.push_pine()`.

### Generated Script Content

```pine
//@version=5
indicator("XAUUSD OI Zones — YYYY-MM-DD", overlay=true, max_lines_count=50)

// ── Session constants (generated by pine_exporter.py) ─────────────────
var float OPEN_P = 4621.78
var float SD1    = 82.04
var float SD2    = 164.07
var float SD3    = 246.11

// ── SD background zones ────────────────────────────────────────────────
// +3SD zone: red
bgcolor(close > OPEN_P + SD2 and close <= OPEN_P + SD3 ? color.new(color.red, 85) : na)
// +2SD zone: orange
bgcolor(close > OPEN_P + SD1 and close <= OPEN_P + SD2 ? color.new(color.orange, 85) : na)
// +1SD zone: grey
bgcolor(close > OPEN_P and close <= OPEN_P + SD1 ? color.new(color.gray, 90) : na)
// Mirror on downside ...

// ── SD level lines ─────────────────────────────────────────────────────
var line l_open  = line.new(bar_index, OPEN_P, bar_index+1, OPEN_P, extend=extend.right, color=color.white, style=line.style_dashed, width=2)
var line l_1up   = line.new(bar_index, OPEN_P+SD1, bar_index+1, OPEN_P+SD1, extend=extend.right, color=color.gray, width=1)
// ... ±2SD, ±3SD lines ...

// ── Box grid (50-pt boundaries within ±3SD) ────────────────────────────
// ... generated box boundaries ...

// ── OI magnet levels (added after Phase 2) ────────────────────────────
var line magnet_1 = line.new(bar_index, 4200.0, bar_index+1, 4200.0, extend=extend.right, color=color.blue, style=line.style_dotted, width=2)
// ... additional magnets ...
```

---

## 9. Discord Alerts (`alerts.py`)

Uses a single Discord webhook URL from `.env`. No bot token required.

### Alert Types

| Trigger | When fired | Deduplicated |
|---|---|---|
| Phase 1 complete | Phase 1 subprocess exits 0 | Once per session date |
| Phase 2 complete | Phase 2 subprocess exits 0 | Once per session date |
| Price enters ±2SD | CDP poll detects zone crossing | Once per band entry per session |
| Price enters ±3SD | CDP poll detects zone crossing | Once per band entry per session |
| Recovery signal | Price beyond 3SD + 25 without reversal | Once per session date |
| Gamma squeeze risk | Price within 10 pts of a delta<5 level | Once per strike per session |
| Phase failure | Subprocess exits non-zero | Every occurrence |

Deduplication is in-memory, reset at midnight UTC+7.

### 3SD Alert Format (example)

Discord embed:
- **Title:** 🔴 XAUUSD at +3SD
- **Color:** Red
- **Fields:** Price, Zone, Signal direction, Entry/TP1/TP2/SL, Confidence, Open price reference

---

## 10. Supabase Database (`db_sync.py`)

### `sessions` Table

```sql
create table sessions (
  id          uuid primary key default gen_random_uuid(),
  date        date unique not null,
  open_price  float,
  iv_pct      float,
  sd_zones    jsonb,
  oi_analysis jsonb,
  phase1_at   timestamptz,
  phase2_at   timestamptz,
  created_at  timestamptz default now()
);
```

### `signals` Table

```sql
create table signals (
  id            uuid primary key default gen_random_uuid(),
  session_date  date references sessions(date),
  fired_at      timestamptz,
  price         float,
  zone          text,
  direction     text,
  entry         float,
  tp1           float,
  tp2           float,
  sl            float,
  confidence    text,
  recovery      boolean,
  outcome       text    -- trader fills manually: WIN / LOSS / BE
);
```

`db_sync.upsert_session()` — called after each phase.
`db_sync.insert_signal()` — called when a HIGH or MEDIUM signal fires.

---

## 11. HTML Dashboard (`dashboard/`)

**Technology:** Vanilla HTML/CSS/JS, no build step. Served at `localhost:8000`.

### Three-Panel Layout

**Left — Zone Map**
- Vertical color-coded zone bar (proportional to SD range)
- ±1SD grey, ±2SD amber, ±3SD red/green
- Live price marker (red dot) positioned in real time via WebSocket
- OI magnet levels: dotted blue horizontal lines
- Box grid: thin white lines every 50 points

**Center — Trade Signal**
- Zone badge: "+2SD ZONE"
- Direction badge: LONG (green) / SHORT (red) / WAIT (grey)
- Confidence: HIGH / MEDIUM / LOW
- Entry, TP1, TP2, SL levels (dashes when WAIT)
- Recovery banner: pulsing red

**Right — OI Analysis**
- Call% vs Put% bar gauge
- Skew verdict text
- Top-2 magnet strikes with volume bars
- Gamma levels in orange
- Phase 2 lock timestamp

**Bottom Bar**
- Live price (large, updates every ~15s from TradingView CDP)
- TradingView connection status dot (green = CDP connected, grey = offline)
- Phase 1 / Phase 2 lock timestamps
- Link to today's Pine Script file

---

## 12. Configuration (`config.py` + `.env`)

```dotenv
# .env — secrets, never committed
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
TV_CDP_PORT=9222
```

`config.py` loads with `python-dotenv`, exposes a typed `Settings` dataclass.

---

## 13. Dependencies

### Added to `requirements.txt`

```
fastapi>=0.111.0
uvicorn>=0.29.0
python-dotenv>=1.0.0
supabase>=2.4.0
websockets>=12.0
websocket-client>=1.8.0
requests>=2.31.0
```

Added alongside existing `playwright>=1.44.0` and `apscheduler>=3.10.0`.

### External Prerequisites

| Prerequisite | Purpose |
|---|---|
| TradingView Desktop, Pro+ account | CDP price feed + Pine Script injection + native alerts |
| TradingView Desktop launched with `--remote-debugging-port=9222` | Enables CDP connection |
| `tradingview-mcp-jackson` (optional, Node.js) | Claude Code interactive sessions; MCP tools for TradingView |

---

## 14. Startup Instructions

```bash
# First time only
pip install -r requirements.txt
python -m playwright install chromium
python login_setup.py          # save CME session in browser_profile/
cp .env.example .env            # fill in secrets

# Launch TradingView Desktop with CDP enabled (do this first)
open -a "TradingView" --args --remote-debugging-port=9222
# Navigate to XAUUSD chart, open Pine Script editor (Alt+P)

# Start the framework
python server.py                # starts server + scheduler + TradingView polling
# Open browser → localhost:8000

# Manual runs (off-schedule)
python collector.py             # Phase 1 only
python oi_collector.py          # Phase 2 only
```

---

## 15. Scope Boundaries (Not in This Phase)

- No automated order execution (signals are advisory only)
- No TradingView webhook receiver (Discord alerts are Python-driven, not TV-driven)
- No backtesting engine (signal history stored in Supabase, evaluated manually)
- No mobile app (Discord handles mobile notifications)
- No multi-asset support (XAUUSD only)
- `outcome` field in `signals` table is filled manually via Supabase Studio

---

## 16. Open Questions Resolved

| Question | Decision |
|---|---|
| Live price source | TradingView Desktop via CDP (replaces MetaApi) |
| Pine Script delivery | Generated locally → injected into TV chart via CDP |
| Alert trigger | Python polling TradingView CDP → Discord webhook (no TV→FastAPI callback needed) |
| Session history | Supabase PostgreSQL (sessions + signals tables) |
| Dashboard tech | Vanilla HTML/JS, no build step |
| Scheduler location | Embedded in FastAPI lifespan (single `python server.py` process) |
| MT5 / MetaApi | Not needed; TradingView Desktop is the price source |
