"""
Generates a Pine Script indicator from session_data.json.

Phase 1 output: SD zone backgrounds + level lines + box grid + right-side labels
Phase 2 output: adds OI magnet hlines + magnet labels

The generated .pine file is written to exports/ and the code string
is returned for direct injection into TradingView via CDP.
"""
import logging
from pathlib import Path

BASE_DIR    = Path(__file__).parent
EXPORTS_DIR = BASE_DIR / "exports"

log = logging.getLogger("pine_exporter")


def generate_pine_script(session: dict) -> str:
    open_p  = session["open_price"]
    sd      = session["sd_zones"]
    date    = session.get("date", "today")
    iv_pct  = session.get("iv_pct", 0)
    sd1_pts = sd["sd1_pts"]

    offset_default = session.get("price_offset", 0.0)

    p3 = sd["zones"]["+3SD"]
    p2 = sd["zones"]["+2SD"]
    p1 = sd["zones"]["+1SD"]
    m1 = sd["zones"]["-1SD"]
    m2 = sd["zones"]["-2SD"]
    m3 = sd["zones"]["-3SD"]

    # ── Box grid: 50-pt solid orange lines ───────────────────────────────
    lo = int(m3 // 50) * 50
    hi = int(p3 // 50 + 1) * 50
    box_prices = list(range(lo, hi + 50, 50))
    box_lines = "\n".join(
        f'hline({b} - offset, "", color=color.new(color.orange, 70), linestyle=hline.style_solid, linewidth=2)'
        for b in box_prices
    )
    n_box = len(box_prices)
    box_var_lines = "\n".join(f"var label _lgrid{i} = na" for i in range(n_box))
    box_del_lines = "\n    ".join(f"label.delete(_lgrid{i})" for i in range(n_box))
    box_new_lines = "\n    ".join(
        f'_lgrid{i} := label.new(time[math.min(bar_index, 60)], {b} - offset, "{b}", xloc=xloc.bar_time, style=label.style_label_right, color=color.new(color.orange, 70), textcolor=color.white, size=size.small)'
        for i, b in enumerate(box_prices)
    )
    box_del_block = f"\n    {box_del_lines}" if n_box else ""
    box_new_block = f"\n    {box_new_lines}" if n_box else ""

    # ── OI magnets (Phase 2) ──────────────────────────────────────────────
    magnets = []
    magnet_hlines = ""
    if session.get("phase2_complete") and session.get("oi_analysis"):
        magnets = session["oi_analysis"].get("magnets", [])
        if magnets:
            parts = ["\n// OI Magnets (Phase 2)"]
            for m in magnets:
                parts.append(
                    f'hline({m:.2f} - offset, "OI {m:.0f}", color=color.blue, linestyle=hline.style_solid, linewidth=2)'
                )
            magnet_hlines = "\n".join(parts)

    # ── OPEN label info line ──────────────────────────────────────────────
    dte = sd.get("dte", 1.0)
    open_info = f"{date}  |  IV {iv_pct:.2f}%  DTE {dte}  ±{sd1_pts:.1f}pt"
    if session.get("phase2_complete") and session.get("oi_analysis"):
        skew = session["oi_analysis"].get("skew_verdict", "")
        oi_rows = session.get("oi_data", [])
        vs = next((r.get("vol_settle") for r in oi_rows if r.get("vol_settle")), None)
        if skew:
            open_info += f"  |  {skew}"
        if vs:
            open_info += f"  |  IntraVol {vs}%"

    # ── Build var label declarations ──────────────────────────────────────
    n_mag = min(len(magnets), 3)
    mag_var_lines   = "\n".join(f"var label _lmag{i} = na" for i in range(n_mag))
    mag_del_lines   = "\n    ".join(f"label.delete(_lmag{i})" for i in range(n_mag))
    mag_new_lines   = "\n    ".join(
        f'_lmag{i} := label.new(bar_index, {m:.2f} - offset, "◆ OI Magnet  {m:.2f}", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.blue, 30), textcolor=color.white, size=size.small)'
        for i, m in enumerate(magnets[:3])
    )

    # Prefix with indent for the if-block
    mag_del_block = f"\n    {mag_del_lines}" if n_mag else ""
    mag_new_block = f"\n    {mag_new_lines}" if n_mag else ""

    return f"""//@version=5
indicator("XAUUSD SD Zones — {date}", overlay=true, max_lines_count=500, max_labels_count=50)

// ── Futures–CFD spread adjustment ──────────────────────────────────────────
// Enter: futures_price − cfd_price
//   Positive → futures trades above CFD  → lines shift DOWN on CFD chart
//   Negative → futures trades below CFD  → lines shift UP   on CFD chart
// OPEN and SD levels are not shifted (calculated from CFD open price).
offset = input.float({offset_default:.2f}, title="Futures–CFD spread (futures − CFD)", step=0.01,
     tooltip="Shifts box grid and OI magnet levels to align with the CFD chart price.\\nOPEN and SD levels are not shifted (calculated from CFD open price).")

// ── Background zones ───────────────────────────────────────────────────
// Red    : close >= +2SD   extreme bull extension — mean-reversion risk
// Orange : +1SD to +2SD    bullish momentum
// Gray   : OPEN ± 1SD      fair value / balanced
// Teal   : -2SD to -1SD    bearish momentum
// Green  : close <= -2SD   extreme bear extension — mean-reversion risk
bgcolor(close >= {p2}                        ? color.new(color.red,    83) : na, title="+2SD+ zone")
bgcolor(close >= {p1}  and close < {p2}      ? color.new(color.orange, 88) : na, title="+1-2SD zone")
bgcolor(close >= {open_p} and close < {p1}   ? color.new(color.gray,   93) : na, title="0+1SD zone")
bgcolor(close <  {open_p} and close >= {m1}  ? color.new(color.gray,   93) : na, title="0-1SD zone")
bgcolor(close <  {m1}  and close >= {m2}     ? color.new(color.teal,   88) : na, title="-1-2SD zone")
bgcolor(close <  {m2}                        ? color.new(color.green,  83) : na, title="-2SD+ zone")

// ── SD level lines ─────────────────────────────────────────────────────
hline({p3},     "+3SD", color=color.red,    linestyle=hline.style_solid,  linewidth=2)
hline({p2},     "+2SD", color=color.orange, linestyle=hline.style_dashed, linewidth=2)
hline({p1},     "+1SD", color=color.gray,   linestyle=hline.style_dashed, linewidth=1)
hline({open_p}, "OPEN", color=color.white,  linestyle=hline.style_dashed, linewidth=2)
hline({m1},     "-1SD", color=color.gray,   linestyle=hline.style_dashed, linewidth=1)
hline({m2},     "-2SD", color=color.teal,   linestyle=hline.style_dashed, linewidth=2)
hline({m3},     "-3SD", color=color.green,  linestyle=hline.style_solid,  linewidth=2)

// ── Box grid (50-pt, solid orange) ────────────────────────────────────
{box_lines}
{magnet_hlines}

// ── CME EOD settlement divider ────────────────────────────────────────
// CME Gold (COMEX) options EOD volume settles at 1:30 PM ET.
// EDT (Apr–Oct) : 17:30 UTC = 00:30 UTC+7
// EST (Nov–Mar) : 18:30 UTC = 01:30 UTC+7
// Detection: bar where UTC clock crosses from before 17:30 to at/after 17:30.
// Works on any intraday timeframe without needing bar size.
if timeframe.isintraday
    _edt        = month >= 4 and month <= 10
    _settle_min = (_edt ? 17 : 18) * 60 + 30
    _prev_min   = hour(time[1], "UTC") * 60 + minute(time[1], "UTC")
    _curr_min   = hour(time,    "UTC") * 60 + minute(time,    "UTC")
    if _prev_min < _settle_min and _curr_min >= _settle_min
        line.new(bar_index, high, bar_index, low, extend=extend.both, color=color.new(color.aqua, 30), style=line.style_solid, width=2)
        label.new(bar_index, na, "CME EOD 1:30PM ET", xloc=xloc.bar_index, yloc=yloc.abovebar, style=label.style_none, textcolor=color.aqua, size=size.tiny)

// ── Labels ─────────────────────────────────────────────────────────────
// SD + OI magnets: right-side, anchored at bar_index, style=label_left.
// Box grid: left-side, anchored at time[60] via xloc.bar_time, style=label_right.
// var + label.delete() ensures only one label exists per level at a time.
var label _lp3 = na
var label _lp2 = na
var label _lp1 = na
var label _lo  = na
var label _lm1 = na
var label _lm2 = na
var label _lm3 = na
{mag_var_lines}
{box_var_lines}

if barstate.islast
    label.delete(_lp3)
    label.delete(_lp2)
    label.delete(_lp1)
    label.delete(_lo)
    label.delete(_lm1)
    label.delete(_lm2)
    label.delete(_lm3){mag_del_block}{box_del_block}
    _lp3 := label.new(bar_index, {p3}, "+3SD  {p3:.2f}  \u25b2 extreme bull — reversal risk", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.red, 25), textcolor=color.white, size=size.small)
    _lp2 := label.new(bar_index, {p2}, "+2SD  {p2:.2f}  strong bull momentum", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.orange, 30), textcolor=color.white, size=size.small)
    _lp1 := label.new(bar_index, {p1}, "+1SD  {p1:.2f}  mild bull", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.gray, 50), textcolor=color.white, size=size.small)
    _lo  := label.new(bar_index, {open_p}, "OPEN  {open_p:.2f}  {open_info}", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.black, 30), textcolor=color.yellow, size=size.small)
    _lm1 := label.new(bar_index, {m1}, "-1SD  {m1:.2f}  mild bear", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.gray, 50), textcolor=color.white, size=size.small)
    _lm2 := label.new(bar_index, {m2}, "-2SD  {m2:.2f}  strong bear momentum", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.teal, 30), textcolor=color.white, size=size.small)
    _lm3 := label.new(bar_index, {m3}, "-3SD  {m3:.2f}  \u25bc extreme bear — reversal risk", xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.green, 25), textcolor=color.white, size=size.small){mag_new_block}{box_new_block}
"""


def run(session: dict) -> str:
    """Generate Pine Script, write to exports/, return code string."""
    EXPORTS_DIR.mkdir(exist_ok=True)
    code = generate_pine_script(session)
    out  = EXPORTS_DIR / f"session_{session.get('date', 'today')}.pine"
    out.write_text(code)
    log.info(f"Pine Script written → {out.name}")
    return code
