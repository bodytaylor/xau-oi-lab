-- XAUUSD OI Framework — Initial Schema
-- Run this in the Supabase SQL editor before starting the server.
-- Dashboard: https://supabase.com/dashboard/project/<your-project>/sql

-- Daily trading sessions (one row per trading day)
create table if not exists sessions (
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

-- Fired trade signals
create table if not exists signals (
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
  outcome       text  -- filled in manually: WIN / LOSS / SCRATCH
);
