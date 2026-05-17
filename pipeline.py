#!/usr/bin/env python3
"""EDGE STACKER self-healing pick pipeline — runs entirely on the VPS.

generate -> audit -> self-heal infra/data -> iterate -> sync -> send.
Holds the email (ntfy only) on any code/design bug or unresolved finding.
See docs/plans/2026-05-17-self-healing-pipeline-design.md
"""
import argparse, json, logging, os, subprocess, sys
from datetime import datetime
from pathlib import Path

REPO = "/root/edge-stacker"
MAX_ATTEMPTS = 3
TARGET_ET_HOUR = {"nhl_sog": 16, "mlb_f5": 15}
WEBHOOK = {
    "nhl_sog": "https://vmi3157940.contaboserver.net/webhook/edge-stacker-nhl",
    "mlb_f5":  "https://vmi3157940.contaboserver.net/webhook/edge-stacker-mlb",
}
NTFY_URL = "https://ntfy.sh/Stuki71-Findings"
MARKER_DIR = os.path.join(REPO, "logs", "pipeline_markers")

logger = logging.getLogger("edge_stacker_pipeline")


def setup_logging():
    """Log to logs/pipeline.log AND stdout (mirrors audit.py's pattern)."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    os.makedirs(os.path.join(REPO, "logs"), exist_ok=True)
    fh = logging.FileHandler(os.path.join(REPO, "logs", "pipeline.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(logging.StreamHandler(sys.stdout))


def should_run(module, et_hour):
    """DST-proof guard: cron fires at two UTC times; only the run landing on the
    module's target ET hour proceeds."""
    return et_hour == TARGET_ET_HOUR[module]


def load_env(path=os.path.join(REPO, ".env")):
    """Load KEY=VALUE lines from .env into os.environ (so the main.py subprocess
    inherits ODDS_API_KEY etc.)."""
    if not os.path.exists(path):
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def generate(module):
    """Run main.py for one module, return the parsed picks JSON dict.
    Raises RuntimeError on failure (caught by the top-level guard)."""
    proc = subprocess.run(
        [sys.executable, "main.py", "--modules", module, "--json-only"],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"main.py failed (rc={proc.returncode}): {proc.stderr[-500:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"main.py output not valid JSON: {e}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="EDGE STACKER self-healing pick pipeline")
    parser.add_argument("--module", required=True, choices=["nhl_sog", "mlb_f5"],
                        help="Which module to run the pipeline for")
    return parser.parse_args(argv)


def main(argv=None):
    """Skeleton entry point — argparse + logging wiring only.
    The self-heal loop / SEND / HELD paths are added by later tasks."""
    args = parse_args(argv)
    setup_logging()
    load_env()
    logger.info(f"pipeline start: module={args.module}")
    # NOTE: loop / audit / send logic is implemented in later tasks.


if __name__ == "__main__":
    main()
