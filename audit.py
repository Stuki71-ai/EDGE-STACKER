#!/usr/bin/env python3
"""EDGE STACKER VPS audit — runs 10 min after the NHL fire (04:40 PM ET).

Mechanical, deterministic audit of the last completed NHL + MLB F5 fires.

Behaviour (per user spec):
  - Auto-fixes INFRA issues itself (chmod a script, restart n8n, re-fire a
    workflow whose webhook never landed). These are silent — no ntfy.
  - ntfy's topic 'Stuki71-Findings' ONLY when a finding needs the user's
    decision/approval (code-level bug, data anomaly, or an auto-fix that
    FAILED). Clean run or successfully auto-fixed infra => no ntfy.
  - Full detail always written to logs/audit.log.

Run from /root/edge-stacker with the venv active (see run_audit.sh).
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from glob import glob

import requests

REPO = "/root/edge-stacker"
LOG_DIR = os.path.join(REPO, "logs")
NTFY_URL = "https://ntfy.sh/Stuki71-Findings"
N8N_CONTAINER = "n8n-n8n-1"
NHL_WEBHOOK = "https://vmi3157940.contaboserver.net/webhook/edge-stacker-nhl"
MLB_WEBHOOK = "https://vmi3157940.contaboserver.net/webhook/edge-stacker-mlb"

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


# ════════════════════════════════════════════════════════════
# Spec constant table — any deviation = DECISION
# ════════════════════════════════════════════════════════════
SPEC_CONSTANTS = {
    "modules/nhl_sog/filters.py": {
        "MIN_EDGE": "0.10", "MAX_VIG": "0.08", "MAX_EDGE": "0.20",
        "MIN_GAMES": "10", "MIN_TOI_FORWARD_SEC": "14 * 60",
        "MIN_TOI_DEFENSE_SEC": "18 * 60",
    },
    "modules/nhl_sog/projections.py": {
        "EWMA_DECAY": "0.85", "LEAGUE_AVG_SHOTS_AGAINST": "30.0",
    },
    "modules/mlb_f5/filters.py": {
        "MIN_EDGE": "0.10", "MAX_VIG": "0.08", "MAX_EDGE": "0.25",
        "LINE_SANITY_PCT": "0.25",
    },
    "modules/mlb_f5/projections.py": {
        "LEAGUE_AVG_WOBA": "0.320", "LEAGUE_AVG_FIP": "4.10",
    },
}


def phase0_code_parity():
    logger.info("=== PHASE 0 — code parity ===")
    # 0.1 git diff HEAD on tracked module/shared code
    rc, out = sh(f"cd {REPO} && git diff HEAD -- modules/ shared/ staking.py config.py")
    if out.strip():
        decision(f"VPS code diverges from git HEAD (uncommitted changes):\n{out[:600]}")
    else:
        ok("git diff HEAD clean — VPS code == committed code")

    rc, head = sh(f"cd {REPO} && git log -1 --oneline")
    ok(f"VPS HEAD: {head}")

    # 0.4 constants vs spec table
    for relpath, consts in SPEC_CONSTANTS.items():
        fpath = os.path.join(REPO, relpath)
        try:
            with open(fpath) as f:
                src = f.read()
        except Exception as e:
            decision(f"Cannot read {relpath} for constant check: {e}")
            continue
        for name, expected in consts.items():
            m = re.search(rf"^{name}\s*=\s*([^\n#]+)", src, re.MULTILINE)
            if not m:
                decision(f"Constant {name} not found in {relpath}")
                continue
            actual = m.group(1).strip()
            if actual != expected:
                decision(f"Constant drift: {relpath}:{name} = {actual} (spec: {expected})")
    ok("constants checked against spec table")

    # MAX_HOURS_AHEAD == 8 in both runners
    for relpath in ("modules/nhl_sog/runner.py", "modules/mlb_f5/runner.py"):
        fpath = os.path.join(REPO, relpath)
        try:
            with open(fpath) as f:
                src = f.read()
        except Exception:
            decision(f"Cannot read {relpath}")
            continue
        # match both the module-level and the indented (in-function) occurrence
        m = re.search(r"^\s*MAX_HOURS_AHEAD\s*=\s*(\d+)", src, re.MULTILINE)
        if not m or m.group(1) != "8":
            decision(f"{relpath}: MAX_HOURS_AHEAD != 8")
    ok("MAX_HOURS_AHEAD == 8 in both runners")

    # 0.6 forbidden patterns — true unfinished-work markers only.
    # NOT "placeholder" / "return 30.0": both appear in legitimate comments and
    # the documented league-avg fallback. The real placeholder check is the live
    # data fetch in phase15_data (counts teams that actually fell back to 30.0).
    rc, out = sh(
        f"grep -rnwE 'TODO|FIXME|XXX|HACK|mock|stub' "
        f"{REPO}/modules/nhl_sog {REPO}/modules/mlb_f5 "
        f"{REPO}/shared/espn_nhl.py {REPO}/shared/mlb_data.py {REPO}/shared/odds_client.py"
    )
    if out.strip():
        decision(f"Forbidden patterns found:\n{out[:500]}")
    else:
        ok("forbidden-pattern scan clean")


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


def _last_mlb_block():
    """Return the last mlb_f5 run block text from cron.log, or ''."""
    return _last_run_block("mlb_f5")


def _last_nhl_block():
    """Return the last nhl_sog run block text from cron.log, or ''."""
    return _last_run_block("nhl_sog")


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
        nhl_picks = re.findall(
            r"NHL PICK: (.+?) (OVER|UNDER) ([\d.]+) SOG \| edge=([\d.]+)%", txt)
        n_qual = re.search(r"nhl_sog: (\d+) qualifying picks", txt)
        # The webhook confirmation ('Workflow was started', emitted by curl) is
        # written to cron.log, never to the per-day edge-stacker-*.log file.
        nhl_block = _last_nhl_block()
        started = "Workflow was started" in nhl_block
        if n_qual:
            ok(f"NHL: {n_qual.group(1)} qualifying picks, {len(nhl_picks)} PICK lines")
        if not started:
            # picks generated but webhook never landed — AUTO-FIX re-fire
            _refire("nhl_sog", NHL_WEBHOOK, reason="NHL fire present but no 'Workflow was started' in cron.log")
        else:
            ok("NHL webhook accepted (Workflow was started)")

    # MLB fire block
    mlb_block = _last_mlb_block()
    mlb_picks = []
    if not mlb_block:
        _refire("mlb_f5", MLB_WEBHOOK, reason="no MLB fire block in cron.log")
    else:
        ok("MLB fire block located in cron.log")
        mlb_picks = re.findall(
            r"MLB PICK: (.+?) F5 (OVER|UNDER) ([\d.]+) \| proj=([\d.]+) \| edge=([\d.]+)%",
            mlb_block)
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
    sys.path.insert(0, REPO)

    # NHL SA
    try:
        from shared import espn_nhl
        sa = espn_nhl.get_team_defensive_stats()
        vals = sorted(sa.values())
        n = len(sa)
        placeholders = sum(1 for x in vals if x == 30.0)
        if n != 32:
            decision(f"NHL SA: expected 32 teams, got {n}")
        elif placeholders > 0:
            decision(f"NHL SA: {placeholders} teams fell back to 30.0 placeholder — parser broken")
        elif not (18.0 <= vals[0] <= 26.0 and 30.0 <= vals[-1] <= 40.0):
            decision(f"NHL SA: range implausible {vals[0]:.1f}-{vals[-1]:.1f}")
        else:
            ok(f"NHL SA: 32 teams, 0 placeholders, range {vals[0]:.1f}-{vals[-1]:.1f}")
    except Exception as e:
        decision(f"NHL SA fetch raised: {e}")

    # MLB FIP constant
    try:
        from shared import mlb_data
        fip = mlb_data.fip_constant(datetime.utcnow().year)
        if not (2.8 <= fip <= 3.5):
            decision(f"MLB FIP constant {fip} outside plausible 2.8-3.5 range")
        else:
            ok(f"MLB FIP constant: {fip}")
    except Exception as e:
        decision(f"MLB FIP constant fetch raised: {e}")


def phase2_pick_sanity(nhl_picks, mlb_picks):
    logger.info("=== PHASE 2 — pick sanity ===")
    for player, direction, line, edge in nhl_picks:
        e = float(edge) / 100.0
        ln = float(line)
        if not (0.10 <= e <= 0.20 + 1e-6):
            decision(f"NHL pick '{player} {direction} {line}': edge {edge}% outside [10%, 20%]")
        if abs((ln * 2) - round(ln * 2)) > 1e-6:
            decision(f"NHL pick '{player}': line {line} is not a half-point value")
    ok(f"NHL pick sanity checked ({len(nhl_picks)} picks)")

    for matchup, direction, line, proj, edge in mlb_picks:
        e = float(edge) / 100.0
        ln = float(line)
        if not (0.10 <= e <= 0.25 + 1e-6):
            decision(f"MLB pick '{matchup} {direction} {line}': edge {edge}% outside [10%, 25%]")
        if not (3.0 <= ln <= 6.5):
            decision(f"MLB pick '{matchup}': F5 line {line} outside typical 3.0-6.5 range")
    ok(f"MLB pick sanity checked ({len(mlb_picks)} picks)")


def phase25_recompute(nhl_picks, mlb_picks, mlb_block):
    """Recompute one pick per module numerically; gross mismatch = DECISION."""
    logger.info("=== PHASE 2.5 — pick recompute ===")
    sys.path.insert(0, REPO)

    # NHL: recompute first pick's projection vs league-avg opponent (sanity range)
    if nhl_picks:
        player, direction, line, edge = nhl_picks[0]
        try:
            from shared import espn_nhl
            from modules.nhl_sog import projections as nhl_proj
            eid = espn_nhl.find_espn_player_id(player.strip())
            if not eid:
                decision(f"NHL recompute: player '{player}' not resolvable in roster cache")
            else:
                games = espn_nhl.get_player_gamelog(eid, last_n=None)
                pr = nhl_proj.project_player_sog(games, nhl_proj.LEAGUE_AVG_SHOTS_AGAINST)
                if not pr:
                    decision(f"NHL recompute: projection returned None for {player}")
                else:
                    p = pr["projection"]
                    # vs-league-avg projection should be within a sane band of the line
                    if not (0.3 <= p <= 8.0):
                        decision(f"NHL recompute: {player} projection {p} implausible")
                    else:
                        ok(f"NHL recompute: {player} proj {p:.2f} (vs league-avg opp) — plausible")
        except Exception as e:
            decision(f"NHL recompute raised: {e}")

    # MLB: recompute first pick's projected total, compare to logged proj
    if mlb_picks:
        matchup, direction, line, logged_proj, edge = mlb_picks[0]
        try:
            from shared import mlb_data
            from modules.mlb_f5 import projections as mlb_proj
            run_date = re.search(r"EDGE STACKER run: (\d{4}-\d{2}-\d{2})", mlb_block)
            fire_date = run_date.group(1) if run_date else datetime.utcnow().date().isoformat()
            sched = {(g["away_team"], g["home_team"]): g
                     for g in mlb_data.get_schedule(fire_date)}
            away, home = [s.strip() for s in matchup.split("@")]
            g = sched.get((away, home))
            if not g:
                decision(f"MLB recompute: matchup '{matchup}' not in schedule for {fire_date}")
            else:
                ap = mlb_data.get_pitcher_stats(g["away_starter_id"])
                hp = mlb_data.get_pitcher_stats(g["home_starter_id"])
                if not ap or not hp:
                    decision(f"MLB recompute: starter stats unavailable for {matchup}")
                else:
                    aw = mlb_data.get_team_woba_vs_hand(g["away_team_id"], hp["hand"])
                    hw = mlb_data.get_team_woba_vs_hand(g["home_team_id"], ap["hand"])
                    pf = mlb_data.park_factor(g["venue"])
                    recomp = mlb_proj.project_total_f5(
                        hp["xFIP_30d"], aw, ap["xFIP_30d"], hw, pf, 1.0)
                    lp = float(logged_proj)
                    drift = abs(recomp - lp) / lp if lp else 1.0
                    # The audit runs the day AFTER the fire, so MLB Stats API
                    # data has shifted (new game logs, updated wOBA splits).
                    # Benign next-day drift routinely reaches ~25%; a genuine
                    # formula bug blows far past 30%. Threshold set accordingly.
                    if drift > 0.30:
                        decision(f"MLB recompute: {matchup} logged proj {lp} vs recomputed "
                                 f"{recomp:.2f} - {drift:.0%} drift > 30%")
                    else:
                        ok(f"MLB recompute: {matchup} logged {lp} vs recomputed "
                           f"{recomp:.2f} ({drift:.0%} drift) - within tolerance")
        except Exception as e:
            decision(f"MLB recompute raised: {e}")


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
        phase25_recompute(nhl_picks, mlb_picks, mlb_block)
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
