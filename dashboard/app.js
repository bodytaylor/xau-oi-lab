// dashboard/app.js
'use strict';

const WS_URL = `ws://${location.host}/ws/price`;
const API     = `${location.origin}/api`;

let session  = null;
let ws       = null;
let wsRetries = 0;
let lastPrice = null;
let eodChart      = null;  // Chart 1 — EOD Volume
let intradayChart = null;  // Chart 2 — Intraday Volume
let oiChart       = null;  // Chart 3 — Open Interest OI
let chartPrice = null;   // current price for the price-line plugin (shared)
let priceOffset = 0.0;   // Futures–CFD spread offset (futures − CFD)
let expiryMs   = null;   // option series expiry in Unix ms, computed once from session
let expiryInterval = null;  // interval handle — cleared on re-run

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

    // Apply Futures–CFD offset so the line aligns with futures strike prices
    // (chart strikes = CME futures; live price = CFD → add offset to map to futures axis)
    const displayPrice = chartPrice + priceOffset;

    // Interpolate displayPrice to a fractional x-index between two strikes
    let xPos = null;
    for (let i = 0; i < strikes.length - 1; i++) {
      if (displayPrice >= strikes[i] && displayPrice <= strikes[i + 1]) {
        xPos = i + (displayPrice - strikes[i]) / (strikes[i + 1] - strikes[i]);
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
    const label = `\u25bc ${displayPrice.toFixed(2)}`;
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

/**
 * Create or replace a Chart.js bar chart for volume/OI data.
 * @param {string}  canvasId       - ID of the <canvas> element
 * @param {Array}   oiData         - [{strike, volume, type, is_magnet}] rows
 * @param {Array}   volCurvePoints - [{strike, iv}] for IV curve overlay (grouped only)
 * @param {boolean} stacked        - If true, use stacked bars (OI chart)
 * @param {Chart}   existing       - Previous Chart.js instance to destroy
 * @returns {Chart|null}
 */
function makeBarChart(canvasId, oiData, volCurvePoints, stacked, existing) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  if (!oiData?.length) return null;
  if (existing) { existing.destroy(); }

  const { strikes, callVols, putVols, magnets, ivCurve } =
    buildChartData(oiData, volCurvePoints);

  const callBg  = strikes.map(s => magnets.includes(s) ? 'rgba(0,230,118,0.9)'  : 'rgba(0,200,83,0.65)');
  const putBg   = strikes.map(s => magnets.includes(s) ? 'rgba(255,82,82,0.9)'  : 'rgba(255,23,68,0.65)');
  const brdCol  = strikes.map(s => magnets.includes(s) ? '#e6b800' : 'transparent');
  const brdW    = strikes.map(s => magnets.includes(s) ? 2 : 0);
  const labels  = strikes.map(s => magnets.includes(s) ? `${s}\u2605` : String(s));

  const ivVals  = ivCurve.filter(v => v !== null);
  const ivMin   = ivVals.length ? Math.floor(Math.min(...ivVals)) - 2 : 20;
  const ivMax   = ivVals.length ? Math.ceil(Math.max(...ivVals))  + 2 : 40;

  const barPct  = stacked ? 0.75 : 0.40;
  const catPct  = stacked ? 0.85 : 0.60;

  const datasets = [
    {
      type: 'bar', label: 'Calls', data: callVols,
      backgroundColor: callBg, borderColor: brdCol, borderWidth: brdW,
      yAxisID: 'yContracts', barPercentage: barPct, categoryPercentage: catPct, order: 2,
      ...(stacked ? { stack: 'stack0' } : {}),
    },
    {
      type: 'bar', label: 'Puts', data: putVols,
      backgroundColor: putBg, borderColor: brdCol, borderWidth: brdW,
      yAxisID: 'yContracts', barPercentage: barPct, categoryPercentage: catPct, order: 2,
      ...(stacked ? { stack: 'stack0' } : {}),
    },
  ];

  if (!stacked) {
    datasets.push({
      type: 'line', label: 'IV %', data: ivCurve,
      borderColor: '#e6b800', backgroundColor: 'rgba(230,184,0,0.05)',
      pointBackgroundColor: '#e6b800', pointBorderColor: '#0d0d0d', pointBorderWidth: 1,
      pointRadius: 3, pointHoverRadius: 5, borderWidth: 2, tension: 0.4, fill: false,
      spanGaps: true,
      yAxisID: 'yIV', order: 1,
      hidden: ivVals.length === 0,
    });
  }

  const ctx = canvas.getContext('2d');
  const chart = new Chart(ctx, {
    type: 'bar',
    plugins: [priceLinePlugin],
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { bottom: 26 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1a2e', borderColor: '#2a2a4a', borderWidth: 1,
          titleColor: '#aaa', bodyColor: '#e0e0e0', padding: 8,
          callbacks: {
            title:  items => `Strike: ${items[0].label}`,
            label:  item  => item.dataset.label === 'IV %'
              ? (item.raw != null ? `  IV:    ${item.raw.toFixed(1)}%` : '  IV:    —')
              : `  ${item.dataset.label.padEnd(5)}: ${(item.raw || 0).toLocaleString()} contracts`,
          },
        },
      },
      scales: {
        x: {
          grid:  { color: 'rgba(255,255,255,0.03)' },
          ticks: { color: '#555', font: { family: 'SF Mono, Consolas, monospace', size: 9 }, maxRotation: 45 },
          ...(stacked ? { stacked: true } : {}),
        },
        yContracts: {
          type: 'linear', position: 'left',
          title: { display: true, text: 'Contracts', color: '#666', font: { size: 9, family: 'SF Mono, Consolas, monospace' } },
          grid:  { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#666', font: { size: 9, family: 'SF Mono, Consolas, monospace' },
                   callback: v => v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v },
          ...(stacked ? { stacked: true } : {}),
        },
        ...(stacked ? {} : {
          yIV: {
            type: 'linear', position: 'right',
            display: ivVals.length > 0,
            title: { display: true, text: 'IV %', color: '#e6b800', font: { size: 9, family: 'SF Mono, Consolas, monospace' } },
            grid:  { display: false },
            ticks: { color: '#e6b800', font: { size: 9, family: 'SF Mono, Consolas, monospace' },
                     callback: v => v.toFixed(1) + '%' },
            min: ivMin, max: ivMax,
          },
        }),
      },
    },
  });

  chart._oiMeta = { strikes };
  return chart;
}

