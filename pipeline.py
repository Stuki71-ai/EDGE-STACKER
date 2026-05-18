#!/usr/bin/env python3
"""EDGE STACKER self-healing pick pipeline — runs entirely on the VPS.

generate -> audit -> self-heal infra/data -> iterate -> sync -> send.
Holds the email (ntfy only) on any code/design bug or unresolved finding.
See docs/plans/2026-05-17-self-healing-pipeline-design.md
"""
import argparse, json, logging, os, subprocess, sys, traceback
from datetime import datetime, timezone
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
    """Log to logs/pipeline.log via a FileHandler only.

    No StreamHandler: pipeline.py / deadman.py run under cron with stdout
    redirected back into logs/pipeline.log (`>> logs/pipeline.log 2>&1`), so a
    StreamHandler would write every line a second time. The cron's `2>&1` still
    captures any crash that happens before this logging is set up.
    """
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    os.makedirs(os.path.join(REPO, "logs"), exist_ok=True)
    fh = logging.FileHandler(os.path.join(REPO, "logs", "pipeline.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)


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
    checks = [
        ("code_parity", lambda: ac.check_code_parity(REPO)),
        ("infra",       lambda: ac.check_infra(REPO, "n8n-n8n-1")),
        ("data_fetch",  lambda: ac.check_data_fetch(module)),
        ("picks",       lambda: ac.check_picks(module, picks)),
    ]
    if picks:
        checks.append(("recompute", lambda: ac.recompute_pick(module, picks[0])))
    for name, fn in checks:
        try:
            findings += fn()
        except Exception as e:
            findings.append(ac.Finding(ac.CODE, f"audit check '{name}' raised: {e}"))
    return findings


def autofix_infra(finding):
    """Apply a mechanical infra fix. Return True on success."""
    if "n8n" in finding.text:
        try:
            rc = subprocess.run("docker start n8n-n8n-1", shell=True,
                                timeout=120).returncode
        except subprocess.TimeoutExpired:
            return False
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

    Returns (outcome, result, findings, autofixes):
      outcome   — 'SEND' | 'HELD'
      result    — the (possibly pick-trimmed) picks JSON
      findings  — the findings that caused a HOLD ([] on SEND)
      autofixes — list[str] of self-heal actions taken this run (infra restarts,
                  dropped picks). Empty when nothing was healed. main() uses it
                  to decide whether a transparency ntfy is warranted on SEND."""
    from shared import audit_checks as ac
    autofixes = []
    attempt = 1
    while attempt <= MAX_ATTEMPTS:
        result = generate(module)
        findings = run_full_audit(module, result)
        worst = ac.classify_worst(findings)
        if worst is None:
            return "SEND", result, [], autofixes
        if worst == ac.CODE:
            return "HELD", result, findings, autofixes
        if worst == ac.DATA:
            refs = {f.pick_ref for f in findings
                    if f.kind == ac.DATA and f.pick_ref}
            result = drop_picks(result, refs)
            autofixes += [f"dropped pick: {r}" for r in sorted(refs)]
            findings = run_full_audit(module, result)
            if ac.classify_worst(findings) is None:
                return "SEND", result, [], autofixes
        if worst == ac.INFRA:
            for f in (x for x in findings if x.kind == ac.INFRA):
                if not autofix_infra(f):
                    return "HELD", result, findings, autofixes
                autofixes.append(f"infra fix: {f.text}")
        attempt += 1
    return "HELD", result, findings, autofixes


def ntfy(title, body):
    """Push a notification to ntfy.sh. Never raises — a failed push is logged,
    not propagated, so it can't crash the pipeline.

    IMPORTANT: HTTP headers are encoded latin-1 by `requests`. The `Title`
    header MUST be pure ASCII — a non-ASCII char (em-dash etc.) raises
    UnicodeEncodeError and the push silently fails. Callers pass ASCII-only
    titles. The body is UTF-8 encoded and may contain anything."""
    try:
        import requests
        requests.post(NTFY_URL, data=body.encode("utf-8"),
                      headers={"Title": title, "Priority": "high",
                               "Tags": "warning"},
                      timeout=15)
    except Exception as e:
        logger.warning(f"ntfy push failed ({title!r}): {e}")


def sync():
    """SAFE parity check of the working tree before a SEND — NOT a git push.

    Deliberate deviation from the plan's 'commit + push auto-fix' sketch:
    in this design code/design bugs are HELD (never auto-patched) and the
    only auto-fix (autofix_infra) runs `docker start` — it never edits
    tracked files. So at SEND time the tree should always be clean. An
    autonomous cron-driven push to main is a real risk with zero upside,
    so sync() only verifies and logs: clean -> log and return; unexpectedly
    dirty -> log a WARNING with the dirty files and return. It never commits
    or pushes."""
    try:
        proc = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=30)
    except Exception as e:
        logger.warning(f"sync: could not run git status: {e}")
        return
    dirty = proc.stdout.strip()
    if not dirty:
        logger.info("sync: repo clean (working tree matches HEAD)")
    else:
        logger.warning("sync: working tree UNEXPECTEDLY DIRTY (not committing, "
                        f"not pushing) — dirty files:\n{dirty}")


def send(module, result):
    """POST the audited-clean picks JSON to the module's n8n webhook.
    Raises requests.HTTPError on a 4xx/5xx status, or RuntimeError if the
    webhook returns 2xx but does not acknowledge the trigger."""
    import requests
    r = requests.post(WEBHOOK[module], json=result, timeout=30)
    r.raise_for_status()
    if "Workflow was started" not in r.text:
        raise RuntimeError(f"webhook did not accept: {r.text[:200]}")


def write_marker(module, outcome):
    """Write the completion marker the dead-man's-switch (deadman.py) reads.
    Called in EVERY terminal branch — SEND, HELD, CRASH — so a silent miss
    is impossible. Guarded so a marker-write failure can't mask the outcome."""
    try:
        os.makedirs(MARKER_DIR, exist_ok=True)
        Path(os.path.join(MARKER_DIR, f"{module}.json")).write_text(
            json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                        "outcome": outcome}))
    except Exception as e:
        logger.error(f"write_marker failed (module={module}, "
                      f"outcome={outcome}): {e}")


