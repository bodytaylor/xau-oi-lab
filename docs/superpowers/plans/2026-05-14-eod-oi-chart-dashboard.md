# EOD OI Chart Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full-width EOD OI chart row below the 3-panel dashboard showing per-strike Call/Put OI bars, an IV curve, a live price line, and a live countdown to option series expiry.

**Architecture:** Backend stores two new fields (`exp_series_name`, `vol_curve_points`) in `session_data.json` during Phase 2. The frontend reads them from `/api/session`, renders a fixed 600×600 Chart.js grouped-bar + line chart, and runs a `setInterval` countdown derived from `locked_at + dte × 86400s` — the same computation every refresh, so the clock continues from where it left off.

**Tech Stack:** Python/Playwright (backend), Chart.js 4.4.0 via CDN (chart), vanilla JS (countdown, price-line custom plugin), CSS Grid (layout)

---

## File Structure

| File | Change |
|------|--------|
| `oi_collector.py` | `_select_expiration()` returns label; `run()` stores `exp_series_name` + `vol_curve_points` |
| `tests/conftest.py` | Add `exp_series_name` and `vol_curve_points` to `SAMPLE_SESSION` |
| `tests/test_oi_collector.py` | New — unit tests for session key logic |
| `dashboard/style.css` | Body grid row change; `#oi-chart-panel` styles; countdown chip; expired state |
| `dashboard/index.html` | Chart.js CDN; `#oi-chart-panel` section; `#expiry-bar` in bottom bar |
| `dashboard/app.js` | `buildChartData()`, `renderOIChart()`, `startExpiryCountdown()`, `tick()`, price plugin; wire into `init()` and `ws.onmessage` |

---

## Task 1: Backend — `_select_expiration()` returns series name

**Files:**
- Modify: `oi_collector.py:188-237`

- [ ] **Step 1: Add `selected_label` tracker and return statement**

  Open `oi_collector.py`. Replace the entire `_select_expiration` function:

  ```python
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
              log.info(f"Expiration matched by date '{target}': {found} ✓")
              page.wait_for_timeout(2000)
              selected_label = found
          else:
              log.warning(f"No expiration found for '{target}' — falling back to first link")
              first_exp = frame.locator("#ctl00_ucSelector_pnlExpirations a").first
              if first_exp.count() > 0:
                  label = first_exp.inner_text(timeout=3000).strip()
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
  ```

- [ ] **Step 2: Capture return value at the call site**

  In `oi_collector.py` around line 104, change:
  ```python
      # Select nearest expiration (front month = default)
      _select_expiration(frame, page)
  ```
  to:
  ```python
      # Select nearest expiration (front month = default)
      series_name = _select_expiration(frame, page)
  ```

- [ ] **Step 3: Store `exp_series_name` in the session update**

  Find the `session.update({` block (around line 928). Add `exp_series_name` as the first key:
  ```python
      session.update({
          "exp_series_name":   series_name or session.get("date", ""),
          "oi_data":          oi_rows,
          "oi_analysis":      analysis,
          "vol_skew_analysis": vol_skew,
          "phase2_complete":  True,
          "phase2_at":        utc7_now().isoformat(),
      })
  ```

---

## Task 2: Backend — store `vol_curve_points` in session

**Files:**
- Modify: `oi_collector.py` (same `session.update` block as Task 1)

`vol_pts` is the list of `{strike: float, iv: float, ...}` dicts returned by `_extract_vol_curve()` and filtered at line 925. These are not currently persisted — only the analysis summary is. The chart needs the raw per-strike IV values.

- [ ] **Step 1: Add `vol_curve_points` to the session update**

  In the same `session.update({...})` block modified in Task 1, add:
  ```python
      session.update({
          "exp_series_name":   series_name or session.get("date", ""),
          "vol_curve_points":  [{"strike": p["strike"], "iv": p["iv"]} for p in vol_pts],
          "oi_data":          oi_rows,
          "oi_analysis":      analysis,
          "vol_skew_analysis": vol_skew,
          "phase2_complete":  True,
          "phase2_at":        utc7_now().isoformat(),
      })
  ```

  `vol_pts` is already in scope at this point (defined at line 925).

