"""
XAUUSD OI Trading Framework — Scheduler / Orchestrator
=======================================================
Runs both phases on a schedule using APScheduler.
Alternatively, use cron (see bottom of file).

Run continuously:  python scheduler.py
"""

import logging
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scheduler")

BASE = Path(__file__).parent
PYTHON = sys.executable


def utc7_now():
    return datetime.now(timezone(timedelta(hours=7)))


def run_phase1():
    """01:30 UTC+7 — Collect open price + IV, lock SD zones."""
    log.info("▶  PHASE 1 triggered — running collector.py")
    result = subprocess.run(
        [PYTHON, str(BASE / "collector.py")],
        capture_output=False,
    )
    if result.returncode == 0:
        log.info("✓  Phase 1 complete")
    else:
        log.error(f"✗  Phase 1 failed (exit {result.returncode})")


def run_phase2():
    """08:30 UTC+7 — Collect intraday OI, analyse sentiment."""
    log.info("▶  PHASE 2 triggered — running oi_collector.py")
    result = subprocess.run(
        [PYTHON, str(BASE / "oi_collector.py")],
        capture_output=False,
    )
    if result.returncode == 0:
        log.info("✓  Phase 2 complete")
    else:
        log.error(f"✗  Phase 2 failed (exit {result.returncode})")


def main():
    if not HAS_SCHEDULER:
        print(
            "APScheduler not installed. Run:\n"
            "  pip install apscheduler\n\n"
            "Or add these cron entries manually:\n"
            "  30 18 * * 0-4  python3 /path/to/collector.py       # 01:30 UTC+7\n"
            "  30  1 * * 1-5  python3 /path/to/oi_collector.py    # 08:30 UTC+7\n"
        )
        sys.exit(1)

    scheduler = BlockingScheduler(timezone="Asia/Bangkok")

    # Phase 1: 01:30 UTC+7, Mon–Fri
    scheduler.add_job(
        run_phase1,
        CronTrigger(hour=1, minute=30, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        id="phase1",
        name="Phase 1 — Open price + IV collector",
    )

    # Phase 2: 08:30 UTC+7, Mon–Fri
    scheduler.add_job(
        run_phase2,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone="Asia/Bangkok"),
        id="phase2",
        name="Phase 2 — OI sentiment collector",
    )

    log.info("Scheduler running. Jobs:")
    for job in scheduler.get_jobs():
        log.info(f"  {job.name} — next: {job.next_run_time}")

    log.info("Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()

# ─────────────────────────────────────────────
# CRON ALTERNATIVE (no Python scheduler needed)
# ─────────────────────────────────────────────
# Run `crontab -e` and add:
#
#   # XAUUSD Framework — Phase 1 at 01:30 WIB (UTC+7)
#   30 18 * * 0-4  cd /path/to/xauusd_automation && python3 collector.py >> logs/phase1.log 2>&1
#
#   # XAUUSD Framework — Phase 2 at 08:30 WIB (UTC+7)
#   30  1 * * 1-5  cd /path/to/xauusd_automation && python3 oi_collector.py >> logs/phase2.log 2>&1
#
# Note: cron uses UTC by default on most Linux systems.
# UTC+7 01:30 = UTC 18:30 previous day (Mon-Fri)
# UTC+7 08:30 = UTC 01:30 same day (Tue-Sat, maps to Mon-Fri sessions)
