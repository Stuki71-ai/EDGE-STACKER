"""Importable audit checks — shared by pipeline.py and audit.py.

A Finding has a kind that drives the pipeline's self-heal decision:
  INFRA  — mechanically auto-fixable on the VPS (n8n down, file/perm drift)
  DATA   — scoped to one pick; heal by dropping that pick
  CODE   — code/design bug; never auto-patched -> pipeline HOLDS the email

The check functions audit a FRESH in-hand result (a picks JSON dict, as
main.py --json-only emits it) instead of scraping past logs. They have no
global state, send no ntfy, and return list[Finding].
"""
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

INFRA, DATA, CODE = "INFRA", "DATA", "CODE"


@dataclass
class Finding:
    kind: str            # INFRA | DATA | CODE
    text: str            # human-readable description
    pick_ref: str = ""   # matchup/player the DATA finding is scoped to ("" if not)


def classify_worst(findings):
    """Return the most severe finding kind present, or None if clean."""
    for kind in (CODE, DATA, INFRA):   # severity order
        if any(f.kind == kind for f in findings):
            return kind
    return None


# ════════════════════════════════════════════════════════════
# Spec constant table — any deviation = CODE finding.
# Ported verbatim from audit.py.
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

# Module-scoped edge bounds (decimal). Mirror the per-module filters.py
# MIN_EDGE / MAX_EDGE used by check_picks.
_EDGE_BOUNDS = {
    "nhl_sog": (0.10, 0.20),
    "mlb_f5": (0.10, 0.25),
}


