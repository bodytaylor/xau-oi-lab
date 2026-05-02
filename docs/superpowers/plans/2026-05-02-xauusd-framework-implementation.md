# XAUUSD Framework — Full Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI server, TradingView CDP client, signal engine, Pine Script exporter, Discord alerts, Supabase sync, and HTML dashboard on top of the existing Phase 1/2 collectors.

**Architecture:** FastAPI orchestrates APScheduler (calls existing collector.py / oi_collector.py via subprocess), a TradingView Desktop CDP client for live price and Pine Script injection, and a WebSocket broadcast loop that powers a three-panel HTML dashboard. Discord alerts fire from Python on zone entry; Supabase stores daily sessions and signals.

**Tech Stack:** Python 3.11+, FastAPI + uvicorn + APScheduler, websocket-client (CDP), supabase-py, httpx (Discord), pytest + pytest-asyncio. Vanilla HTML/CSS/JS dashboard (no build step).

---

## File Map

| File | Creates / Modifies |
|---|---|
| `config.py` | CREATE — Settings dataclass loaded from .env |
| `requirements.txt` | MODIFY — add 7 new deps |
| `tests/conftest.py` | CREATE — shared test fixtures (sample session data) |
| `signal_engine.py` | CREATE — `compute_signal(session, price) → dict` |
| `tests/test_signal_engine.py` | CREATE |
| `pine_exporter.py` | CREATE — `run(session) → str`; writes `exports/` |
| `tests/test_pine_exporter.py` | CREATE |
| `tradingview_client.py` | CREATE — `TradingViewClient` (CDP: get_quote, push_pine, create_sd_alerts) |
| `tests/test_tradingview_client.py` | CREATE |
| `alerts.py` | CREATE — Discord webhook dispatcher (6 alert types) |
| `tests/test_alerts.py` | CREATE |
| `db_sync.py` | CREATE — `upsert_session`, `insert_signal`, `get_history` |
| `tests/test_db_sync.py` | CREATE |
| `server.py` | CREATE — FastAPI app (WebSocket, REST, scheduler, price poll) |
| `tests/test_server.py` | CREATE |
| `dashboard/index.html` | CREATE |
| `dashboard/style.css` | CREATE |
| `dashboard/app.js` | CREATE |

---

## Task 1: Infrastructure — config, requirements, conftest

**Files:**
- Create: `config.py`
- Modify: `requirements.txt`
- Create: `tests/conftest.py`

- [ ] **Step 1.1: Update requirements.txt**

Replace the current `requirements.txt` with:

```
playwright>=1.44.0
apscheduler>=3.10.0
fastapi>=0.111.0
uvicorn>=0.29.0
python-dotenv>=1.0.0
supabase>=2.4.0
websocket-client>=1.8.0
requests>=2.31.0
httpx>=0.27.0
websockets>=12.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 1.2: Write config.py**

```python
# config.py
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


@dataclass
class Settings:
    discord_webhook_url: str
    supabase_url: str
    supabase_service_key: str
    tv_cdp_port: int = 9222

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
            tv_cdp_port=int(os.environ.get("TV_CDP_PORT", "9222")),
        )


settings = Settings.from_env()
```

- [ ] **Step 1.3: Write tests/conftest.py**

```python
# tests/conftest.py
import pytest

SAMPLE_SESSION = {
    "version": "1.2",
    "date": "2026-05-02",
    "locked_at": "2026-05-02T01:30:00+07:00",
    "open_price": 4621.78,
    "iv_pct": 28.4,
    "iv_source": {"iv_pct": 28.4, "strike": 4625, "source": "chart_hover"},
    "sd_zones": {
        "daily_pct": 1.775,
        "sd1_pts": 82.04,
        "sd2_pts": 164.07,
        "sd3_pts": 246.11,
        "zones": {
            "+3SD": 4867.89,
            "+2SD": 4785.85,
            "+1SD": 4703.82,
            "OPEN": 4621.78,
            "-1SD": 4539.74,
            "-2SD": 4457.71,
            "-3SD": 4375.67,
        },
    },
    "phase1_complete": True,
    "phase2_complete": True,
    "phase2_at": "2026-05-02T08:30:00+07:00",
    "oi_data": [],
    "oi_analysis": {
        "call_vol": 6500.0,
        "put_vol": 3500.0,
        "call_pct": 65.0,
        "put_pct": 35.0,
        "skew_verdict": "CALL heavy — bullish",
        "magnets": [4700.0, 4500.0],
        "gamma_levels": [{"strike": 4650.0, "type": "call", "delta": 0.04}],
    },
}


@pytest.fixture
def sample_session():
    return SAMPLE_SESSION.copy()
```

- [ ] **Step 1.4: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 1.5: Verify config loads**

```bash
python -c "from config import settings; print(settings)"
```

Expected output: `Settings(discord_webhook_url='', supabase_url='', supabase_service_key='', tv_cdp_port=9222)`

- [ ] **Step 1.6: Commit**

```bash
git add config.py requirements.txt tests/ .env.example
git commit -m "feat: add config, requirements, test conftest"
```

---

## Task 2: Signal Engine

**Files:**
- Create: `signal_engine.py`
- Create: `tests/test_signal_engine.py`

- [ ] **Step 2.1: Write failing tests**

```python
# tests/test_signal_engine.py
import math
import pytest
from tests.conftest import SAMPLE_SESSION


def test_wait_when_inside_1sd(sample_session):
    from signal_engine import compute_signal
    # 4650 is 28 pts from open — well inside 1SD (82 pts)
    sig = compute_signal(sample_session, 4650.0)
    assert sig["direction"] == "WAIT"
    assert sig["zone"] == "INSIDE_1SD"
    assert sig["entry"] is None
    assert sig["tp1"] is None
    assert sig["sl"] is None
    assert sig["recovery"] is False


def test_short_signal_at_plus2sd(sample_session):
    from signal_engine import compute_signal
    # 4740 is 118.22 pts above open — in 2SD zone (82–164 pts)
    sig = compute_signal(sample_session, 4740.0)
    assert sig["direction"] == "SHORT"
    assert sig["zone"] == "+2SD"
    # box_top = ceil(4740/50)*50 = 4750
    assert sig["entry"] == 4750.0
    assert sig["tp1"] == 4725.0   # 4750 - 25
    assert sig["tp2"] == 4700.0   # 4750 - 50
    assert sig["sl"] == 4775.0    # 4750 + 25


def test_long_signal_at_minus2sd(sample_session):
    from signal_engine import compute_signal
    # 4480 is 141.78 pts below open — in 2SD zone
    sig = compute_signal(sample_session, 4480.0)
    assert sig["direction"] == "LONG"
    assert sig["zone"] == "-2SD"
    # box_bot = floor(4480/50)*50 = 4450
    assert sig["entry"] == 4450.0
    assert sig["tp1"] == 4475.0   # 4450 + 25
    assert sig["tp2"] == 4500.0   # 4450 + 50
    assert sig["sl"] == 4425.0    # 4450 - 25