---

## Task 3: Tests — conftest + unit tests for new session keys

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_oi_collector.py`

- [ ] **Step 1: Add new fields to `SAMPLE_SESSION` in conftest.py**

  In `tests/conftest.py`, add to the `SAMPLE_SESSION` dict (after `"vol_skew_analysis"`):
  ```python
      "exp_series_name": "06 May 2026",
      "vol_curve_points": [
          {"strike": 4550.0, "iv": 32.1},
          {"strike": 4600.0, "iv": 30.5},
          {"strike": 4650.0, "iv": 28.8},
          {"strike": 4700.0, "iv": 27.2},
          {"strike": 4750.0, "iv": 28.1},
          {"strike": 4800.0, "iv": 30.4},
      ],
  ```

- [ ] **Step 2: Write failing tests**

  Create `tests/test_oi_collector.py`:
  ```python
  # tests/test_oi_collector.py
  """Unit tests for session key logic in oi_collector (no browser required)."""


  def _apply_session_update(session: dict, series_name, vol_pts: list) -> dict:
      """Mirrors the session.update() logic in oi_collector.run()."""
      session.update({
          "exp_series_name":  series_name or session.get("date", ""),
          "vol_curve_points": [{"strike": p["strike"], "iv": p["iv"]} for p in vol_pts],
      })
      return session


  def test_exp_series_name_stored_when_found():
      session = {"date": "2026-05-14"}
      result = _apply_session_update(session, "06 May 2026", [])
      assert result["exp_series_name"] == "06 May 2026"


  def test_exp_series_name_falls_back_to_date_when_none():
      session = {"date": "2026-05-14"}
      result = _apply_session_update(session, None, [])
      assert result["exp_series_name"] == "2026-05-14"


  def test_exp_series_name_falls_back_to_empty_when_no_date():
      session = {}
      result = _apply_session_update(session, None, [])
      assert result["exp_series_name"] == ""


  def test_vol_curve_points_stored():
      vol_pts = [
          {"strike": 4700.0, "iv": 27.2, "extra": "ignored"},
          {"strike": 4750.0, "iv": 28.1, "extra": "ignored"},
      ]
      session = {"date": "2026-05-14"}
      result = _apply_session_update(session, "06 May 2026", vol_pts)
      assert result["vol_curve_points"] == [
          {"strike": 4700.0, "iv": 27.2},
          {"strike": 4750.0, "iv": 28.1},
      ]


  def test_vol_curve_points_empty_when_no_vol_pts():
      session = {"date": "2026-05-14"}
      result = _apply_session_update(session, "06 May 2026", [])
      assert result["vol_curve_points"] == []
  ```

- [ ] **Step 3: Run tests — expect PASS (pure logic, no browser)**

  ```bash
  pytest tests/test_oi_collector.py -v
  ```

  Expected output:
  ```
  tests/test_oi_collector.py::test_exp_series_name_stored_when_found PASSED
  tests/test_oi_collector.py::test_exp_series_name_falls_back_to_date_when_none PASSED
  tests/test_oi_collector.py::test_exp_series_name_falls_back_to_empty_when_no_date PASSED
  tests/test_oi_collector.py::test_vol_curve_points_stored PASSED
  tests/test_oi_collector.py::test_vol_curve_points_empty_when_no_vol_pts PASSED
  ```

- [ ] **Step 4: Commit backend changes**

  ```bash
  git add oi_collector.py tests/conftest.py tests/test_oi_collector.py
  git commit -m "feat: store exp_series_name and vol_curve_points in session_data"
  ```

---

## Task 4: CSS — chart panel, countdown, expired state

**Files:**
- Modify: `dashboard/style.css`

- [ ] **Step 1: Change body grid to accommodate chart panel row**

  In `style.css`, find the `body` rule and change `grid-template-rows` and `overflow`:
  ```css
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'SF Mono', 'Consolas', monospace;
    font-size: 13px;
    min-height: 100vh;
    display: grid;
    grid-template-rows: 1fr auto auto;
    overflow: auto;
  }
  ```

  Also add `min-height` to `#main` so the top panels don't collapse when the chart panel is large:
  ```css
  #main {
    display: grid;
    grid-template-columns: 180px 1fr 220px;
    gap: 1px;
    background: var(--border);
    overflow: hidden;
    min-height: 300px;
  }
  ```