/** Show a placeholder message when chart data is unavailable. */
function showChartPlaceholder(panelId, canvasId, phId, msg) {
  const canvas = document.getElementById(canvasId);
  if (canvas) canvas.style.display = 'none';
  const panel = document.getElementById(panelId);
  if (panel && !document.getElementById(phId)) {
    const ph = document.createElement('div');
    ph.id = phId;
    ph.style.cssText = 'color:var(--muted);font-size:11px;padding:16px 0;flex:1';
    ph.textContent = msg;
    const legend = panel.querySelector('.chart-panel-legend');
    panel.insertBefore(ph, legend);
  }
}

/** Hide placeholder and restore canvas when data arrives. */
function hideChartPlaceholder(canvasId, phId) {
  const canvas = document.getElementById(canvasId);
  if (canvas) canvas.style.display = '';
  const ph = document.getElementById(phId);
  if (ph) ph.remove();
}

function renderAllCharts() {
  chartPrice = lastPrice || session?.open_price || null;

  // ── Chart 1: Volume EOD ──────────────────────────────────
  if (!session?.eod_data?.length) {
    showChartPlaceholder('eod-chart-panel', 'eod-canvas', 'eod-ph', 'Waiting for EOD data\u2026');
  } else {
    hideChartPlaceholder('eod-canvas', 'eod-ph');
    const eodCurve = session.eod_vol_curve_points?.length ? session.eod_vol_curve_points : session.vol_curve_points;
    eodChart = makeBarChart('eod-canvas', session.eod_data, eodCurve, false, eodChart);
  }

  // ── Chart 2: Volume Intraday (Phase 2 only) ──────────────
  if (!session?.phase2_complete || !session?.oi_data?.length) {
    showChartPlaceholder('intraday-chart-panel', 'intraday-canvas', 'intraday-ph', 'Waiting for Phase 2\u2026');
  } else {
    hideChartPlaceholder('intraday-canvas', 'intraday-ph');
    intradayChart = makeBarChart('intraday-canvas', session.oi_data, session.vol_curve_points, false, intradayChart);
  }

  // ── Chart 3: Open Interest OI (Phase 2 only) ─────────────
  if (!session?.phase2_complete || !session?.oi_interest_data?.length) {
    showChartPlaceholder('oi-chart-panel', 'oi-canvas', 'oi-ph', 'Waiting for Phase 2\u2026');
  } else {
    hideChartPlaceholder('oi-canvas', 'oi-ph');
    oiChart = makeBarChart('oi-canvas', session.oi_interest_data, session.vol_curve_points, false, oiChart);
  }
}

function padZ(n) { return String(Math.floor(Math.abs(n))).padStart(2, '0'); }
function fmtDur(secs) {
  const a = Math.abs(secs);
  return `${padZ(a / 3600)}:${padZ((a % 3600) / 60)}:${padZ(a % 60)}`;
}

