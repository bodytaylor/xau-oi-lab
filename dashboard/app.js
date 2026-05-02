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