- [ ] **Step 2: Add all chart panel styles at the end of style.css**

  Append to `style.css`:
  ```css
  /* ── EOD OI Chart panel ──────────────────────────────── */
  #oi-chart-panel {
    background: var(--panel);
    border-top: 1px solid var(--border);
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
  }

  #oi-chart-panel.expired {
    border-top: 2px solid rgba(255,23,68,0.5);
  }

  #oi-chart-panel.expired #oi-canvas {
    opacity: 0.4;
  }

  #oi-chart-header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
    margin-bottom: 12px;
  }

  .chart-series-name {
    font-size: 15px;
    font-weight: bold;
    color: var(--text);
  }

  #oi-chart-panel.expired .chart-series-name {
    color: var(--red);
  }

  .chart-type-tag {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 1px;
  }

  .chart-meta-sep { color: var(--border); }

  .chart-meta { font-size: 10px; color: #777; }
  .chart-meta-k { color: var(--muted); }

  #chart-expires.expired {
    color: var(--red);
    text-decoration: line-through;
  }

  /* Countdown chip in chart header */
  .countdown-chip {
    margin-left: auto;
    border-radius: 4px;
    padding: 4px 14px;
    font-size: 14px;
    font-weight: bold;
    font-variant-numeric: tabular-nums;
    min-width: 160px;
    text-align: center;
  }

  .countdown-chip.live {
    background: rgba(0,200,83,.1);
    border: 1px solid rgba(0,200,83,.35);
    color: var(--green);
  }

  .countdown-chip.expired {
    background: rgba(255,23,68,.15);
    border: 1px solid rgba(255,23,68,.5);
    color: var(--red);
    animation: pulse 1.5s infinite;
  }

  /* Fixed 600×600 canvas */
  #oi-canvas {
    width: 600px;
    height: 600px;
    flex-shrink: 0;
  }

  /* Legend row */
  #oi-chart-legend {
    display: flex;
    gap: 16px;
    margin-top: 10px;
    font-size: 9px;
    color: var(--muted);
    align-items: center;
    width: 600px;
  }

  .leg { display: flex; align-items: center; gap: 5px; }
  .leg-bar  { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
  .leg-line { width: 16px; height: 2px; flex-shrink: 0; }
  .leg-dashed { width: 16px; border-top: 2px dashed rgba(255,255,255,0.5); flex-shrink: 0; }

  /* Expiry bar in bottom bar */
  #expiry-bar {
    font-size: 12px;
    font-weight: bold;
    font-variant-numeric: tabular-nums;
  }

  #expiry-bar.live    { color: var(--green); }
  #expiry-bar.expired { color: var(--red); animation: pulse 1.5s infinite; }
  ```

---

## Task 5: HTML — chart panel structure

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 1: Add Chart.js CDN script before `app.js`**

  In `index.html`, find:
  ```html
  <script src="app.js"></script>
  ```
  Replace with:
  ```html
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="app.js"></script>
  ```

