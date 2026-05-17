#!/usr/bin/env python3
"""EDGE STACKER dead-man's-switch — runs ~2h after each pipeline fire.

The pipeline ntfys for itself on HELD/CRASH. But if the cron never fires, or
the process dies before it can write a marker, you get NO signal at all — a
silent miss. deadman.py catches exactly that: it checks whether the module's
pipeline left a FRESH completion marker today.

  fresh marker  -> stay silent, exit 0 (pipeline already handled its outcome)
  missing/stale -> ntfy "pipeline did NOT run"
"""
import argparse, json, logging, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

from pipeline import MARKER_DIR, ntfy, setup_logging

# A same-day fire's marker is at most ~8h old at deadman time; a marker left
# over from a previous day is >24h old. 12h cleanly separates the two.
STALE_AFTER_HOURS = 12

logger = logging.getLogger("edge_stacker_pipeline")


def check(module):
    """Return None if a fresh marker exists, else a reason string (missing/stale)."""
    path = Path(MARKER_DIR) / f"{module}.json"
    if not path.exists():
        return "marker file is MISSING"
    try:
        ts = datetime.fromisoformat(json.loads(path.read_text())["ts"])
    except Exception as e:
        return f"marker file is unreadable ({e})"
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    if age_h > STALE_AFTER_HOURS:
        return f"marker is STALE ({age_h:.1f}h old, last fire was not today)"
    return None


def main(argv=None):
    """Check one module's marker; ntfy only if the pipeline did not run today."""
    parser = argparse.ArgumentParser(
        description="EDGE STACKER dead-man's-switch")
    parser.add_argument("--module", required=True, choices=["nhl_sog", "mlb_f5"],
                        help="Which module's pipeline marker to check")
    args = parser.parse_args(argv)
    setup_logging()
    module = args.module
    try:
        reason = check(module)
        if reason is None:
            logger.info(f"deadman: {module} marker fresh — pipeline ran, silent")
            return
        logger.warning(f"deadman: {module} — {reason}")
        ntfy(f"EDGE STACKER - {module} pipeline did NOT run",
             f"module={module}: {reason}. The pipeline did not complete today "
             "and NO picks email was sent for this module. Investigate the "
             "cron job / VPS now.")
    except Exception as e:
        # A dead-man's-switch that dies silently is useless — ntfy anyway.
        tb = traceback.format_exc()
        logger.error(f"deadman CRASHED (module={module}): {e}\n{tb}")
        ntfy("EDGE STACKER - deadman switch CRASHED",
             f"module={module}: deadman.py itself crashed and could not verify "
             f"the pipeline ran.\n\n{e}\n\n{tb}")


if __name__ == "__main__":
    main()