def _sh(cmd):
    """Run a shell command, return (rc, stdout+stderr)."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


# ════════════════════════════════════════════════════════════
# check_code_parity — ported from audit.py phase0_code_parity
# ════════════════════════════════════════════════════════════
def check_code_parity(repo):
    """Audit VPS code vs git HEAD + spec constants. Returns list[Finding]."""
    out = []

    # git diff HEAD on tracked module/shared code
    rc, diff = _sh(f"cd {repo} && git diff HEAD -- modules/ shared/ "
                   f"staking.py config.py")
    if diff.strip():
        out.append(Finding(CODE, "VPS code diverges from git HEAD "
                                 f"(uncommitted changes):\n{diff[:600]}"))

    # constants vs spec table
    for relpath, consts in SPEC_CONSTANTS.items():
        fpath = os.path.join(repo, relpath)
        try:
            with open(fpath) as f:
                src = f.read()
        except Exception as e:
            out.append(Finding(CODE, f"Cannot read {relpath} for constant "
                                     f"check: {e}"))
            continue
        for name, expected in consts.items():
            m = re.search(rf"^{name}\s*=\s*([^\n#]+)", src, re.MULTILINE)
            if not m:
                out.append(Finding(CODE, f"Constant {name} not found in "
                                         f"{relpath}"))
                continue
            actual = m.group(1).strip()
            if actual != expected:
                out.append(Finding(CODE, f"Constant drift: {relpath}:{name} "
                                         f"= {actual} (spec: {expected})"))

    # MAX_HOURS_AHEAD == 8 in both runners
    for relpath in ("modules/nhl_sog/runner.py", "modules/mlb_f5/runner.py"):
        fpath = os.path.join(repo, relpath)
        try:
            with open(fpath) as f:
                src = f.read()
        except Exception:
            out.append(Finding(CODE, f"Cannot read {relpath}"))
            continue
        m = re.search(r"^\s*MAX_HOURS_AHEAD\s*=\s*(\d+)", src, re.MULTILINE)
        if not m or m.group(1) != "8":
            out.append(Finding(CODE, f"{relpath}: MAX_HOURS_AHEAD != 8"))

    # forbidden patterns — true unfinished-work markers only
    rc, grep_out = _sh(
        f"grep -rnwE 'TODO|FIXME|XXX|HACK|mock|stub' "
        f"{repo}/modules/nhl_sog {repo}/modules/mlb_f5 "
        f"{repo}/shared/espn_nhl.py {repo}/shared/mlb_data.py "
        f"{repo}/shared/odds_client.py"
    )
    if grep_out.strip():
        out.append(Finding(CODE, f"Forbidden patterns found:\n{grep_out[:500]}"))

    return out


# ════════════════════════════════════════════════════════════
# check_infra — ported from audit.py phase1_infra (n8n + scripts)
# ════════════════════════════════════════════════════════════
def check_infra(repo, n8n_container):
    """Audit infra: n8n container up + core pipeline files present.

    n8n down -> INFRA (mechanically auto-fixable).
    Missing core file (pipeline.py/deadman.py) -> CODE (a deploy is
    broken, not auto-fixable).
    """
    out = []

    # n8n container up
    rc, status = _sh(f"docker ps --filter name={n8n_container} "
                     f"--format '{{{{.Status}}}}'")
    if "Up" not in status:
        out.append(Finding(INFRA, f"n8n container '{n8n_container}' is not "
                                  f"Up (status: {status or 'not found'})"))

    # core pipeline files present — these are what the current VPS-native
    # pipeline needs to function (the obsolete run_afternoon_*.sh / run_audit.sh
    # shell scripts were deleted in the cron cutover and are NOT required).
    for fname in ("pipeline.py", "deadman.py"):
        if not os.path.exists(os.path.join(repo, fname)):
            out.append(Finding(CODE, f"Missing required file: {fname}"))

    return out


# ════════════════════════════════════════════════════════════
# check_data_fetch — ported from audit.py phase15_data
# ════════════════════════════════════════════════════════════
def check_data_fetch(module):
    """Verify upstream data fetch for a module. Returns list[Finding].

    A broken parser / missing static coverage is a CODE bug — it corrupts
    every pick the module produces, so it is never pick-scoped.
    """
    out = []

    if module == "nhl_sog":
        try:
            from shared import espn_nhl
            sa = espn_nhl.get_team_defensive_stats()
            vals = sorted(sa.values())
            n = len(sa)
            placeholders = sum(1 for x in vals if x == 30.0)
            if n != 32:
                out.append(Finding(CODE, f"NHL SA: expected 32 teams, "
                                         f"got {n}"))
            elif placeholders > 0:
                out.append(Finding(CODE, f"NHL SA: {placeholders} teams fell "
                                         f"back to 30.0 placeholder — parser "
                                         f"broken"))
            elif not (18.0 <= vals[0] <= 26.0 and 30.0 <= vals[-1] <= 40.0):
                out.append(Finding(CODE, f"NHL SA: range implausible "
                                         f"{vals[0]:.1f}-{vals[-1]:.1f}"))
        except Exception as e:
            out.append(Finding(CODE, f"NHL SA fetch raised: {e}"))

    elif module == "mlb_f5":
        # MLB FIP constant
        try:
            from shared import mlb_data
            fip = mlb_data.fip_constant(datetime.now(timezone.utc).year)
            if not (2.8 <= fip <= 3.5):
                out.append(Finding(CODE, f"MLB FIP constant {fip} outside "
                                         f"plausible 2.8-3.5 range"))
        except Exception as e:
            out.append(Finding(CODE, f"MLB FIP constant fetch raised: {e}"))

        # MLB park-factor coverage — every active venue must be keyed.
        # A miss = silent 1.00 fallback that corrupts every home F5 total.
        try:
            year = datetime.now(timezone.utc).year
            r = requests.get(
                f"https://statsapi.mlb.com/api/v1/teams?sportId=1"
                f"&season={year}", timeout=20)
            r.raise_for_status()
            venues = {t.get("venue", {}).get("name", "")
                      for t in r.json().get("teams", [])
                      if t.get("venue", {}).get("name")}
            pf_path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "static", "mlb_park_factors.json")
            with open(pf_path) as f:
                pf = json.load(f)
            missing = sorted(v for v in venues if v not in pf)
            if missing:
                out.append(Finding(CODE, f"MLB park factors: {len(missing)} "
                                         f"venue(s) have NO entry — silent "
                                         f"1.00 fallback corrupts those "
                                         f"teams' home games: {missing}"))
        except Exception as e:
            out.append(Finding(CODE, f"MLB park-factor coverage check "
                                     f"raised: {e}"))

    return out


# ════════════════════════════════════════════════════════════
# check_picks — ported from audit.py phase2_pick_sanity
# Operates on the FRESH picks dict (main.py --json-only output).
# ════════════════════════════════════════════════════════════
def _pick_ref(p):
    """Human-readable reference for a pick (matchup / player + description)."""
    desc = p.get("pick_description", "")
    matchup = p.get("matchup", "")
    return f"{matchup} — {desc}".strip(" —")


def check_picks(module, picks):
    """Sanity-check every pick in a fresh result. Returns list[Finding].

    `picks` is the list under output["picks"] from main.py --json-only.
    A pick that fails -> DATA with pick_ref set (heal by dropping that pick).
    """
    out = []
    lo, hi = _EDGE_BOUNDS.get(module, (0.10, 0.25))

    for p in picks:
        if p.get("module") != module:
            continue
        ref = _pick_ref(p)
        try:
            ctx = p.get("context", {})
            edge = float(p.get("edge_pct", 0.0))   # decimal (0.12 = 12%)
            line = ctx.get("line")

            # edge within [MIN_EDGE, MAX_EDGE]
            if not (lo - 1e-6 <= edge <= hi + 1e-6):
                out.append(Finding(DATA, f"{module} pick edge {edge:.1%} "
                                         f"outside [{lo:.0%}, {hi:.0%}]",
                                   pick_ref=ref))

            # line sanity
            if line is None:
                out.append(Finding(DATA, f"{module} pick has no line in "
                                         f"context", pick_ref=ref))
            elif module == "nhl_sog":
                ln = float(line)
                if abs((ln * 2) - round(ln * 2)) > 1e-6:
                    out.append(Finding(DATA, f"NHL pick line {line} is not a "
                                             f"half-point value",
                                       pick_ref=ref))
            elif module == "mlb_f5":
                ln = float(line)
                if not (3.0 <= ln <= 6.5):
                    out.append(Finding(DATA, f"MLB F5 line {line} outside "
                                             f"typical 3.0-6.5 range",
                                       pick_ref=ref))

            # 8h tipoff window — game_time must not already be in the past
            # and must be within 8h of now (the runner enforces this;
            # re-verify here).
            gt_iso = ctx.get("game_date", "")
            gt_str = p.get("game_time", "")
            if gt_iso and gt_str:
                dt = _parse_game_dt(gt_iso, gt_str)
                if dt is not None:
                    hours = (dt - datetime.now(timezone.utc)
                             ).total_seconds() / 3600.0
                    if hours < -0.5:
                        out.append(Finding(DATA, f"{module} pick game already "
                                                 f"started ({hours:.1f}h ago)",
                                           pick_ref=ref))
                    elif hours > 8.0:
                        out.append(Finding(DATA, f"{module} pick game "
                                                 f"{hours:.1f}h out — beyond "
                                                 f"8h window", pick_ref=ref))

            # postponed-game guard (mlb_f5): re-check live schedule status
            if module == "mlb_f5":
                status = _mlb_game_status(p)
                if status and any(bad in status for bad in
                                  ("Postponed", "Cancel", "Suspend")):
                    out.append(Finding(DATA, f"MLB pick game status "
                                             f"'{status}' — "
                                             f"postponed/cancelled",
                                       pick_ref=ref))
        except Exception as e:
            # One malformed pick is itself a DATA finding (pipeline drops it);
            # the remaining picks are still checked.
            out.append(Finding(DATA, f"{module} pick unprocessable: {e}",
                               pick_ref=ref))
            continue

    return out


def _parse_game_dt(date_iso, time_str):
    """Best-effort parse of game datetime from a pick. Returns aware UTC dt.

    game_date is "YYYY-MM-DD" (ET), game_time is e.g. "7:00 PM ET". Treat
    the wall clock as ET (UTC-4/-5; use -4 — the small offset error is far
    inside the 8h window tolerance)."""
    try:
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)", time_str, re.IGNORECASE)
        if not m:
            return None
        hour = int(m.group(1)) % 12
        if m.group(3).upper() == "PM":
            hour += 12
        minute = int(m.group(2))
        d = datetime.fromisoformat(date_iso)
        et = timezone(timedelta(hours=-4))
        return d.replace(hour=hour, minute=minute, tzinfo=et).astimezone(
            timezone.utc)
    except (ValueError, TypeError):
        return None


def _mlb_game_status(pick):
    """Look up the live MLB Stats API status for a pick's game, or ''."""
    try:
        from shared import mlb_data
        ctx = pick.get("context", {})
        date_iso = ctx.get("game_date", "")
        if not date_iso:
            return ""
        matchup = pick.get("matchup", "")
        if "@" not in matchup:
            return ""
        away, home = [s.strip() for s in matchup.split("@", 1)]
        for g in mlb_data.get_schedule(date_iso):
            if g["away_team"] == away and g["home_team"] == home:
                return g.get("status", "")
    except Exception:
        return ""
    return ""