- [ ] **Step 2: Add `#oi-chart-panel` between `#main` and `#bottom-bar`**

  In `index.html`, find the closing `</div>` of `#main` and the `<!-- BOTTOM BAR -->` comment. Insert between them:
  ```html
  <!-- EOD OI CHART -->
  <div id="oi-chart-panel">
    <div id="oi-chart-header">
      <span id="chart-series-name" class="chart-series-name">—</span>
      <span class="chart-type-tag">EOD OI</span>
      <span class="chart-meta-sep">|</span>
      <span class="chart-meta"><span class="chart-meta-k">Extracted</span>&nbsp;<span id="chart-extracted">—</span></span>
      <span class="chart-meta-sep">|</span>
      <span class="chart-meta"><span class="chart-meta-k">Expires</span>&nbsp;<span id="chart-expires">—</span></span>
      <div id="countdown-chip" class="countdown-chip live">⏱ —</div>
    </div>
    <canvas id="oi-canvas" width="600" height="600"></canvas>
    <div id="oi-chart-legend">
      <span class="leg"><span class="leg-bar" style="background:rgba(0,200,83,0.8)"></span>Calls</span>
      <span class="leg"><span class="leg-bar" style="background:rgba(255,23,68,0.8)"></span>Puts</span>
      <span class="leg"><span class="leg-line" style="background:#e6b800"></span>IV %</span>
      <span class="leg"><span class="leg-dashed"></span>Current Price</span>
    </div>
  </div>
  ```

- [ ] **Step 3: Add `#expiry-bar` to the bottom bar**

  In `index.html`, find the bottom bar. Add `#expiry-bar` before `#pine-link`:
  ```html
  <!-- BOTTOM BAR -->
  <div id="bottom-bar">
    <div id="live-price">—</div>
    <span><span class="status-dot dot-grey" id="ws-dot"></span>TradingView</span>
    <span id="ts-label" style="color:var(--muted)">—</span>
    <span id="expiry-bar" class="expiry-bar live">—</span>
    <a id="pine-link" href="#" target="_blank">📄 Pine Script</a>
  </div>
  ```

- [ ] **Step 4: Visual check — open dashboard, verify chart panel placeholder appears**

  Start the server: `python server.py`
  Open `http://localhost:8000` in a browser.
  Expected: a new row below the 3 panels with `—` placeholders in the header and an empty canvas area. Bottom bar shows `—` for expiry. No JS errors in console.

---

## Task 6: JS — `buildChartData()` aggregation

**Files:**
- Modify: `dashboard/app.js`

- [ ] **Step 1: Add module-level chart state variables after the existing `let` declarations**

  In `app.js`, after:
  ```js
  let lastPrice = null;
  ```
  Add:
  ```js
  let oiChart    = null;   // Chart.js instance — used to refresh price line on WS price
  let chartPrice = null;   // current price for the price-line plugin
  let expiryMs   = null;   // option series expiry in Unix ms, computed once from session
  ```

- [ ] **Step 2: Add `buildChartData()` — pure aggregation function**

  Add this function before `init()`:
  ```js
  // ── OI Chart helpers ───────────────────────────────────────────────────────

  /**
   * Aggregates raw oi_data rows into chart-ready arrays.
   * @param {Array}  oiData         - session.oi_data
   * @param {Array}  volCurvePoints - session.vol_curve_points [{strike, iv}]
   * @returns {{ strikes, callVols, putVols, magnets, ivCurve }}
   */
  function buildChartData(oiData, volCurvePoints) {
    const map = {};
    for (const row of oiData) {
      if (!map[row.strike]) map[row.strike] = { call: 0, put: 0, isMagnet: false };
      if (row.type === 'call') map[row.strike].call += row.volume;
      else                      map[row.strike].put  += row.volume;
      if (row.is_magnet) map[row.strike].isMagnet = true;
    }

    const strikes  = Object.keys(map).map(Number).sort((a, b) => a - b);
    const callVols = strikes.map(s => map[s].call);
    const putVols  = strikes.map(s => map[s].put);
    const magnets  = strikes.filter(s => map[s].isMagnet);

    // Per-strike IV from vol_curve_points
    const ivMap = {};
    for (const p of (volCurvePoints || [])) ivMap[p.strike] = p.iv;
    const ivCurve = strikes.map(s => ivMap[s] ?? null);

    return { strikes, callVols, putVols, magnets, ivCurve };
  }
  ```

---

## Task 7: JS — `renderOIChart()` with Chart.js and price-line plugin

