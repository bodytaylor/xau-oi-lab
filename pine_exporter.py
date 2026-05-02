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
