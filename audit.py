#!/usr/bin/env python3
"""EDGE STACKER VPS audit — runs 10 min after the NHL fire (04:40 PM ET).

Mechanical, deterministic audit of the last completed NHL + MLB F5 fires.

This is the ad-hoc CLI audit. The CHECK LOGIC (spec-constant parity, infra
verification, data-fetch verification, per-pick sanity, one-pick recompute)
lives in ONE place — shared/audit_checks.py — and is shared with pipeline.py.
audit.py delegates to it and keeps only its own distinct responsibilities:

  - locating the last completed fire by scraping the VPS logs,
  - extracting that fire's picks out of the log text,
  - auto-fixing INFRA issues itself (chmod a script, restart n8n, re-fire a
    workflow whose webhook never landed) — silent, no ntfy,
  - ntfy'ing topic 'Stuki71-Findings' ONLY when something needs the user's
    decision (a CODE/DATA finding, or an auto-fix that FAILED).

Finding-kind -> outcome mapping (the bridge to audit_checks):
  INFRA  -> mechanically auto-fixable; audit.py attempts the fix and reports
            AUTOFIXED on success, DECISION on failure.
  CODE / DATA  -> always DECISION (needs the user's approval).

Run from /root/edge-stacker with the venv active (see run_audit.sh).
"""

import logging
import os
import re
import subprocess
import sys
from glob import glob

import requests

REPO = "/root/edge-stacker"
LOG_DIR = os.path.join(REPO, "logs")
NTFY_URL = "https://ntfy.sh/Stuki71-Findings"
N8N_CONTAINER = "n8n-n8n-1"
NHL_WEBHOOK = "https://vmi3157940.contaboserver.net/webhook/edge-stacker-nhl"
MLB_WEBHOOK = "https://vmi3157940.contaboserver.net/webhook/edge-stacker-mlb"

sys.path.insert(0, REPO)
from shared import audit_checks  # noqa: E402

# ── audit logger ──
logger = logging.getLogger("edge_stacker_audit")
logger.setLevel(logging.INFO)
_h = logging.FileHandler(os.path.join(LOG_DIR, "audit.log"))
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))

# ── findings: each is (kind, text). kind in {"AUTOFIXED", "DECISION"} ──
findings = []


def decision(text):
    findings.append(("DECISION", text))
    logger.error(f"DECISION-NEEDED: {text}")


def autofixed(text):
    findings.append(("AUTOFIXED", text))
    logger.warning(f"AUTO-FIXED: {text}")


def ok(text):
    logger.info(f"OK: {text}")