def _findings_text(findings):
    """Render a Finding list as a human-readable ntfy body."""
    lines = []
    for f in findings:
        ref = f" [{f.pick_ref}]" if getattr(f, "pick_ref", "") else ""
        lines.append(f"- {f.kind}: {f.text}{ref}")
    return "\n".join(lines) if lines else "(no findings detail)"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="EDGE STACKER self-healing pick pipeline")
    parser.add_argument("--module", required=True, choices=["nhl_sog", "mlb_f5"],
                        help="Which module to run the pipeline for")
    return parser.parse_args(argv)


def _current_et_hour():
    """Current hour (0-23) in US Eastern — DST-correct via stdlib zoneinfo."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).hour


def main(argv=None):
    """Entry point: DST guard -> self-heal loop -> SEND or HELD.

    The cron fires at two UTC times by design; should_run() lets only the
    fire landing on the module's target ET hour proceed. The whole body is
    wrapped in a crash guard that ntfys and writes a CRASH marker."""
    args = parse_args(argv)
    setup_logging()
    load_env()
    module = args.module
    logger.info(f"pipeline start: module={module}")

    try:
        if not should_run(module, _current_et_hour()):
            logger.info("not the target ET hour, exiting (other cron fire "
                         "handles this module)")
            return

        outcome, result, findings, autofixes = heal_loop(module)

        if outcome == "SEND":
            sync()
            send(module, result)
            n_picks = len(result.get("picks", []))
            if autofixes:
                ntfy("EDGE STACKER - picks sent after auto-fix",
                     f"module={module}: email sent ({n_picks} picks). "
                     "Self-heal actions taken this run:\n"
                     + "\n".join(f"- {a}" for a in autofixes))
                logger.info(f"SEND: {n_picks} picks sent; "
                            f"auto-fixes: {autofixes}")
            else:
                logger.info(f"SEND: {n_picks} picks sent; no auto-fix needed")
            write_marker(module, "SEND")
        else:  # HELD
            body = (f"module={module}: picks HELD, NO email sent.\n\n"
                    + _findings_text(findings))
            ntfy("EDGE STACKER - picks HELD", body)
            logger.warning(f"HELD: module={module}; "
                            f"findings:\n{_findings_text(findings)}")
            write_marker(module, "HELD")

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"pipeline CRASHED: {e}\n{tb}")
        ntfy("EDGE STACKER - pipeline CRASHED",
             f"module={module}: unhandled crash, NO email sent.\n\n"
             f"{e}\n\n{tb}")
        write_marker(module, "CRASH")


if __name__ == "__main__":
    main()
