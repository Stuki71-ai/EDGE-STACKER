#!/usr/bin/env python3
"""EDGE STACKER self-healing pick pipeline — runs entirely on the VPS.

generate -> audit -> self-heal infra/data -> iterate -> sync -> send.
Holds the email (ntfy only) on any code/design bug or unresolved finding.
See docs/plans/2026-05-17-self-healing-pipeline-design.md
"""
import argparse, json, logging, os, subprocess, sys
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
    inherits ODDS_API_KEY etc.).
    Expects simple KEY=VALUE lines with no inline comments (opaque tokens only)."""
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
    try:
        proc = subprocess.run(
            [sys.executable, "main.py", "--modules", module, "--json-only"],
            cwd=REPO, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("main.py timed out after 600s")
    if proc.returncode != 0:
        raise RuntimeError(f"main.py failed (rc={proc.returncode}): {proc.stderr[-500:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"main.py output not valid JSON: {e}")


def run_full_audit(module, result):
    """Run every Task-2 check against a fresh in-hand result and concatenate
    the Finding lists. A zero-pick result is legitimate (a 'no picks tonight'
    night): code/infra/data-fetch are still audited, but check_picks has
    nothing to flag and recompute_pick is skipped, so a clean zero-pick
    result returns []."""
    from shared import audit_checks as ac
    picks = result.get("picks", [])
    findings = []
    findings += ac.check_code_parity(REPO)
    findings += ac.check_infra(REPO, "n8n-n8n-1")
    findings += ac.check_data_fetch(module)
    findings += ac.check_picks(module, picks)
    if picks:
        findings += ac.recompute_pick(module, picks[0])
    return findings


def autofix_infra(finding):
    """Apply a mechanical infra fix. Return True on success."""
    if "n8n" in finding.text:
        rc = subprocess.run("docker start n8n-n8n-1", shell=True).returncode
        return rc == 0
    # add other infra fixes here as the audit grows
    return False


def drop_picks(result, pick_refs):
    """Return a copy of result with the named picks removed.

    Picks are matched by audit_checks._pick_ref(p) — the SAME ref string a
    DATA Finding carries in .pick_ref — so this works for both modules
    (nhl_sog picks are players, not matchups)."""
    from shared.audit_checks import _pick_ref
    kept = [p for p in result.get("picks", [])
            if _pick_ref(p) not in pick_refs]
    out = dict(result)
    out["picks"] = kept
    return out


def heal_loop(module):
    """generate -> audit -> self-heal infra/data -> iterate.
    Returns (outcome, result, findings) with outcome in {'SEND','HELD'}."""
    from shared import audit_checks as ac
    attempt = 1
    while attempt <= MAX_ATTEMPTS:
        result = generate(module)
        findings = run_full_audit(module, result)
        worst = ac.classify_worst(findings)
        if worst is None:
            return "SEND", result, []
        if worst == ac.CODE:
            return "HELD", result, findings
        if worst == ac.DATA:
            refs = {f.pick_ref for f in findings
                    if f.kind == ac.DATA and f.pick_ref}
            result = drop_picks(result, refs)
            findings = run_full_audit(module, result)
            if ac.classify_worst(findings) is None:
                return "SEND", result, []
        if worst == ac.INFRA:
            for f in (x for x in findings if x.kind == ac.INFRA):
                if not autofix_infra(f):
                    return "HELD", result, findings
        attempt += 1
    return "HELD", result, findings


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