def sh(cmd):
    """Run a shell command, return (rc, stdout+stderr)."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _report(check_findings):
    """Bridge: turn audit_checks Finding objects into AUTOFIXED/DECISION items.

    CODE / DATA findings always need the user's decision. INFRA findings are
    handled by the dedicated auto-fix paths (phase1_infra), so any INFRA
    finding reaching here unfixed is also surfaced as a DECISION."""
    for f in check_findings:
        decision(f.text if not f.pick_ref else f"[{f.pick_ref}] {f.text}")
    return check_findings


def phase0_code_parity():
    logger.info("=== PHASE 0 — code parity ===")
    rc, head = sh(f"cd {REPO} && git log -1 --oneline")
    ok(f"VPS HEAD: {head}")
    parity_findings = _report(audit_checks.check_code_parity(REPO))
    if not parity_findings:
        ok("code parity clean — git HEAD, spec constants, forbidden-pattern scan")


def _latest_nhl_log():
    files = glob(os.path.join(LOG_DIR, "edge-stacker-*-2030.log"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _last_run_block(module_token):
    """Return the last run block for a module from cron.log, or ''.

    Both run_afternoon_*.sh scripts send the python run's stderr (where the
    logging StreamHandler writes) to cron.log, and the cron line appends the
    curl output too. So the 'Workflow was started' webhook confirmation lives
    in cron.log — NOT in the per-day edge-stacker-*.log file.
    """
    cron = os.path.join(LOG_DIR, "cron.log")
    if not os.path.exists(cron):
        return ""
    marker = f"modules: ['{module_token}']"
    block, capturing = [], False
    with open(cron, errors="replace") as f:
        for line in f:
            if marker in line:
                block, capturing = [line], True
            elif capturing:
                # a different module's run starts a new block — stop capturing
                if "modules: ['" in line and marker not in line:
                    capturing = False
                else:
                    block.append(line)
    return "".join(block)


# ════════════════════════════════════════════════════════════
# Log-scraping -> pick dicts (audit.py's own responsibility).
# audit_checks.check_picks / recompute_pick consume the picks-JSON dict shape
# main.py emits (top-level edge_pct / matchup / game_time, nested context).
# The ad-hoc audit only has the fire's LOG TEXT, so it reconstructs the
# minimal subset of that shape the check functions actually read.
# ════════════════════════════════════════════════════════════
def _scrape_nhl_picks(txt):
    """Reconstruct nhl_sog pick dicts from per-day log text."""
    picks = []
    for player, direction, line, edge in re.findall(
            r"NHL PICK: (.+?) (OVER|UNDER) ([\d.]+) SOG \| edge=([\d.]+)%", txt):
        picks.append({
            "module": "nhl_sog",
            "matchup": "",
            "pick_description": f"{player.strip()} {direction} {line} SOG",
            "edge_pct": float(edge) / 100.0,
            "game_time": "",
            "context": {"player": player.strip(), "line": float(line)},
        })
    return picks


def _scrape_mlb_picks(block):
    """Reconstruct mlb_f5 pick dicts from the cron.log run block."""
    run_date = re.search(r"EDGE STACKER run: (\d{4}-\d{2}-\d{2})", block)
    game_date = run_date.group(1) if run_date else ""
    picks = []
    for matchup, direction, line, proj, edge in re.findall(
            r"MLB PICK: (.+?) F5 (OVER|UNDER) ([\d.]+) \| proj=([\d.]+) \| edge=([\d.]+)%",
            block):
        picks.append({
            "module": "mlb_f5",
            "matchup": matchup.strip(),
            "pick_description": f"F5 {direction} {line}",
            "edge_pct": float(edge) / 100.0,
            "game_time": "",
            "context": {
                "line": float(line),
                "projection": float(proj),
                "game_date": game_date,
            },
        })
    return picks


def phase1_infra():
    logger.info("=== PHASE 1 — infra (auto-fix authorized) ===")

    # cron scripts executable — AUTO-FIX
    for script in ("run_afternoon_nhl.sh", "run_afternoon_mlb.sh", "run_audit.sh"):
        path = os.path.join(REPO, script)
        if not os.path.exists(path):
            decision(f"Missing script: {script}")
            continue
        if not os.access(path, os.X_OK):
            rc, _ = sh(f"chmod +x {path}")
            if rc == 0:
                autofixed(f"chmod +x {script} (was not executable)")
            else:
                decision(f"{script} not executable and chmod failed")
        else:
            ok(f"{script} executable")

    # n8n container up — AUTO-FIX restart
    rc, out = sh(f"docker ps --filter name={N8N_CONTAINER} --format '{{{{.Status}}}}'")
    if "Up" not in out:
        rc2, _ = sh(f"docker start {N8N_CONTAINER}")
        if rc2 == 0:
            autofixed(f"n8n container was down — restarted")
        else:
            decision(f"n8n container down and restart failed: {out}")
    else:
        ok(f"n8n container Up ({out})")

    # NHL fire log
    nhl_log = _latest_nhl_log()
    nhl_picks = []
    if not nhl_log:
        # cron likely failed — AUTO-FIX: fire NHL now
        _refire("nhl_sog", NHL_WEBHOOK, reason="no NHL fire log found")
    else:
        ok(f"NHL log: {os.path.basename(nhl_log)}")
        with open(nhl_log, errors="replace") as f:
            txt = f.read()
        nhl_picks = _scrape_nhl_picks(txt)
        n_qual = re.search(r"nhl_sog: (\d+) qualifying picks", txt)
        # The webhook confirmation ('Workflow was started', emitted by curl) is
        # written to cron.log, never to the per-day edge-stacker-*.log file.
        nhl_block = _last_run_block("nhl_sog")
        started = "Workflow was started" in nhl_block
        if n_qual:
            ok(f"NHL: {n_qual.group(1)} qualifying picks, {len(nhl_picks)} PICK lines")
        if not started:
            # picks generated but webhook never landed — AUTO-FIX re-fire
            _refire("nhl_sog", NHL_WEBHOOK, reason="NHL fire present but no 'Workflow was started' in cron.log")
        else:
            ok("NHL webhook accepted (Workflow was started)")

    # MLB fire block
    mlb_block = _last_run_block("mlb_f5")
    mlb_picks = []
    if not mlb_block:
        _refire("mlb_f5", MLB_WEBHOOK, reason="no MLB fire block in cron.log")
    else:
        ok("MLB fire block located in cron.log")
        mlb_picks = _scrape_mlb_picks(mlb_block)
        n_qual = re.search(r"mlb_f5: (\d+) qualifying picks", mlb_block)
        started = "Workflow was started" in mlb_block
        if n_qual:
            ok(f"MLB: {n_qual.group(1)} qualifying picks, {len(mlb_picks)} PICK lines")
        if not started:
            _refire("mlb_f5", MLB_WEBHOOK, reason="MLB fire block present but no 'Workflow was started'")
        else:
            ok("MLB webhook accepted (Workflow was started)")

    return nhl_log, nhl_picks, mlb_block, mlb_picks


def _refire(module, webhook, reason):
    """Auto-fix: re-run a module and POST to its webhook. Infra fix => no ntfy
    unless it fails."""
    logger.warning(f"AUTO-FIX re-fire {module}: {reason}")
    rc, out = sh(
        f"cd {REPO} && python main.py --modules {module} --json-only "
        f"> /tmp/_audit_{module}.json 2>>{LOG_DIR}/audit.log"
    )
    if rc != 0 or not os.path.exists(f"/tmp/_audit_{module}.json"):
        decision(f"Auto-refire of {module} FAILED to produce output ({reason}) — needs manual run")
        return
    size = os.path.getsize(f"/tmp/_audit_{module}.json")
    if size < 10:
        decision(f"Auto-refire of {module} produced empty output ({reason}) — investigate")
        return
    rc2, out2 = sh(
        f'curl -s -X POST {webhook} -H "Content-Type: application/json" '
        f'-d @/tmp/_audit_{module}.json'
    )
    if "Workflow was started" in out2:
        autofixed(f"{module}: {reason} — re-fired, webhook accepted")
    else:
        decision(f"{module}: {reason} — re-fire POST did not return 'Workflow was started': {out2[:200]}")


def phase15_data():
    logger.info("=== PHASE 1.5 — data-fetch verification ===")
    dec_findings = _report(audit_checks.check_data_fetch("nhl_sog"))
    dec_findings += _report(audit_checks.check_data_fetch("mlb_f5"))
    if not dec_findings:
        ok("data-fetch verification clean — NHL SA, MLB FIP, MLB park factors")


def phase2_pick_sanity(nhl_picks, mlb_picks):
    logger.info("=== PHASE 2 — pick sanity ===")
    nf = _report(audit_checks.check_picks("nhl_sog", nhl_picks))
    if not nf:
        ok(f"NHL pick sanity clean ({len(nhl_picks)} picks)")
    mf = _report(audit_checks.check_picks("mlb_f5", mlb_picks))
    if not mf:
        ok(f"MLB pick sanity clean ({len(mlb_picks)} picks)")


def phase25_recompute(nhl_picks, mlb_picks):
    """Recompute one pick per module via audit_checks — gross mismatch
    surfaces as a DECISION.

    audit.py is the DAY-AFTER CLI audit, so the MLB recompute runs against
    upstream data that has moved on since the fire (new game logs, updated
    wOBA splits). That benign next-day MLB Stats API drift routinely reaches
    ~25%, so we pass an explicit 30% tolerance — pipeline.py keeps the tight
    3% default because it audits immediately after generation (no drift)."""
    logger.info("=== PHASE 2.5 — pick recompute ===")
    if nhl_picks:
        rf = _report(audit_checks.recompute_pick("nhl_sog", nhl_picks[0]))
        if not rf:
            ok(f"NHL recompute clean ({nhl_picks[0]['context']['player']})")
    if mlb_picks:
        rf = _report(audit_checks.recompute_pick(
            "mlb_f5", mlb_picks[0], tolerance=0.30))
        if not rf:
            ok(f"MLB recompute clean ({mlb_picks[0]['matchup']})")


def send_ntfy(decision_items):
    body_lines = ["EDGE STACKER audit - items needing your decision:\n"]
    for i, text in enumerate(decision_items, 1):
        body_lines.append(f"{i}. {text}")
    body = "\n".join(body_lines)
    # HTTP headers are latin-1 encoded by requests — keep Title strictly ASCII
    # (an em-dash here crashes the POST). The body is UTF-8 encoded, so it may
    # contain any character.
    try:
        requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={"Title": "EDGE STACKER audit - action needed",
                     "Priority": "high", "Tags": "warning"},
            timeout=15,
        )
        logger.info(f"ntfy sent ({len(decision_items)} decision items)")
    except Exception as e:
        logger.error(f"ntfy POST failed: {e}")


def main():
    logger.info("########## EDGE STACKER VPS AUDIT START ##########")
    try:
        phase0_code_parity()
        nhl_log, nhl_picks, mlb_block, mlb_picks = phase1_infra()
        phase15_data()
        phase2_pick_sanity(nhl_picks, mlb_picks)
        phase25_recompute(nhl_picks, mlb_picks)
    except Exception as e:
        decision(f"Audit script itself raised an unhandled exception: {e}")

    autofixes = [t for k, t in findings if k == "AUTOFIXED"]
    decisions = [t for k, t in findings if k == "DECISION"]

    logger.info(f"SUMMARY: {len(autofixes)} auto-fixed, {len(decisions)} need decision")
    for t in autofixes:
        logger.info(f"  [auto-fixed] {t}")
    for t in decisions:
        logger.info(f"  [DECISION]   {t}")

    # ntfy ONLY if something needs the user's decision/approval
    if decisions:
        send_ntfy(decisions)
    else:
        logger.info("No decisions needed — staying silent (no ntfy).")

    logger.info("########## EDGE STACKER VPS AUDIT END ##########")


if __name__ == "__main__":
    main()