**Files:**
- Modify: `dashboard/app.js`

- [ ] **Step 1: Add the price-line Canvas plugin**

  Add immediately after `buildChartData()`:
  ```js
  /**
   * Custom Chart.js plugin: draws a dashed vertical price line through the plot
   * area and a frosted badge just below the x-axis showing the current price.
   */
  const priceLinePlugin = {
    id: 'priceLinePlugin',
    afterDraw(chart) {
      if (chartPrice === null) return;
      const { strikes } = chart._oiMeta || {};
      if (!strikes || strikes.length < 2) return;

      // Interpolate price to a fractional x-index between two strikes
      let xPos = null;
      for (let i = 0; i < strikes.length - 1; i++) {
        if (chartPrice >= strikes[i] && chartPrice <= strikes[i + 1]) {
          xPos = i + (chartPrice - strikes[i]) / (strikes[i + 1] - strikes[i]);
          break;
        }
      }
      if (xPos === null) return;

      const { ctx, chartArea: ca, scales } = chart;
      const px = scales.x.getPixelForValue(xPos);

      ctx.save();

      // Dashed vertical line
      ctx.beginPath();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = 'rgba(255,255,255,0.6)';
      ctx.lineWidth   = 1.5;
      ctx.moveTo(px, ca.top);
      ctx.lineTo(px, ca.bottom);
      ctx.stroke();
      ctx.setLineDash([]);

      // Price badge below chart, above tick labels
      const label = `\u25bc ${chartPrice.toFixed(2)}`;
      ctx.font     = 'bold 10px SF Mono, Consolas, monospace';
      const tw     = ctx.measureText(label).width;
      const bw     = tw + 12;
      const bh     = 18;
      const bx     = px - bw / 2;
      const by     = ca.bottom + 4;

      ctx.fillStyle = 'rgba(255,255,255,0.15)';
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(bx, by, bw, bh, 3);
      else ctx.rect(bx, by, bw, bh);
      ctx.fill();

      ctx.strokeStyle = 'rgba(255,255,255,0.5)';
      ctx.lineWidth   = 1;
      ctx.stroke();

      ctx.fillStyle    = '#ffffff';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, px, by + bh / 2);

      ctx.restore();
    },
  };
  ```

