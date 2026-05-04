#!/usr/bin/env python3
"""
Push today's (or latest) Pine Script from exports/ into TradingView via CDP.

Usage:
    python push_pine.py            # push today's session file
    python push_pine.py --latest   # push whichever .pine file is newest
    python push_pine.py --file exports/session_2026-05-03.pine

Prerequisites:
    1. TradingView Desktop running with CDP:
           bash launch_tradingview.sh
    2. A XAUUSD chart open in TradingView.
"""
import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

from tradingview_client import TradingViewClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("push_pine")

EXPORTS_DIR = Path(__file__).parent / "exports"


def resolve_pine_file(args) -> Path:
    if args.file:
        p = Path(args.file)
        if not p.exists():
            log.error(f"File not found: {p}")
            sys.exit(1)
        return p

    if args.latest:
        files = sorted(EXPORTS_DIR.glob("session_*.pine"), key=lambda f: f.stat().st_mtime)
        if not files:
            log.error(f"No .pine files found in {EXPORTS_DIR}")
            sys.exit(1)
        return files[-1]

    # Default: today's file
    today = date.today().isoformat()
    p = EXPORTS_DIR / f"session_{today}.pine"
    if not p.exists():
        log.error(
            f"Today's file not found: {p}\n"
            "Run the collector first, or use --latest / --file."
        )
        sys.exit(1)
    return p


def main():
    parser = argparse.ArgumentParser(description="Push Pine Script to TradingView")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true", help="Push the newest .pine file")
    group.add_argument("--file", metavar="PATH", help="Push a specific .pine file")
    args = parser.parse_args()

    pine_file = resolve_pine_file(args)
    log.info(f"Pine file: {pine_file.name}")

    tv = TradingViewClient()

    if not tv.is_connected():
        log.error(
            "TradingView not reachable via CDP on port 9222.\n"
            "Run:  bash launch_tradingview.sh"
        )
        sys.exit(1)

    # Step 1: open the Pine Script editor panel
    log.info("Step 1: Opening Pine Script editor…")
    opened = tv.open_pine_editor()
    if not opened:
        log.warning(
            "Could not auto-open the editor panel.\n"
            "Please click 'Pine Editor' at the bottom of TradingView manually, then re-run."
        )
        sys.exit(1)

    # Wait for the panel animation to complete before touching the editor
    time.sleep(2.0)

    # Step 2: inject code + click Add to Chart
    # Rename must come AFTER injection — TradingView refuses to save a script
    # with an empty editor ("cannot save empty source code").
    log.info("Step 2: Injecting Pine Script and clicking Add to Chart…")
    code = pine_file.read_text()
    ok = tv.push_pine(code)

    if not ok:
        log.error(
            "push_pine failed.\n"
            "Make sure the Pine Editor panel is open and a XAUUSD chart is active."
        )
        sys.exit(1)

    # Wait for Monaco to settle before opening the rename dialog
    time.sleep(1.5)

    # Step 3: rename the script to "OI data"
    log.info("Step 3: Renaming script to 'OI data'…")
    renamed = tv.rename_pine_script("OI data")
    if not renamed:
        log.warning("Could not rename script — continuing anyway.")

    log.info(f"Done — {pine_file.name} pushed to TradingView ✓")


if __name__ == "__main__":
    main()
