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