- [ ] **Step 2: Add `renderOIChart()`**

  Add immediately after `priceLinePlugin`:
  ```js
  function renderOIChart() {
    const panel = document.getElementById('oi-chart-panel');
    if (!session?.oi_data?.length) {
      // No Phase 2 data yet — show placeholder text, hide canvas
      document.getElementById('oi-canvas').style.display = 'none';
      const ph = document.createElement('div');
      ph.style.cssText = 'color:var(--muted);font-size:12px;padding:20px 0';
      ph.textContent   = 'Waiting for Phase 2…';
      panel.insertBefore(ph, document.getElementById('oi-chart-legend'));
      return;
    }

    const { strikes, callVols, putVols, magnets, ivCurve } =
      buildChartData(session.oi_data, session.vol_curve_points);

    // Set initial price from session; WS updates will refresh via oiChart.update('none')
    chartPrice = lastPrice || session.open_price || null;

    // Bar colours — magnets are brighter + gold border
    const callBg  = strikes.map(s => magnets.includes(s) ? 'rgba(0,230,118,0.9)'  : 'rgba(0,200,83,0.65)');
    const putBg   = strikes.map(s => magnets.includes(s) ? 'rgba(255,82,82,0.9)'  : 'rgba(255,23,68,0.65)');
    const brdCol  = strikes.map(s => magnets.includes(s) ? '#e6b800' : 'transparent');
    const brdW    = strikes.map(s => magnets.includes(s) ? 2 : 0);
    const labels  = strikes.map(s => magnets.includes(s) ? `${s}\u2605` : String(s));

    // IV axis bounds from actual data
    const ivVals = ivCurve.filter(v => v !== null);
    const ivMin  = ivVals.length ? Math.floor(Math.min(...ivVals)) - 2 : 20;
    const ivMax  = ivVals.length ? Math.ceil(Math.max(...ivVals))  + 2 : 40;

    const canvas = document.getElementById('oi-canvas');
    const ctx    = canvas.getContext('2d');

    oiChart = new Chart(ctx, {
      plugins: [priceLinePlugin],
      data: {
        labels,
        datasets: [
          {
            type: 'bar', label: 'Calls', data: callVols,
            backgroundColor: callBg, borderColor: brdCol, borderWidth: brdW,
            yAxisID: 'yContracts', barPercentage: 0.40, categoryPercentage: 0.60, order: 2,
          },
          {
            type: 'bar', label: 'Puts', data: putVols,
            backgroundColor: putBg, borderColor: brdCol, borderWidth: brdW,
            yAxisID: 'yContracts', barPercentage: 0.40, categoryPercentage: 0.60, order: 2,
          },
          {
            type: 'line', label: 'IV %', data: ivCurve,
            borderColor: '#e6b800', backgroundColor: 'rgba(230,184,0,0.05)',
            pointBackgroundColor: '#e6b800', pointBorderColor: '#0d0d0d', pointBorderWidth: 1,
            pointRadius: 4, pointHoverRadius: 6, borderWidth: 2, tension: 0.4, fill: false,
            yAxisID: 'yIV', order: 1,
            // hide IV dataset entirely if no data
            hidden: ivVals.length === 0,
          },
        ],
      },
      options: {
        responsive: false,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        layout: { padding: { bottom: 26 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1a1a2e', borderColor: '#2a2a4a', borderWidth: 1,
            titleColor: '#aaa', bodyColor: '#e0e0e0', padding: 10,
            callbacks: {
              title:  items => `Strike: ${items[0].label}`,
              label:  item  => item.dataset.label === 'IV %'
                ? `  IV:    ${item.raw.toFixed(1)}%`
                : `  ${item.dataset.label.padEnd(5)}: ${(item.raw || 0).toLocaleString()} contracts`,
            },
          },
        },
        scales: {
          x: {
            grid:  { color: 'rgba(255,255,255,0.03)' },
            ticks: { color: '#555', font: { family: 'SF Mono, Consolas, monospace', size: 10 }, maxRotation: 0 },
          },
          yContracts: {
            type: 'linear', position: 'left',
            title: { display: true, text: 'Contracts (OI)', color: '#666', font: { size: 10, family: 'SF Mono, Consolas, monospace' } },
            grid:  { color: 'rgba(255,255,255,0.04)' },
            ticks: { color: '#666', font: { size: 10, family: 'SF Mono, Consolas, monospace' },
                     callback: v => v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v },
          },
          yIV: {
            type: 'linear', position: 'right',
            display: ivVals.length > 0,
            title: { display: true, text: 'Implied Volatility %', color: '#e6b800', font: { size: 10, family: 'SF Mono, Consolas, monospace' } },
            grid:  { display: false },
            ticks: { color: '#e6b800', font: { size: 10, family: 'SF Mono, Consolas, monospace' },
                     callback: v => v.toFixed(1) + '%' },
            min: ivMin, max: ivMax,
          },
        },
      },
    });

    // Attach strikes metadata for the price-line plugin to read
    oiChart._oiMeta = { strikes };
  }
  ```

---

## Task 8: JS — `startExpiryCountdown()` live clock

**Files:**
- Modify: `dashboard/app.js`

- [ ] **Step 1: Add helper `padZ` and `fmtDur`**

  Add before `startExpiryCountdown()`:
  ```js
  function padZ(n) { return String(Math.floor(Math.abs(n))).padStart(2, '0'); }
  function fmtDur(secs) {
    const a = Math.abs(secs);
    return `${padZ(a / 3600)}:${padZ((a % 3600) / 60)}:${padZ(a % 60)}`;
  }
  ```