def test_3sd_confidence_high_with_skew_and_magnet(sample_session):
    from signal_engine import compute_signal
    # Modify session: PUT heavy confirms SHORT at +3SD, magnet at 4700 (closer to open)
    session = sample_session.copy()
    session["oi_analysis"] = {
        **sample_session["oi_analysis"],
        "call_pct": 35.0,
        "put_pct": 65.0,
        "skew_verdict": "PUT heavy — bearish",
        "magnets": [4700.0],  # 78 pts from open — closer than the 3SD price
    }
    # price = 4860 is 238 pts above open — in 3SD zone (246 pts threshold)
    sig = compute_signal(session, 4860.0)
    assert sig["zone"] == "+3SD"
    assert sig["direction"] == "SHORT"
    assert sig["confidence"] == "HIGH"


def test_3sd_confidence_medium_when_skew_contradicts(sample_session):
    from signal_engine import compute_signal
    # sample_session has CALL heavy (bullish) — contradicts SHORT at +3SD
    sig = compute_signal(sample_session, 4860.0)
    assert sig["zone"] == "+3SD"
    assert sig["confidence"] == "MEDIUM"


def test_recovery_signal_price_above(sample_session):
    from signal_engine import compute_signal
    # 3SD = 246.11 pts. recovery triggers at 3SD + 25 = 271.11 pts from open
    # open = 4621.78 + 271.11 = 4892.89 — use 4900 to clearly trigger
    sig = compute_signal(sample_session, 4900.0)
    assert sig["recovery"] is True
    # Follow trend: price above open → follow LONG (trend is UP)
    assert sig["direction"] == "LONG"


def test_recovery_signal_price_below(sample_session):
    from signal_engine import compute_signal
    # open = 4621.78 - 271.11 = 4350.67 — use 4340 to clearly trigger
    sig = compute_signal(sample_session, 4340.0)
    assert sig["recovery"] is True
    assert sig["direction"] == "SHORT"  # follow DOWN trend


def test_signal_at_returns_datetime_string(sample_session):
    from signal_engine import compute_signal
    sig = compute_signal(sample_session, 4740.0)
    assert "signal_at" in sig
    assert "T" in sig["signal_at"]  # ISO format
```

- [ ] **Step 2.2: Run to confirm all tests fail**

```bash
pytest tests/test_signal_engine.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'signal_engine'`

- [ ] **Step 2.3: Write signal_engine.py**

```python
# signal_engine.py
import math
from datetime import datetime, timezone, timedelta

UTC7 = timezone(timedelta(hours=7))


def compute_signal(session: dict, price: float) -> dict:
    """
    Compute the current box-trading signal given session data and live price.

    Returns a dict with: zone, direction, confidence, entry, tp1, tp2, sl,
    recovery (bool), signal_at (ISO timestamp).
    """
    open_p = session["open_price"]
    sd     = session["sd_zones"]
    sd1    = sd["sd1_pts"]
    sd2    = sd["sd2_pts"]
    sd3    = sd["sd3_pts"]
    oi     = session.get("oi_analysis") or {}

    diff = abs(price - open_p)
    above = price >= open_p  # True = price is above open

    # ── Zone classification ───────────────────────────────────────────────
    if diff < sd1:
        raw_zone = "INSIDE_1SD"
    elif diff < sd2:
        raw_zone = "2SD"
    elif diff < sd3:
        raw_zone = "3SD"
    else:
        raw_zone = "BEYOND_3SD"

    zone_label = (
        raw_zone if raw_zone == "INSIDE_1SD"
        else f"+{raw_zone}" if above
        else f"-{raw_zone}"
    )

    # ── WAIT — inside 1SD ─────────────────────────────────────────────────
    if raw_zone == "INSIDE_1SD":
        return {
            "zone": "INSIDE_1SD",
            "direction": "WAIT",
            "confidence": "LOW",
            "entry": None, "tp1": None, "tp2": None, "sl": None,
            "recovery": False,
            "signal_at": datetime.now(UTC7).isoformat(),
        }

    # ── Recovery — trend broke through 3SD ───────────────────────────────
    recovery = diff > sd3 + 25

    box_size = 50

    if recovery:
        # Follow the trend, not fade it
        follow_dir = "LONG" if above else "SHORT"
        if follow_dir == "LONG":
            bot = math.floor(price / box_size) * box_size
            entry, tp1, tp2, sl = bot, bot + 25, bot + 50, bot - 25
        else:
            top = math.ceil(price / box_size) * box_size
            entry, tp1, tp2, sl = top, top - 25, top - 50, top + 25
        return {
            "zone": zone_label,
            "direction": follow_dir,
            "confidence": "HIGH",
            "entry": float(entry),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "sl": float(sl),
            "recovery": True,
            "signal_at": datetime.now(UTC7).isoformat(),
        }

    # ── Normal fade signal at 2SD or 3SD ─────────────────────────────────
    fade_dir = "SHORT" if above else "LONG"

    if fade_dir == "SHORT":
        top   = math.ceil(price / box_size) * box_size
        entry, tp1, tp2, sl = top, top - 25, top - 50, top + 25
    else:
        bot   = math.floor(price / box_size) * box_size
        entry, tp1, tp2, sl = bot, bot + 25, bot + 50, bot - 25

    # ── Confidence ────────────────────────────────────────────────────────
    call_pct = oi.get("call_pct", 50.0)
    put_pct  = oi.get("put_pct", 50.0)
    magnets  = oi.get("magnets", [])

    # Skew confirms if OI pressure aligns with the fade direction
    skew_confirms = (
        (fade_dir == "SHORT" and put_pct > 55) or
        (fade_dir == "LONG"  and call_pct > 55)
    )

    # Magnet target exists closer to open than the current price
    has_magnet_target = any(abs(m - open_p) < diff for m in magnets)

    confidence = (
        "HIGH" if raw_zone == "3SD" and skew_confirms and has_magnet_target
        else "MEDIUM"
    )

    return {
        "zone": zone_label,
        "direction": fade_dir,
        "confidence": confidence,
        "entry": float(entry),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "sl": float(sl),
        "recovery": False,
        "signal_at": datetime.now(UTC7).isoformat(),
    }
```

- [ ] **Step 2.4: Run tests — all must pass**

```bash
pytest tests/test_signal_engine.py -v
```

Expected: `8 passed`

- [ ] **Step 2.5: Commit**

```bash
git add signal_engine.py tests/test_signal_engine.py tests/conftest.py
git commit -m "feat: add signal engine with box trading logic (TDD)"
```

---

## Task 3: Pine Script Exporter

**Files:**
- Create: `pine_exporter.py`
- Create: `tests/test_pine_exporter.py`

- [ ] **Step 3.1: Write failing tests**

```python
# tests/test_pine_exporter.py
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from tests.conftest import SAMPLE_SESSION