# ════════════════════════════════════════════════════════════
# recompute_pick — ported from audit.py phase25_recompute
# Default tolerance is TIGHT (<=3%) — pipeline.py audits immediately after
# generation, so there is no next-day upstream data drift. The day-after CLI
# audit (audit.py) passes a looser tolerance explicitly (see its call site).
# ════════════════════════════════════════════════════════════
RECOMPUTE_TOLERANCE = 0.03


def _recompute_projection(module, pick):
    """Private end-to-end recompute core — the SINGLE copy of the recompute
    math, shared by recompute_value and recompute_pick.

    Returns a (value, failure) tuple:
      value   — the bare recomputed projection float, or None if it could
                not be produced.
      failure — None on success, otherwise a (cause, detail) tuple naming
                why the recompute could not produce a number. `cause` is a
                stable string code; `detail` carries the cause-specific text
                (player name, matchup, or exception message) that
                recompute_pick needs to reproduce its per-step Findings.

    This never raises: any exception is collapsed into a ("raised", str(e))
    failure so both callers stay total.
    """
    ctx = pick.get("context", {})

    if module == "nhl_sog":
        try:
            from shared import espn_nhl
            from modules.nhl_sog import projections as nhl_proj
            player = ctx.get("player", "")
            eid = espn_nhl.find_espn_player_id(player.strip())
            if not eid:
                return None, ("player_unresolvable", player)
            games = espn_nhl.get_player_gamelog(eid, last_n=None)
            pr = nhl_proj.project_player_sog(
                games, nhl_proj.LEAGUE_AVG_SHOTS_AGAINST)
            if not pr:
                return None, ("projection_none", player)
            return pr["projection"], None
        except Exception as e:
            return None, ("raised", str(e))

    elif module == "mlb_f5":
        try:
            from shared import mlb_data
            from modules.mlb_f5 import projections as mlb_proj
            matchup = pick.get("matchup", "")
            if "@" not in matchup:
                return None, ("matchup_unparseable", matchup)
            fire_date = ctx.get("game_date") or \
                datetime.now(timezone.utc).date().isoformat()
            sched = {(g["away_team"], g["home_team"]): g
                     for g in mlb_data.get_schedule(fire_date)}
            away, home = [s.strip() for s in matchup.split("@", 1)]
            g = sched.get((away, home))
            if not g:
                return None, ("matchup_not_scheduled", (matchup, fire_date))
            ap = mlb_data.get_pitcher_stats(g["away_starter_id"])
            hp = mlb_data.get_pitcher_stats(g["home_starter_id"])
            if not ap or not hp:
                return None, ("starter_stats_unavailable", matchup)
            aw = mlb_data.get_team_woba_vs_hand(g["away_team_id"], hp["hand"])
            hw = mlb_data.get_team_woba_vs_hand(g["home_team_id"], ap["hand"])
            pf = mlb_data.park_factor(g["venue"])
            wf = ctx.get("weather_factor", 1.0)
            value = mlb_proj.project_total_f5(
                hp["xFIP_30d"], aw, ap["xFIP_30d"], hw, pf, wf)
            return value, None
        except Exception as e:
            return None, ("raised", str(e))

    return None, ("unknown_module", module)