function tick() {
  if (expiryMs === null) return;
  const rem   = Math.floor((expiryMs - Date.now()) / 1000);
  const chip  = document.getElementById('countdown-chip');
  const bar   = document.getElementById('expiry-bar');
  const panel = document.getElementById('charts-row');
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

  const seriesEl  = document.getElementById('chart-series-name');
  const extractEl = document.getElementById('chart-extracted');
  const expiresEl = document.getElementById('chart-expires');

  if (seriesEl) {
    const rawName = session.exp_series_name || session.date || '';
    const cleanName = rawName.replace(/\s+/g, ' ').trim();
    seriesEl.textContent = `GC \u00b7 ${cleanName}`;
  }
  if (extractEl) extractEl.textContent = fmtLocal(lockedAtMs);
  if (expiresEl) expiresEl.textContent  = fmtLocal(expiryMs);

  tick();                        // immediate first render
  if (expiryInterval) clearInterval(expiryInterval);
  expiryInterval = setInterval(tick, 1000);       // live tick every second
}

// ── Bootstrap ────────────────────────────────────────────────────────────────

async function refreshSession() {
  try {
    const resp = await fetch(`${API}/session`);
    if (resp.ok) session = await resp.json();
  } catch (_) {}
  chartPrice = lastPrice || session?.open_price || null;
  renderZoneMap();
  renderOI();
  renderAllCharts();
  startExpiryCountdown();
}