- [ ] **Step 2: Add `tick()` — updates both chips on every second**

  ```js
  function tick() {
    if (expiryMs === null) return;
    const rem  = Math.floor((expiryMs - Date.now()) / 1000);
    const chip = document.getElementById('countdown-chip');
    const bar  = document.getElementById('expiry-bar');
    const panel = document.getElementById('oi-chart-panel');
    const expiresEl = document.getElementById('chart-expires');
    const seriesTag = session?.exp_series_name
      ? `GC ${session.exp_series_name}`
      : 'Series';
    const t = fmtDur(rem);

    if (rem > 0) {
      chip.textContent  = `\u23f1 ${t}`;
      chip.className    = 'countdown-chip live';
      bar.textContent   = `\u23f1 ${seriesTag} expires in ${t}`;
      bar.className     = 'expiry-bar live';
      panel.classList.remove('expired');
      if (expiresEl) expiresEl.classList.remove('expired');
    } else {
      chip.textContent  = `\u26a0 EXPIRED  ${t} ago`;
      chip.className    = 'countdown-chip expired';
      bar.textContent   = `\u26a0 ${seriesTag} expired ${t} ago`;
      bar.className     = 'expiry-bar expired';
      panel.classList.add('expired');
      if (expiresEl) expiresEl.classList.add('expired');
    }
  }
  ```

- [ ] **Step 3: Add `startExpiryCountdown()`**

  ```js
  function startExpiryCountdown() {
    if (!session?.locked_at || !session?.dte) return;

    // Compute once — same result on every page refresh
    const lockedAtMs = new Date(session.locked_at).getTime();
    expiryMs         = lockedAtMs + session.dte * 86400 * 1000;

    // Populate static metadata labels (user's local time)
    const fmtLocal = ms => new Date(ms).toLocaleString([], {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });

    const seriesEl = document.getElementById('chart-series-name');
    const extractEl = document.getElementById('chart-extracted');
    const expiresEl  = document.getElementById('chart-expires');

    if (seriesEl) {
      seriesEl.textContent = session.exp_series_name
        ? `GC \u00b7 ${session.exp_series_name}`
        : `GC \u00b7 ${session.date}`;
    }
    if (extractEl) extractEl.textContent = fmtLocal(lockedAtMs);
    if (expiresEl) expiresEl.textContent  = fmtLocal(expiryMs);

    tick();                        // immediate first render
    setInterval(tick, 1000);       // live tick every second
  }
  ```

---

## Task 9: JS — wire into `init()` and refresh price line on WS

**Files:**
- Modify: `dashboard/app.js`

- [ ] **Step 1: Call `renderOIChart()` and `startExpiryCountdown()` from `init()`**

  In `init()`, add the two calls after `renderOI()`:
  ```js
  async function init() {
    try {
      const resp = await fetch(`${API}/session`);
      if (resp.ok) session = await resp.json();
    } catch (_) {}

    // Pine Script link
    const today = new Date().toISOString().slice(0, 10);
    const pineLink = document.getElementById('pine-link');
    pineLink.href        = `/exports/session_${today}.pine`;
    pineLink.textContent = `\ud83d\udcc4 Pine Script ${today}`;

    if (session?.phase2_at) {
      document.getElementById('phase2-ts').textContent =
        'Phase 2: ' + session.phase2_at.slice(11, 16) + ' UTC+7';
    }

    renderZoneMap();
    renderOI();
    renderOIChart();           // ← new
    startExpiryCountdown();    // ← new
    connectWS();
  }
  ```

- [ ] **Step 2: Update `chartPrice` and refresh chart on WebSocket price messages**

  In `ws.onmessage`, add two lines inside the `if (d.price !== null && d.price !== undefined)` block:
  ```js
    ws.onmessage = (e) => {
      const d = JSON.parse(e.data);
      if (d.price !== null && d.price !== undefined) {
        lastPrice  = d.price;
        chartPrice = d.price;                      // ← new: update price for plugin
        document.getElementById('live-price').textContent = d.price.toFixed(2);
        updatePriceDot(d.price);
        if (oiChart) oiChart.update('none');       // ← new: redraw price line, no animation
      }
      if (d.signal) renderSignal(d.signal);
      setDot('ws-dot', d.tv_online ? 'green' : 'amber');
      if (d.ts) document.getElementById('ts-label').textContent =
        'Updated ' + d.ts.slice(11, 19) + ' UTC+7';
    };
  ```