def recompute_value(module, pick):
    """Independently recompute one pick's projection end-to-end from raw
    upstream data and return the bare number (float), or None if it cannot
    be produced (player/matchup unresolvable, stats unavailable, raise).

    This is the same math recompute_pick performs internally, exposed as a
    value so the deep-audit evidence bundle can carry the recomputed
    projection alongside the logged one (audit-spec Check 5). recompute_pick
    keeps its own findings-producing logic; this helper never returns
    Findings and never raises.
    """
    return _recompute_projection(module, pick)[0]


def recompute_pick(module, pick, tolerance=RECOMPUTE_TOLERANCE):
    """Recompute one pick end-to-end from raw upstream data.

    Mismatch beyond `tolerance` -> CODE (formula bug), unless the cause is a
    pick-specific bad upstream value -> DATA with pick_ref. Returns
    list[Finding].

    `tolerance` only governs the MLB projected-total drift comparison.
    pipeline.py omits it (default 0.03 — immediate post-generation audit,
    no drift). audit.py is the day-after CLI audit and passes a looser value
    so benign next-day MLB Stats API data drift is not a false positive.
    The NHL branch uses a plausibility band, not a tolerance — unaffected.
    """
    out = []
    ref = _pick_ref(pick)
    ctx = pick.get("context", {})

    # Recompute the projection through the single shared core. `failure`
    # names the cause when no value could be produced, so the per-step
    # Findings below stay byte-identical to the old inline implementation.
    recomp, failure = _recompute_projection(module, pick)

    if module == "nhl_sog":
        if failure is not None:
            cause, detail = failure
            if cause == "player_unresolvable":
                out.append(Finding(DATA, f"NHL recompute: player '{detail}' "
                                         f"not resolvable in roster cache",
                                   pick_ref=ref))
            elif cause == "projection_none":
                out.append(Finding(DATA, f"NHL recompute: projection returned "
                                         f"None for {detail}", pick_ref=ref))
            else:  # raised
                out.append(Finding(CODE, f"NHL recompute raised: {detail}"))
            return out
        # The recompute uses a league-avg opponent, while the pick used
        # the real opponent SA (capped 0.85-1.15). So compare against a
        # plausibility band, not the logged projection.
        player = ctx.get("player", "")
        if not (0.3 <= recomp <= 8.0):
            out.append(Finding(CODE, f"NHL recompute: {player} projection "
                                     f"{recomp} implausible"))

    elif module == "mlb_f5":
        matchup = pick.get("matchup", "")
        logged_proj = ctx.get("projection")
        if failure is not None:
            cause, detail = failure
            if cause == "matchup_unparseable":
                out.append(Finding(DATA, f"MLB recompute: matchup '{detail}' "
                                         f"unparseable", pick_ref=ref))
            elif cause == "matchup_not_scheduled":
                mu, fire_date = detail
                out.append(Finding(DATA, f"MLB recompute: matchup '{mu}' "
                                         f"not in schedule for {fire_date}",
                                   pick_ref=ref))
            elif cause == "starter_stats_unavailable":
                out.append(Finding(DATA, f"MLB recompute: starter stats "
                                         f"unavailable for {detail}",
                                   pick_ref=ref))
            else:  # raised
                out.append(Finding(CODE, f"MLB recompute raised: {detail}"))
            return out
        if logged_proj is None:
            out.append(Finding(DATA, f"MLB recompute: pick has no logged "
                                     f"projection", pick_ref=ref))
            return out
        lp = float(logged_proj)
        drift = abs(recomp - lp) / lp if lp else 1.0
        if drift > tolerance:
            out.append(Finding(CODE, f"MLB recompute: {matchup} logged "
                                     f"proj {lp} vs recomputed "
                                     f"{recomp:.2f} — {drift:.1%} drift > "
                                     f"{tolerance:.0%}"))

    return out