async function init() {
  try {
    const resp = await fetch(`${API}/session`);
    if (resp.ok) session = await resp.json();
  } catch (_) {}

  // Load saved Futures–CFD offset
  try {
    const resp = await fetch(`${API}/offset`);
    if (resp.ok) {
      const data = await resp.json();
      priceOffset = data.offset || 0.0;
      const input = document.getElementById('price-offset-input');
      if (input) input.value = priceOffset;
    }
  } catch (_) {}

  // Load saved QuikStrike URL
  try {
    const resp = await fetch(`${API}/quikstrike-url`);
    if (resp.ok) {
      const data = await resp.json();
      const urlInput = document.getElementById('quikstrike-url-input');
      if (urlInput && data.url) urlInput.value = data.url;
    }
  } catch (_) {}

  // Pine Script link
  const today = new Date().toISOString().slice(0, 10);
  const pineLink = document.getElementById('pine-link');
  pineLink.href   = `/exports/session_${today}.pine`;
  pineLink.textContent = `📄 Pine Script ${today}`;

  if (session?.phase2_at) {
    document.getElementById('phase2-ts').textContent =
      'Phase 2: ' + session.phase2_at.slice(11, 16) + ' UTC+7';
  }

  renderZoneMap();
  renderOI();
  renderAllCharts();
  startExpiryCountdown();

  // Futures–CFD offset input handler
  // Charts update immediately; server save is debounced to avoid mid-entry POSTs
  let _offsetSaveTimer = null;
  const offsetInput = document.getElementById('price-offset-input');
  if (offsetInput) {
    offsetInput.addEventListener('input', () => {
      priceOffset = parseFloat(offsetInput.value) || 0.0;
      [eodChart, intradayChart, oiChart].forEach(c => { if (c) c.update('none'); });
      clearTimeout(_offsetSaveTimer);
      _offsetSaveTimer = setTimeout(() => {
        fetch(`${API}/offset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ offset: priceOffset }),
        }).catch(() => {});
      }, 300);
    });
  }

  // Phase run buttons
  function makeRunHandler(btnId, endpoint, label) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.addEventListener('click', async () => {
      btn.textContent = `⏳ ${label}…`;
      btn.classList.add('running');
      btn.disabled = true;
      try {
        await fetch(`${API}/${endpoint}`, { method: 'POST' });
        btn.textContent = `⏳ ${label} running…`;
        btn.classList.remove('running');
        btn.classList.add('done');
        setTimeout(() => {
          btn.textContent = `▶ ${label}`;
          btn.classList.remove('done');
          btn.disabled = false;
        }, 4000);
      } catch (_) {
        btn.textContent = `▶ ${label}`;
        btn.classList.remove('running');
        btn.disabled = false;
      }
    });
  }
  makeRunHandler('run-phase1-btn', 'refresh/phase1', 'Phase 1');
  makeRunHandler('run-phase2-btn', 'refresh/phase2', 'Phase 2');

  // QuikStrike URL save button
  const qsSaveBtn = document.getElementById('quikstrike-url-save');
  if (qsSaveBtn) {
    qsSaveBtn.addEventListener('click', async () => {
      const urlInput = document.getElementById('quikstrike-url-input');
      const url = urlInput ? urlInput.value.trim() : '';
      try {
        const resp = await fetch(`${API}/quikstrike-url`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        if (resp.ok) {
          qsSaveBtn.textContent = 'Saved ✓';
          qsSaveBtn.classList.add('saved');
          setTimeout(() => {
            qsSaveBtn.textContent = 'Save';
            qsSaveBtn.classList.remove('saved');
          }, 2000);
        } else {
          const data = await resp.json().catch(() => ({}));
          qsSaveBtn.textContent = data.error || 'Error';
          qsSaveBtn.style.background = 'var(--red, #f44)';
          setTimeout(() => {
            qsSaveBtn.textContent = 'Save';
            qsSaveBtn.style.background = '';
          }, 3000);
        }
      } catch (_) {
        qsSaveBtn.textContent = 'Error';
        setTimeout(() => { qsSaveBtn.textContent = 'Save'; }, 3000);
      }
    });
  }

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

    // Phase completed — re-fetch full session and re-render all charts
    if (d.type === 'session_updated') {
      refreshSession();
      return;
    }

    if (d.price !== null && d.price !== undefined) {
      lastPrice  = d.price;
      chartPrice = d.price;                      // update price for plugin
      document.getElementById('live-price').textContent = d.price.toFixed(2);
      updatePriceDot(d.price);
      // Redraw price line on all charts, no animation
      [eodChart, intradayChart, oiChart].forEach(c => { if (c) c.update('none'); });
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
    label.style.color = color;
    label.textContent = `${key}  ${z[key].toFixed(1)}`;
    bar.appendChild(label);
  });

  // OI magnets — labels on left side.
  // Note: if two magnets land within ~1% of the range apart, their labels will
  // visually overlap. No collision avoidance is applied; rare in practice.
  const magnets = session.oi_analysis?.magnets || [];
  magnets.forEach(m => {
    const line = document.createElement('div');
    line.className = 'sd-line';
    line.style.bottom = pct(m);
    line.style.background = '#2979ff';
    line.style.borderTop = '1px dotted #2979ff';
    bar.appendChild(line);

    const label = document.createElement('div');
    label.className = 'magnet-label';
    label.style.bottom = pct(m);
    label.textContent = `◆ ${m.toFixed(1)}`;
    bar.appendChild(label);
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
  const conf  = sig.confidence || '—';

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

// ── Today's Bias ──────────────────────────────────────────────────────────────

function renderBias() {
  const verdictEl = document.getElementById('bias-verdict');
  const detailEl  = document.getElementById('bias-detail');
  if (!verdictEl) return;

  const oi      = session?.oi_analysis;
  const volSkew = session?.vol_skew_analysis;

  if (!oi) { verdictEl.textContent = 'Waiting…'; return; }

  const oiV  = oi.skew_verdict  || 'Neutral';
  const volV = volSkew?.verdict || null;

  const oiBear  = oiV.includes('PUT');
  const oiBull  = oiV.includes('CALL');
  const volBear = volV?.includes('LEFT');
  const volBull = volV?.includes('RIGHT');

  let bias = 'NEUTRAL', conf = '', color = 'var(--muted)';

  if      (volBear && oiBear)              { bias = 'BEARISH'; conf = 'HIGH';   color = 'var(--red)';   }
  else if (volBull && oiBull)              { bias = 'BULLISH'; conf = 'HIGH';   color = 'var(--green)'; }
  else if (volBear && !oiBull)             { bias = 'BEARISH'; conf = 'MEDIUM'; color = 'var(--red)';   }
  else if (volBull && !oiBear)             { bias = 'BULLISH'; conf = 'MEDIUM'; color = 'var(--green)'; }
  else if (oiBear  && !volBull)            { bias = 'BEARISH'; conf = 'LOW';    color = 'var(--amber)'; }
  else if (oiBull  && !volBear)            { bias = 'BULLISH'; conf = 'LOW';    color = 'var(--amber)'; }
  else if ((volBear && oiBull) || (volBull && oiBear)) {
                                             bias = 'CONFLICT'; conf = '';      color = 'var(--amber)'; }

  verdictEl.textContent = conf ? `${bias} · ${conf}` : bias;
  verdictEl.style.color = color;

  detailEl.innerHTML = '';
  const lines = [
    `OI skew  : ${oiV}`,
    `Vol skew : ${volV || 'not available'}`,
  ];
  if (volSkew?.slope_ratio && volV !== null) {
    lines.push(`L/R ratio : ${volSkew.slope_ratio.toFixed(3)}`);
  }
  lines.forEach(txt => {
    const d = document.createElement('div');
    d.className = 'bias-detail-row';
    d.textContent = txt;
    detailEl.appendChild(d);
  });
}

// ── OI Panel ─────────────────────────────────────────────────────────────────

function renderOI() {
  if (!session?.oi_analysis) return;
  renderBias();
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