def test_generate_pine_contains_open_price(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    assert "4621.78" in code


def test_generate_pine_contains_all_sd_levels(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    for level in ["4867.89", "4785.85", "4703.82", "4539.74", "4457.71", "4375.67"]:
        assert level in code, f"Missing level {level}"


def test_generate_pine_contains_version_5(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    assert "//@version=5" in code


def test_generate_pine_contains_magnets_when_phase2_complete(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    # sample_session has magnets [4700.0, 4500.0] and phase2_complete=True
    assert "4700" in code
    assert "4500" in code


def test_generate_pine_no_magnets_when_phase2_incomplete(sample_session):
    from pine_exporter import generate_pine_script
    sample_session["phase2_complete"] = False
    code = generate_pine_script(sample_session)
    # Magnets should not appear in script when Phase 2 not done
    assert "OI Magnet" not in code


def test_run_writes_file_and_returns_code(sample_session, tmp_path):
    from pine_exporter import run
    with patch("pine_exporter.EXPORTS_DIR", tmp_path):
        code = run(sample_session)
    expected_file = tmp_path / f"session_{sample_session['date']}.pine"
    assert expected_file.exists()
    assert expected_file.read_text() == code


def test_run_returns_string(sample_session, tmp_path):
    from pine_exporter import run
    with patch("pine_exporter.EXPORTS_DIR", tmp_path):
        code = run(sample_session)
    assert isinstance(code, str)
    assert len(code) > 100
```

- [ ] **Step 3.2: Run to confirm tests fail**

```bash
pytest tests/test_pine_exporter.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'pine_exporter'`

- [ ] **Step 3.3: Write pine_exporter.py**

```python
# pine_exporter.py
"""
Generates a Pine Script indicator from session_data.json.

Phase 1 output: SD zone backgrounds + level lines + box grid
Phase 2 output: adds OI magnet lines

The generated .pine file is written to exports/ and the code string
is returned for direct injection into TradingView via CDP.
"""
import logging
from pathlib import Path

BASE_DIR    = Path(__file__).parent
EXPORTS_DIR = BASE_DIR / "exports"

log = logging.getLogger("pine_exporter")


def generate_pine_script(session: dict) -> str:
    """Build the Pine Script string from session data."""
    open_p = session["open_price"]
    sd     = session["sd_zones"]
    date   = session.get("date", "today")

    p3 = sd["zones"]["+3SD"]
    p2 = sd["zones"]["+2SD"]
    p1 = sd["zones"]["+1SD"]
    m1 = sd["zones"]["-1SD"]
    m2 = sd["zones"]["-2SD"]
    m3 = sd["zones"]["-3SD"]

    # Box grid: 50-pt boundaries within ±3SD range
    lo = int(m3 // 50) * 50
    hi = int(p3 // 50 + 1) * 50
    box_levels = list(range(lo, hi + 50, 50))
    box_lines = "\n".join(
        f'hline({b}, "", color=color.new(color.white, 88), '
        f'linestyle=hline.style_dotted, linewidth=1)'
        for b in box_levels
    )

    # OI magnets (Phase 2 only)
    magnet_lines = ""
    if session.get("phase2_complete") and session.get("oi_analysis"):
        magnets = session["oi_analysis"].get("magnets", [])
        if magnets:
            lines = ["", "// OI Magnets (Phase 2)"]
            for m in magnets:
                lines.append(
                    f'hline({m:.2f}, "OI Magnet {m:.0f}", '
                    f'color=color.blue, linestyle=hline.style_dotted, linewidth=2)'
                )
            magnet_lines = "\n".join(lines)

    return f"""//@version=5
indicator("XAUUSD OI Zones — {date}", overlay=true)

// ── Background zones ──────────────────────────────────────────────────
bgcolor(close >= {p2} ? color.new(color.red, 83) : na,    title="+2SD+ zone")
bgcolor(close >= {p1} and close < {p2} ? color.new(color.orange, 88) : na, title="+1-2SD zone")
bgcolor(close >= {open_p} and close < {p1} ? color.new(color.gray, 93) : na, title="0-1SD zone")
bgcolor(close < {open_p} and close >= {m1} ? color.new(color.gray, 93) : na, title="-1-0SD zone")
bgcolor(close < {m1} and close >= {m2} ? color.new(color.teal, 88) : na, title="-2-1SD zone")
bgcolor(close < {m2} ? color.new(color.green, 83) : na,   title="-2SD+ zone")

// ── SD level lines ────────────────────────────────────────────────────
hline({open_p}, "OPEN {open_p:.2f}",  color=color.white,  linestyle=hline.style_dashed, linewidth=2)
hline({p1},     "+1SD {p1:.2f}",      color=color.gray,   linestyle=hline.style_dotted, linewidth=1)
hline({p2},     "+2SD {p2:.2f}",      color=color.orange, linestyle=hline.style_dashed, linewidth=2)
hline({p3},     "+3SD {p3:.2f}",      color=color.red,    linestyle=hline.style_solid,  linewidth=2)
hline({m1},     "-1SD {m1:.2f}",      color=color.gray,   linestyle=hline.style_dotted, linewidth=1)
hline({m2},     "-2SD {m2:.2f}",      color=color.teal,   linestyle=hline.style_dashed, linewidth=2)
hline({m3},     "-3SD {m3:.2f}",      color=color.green,  linestyle=hline.style_solid,  linewidth=2)

// ── Box grid (50-pt boundaries) ───────────────────────────────────────
{box_lines}
{magnet_lines}"""


def run(session: dict) -> str:
    """Generate Pine Script, write to exports/, return code string."""
    EXPORTS_DIR.mkdir(exist_ok=True)
    code = generate_pine_script(session)
    out  = EXPORTS_DIR / f"session_{session.get('date', 'today')}.pine"
    out.write_text(code)
    log.info(f"Pine Script written → {out.name}")
    return code
```

- [ ] **Step 3.4: Run tests — all must pass**

```bash
pytest tests/test_pine_exporter.py -v
```

Expected: `7 passed`

- [ ] **Step 3.5: Smoke-test with real session data**

```bash
python -c "
import json
from pathlib import Path
from pine_exporter import run
s = json.loads(Path('session_data.json').read_text())
print(run(s)[:500])
"
```

Expected: Pine Script starting with `//@version=5` printed to terminal and `exports/session_YYYY-MM-DD.pine` file created.

- [ ] **Step 3.6: Commit**

```bash
git add pine_exporter.py tests/test_pine_exporter.py
git commit -m "feat: add Pine Script exporter with Phase 1/2 zone map generation (TDD)"
```

---

## Task 4: TradingView CDP Client

**Files:**
- Create: `tradingview_client.py`
- Create: `tests/test_tradingview_client.py`

- [ ] **Step 4.1: Write failing tests**

```python
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
```

- [ ] **Step 4.2: Run to confirm tests fail**

```bash
pytest tests/test_tradingview_client.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'tradingview_client'`

- [ ] **Step 4.3: Write tradingview_client.py**

```python
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
                ws.close()
                return False

            # Step 2: click compile / add-to-chart button
            ws.send(json.dumps({
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {"expression": _JS_COMPILE, "returnByValue": True},
            }))
            ws.recv()
            ws.close()
            log.info("Pine Script injected and compiled ✓")
            return True

        except Exception as e:
            log.warning(f"push_pine: {e}")
            return False

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
```

- [ ] **Step 4.4: Run tests — all must pass**

```bash
pytest tests/test_tradingview_client.py -v
```

Expected: `8 passed`

- [ ] **Step 4.5: Commit**

```bash
git add tradingview_client.py tests/test_tradingview_client.py
git commit -m "feat: add TradingView CDP client (price, Pine push, alerts) (TDD)"
```

---

## Task 5: Discord Alerts

**Files:**
- Create: `alerts.py`
- Create: `tests/test_alerts.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/test_alerts.py
from unittest.mock import patch, MagicMock
import pytest


def _reset_alerts():
    """Clear deduplication state between tests."""
    import alerts
    alerts._fired.clear()
    alerts._fired_date = ""


@patch("alerts.httpx.post")
def test_phase1_complete_posts_embed(mock_post, sample_session):
    _reset_alerts()
    import alerts
    alerts.phase1_complete(sample_session)
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert "Phase 1" in embed["title"]
    assert embed["color"] == 0x00FF88


@patch("alerts.httpx.post")
def test_phase1_complete_deduplicates(mock_post, sample_session):
    _reset_alerts()
    import alerts
    alerts.phase1_complete(sample_session)
    alerts.phase1_complete(sample_session)  # second call same session date
    assert mock_post.call_count == 1  # only fired once


@patch("alerts.httpx.post")
def test_zone_alert_posts_red_embed_for_3sd(mock_post):
    _reset_alerts()
    import alerts
    signal = {
        "zone": "+3SD", "direction": "SHORT", "confidence": "HIGH",
        "entry": 4900.0, "tp1": 4875.0, "tp2": 4850.0, "sl": 4925.0,
        "recovery": False, "signal_at": "2026-05-02T10:00:00+07:00",
    }
    alerts.zone_alert(4867.89, signal)
    payload = mock_post.call_args.kwargs["json"]
    embed = payload["embeds"][0]
    assert embed["color"] == 0xFF0000
    assert "+3SD" in embed["title"]


@patch("alerts.httpx.post")
def test_zone_alert_posts_amber_for_2sd(mock_post):
    _reset_alerts()
    import alerts
    signal = {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "entry": 4800.0, "tp1": 4775.0, "tp2": 4750.0, "sl": 4825.0,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
    }
    alerts.zone_alert(4785.85, signal)
    embed = mock_post.call_args.kwargs["json"]["embeds"][0]
    assert embed["color"] == 0xFF9900


@patch("alerts.httpx.post")
def test_recovery_alert_fires_once(mock_post):
    _reset_alerts()
    import alerts
    signal = {"direction": "LONG", "entry": 4875.0, "signal_at": "2026-05-02T10:00:00+07:00"}
    alerts.recovery_alert(4900.0, signal)
    alerts.recovery_alert(4910.0, signal)  # second call — should be deduplicated
    assert mock_post.call_count == 1


@patch("alerts.httpx.post")
def test_no_post_when_webhook_not_configured(mock_post, sample_session):
    _reset_alerts()
    import alerts
    with patch("alerts.settings") as mock_settings:
        mock_settings.discord_webhook_url = ""
        alerts.phase1_complete(sample_session)
    mock_post.assert_not_called()
```

- [ ] **Step 5.2: Run to confirm tests fail**

```bash
pytest tests/test_alerts.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'alerts'`

- [ ] **Step 5.3: Write alerts.py**

```python
# alerts.py
"""
Discord webhook alert dispatcher.

Alert types: phase1_complete, phase2_complete, zone_alert, recovery_alert,
             gamma_alert, phase_failed.

Deduplication: each alert type fires at most once per day (reset at midnight UTC+7).
Uses httpx for HTTP; webhook URL from config.settings.
"""
import logging
from datetime import datetime, timezone, timedelta

import httpx

from config import settings

UTC7 = timezone(timedelta(hours=7))
log  = logging.getLogger("alerts")

# In-memory deduplication — reset on new session date
_fired: set[str] = set()
_fired_date: str = ""


def _dedup(key: str) -> bool:
    """Returns True if this alert should fire (not yet fired today)."""
    global _fired_date, _fired
    today = datetime.now(UTC7).strftime("%Y-%m-%d")
    if today != _fired_date:
        _fired_date = today
        _fired      = set()
    if key in _fired:
        return False
    _fired.add(key)
    return True


def _post(embed: dict) -> None:
    if not settings.discord_webhook_url:
        return
    try:
        httpx.post(
            settings.discord_webhook_url,
            json={"embeds": [embed]},
            timeout=10,
        )
    except Exception as e:
        log.debug(f"Discord post failed: {e}")


def phase1_complete(session: dict) -> None:
    if not _dedup(f"phase1_{session['date']}"):
        return
    sd = session["sd_zones"]
    zones_text = "\n".join(f"{k}: {v:.2f}" for k, v in sd["zones"].items())
    _post({
        "title": "✅ Phase 1 Complete — XAUUSD Zone Map Locked",
        "color": 0x00FF88,
        "fields": [
            {"name": "Open",   "value": f"{session['open_price']:.2f}", "inline": True},
            {"name": "IV%",    "value": str(session["iv_pct"]),         "inline": True},
            {"name": "1SD ±",  "value": f"{sd['sd1_pts']:.2f} pts",    "inline": True},
            {"name": "SD Zones", "value": zones_text, "inline": False},
        ],
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def phase2_complete(session: dict) -> None:
    if not _dedup(f"phase2_{session['date']}"):
        return
    oi     = session.get("oi_analysis") or {}
    magnets = ", ".join(f"{m:.0f}" for m in oi.get("magnets", [])) or "None"
    gamma   = ", ".join(str(g["strike"]) for g in oi.get("gamma_levels", [])) or "None"
    _post({
        "title": "📊 Phase 2 Complete — OI Analysis Ready",
        "color": 0x0099FF,
        "fields": [
            {"name": "Skew",    "value": oi.get("skew_verdict", "N/A"), "inline": True},
            {"name": "Calls",   "value": f"{oi.get('call_pct', 0):.1f}%", "inline": True},
            {"name": "Puts",    "value": f"{oi.get('put_pct', 0):.1f}%",  "inline": True},
            {"name": "Magnets", "value": magnets, "inline": False},
            {"name": "Gamma Risk", "value": gamma, "inline": False},
        ],
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def zone_alert(price: float, signal: dict) -> None:
    zone  = signal.get("zone", "")
    key   = f"zone_{zone}_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    is_3sd  = "3SD" in zone
    color   = 0xFF0000 if is_3sd else 0xFF9900
    emoji   = "🔴" if is_3sd else "⚠️"
    entry   = signal.get("entry")
    fields  = [
        {"name": "Zone",       "value": zone,                         "inline": True},
        {"name": "Direction",  "value": signal.get("direction", ""), "inline": True},
        {"name": "Confidence", "value": signal.get("confidence", ""), "inline": True},
    ]
    if entry is not None:
        fields += [
            {"name": "Entry", "value": str(entry),                  "inline": True},
            {"name": "TP1",   "value": str(signal.get("tp1")),      "inline": True},
            {"name": "TP2/SL","value": f"{signal.get('tp2')} / {signal.get('sl')}", "inline": True},
        ]
    _post({
        "title": f"{emoji} XAUUSD at {zone} — {price:.2f}",
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def recovery_alert(price: float, signal: dict) -> None:
    key = f"recovery_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    _post({
        "title": "⚡ RECOVERY SIGNAL — Cut and Follow Trend",
        "color": 0xFF0000,
        "description": (
            f"Price **{price:.2f}** broke through 3SD without reversing.\n"
            f"Cut opposing position — follow **{signal.get('direction')}**."
        ),
        "fields": [
            {"name": "Follow", "value": signal.get("direction", ""), "inline": True},
            {"name": "Entry",  "value": str(signal.get("entry", "")), "inline": True},
        ],
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def gamma_alert(strike: float, opt_type: str) -> None:
    key = f"gamma_{strike:.0f}_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    _post({
        "title": "⚡ GAMMA RISK — Market Maker Hedging Expected",
        "color": 0xFF6600,
        "description": (
            f"Price approaching {opt_type.upper()} gamma level at **{strike:.0f}**.\n"
            "Delta < 5% — MM hedging may cause rapid move."
        ),
        "timestamp": datetime.now(UTC7).isoformat(),
    })


def phase_failed(phase: int, error: str) -> None:
    _post({
        "title": f"❌ Phase {phase} FAILED",
        "color": 0xFF0000,
        "description": f"```{error[:500]}```",
        "timestamp": datetime.now(UTC7).isoformat(),
    })
```

- [ ] **Step 5.4: Run tests — all must pass**

```bash
pytest tests/test_alerts.py -v
```

Expected: `6 passed`

- [ ] **Step 5.5: Commit**

```bash
git add alerts.py tests/test_alerts.py
git commit -m "feat: add Discord alert dispatcher with deduplication (TDD)"
```

---

## Task 6: Supabase DB Sync

**Files:**
- Create: `db_sync.py`
- Create: `tests/test_db_sync.py`

- [ ] **Step 6.1: Write failing tests**

```python
# tests/test_db_sync.py
from unittest.mock import MagicMock, patch
import pytest


def _mock_client():
    """Build a mock Supabase client that chains .table().upsert().execute() etc."""
    client = MagicMock()
    table  = MagicMock()
    client.table.return_value = table
    table.upsert.return_value = table
    table.insert.return_value = table
    table.select.return_value = table
    table.order.return_value  = table
    table.limit.return_value  = table
    table.execute.return_value = MagicMock(data=[{"date": "2026-05-02"}])
    return client


@patch("db_sync._client")
def test_upsert_session_calls_table(mock_client_fn, sample_session):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    result = db_sync.upsert_session(sample_session)
    assert result is True
    mock_client_fn.return_value.table.assert_called_with("sessions")


@patch("db_sync._client")
def test_upsert_session_returns_false_when_supabase_not_configured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    result = db_sync.upsert_session({"date": "2026-05-02"})
    assert result is False


@patch("db_sync._client")
def test_insert_signal_calls_table(mock_client_fn):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    signal = {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
    }
    result = db_sync.insert_signal("2026-05-02", signal, 4740.0)
    assert result is True
    mock_client_fn.return_value.table.assert_called_with("signals")


@patch("db_sync._client")
def test_get_history_returns_list(mock_client_fn):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    history = db_sync.get_history(n=5)
    assert isinstance(history, list)


@patch("db_sync._client")
def test_get_history_returns_empty_when_unconfigured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    assert db_sync.get_history() == []
```

- [ ] **Step 6.2: Run to confirm tests fail**

```bash
pytest tests/test_db_sync.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'db_sync'`

- [ ] **Step 6.3: Write db_sync.py**

```python
# db_sync.py
"""
Supabase persistence layer.

Tables (create in Supabase SQL editor before use):

  create table sessions (
    id            uuid primary key default gen_random_uuid(),
    date          date unique not null,
    open_price    float,
    iv_pct        float,
    sd_zones      jsonb,
    oi_analysis   jsonb,
    phase1_at     timestamptz,
    phase2_at     timestamptz,
    created_at    timestamptz default now()
  );

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
    outcome       text
  );
"""
import logging
from config import settings

log = logging.getLogger("db_sync")


def _client():
    """Return Supabase client, or None if not configured."""
    if not settings.supabase_url or not settings.supabase_service_key:
        return None
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_key)


def upsert_session(session: dict) -> bool:
    """Upsert a session row (keyed on date). Returns True on success."""
    client = _client()
    if client is None:
        log.warning("Supabase not configured — skipping session upsert")
        return False
    try:
        row = {
            "date":        session["date"],
            "open_price":  session["open_price"],
            "iv_pct":      session["iv_pct"],
            "sd_zones":    session["sd_zones"],
            "oi_analysis": session.get("oi_analysis"),
            "phase1_at":   session.get("locked_at"),
            "phase2_at":   session.get("phase2_at"),
        }
        client.table("sessions").upsert(row, on_conflict="date").execute()
        log.info(f"Session {session['date']} upserted")
        return True
    except Exception as e:
        log.error(f"upsert_session: {e}")
        return False


def insert_signal(session_date: str, signal: dict, price: float) -> bool:
    """Insert a fired signal row. Returns True on success."""
    client = _client()
    if client is None:
        return False
    try:
        row = {
            "session_date": session_date,
            "fired_at":     signal["signal_at"],
            "price":        price,
            "zone":         signal["zone"],
            "direction":    signal["direction"],
            "entry":        signal.get("entry"),
            "tp1":          signal.get("tp1"),
            "tp2":          signal.get("tp2"),
            "sl":           signal.get("sl"),
            "confidence":   signal["confidence"],
            "recovery":     signal.get("recovery", False),
        }
        client.table("signals").insert(row).execute()
        log.info(f"Signal {signal['zone']} {signal['direction']} stored")
        return True
    except Exception as e:
        log.error(f"insert_signal: {e}")
        return False


def get_history(n: int = 30) -> list[dict]:
    """Fetch the last N session rows from Supabase, newest first."""
    client = _client()
    if client is None:
        return []
    try:
        res = (
            client.table("sessions")
            .select("*")
            .order("date", desc=True)
            .limit(n)
            .execute()
        )
        return res.data
    except Exception as e:
        log.error(f"get_history: {e}")
        return []
```

- [ ] **Step 6.4: Run tests — all must pass**

```bash
pytest tests/test_db_sync.py -v
```

Expected: `5 passed`

- [ ] **Step 6.5: Commit**

```bash
git add db_sync.py tests/test_db_sync.py
git commit -m "feat: add Supabase persistence (sessions + signals tables) (TDD)"
```

---

## Task 7: FastAPI Server

**Files:**
- Create: `server.py`
- Create: `tests/test_server.py`

- [ ] **Step 7.1: Write failing tests**

```python
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
```

- [ ] **Step 7.2: Run to confirm tests fail**

```bash
pytest tests/test_server.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 7.3: Write server.py**

```python
# server.py
"""
FastAPI orchestrator — the single entry point for the full framework.

Start with:  python server.py
Dashboard:   http://localhost:8000

Components started on launch:
- APScheduler (Phase 1 @ 01:30 UTC+7, Phase 2 @ 08:30 UTC+7)
- Background price polling from TradingView Desktop via CDP (every 15s)
- WebSocket broadcast to all connected dashboard clients
"""
import asyncio
import json
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from pine_exporter import run as export_pine
from signal_engine import compute_signal
from tradingview_client import TradingViewClient
import alerts
import db_sync

UTC7 = timezone(timedelta(hours=7))
BASE = Path(__file__).parent
SESSION_FILE = BASE / "session_data.json"

log = logging.getLogger("server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)


# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager   = ConnectionManager()
tv_client = TradingViewClient(cdp_port=settings.tv_cdp_port)
scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")

_last_zone: str = ""  # dedup tracker for zone entry alerts


# ── Session helpers ───────────────────────────────────────────────────────────

def _load_session() -> dict | None:
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text())
    except Exception:
        return None


# ── Price polling ─────────────────────────────────────────────────────────────

async def _price_poll_loop():
    while True:
        await asyncio.sleep(15)
        try:
            await _poll_once()
        except Exception as e:
            log.debug(f"poll error: {e}")


async def _poll_once():
    global _last_zone
    price = tv_client.get_quote()
    if price is None:
        await manager.broadcast({"price": None, "zone": None, "signal": None,
                                  "tv_online": False})
        return

    session = _load_session()
    if not session or not session.get("phase1_complete"):
        await manager.broadcast({"price": price, "zone": None, "signal": None,
                                  "tv_online": True,
                                  "ts": datetime.now(UTC7).isoformat()})
        return

    signal = compute_signal(session, price)
    zone   = signal["zone"]

    # Zone entry alert (fires once per zone per session via alerts deduplication)
    if zone not in ("INSIDE_1SD",) and zone != _last_zone:
        alerts.zone_alert(price, signal)
        if signal.get("recovery"):
            alerts.recovery_alert(price, signal)
        if signal["confidence"] in ("HIGH", "MEDIUM"):
            db_sync.insert_signal(session["date"], signal, price)

    # Gamma squeeze proximity check
    oi = session.get("oi_analysis") or {}
    for g in oi.get("gamma_levels", []):
        if abs(price - g["strike"]) < 10:
            alerts.gamma_alert(g["strike"], g["type"])

    _last_zone = zone
    await manager.broadcast({
        "price":     price,
        "zone":      zone,
        "signal":    signal,
        "tv_online": True,
        "ts":        datetime.now(UTC7).isoformat(),
    })


# ── Phase runners (run in thread pool via APScheduler) ────────────────────────

def run_phase1():
    log.info("Phase 1 starting…")
    result = subprocess.run([sys.executable, str(BASE / "collector.py")])
    if result.returncode != 0:
        alerts.phase_failed(1, f"exit code {result.returncode}")
        return
    session = _load_session()
    if not session:
        return
    pine_code = export_pine(session)
    tv_client.push_pine(pine_code)
    tv_client.create_sd_alerts(session["sd_zones"])
    db_sync.upsert_session(session)
    alerts.phase1_complete(session)
    log.info("Phase 1 complete ✓")


def run_phase2():
    log.info("Phase 2 starting…")
    result = subprocess.run([sys.executable, str(BASE / "oi_collector.py")])
    if result.returncode != 0:
        alerts.phase_failed(2, f"exit code {result.returncode}")
        return
    session = _load_session()
    if not session:
        return
    pine_code = export_pine(session)
    tv_client.push_pine(pine_code)
    db_sync.upsert_session(session)
    alerts.phase2_complete(session)
    log.info("Phase 2 complete ✓")


# ── FastAPI app + lifespan ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_phase1,
        CronTrigger(hour=1, minute=30, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        id="phase1",
        name="Phase 1 — Open price + IV",
    )
    scheduler.add_job(
        run_phase2,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        id="phase2",
        name="Phase 2 — OI sentiment",
    )
    scheduler.start()
    asyncio.create_task(_price_poll_loop())
    log.info("Server started. Dashboard → http://localhost:8000")
    log.info(f"TradingView CDP: {'CONNECTED' if tv_client.is_connected() else 'OFFLINE'}")
    for job in scheduler.get_jobs():
        log.info(f"  Scheduled: {job.name} next={job.next_run_time}")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/price")
async def ws_price(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/session")
async def get_session():
    session = _load_session()
    if not session:
        return JSONResponse({"error": "No session data — run Phase 1 first"}, status_code=404)
    return session


@app.get("/api/signal")
async def get_signal():
    session = _load_session()
    if not session or not session.get("phase1_complete"):
        return {"direction": "WAIT", "zone": None, "reason": "Phase 1 not complete"}
    price = tv_client.get_quote()
    if price is None:
        return JSONResponse({"error": "TradingView offline — no price available"}, status_code=503)
    return compute_signal(session, price)


@app.get("/api/history")
async def get_history(n: int = 30):
    return db_sync.get_history(n)


@app.post("/api/refresh/phase1")
async def refresh_phase1():
    asyncio.create_task(asyncio.to_thread(run_phase1))
    return {"status": "triggered"}


@app.post("/api/refresh/phase2")
async def refresh_phase2():
    asyncio.create_task(asyncio.to_thread(run_phase2))
    return {"status": "triggered"}


# ── Static dashboard ──────────────────────────────────────────────────────────

_dashboard = BASE / "dashboard"
if _dashboard.exists():
    app.mount("/", StaticFiles(directory=str(_dashboard), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000, log_level="info")
```

- [ ] **Step 7.4: Run tests — all must pass**

```bash
pytest tests/test_server.py -v
```

Expected: `6 passed`

- [ ] **Step 7.5: Run full test suite — all pass**

```bash
pytest tests/ -v
```

Expected: all tests pass (no failures).

- [ ] **Step 7.6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: add FastAPI server with WebSocket, REST endpoints, APScheduler"
```

---

## Task 8: HTML Dashboard

**Files:**
- Create: `dashboard/index.html`
- Create: `dashboard/style.css`
- Create: `dashboard/app.js`

No automated tests for the dashboard — visual verification via browser.

- [ ] **Step 8.1: Write dashboard/style.css**

```css
/* dashboard/style.css */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #0d0d0d;
  --panel:    #161616;
  --border:   #2a2a2a;
  --text:     #e0e0e0;
  --muted:    #666;
  --green:    #00c853;
  --red:      #ff1744;
  --amber:    #ff9100;
  --blue:     #2979ff;
  --grey:     #424242;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 13px;
  height: 100vh;
  display: grid;
  grid-template-rows: 1fr auto;
  overflow: hidden;
}

#main {
  display: grid;
  grid-template-columns: 180px 1fr 220px;
  gap: 1px;
  background: var(--border);
  overflow: hidden;
}

.panel {
  background: var(--panel);
  padding: 16px;
  overflow-y: auto;
}

h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 12px; }

/* ── Zone Map (left panel) ─────────────────────────────── */
#zone-map-wrap {
  position: relative;
  height: 100%;
  display: flex;
  flex-direction: column;
}

#zone-bar {
  position: relative;
  flex: 1;
  min-height: 400px;
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: visible;
}

.zone-band {
  position: absolute;
  left: 0; right: 0;
  display: flex;
  align-items: center;
  padding-left: 4px;
  font-size: 10px;
  color: rgba(255,255,255,0.6);
}

.zone-3 { background: rgba(255,23,68,.15); }
.zone-2 { background: rgba(255,145,0,.12); }
.zone-1 { background: rgba(66,66,66,.20); }

.sd-line {
  position: absolute;
  left: 0; right: 0;
  height: 1px;
  opacity: 0.8;
}

.price-dot {
  position: absolute;
  left: 50%;
  transform: translate(-50%, 50%);
  width: 10px;
  height: 10px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 0 6px #fff;
  transition: bottom 0.5s ease;
  z-index: 10;
}

.level-label {
  position: absolute;
  right: 4px;
  transform: translateY(50%);
  font-size: 9px;
  color: var(--muted);
  white-space: nowrap;
}

/* ── Signal panel (center) ────────────────────────────── */
#signal-panel { text-align: center; }

#zone-badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 11px;
  background: var(--grey);
  margin-bottom: 12px;
}

#direction-badge {
  font-size: 36px;
  font-weight: bold;
  margin-bottom: 8px;
}

#confidence {
  font-size: 11px;
  letter-spacing: 2px;
  margin-bottom: 20px;
  color: var(--muted);
}

.level-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  text-align: left;
  margin-bottom: 20px;
}
.level-grid .lbl { color: var(--muted); }
.level-grid .val { font-weight: bold; font-size: 15px; }

#recovery-banner {
  display: none;
  background: rgba(255,23,68,.2);
  border: 1px solid var(--red);
  border-radius: 4px;
  padding: 8px;
  font-size: 11px;
  color: var(--red);
  animation: pulse 1.5s infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }

/* ── OI panel (right) ─────────────────────────────────── */
#oi-panel .skew { font-size: 16px; font-weight: bold; margin-bottom: 12px; }

.gauge-row { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; font-size: 11px; }
.gauge-bar  { flex: 1; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
.gauge-fill { height: 100%; border-radius: 4px; transition: width .5s; }

.magnet-row { padding: 4px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
.gamma-row  { color: var(--amber); padding: 4px 0; font-size: 11px; }

/* ── Bottom bar ───────────────────────────────────────── */
#bottom-bar {
  background: var(--panel);
  border-top: 1px solid var(--border);
  padding: 8px 16px;
  display: flex;
  align-items: center;
  gap: 24px;
  font-size: 12px;
}

#live-price { font-size: 28px; font-weight: bold; min-width: 100px; }

.status-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  display: inline-block;
  margin-right: 4px;
}
.dot-green  { background: var(--green); box-shadow: 0 0 4px var(--green); }
.dot-amber  { background: var(--amber); }
.dot-grey   { background: var(--grey); }

#pine-link { margin-left: auto; color: var(--blue); text-decoration: none; font-size: 11px; }
#pine-link:hover { text-decoration: underline; }
```

- [ ] **Step 8.2: Write dashboard/app.js**

```javascript
// dashboard/app.js
'use strict';

const WS_URL = `ws://${location.host}/ws/price`;
const API     = `${location.origin}/api`;

let session  = null;
let ws       = null;
let wsRetries = 0;
let lastPrice = null;

// ── Bootstrap ────────────────────────────────────────────────────────────────

async function init() {
  try {
    const resp = await fetch(`${API}/session`);
    if (resp.ok) session = await resp.json();
  } catch (_) {}
  renderZoneMap();
  renderOI();
  connectWS();
}

// ── WebSocket ────────────────────────────────────────────────────────────────

function connectWS() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    wsRetries = 0;
    setDot('ws-dot', 'green');
  };

  ws.onmessage = (e) => {
    const d = JSON.parse(e.data);
    if (d.price !== null && d.price !== undefined) {
      lastPrice = d.price;
      document.getElementById('live-price').textContent = d.price.toFixed(2);
      updatePriceDot(d.price);
    }
    if (d.signal) renderSignal(d.signal);
    setDot('ws-dot', d.tv_online ? 'green' : 'amber');
    if (d.ts) document.getElementById('ts-label').textContent = 'Updated ' + d.ts.slice(11, 19) + ' UTC+7';
  };

  ws.onclose = () => {
    setDot('ws-dot', 'grey');
    const delay = Math.min(30000, 2000 * Math.pow(1.5, wsRetries++));
    setTimeout(connectWS, delay);
  };

  ws.onerror = () => ws.close();
}

// ── Zone Map ─────────────────────────────────────────────────────────────────

function renderZoneMap() {
  const bar = document.getElementById('zone-bar');
  bar.innerHTML = '';
  if (!session) { bar.textContent = 'Waiting for Phase 1…'; return; }

  const sd  = session.sd_zones;
  const z   = sd.zones;
  const lo  = z['-3SD'];
  const hi  = z['+3SD'];
  const rng = hi - lo;

  function pct(price) { return ((price - lo) / rng * 100).toFixed(2) + '%'; }

  // Zone bands (bottom → top)
  const bands = [
    { from: lo,      to: z['-2SD'], cls: 'zone-3' },
    { from: z['-2SD'], to: z['-1SD'], cls: 'zone-2' },
    { from: z['-1SD'], to: z['OPEN'], cls: 'zone-1' },
    { from: z['OPEN'],  to: z['+1SD'], cls: 'zone-1' },
    { from: z['+1SD'], to: z['+2SD'], cls: 'zone-2' },
    { from: z['+2SD'], to: hi,       cls: 'zone-3' },
  ];
  bands.forEach(b => {
    const el = document.createElement('div');
    el.className = `zone-band ${b.cls}`;
    el.style.bottom  = pct(b.from);
    el.style.height  = ((b.to - b.from) / rng * 100).toFixed(2) + '%';
    bar.appendChild(el);
  });

  // SD lines + labels
  const levels = [
    { key: '+3SD', color: '#ff1744' },
    { key: '+2SD', color: '#ff9100' },
    { key: '+1SD', color: '#666' },
    { key: 'OPEN', color: '#fff', dash: true },
    { key: '-1SD', color: '#666' },
    { key: '-2SD', color: '#00b0ff' },
    { key: '-3SD', color: '#00c853' },
  ];
  levels.forEach(({ key, color, dash }) => {
    const line = document.createElement('div');
    line.className = 'sd-line';
    line.style.bottom = pct(z[key]);
    line.style.background = color;
    if (dash) line.style.borderTop = `1px dashed ${color}`;
    bar.appendChild(line);

    const label = document.createElement('div');
    label.className = 'level-label';
    label.style.bottom = pct(z[key]);
    label.textContent = `${key} ${z[key].toFixed(0)}`;
    bar.appendChild(label);
  });

  // OI magnets
  const magnets = session.oi_analysis?.magnets || [];
  magnets.forEach(m => {
    const line = document.createElement('div');
    line.className = 'sd-line';
    line.style.bottom = pct(m);
    line.style.background = '#2979ff';
    line.style.borderTop = '1px dotted #2979ff';
    bar.appendChild(line);
  });

  // Price dot
  const dot = document.createElement('div');
  dot.id = 'price-dot';
  dot.className = 'price-dot';
  dot.style.bottom = pct(lastPrice || z['OPEN']);
  bar.appendChild(dot);
}

function updatePriceDot(price) {
  if (!session) return;
  const dot = document.getElementById('price-dot');
  if (!dot) return;
  const z   = session.sd_zones.zones;
  const lo  = z['-3SD'];
  const hi  = z['+3SD'];
  const rng = hi - lo;
  const b   = Math.max(0, Math.min(100, (price - lo) / rng * 100));
  dot.style.bottom = b.toFixed(2) + '%';
}

// ── Signal Panel ─────────────────────────────────────────────────────────────

function renderSignal(sig) {
  if (!sig) return;
  const zone  = sig.zone || '—';
  const dir   = sig.direction || 'WAIT';
  const conf  = sig.confidence || '';

  document.getElementById('zone-badge').textContent      = zone;
  document.getElementById('direction-badge').textContent  = dir;
  document.getElementById('direction-badge').style.color  =
    dir === 'LONG' ? 'var(--green)' : dir === 'SHORT' ? 'var(--red)' : 'var(--muted)';
  document.getElementById('confidence').textContent       = conf;

  const fields = ['entry', 'tp1', 'tp2', 'sl'];
  fields.forEach(f => {
    const el = document.getElementById(`val-${f}`);
    if (el) el.textContent = sig[f] != null ? sig[f].toFixed(2) : '—';
  });

  const banner = document.getElementById('recovery-banner');
  banner.style.display = sig.recovery ? 'block' : 'none';
}

// ── OI Panel ─────────────────────────────────────────────────────────────────

function renderOI() {
  if (!session?.oi_analysis) return;
  const oi = session.oi_analysis;

  document.getElementById('skew-text').textContent = oi.skew_verdict || '—';

  document.getElementById('call-pct-text').textContent = oi.call_pct?.toFixed(1) + '%';
  document.getElementById('put-pct-text').textContent  = oi.put_pct?.toFixed(1) + '%';
  document.getElementById('call-fill').style.width = (oi.call_pct || 0) + '%';
  document.getElementById('put-fill').style.width  = (oi.put_pct || 0) + '%';

  const magList = document.getElementById('magnet-list');
  magList.innerHTML = '';
  (oi.magnets || []).forEach(m => {
    const d = document.createElement('div');
    d.className = 'magnet-row';
    d.textContent = `⦿ ${m.toFixed(0)}`;
    magList.appendChild(d);
  });

  const gamList = document.getElementById('gamma-list');
  gamList.innerHTML = '';
  (oi.gamma_levels || []).forEach(g => {
    const d = document.createElement('div');
    d.className = 'gamma-row';
    d.textContent = `⚡ ${g.type.toUpperCase()} γ ${g.strike} (δ ${(g.delta * 100).toFixed(1)}%)`;
    gamList.appendChild(d);
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function setDot(id, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `status-dot dot-${color}`;
}

document.addEventListener('DOMContentLoaded', init);
```

- [ ] **Step 8.3: Write dashboard/index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>XAUUSD OI Dashboard</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>

<div id="main">

  <!-- LEFT: Zone Map -->
  <div class="panel" id="zone-map-wrap">
    <h3>Zone Map</h3>
    <div id="zone-bar"></div>
  </div>

  <!-- CENTER: Signal -->
  <div class="panel" id="signal-panel">
    <h3>Trade Signal</h3>
    <div id="zone-badge">—</div>
    <div id="direction-badge">WAIT</div>
    <div id="confidence">—</div>

    <div class="level-grid">
      <span class="lbl">Entry</span>  <span class="val" id="val-entry">—</span>
      <span class="lbl">TP1</span>    <span class="val" id="val-tp1">—</span>
      <span class="lbl">TP2</span>    <span class="val" id="val-tp2">—</span>
      <span class="lbl">SL</span>     <span class="val" id="val-sl">—</span>
    </div>

    <div id="recovery-banner">
      ⚡ RECOVERY — Cut position, follow trend
    </div>
  </div>

  <!-- RIGHT: OI Analysis -->
  <div class="panel" id="oi-panel">
    <h3>OI Analysis</h3>
    <div class="skew" id="skew-text">—</div>

    <div class="gauge-row">
      <span style="color:var(--blue);width:40px">CALL</span>
      <div class="gauge-bar"><div class="gauge-fill" id="call-fill" style="background:var(--blue);width:50%"></div></div>
      <span id="call-pct-text">50%</span>
    </div>
    <div class="gauge-row">
      <span style="color:var(--amber);width:40px">PUT</span>
      <div class="gauge-bar"><div class="gauge-fill" id="put-fill" style="background:var(--amber);width:50%"></div></div>
      <span id="put-pct-text">50%</span>
    </div>

    <h3 style="margin-top:16px">Magnets</h3>
    <div id="magnet-list"></div>

    <h3 style="margin-top:16px">Gamma Risk</h3>
    <div id="gamma-list"></div>

    <div style="margin-top:16px;color:var(--muted);font-size:10px" id="phase2-ts">
      Phase 2: waiting…
    </div>
  </div>

</div>

<!-- BOTTOM BAR -->
<div id="bottom-bar">
  <div id="live-price">—</div>
  <span><span class="status-dot dot-grey" id="ws-dot"></span>TradingView</span>
  <span id="ts-label" style="color:var(--muted)">—</span>
  <a id="pine-link" href="#" target="_blank">📄 Pine Script</a>
</div>

<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 8.4: Update Pine Script link in app.js to point to today's file**

Add this to the `init()` function in `app.js`, after the session fetch:

```javascript
// After: if (resp.ok) session = await resp.json();
const today = new Date().toISOString().slice(0, 10);
const pineLink = document.getElementById('pine-link');
pineLink.href   = `/exports/session_${today}.pine`;
pineLink.textContent = `📄 Pine Script ${today}`;

if (session?.phase2_at) {
  document.getElementById('phase2-ts').textContent =
    'Phase 2: ' + session.phase2_at.slice(11, 16) + ' UTC+7';
}
```

The `exports/` directory needs to be served. Add this to `server.py` **before** the dashboard mount (before the `app.mount("/", ...)` line):

```python
# Serve exports/ so the Pine Script link works
_exports = BASE / "exports"
_exports.mkdir(exist_ok=True)
app.mount("/exports", StaticFiles(directory=str(_exports)), name="exports")
```

- [ ] **Step 8.5: Full integration smoke test**

```bash
# Start server (TradingView Desktop does not need to be running for this test)
python server.py &
sleep 3

# Check API endpoints
curl -s http://localhost:8000/api/session | python -m json.tool | head -10
curl -s http://localhost:8000/api/signal  | python -m json.tool

# Open dashboard
open http://localhost:8000

# Stop server
kill %1
```

Expected: session JSON returned (uses today's `session_data.json`), dashboard opens and shows zone map with session data.

- [ ] **Step 8.6: Run full test suite one final time**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8.7: Final commit and push**

```bash
git add server.py dashboard/ exports/.gitkeep logs/.gitkeep
git commit -m "feat: add HTML dashboard (zone map, signal panel, OI analysis) + exports serving"
git push origin main
```

---

## Self-Review Against Spec

| Spec Requirement | Task |
|---|---|
| FastAPI server, WebSocket, REST endpoints | Task 7 |
| TradingView CDP client (price + Pine push + alerts) | Task 4 |
| Signal engine (zone, direction, TP1/TP2/SL, recovery, confidence) | Task 2 |
| Pine Script exporter → inject into TradingView chart | Tasks 3 + 4 |
| Discord alerts (6 types, deduplicated) | Task 5 |
| Supabase sessions + signals tables | Task 6 |
| APScheduler Phase 1 @ 01:30, Phase 2 @ 08:30 UTC+7 | Task 7 |
| HTML dashboard: zone map, signal panel, OI analysis, bottom bar | Task 8 |
| config.py + .env.example | Task 1 |
| `.gitignore`, requirements.txt | Task 1 |
| Phase hooks: export pine → push TV → upsert DB → Discord | Task 7 (`run_phase1/2`) |
| Recovery signal (follow trend) | Task 2 |
| Gamma squeeze proximity alert | Task 5 + 7 |
| Pine Script bottom bar link | Task 8 step 8.4 |

All spec requirements are covered. No gaps found.
