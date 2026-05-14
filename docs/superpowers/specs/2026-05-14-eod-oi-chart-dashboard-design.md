# EOD OI Chart Dashboard Feature

**Date:** 2026-05-14
**Status:** Approved

## Overview

Add a full-width EOD OI chart row below the existing 3-panel dashboard layout. The chart renders per-strike call/put open interest as grouped vertical bars, overlays the implied volatility curve, marks the current futures price, and shows a live countdown to option series expiry that persists across page refreshes.

---

## Layout

The `body` grid changes from `1fr auto` to `auto 1fr auto`:

```
┌────────────────────────────────────────────────────────┐
│  Zone Map (180px)  │  Trade Signal  │  OI Analysis     │  ← 180px row (unchanged)
├────────────────────────────────────────────────────────┤
│                 EOD OI Chart panel                      │  ← new: grows to fit 600×600 canvas
├────────────────────────────────────────────────────────┤
│                    Bottom bar                           │  ← 44px (unchanged)
└────────────────────────────────────────────────────────┘
```

The chart panel contains:
- **Header row**: series name · type tag · Extracted datetime · Expires datetime · countdown chip
- **Canvas**: fixed 600×600px, `responsive: false`
- **Legend row**: Calls · Puts · IV% · Current Price · ★ magnet note

---

## Chart (Chart.js — CDN)

**Library:** `chart.js@4.4.0` via jsDelivr CDN (one `<script>` tag in `index.html`).

**Datasets:**

| Dataset | Type | Y-axis | Colour |
|---------|------|--------|--------|
| Calls | bar | left (`yContracts`) | `rgba(0,200,83,0.65)`, magnet: `rgba(0,230,118,0.9)` |
| Puts | bar | left (`yContracts`) | `rgba(255,23,68,0.65)`, magnet: `rgba(255,82,82,0.9)` |
| IV % | line | right (`yIV`) | `#e6b800`, tension 0.4, smooth |

**Bar sizing:** `barPercentage: 0.40`, `categoryPercentage: 0.60` — thin bars, call+put pair grouped tightly per strike.

**Magnet strikes** (top-2 by total OI): brighter colour + `#e6b800` border, label appended with `★`.

**Y-axes:**
- Left (`yContracts`): "Contracts (OI)", ticks formatted as `1.2k`.
- Right (`yIV`): "Implied Volatility %", amber ticks, `min: 24 max: 38` (auto-fit in production).

**Current price line:** Custom Canvas plugin (`afterDraw`) draws:
1. White dashed vertical line through the full plot area at the interpolated x-position of `session.futures_price`.
2. A frosted badge (`▼ 4692.50`) on the x-axis immediately below the chart bottom, above the tick labels.

**Tooltip:** `mode: 'index'`, shows strike label + contracts for calls/puts + IV% on hover.

**Data source:** `session.oi_data` (array of `{strike, volume, type, is_magnet}`) + `session.vol_skew_analysis` strike/IV points.

---

## Expiry Countdown

### Computation (frontend JS)

```js
// Computed once from session data — same result on every refresh
const expiryMs = new Date(session.locked_at).getTime() + session.dte * 86400 * 1000;
```

`session.locked_at` is the Phase 1 extraction ISO timestamp. `session.dte` is the DTE at extraction time. The result is a fixed Unix millisecond timestamp; refreshing always yields the same value, so the clock continues from where it left off.

### Display

| State | Countdown chip (header) | Bottom bar |
|-------|------------------------|------------|
| Live | `⏱ 08:38:44` (green chip) | `⏱ GC 06 May 2026 expires in 08:38:44` |
| Expired | `⚠ EXPIRED 00:43:17 ago` (red, pulsing) | `⚠ GC 06 May 2026 expired 00:43:17 ago` |

`setInterval(tick, 1000)` drives both elements. On expiry: chip and bar switch to red pulsing class; chart area dims to `opacity: 0.4`; panel border changes to `rgba(255,23,68,0.5)`.

---

## Metadata Display

Chart header (left → right):
```
GC · 06 May 2026  ·  EOD OI  |  Extracted  14 May 2026 08:51 local  |  Expires  14 May 2026 17:06 local    [⏱ 08:38:44]
```

- **Series name** (`session.exp_series_name`): e.g. `"06 May 2026"` — displayed as `GC · <name>`.
- **Extracted** datetime: `new Date(session.locked_at)` formatted to user's local time.
- **Expires** datetime: `new Date(expiryMs)` formatted to user's local time.
- Both datetimes formatted with `toLocaleString` (day/month/year hour:minute, 24h).

---

## Backend Change — `exp_series_name`

`oi_collector.py` → `_select_expiration()`: after clicking the matched expiration link, read its inner text and store it in `session_data.json`:

```python
session["exp_series_name"] = label   # e.g. "06 May 2026"
```

Fallback: if the field is missing, `app.js` falls back to `session.date` (`"2026-05-14"`).

---

## Files Changed

| File | Change |
|------|--------|
| `oi_collector.py` | Store `exp_series_name` after selecting expiration |
| `dashboard/index.html` | Add Chart.js CDN; add `#oi-chart-panel` section; add countdown chip; add expiry bar item |
| `dashboard/style.css` | Styles for `#oi-chart-panel`, `.countdown`, `.expiry-bar`, expired state |
| `dashboard/app.js` | `renderOIChart()`, `startExpiryCountdown()`, `tick()`, price-line Canvas plugin |

---

## Error / Missing Data States

- **No `oi_data`**: chart panel shows "Waiting for Phase 2…" placeholder text, no canvas rendered.
- **No `vol_skew_analysis`**: IV line dataset omitted; right Y-axis hidden.
- **No `exp_series_name`**: falls back to `session.date`.
- **`futures_price` out of strike range**: price line not drawn; no badge shown.
