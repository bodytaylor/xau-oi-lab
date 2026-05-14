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

_last_zone: str = ""                               # dedup tracker for zone entry alerts
_main_loop: asyncio.AbstractEventLoop | None = None  # set in lifespan, used by sync phase runners


def _broadcast_sync(data: dict):
    """Broadcast a WebSocket message from a synchronous context (APScheduler thread)."""
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast(data), _main_loop)
        log.info(f"Broadcast → {data}")


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
    export_pine(session)
    subprocess.run([sys.executable, str(BASE / "push_pine.py"), "--latest"])
    tv_client.create_sd_alerts(session["sd_zones"])
    db_sync.upsert_session(session)
    alerts.phase1_complete(session)
    _broadcast_sync({"type": "session_updated", "phase": 1})
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
    export_pine(session)
    subprocess.run([sys.executable, str(BASE / "push_pine.py"), "--latest"])
    db_sync.upsert_session(session)
    alerts.phase2_complete(session)
    _broadcast_sync({"type": "session_updated", "phase": 2})
    log.info("Phase 2 complete ✓")


# ── FastAPI app + lifespan ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_running_loop()
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

_exports = BASE / "exports"
_exports.mkdir(exist_ok=True)
app.mount("/exports", StaticFiles(directory=str(_exports)), name="exports")

_dashboard = BASE / "dashboard"
if _dashboard.exists():
    app.mount("/", StaticFiles(directory=str(_dashboard), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000, log_level="info")