- [ ] **Step 3: Run full test suite to check no regressions**

  ```bash
  pytest tests/ -v --ignore=tests/test_tradingview_client.py
  ```

  Expected: all tests pass (tradingview_client tests require a live browser so are excluded).

- [ ] **Step 4: Visual integration check**

  1. Ensure `session_data.json` has `oi_data`, `vol_curve_points`, `exp_series_name`, `locked_at`, `dte`.
     - If running for the first time: run `python oi_collector.py` to populate these fields.
     - Alternatively patch `session_data.json` manually with the sample values from `tests/conftest.py`.
  2. Start server: `python server.py`
  3. Open `http://localhost:8000`
  4. Verify:
     - Chart panel appears below the 3 existing panels
     - Series name shows `GC · <date>` in the header
     - Extracted and Expires datetimes show in local time
     - Countdown ticks every second without resetting on refresh
     - Call bars (green) and Put bars (red) appear side-by-side, closely grouped per strike
     - Yellow IV curve overlaid on bars with right Y-axis
     - White dashed price line with `▼ <price>` badge on x-axis
     - Magnet strikes show brighter bars with gold border + ★
     - When countdown hits 0: chip turns red + pulses, bottom bar updates, panel gets red border, canvas dims

- [ ] **Step 5: Commit**

  ```bash
  git add dashboard/style.css dashboard/index.html dashboard/app.js
  git commit -m "feat: add EOD OI chart panel with countdown and IV curve"
  ```

---

## Self-Review

**Spec coverage:**
- ✅ Full-width row below 3-panel layout → body grid `1fr auto auto`, `#oi-chart-panel`
- ✅ Grouped vertical bars, calls green / puts red → Chart.js bar datasets, `barPercentage: 0.40`
- ✅ IV curve on right Y-axis → line dataset, `yIV` scale, hidden when no data
- ✅ Left Y-axis: contracts → `yContracts` scale with `k` formatter
- ✅ Right Y-axis: IV % → `yIV` scale, amber colour
- ✅ Current price dashed line + badge on x-axis → `priceLinePlugin`, updates on WS message
- ✅ Series name in chart header → `exp_series_name` from session, `GC · <name>` format
- ✅ Extraction datetime in local time → `fmtLocal(lockedAtMs)`
- ✅ Expiry datetime in local time → `fmtLocal(expiryMs)`
- ✅ Countdown computed from `locked_at + dte × 86400s` → persists across refreshes
- ✅ Magnet strikes highlighted → brighter bg + `#e6b800` border + ★ label
- ✅ Expired state: red chip, red bar, dimmed chart, red panel border → CSS `.expired` class, `tick()`
- ✅ Missing Phase 2 data: placeholder text, canvas hidden → `renderOIChart()` guard
- ✅ Missing IV data: right axis hidden, IV line hidden → `ivVals.length === 0` guard
- ✅ `exp_series_name` backend → Task 1
- ✅ `vol_curve_points` backend → Task 2
- ✅ Fallback when `exp_series_name` null → `session.date` fallback in `startExpiryCountdown()`

**Type consistency:**
- `buildChartData()` returns `{ strikes, callVols, putVols, magnets, ivCurve }` — all consumed in `renderOIChart()` ✅
- `oiChart._oiMeta = { strikes }` set in `renderOIChart()`, read in `priceLinePlugin` ✅
- `chartPrice` set in `renderOIChart()` (initial) and `ws.onmessage` (live updates), read in plugin ✅
- `expiryMs` set in `startExpiryCountdown()`, read in `tick()` ✅
- `session.vol_curve_points` written in Task 2, read in `buildChartData()` ✅
