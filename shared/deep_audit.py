"""Claude-API deep audit — evidence-gathering layer.

Task 2 of docs/plans/2026-05-18-claude-api-deep-audit.md: gather_evidence()
collects a deterministic evidence bundle (picks, re-fetched upstream
summaries, code/constant parity, end-to-end recompute, fire log, mechanical
audit findings) for a freshly-generated picks result. A later task feeds the
bundle to the Claude API for judgment.

gather_evidence() is a thin public wrapper around the private _collect():
ALL heavy work (git, subprocess, fetchers, recompute, filesystem) lives in
_collect so tests can monkeypatch it and never touch the network or the VPS.
"""
import glob
import os
from datetime import datetime, timezone

import requests

from shared import audit_checks as ac

# Repo root is the parent of shared/ — resolve relative to this file so the
# evidence layer works regardless of the pipeline's cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cap the fire log embedded in the bundle so the JSON stays tens of KB.
MAX_FIRE_LOG_BYTES = 40_000


def _serialize_findings(findings):
    """Render a list[Finding] as plain JSON-safe dicts."""
    return [{"kind": f.kind, "text": f.text, "pick_ref": f.pick_ref}
            for f in findings]


def _read_fire_log(repo):
    """Return the newest logs/edge-stacker-*.log contents, truncated to the
    last MAX_FIRE_LOG_BYTES. Empty string if no fire log exists."""
    matches = glob.glob(os.path.join(repo, "logs", "edge-stacker-*.log"))
    if not matches:
        return ""
    newest = max(matches, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return text[-MAX_FIRE_LOG_BYTES:]


def _data_fetch_summary(module):
    """Re-fetch the upstream SUMMARIES a deep audit needs (ESPN / MLB-StatsAPI
    only — never the Odds API, which costs credits). Returns a plain dict.

    Per-source failures are captured as an `error` string rather than raised:
    a missing section is itself audit evidence."""
    summary = {}

    if module == "nhl_sog":
        try:
            from shared import espn_nhl
            sa = espn_nhl.get_team_defensive_stats()
            vals = sorted(sa.values())
            summary["nhl_shots_against"] = {
                "team_count": len(sa),
                "placeholder_count": sum(1 for x in vals if x == 30.0),
                "min": vals[0] if vals else None,
                "max": vals[-1] if vals else None,
            }
        except Exception as e:
            summary["nhl_shots_against"] = {"error": str(e)}

    elif module == "mlb_f5":
        year = datetime.now(timezone.utc).year
        try:
            from shared import mlb_data
            summary["mlb_fip_constant"] = {
                "season": year,
                "value": mlb_data.fip_constant(year),
            }
        except Exception as e:
            summary["mlb_fip_constant"] = {"error": str(e)}

        try:
            import json
            r = requests.get(
                f"https://statsapi.mlb.com/api/v1/teams?sportId=1"
                f"&season={year}", timeout=20)
            r.raise_for_status()
            venues = {t.get("venue", {}).get("name", "")
                      for t in r.json().get("teams", [])
                      if t.get("venue", {}).get("name")}
            pf_path = os.path.join(REPO_ROOT, "static",
                                   "mlb_park_factors.json")
            with open(pf_path, encoding="utf-8") as f:
                pf = json.load(f)
            summary["mlb_park_factors"] = {
                "venue_count": len(venues),
                "keyed_count": len(pf),
                "missing_venues": sorted(v for v in venues if v not in pf),
            }
        except Exception as e:
            summary["mlb_park_factors"] = {"error": str(e)}

    return summary


def _collect(module, result):
    """Heavy evidence-gathering: git/subprocess, upstream re-fetches, the
    end-to-end recompute, the fire log, and the mechanical audit. Kept private
    so tests can stub it. Returns the evidence dict."""
    picks = result.get("picks", [])

    code_parity = ac.check_code_parity(REPO_ROOT)
    mechanical = list(code_parity)
    mechanical += ac.check_infra(REPO_ROOT, "n8n-n8n-1")
    mechanical += ac.check_data_fetch(module)
    mechanical += ac.check_picks(module, picks)

    recompute = []
    for p in picks:
        findings = ac.recompute_pick(module, p)
        # Also capture the bare recomputed projection: recompute_pick's
        # findings fire ONLY on drift beyond its internal threshold, so a
        # clean pick yields no number. The Claude judge needs both values
        # to run audit-spec Check 5's tolerance comparison.
        recompute.append({
            "pick_ref": ac._pick_ref(p),
            "logged_projection": p.get("context", {}).get("projection"),
            "recomputed_projection": ac.recompute_value(module, p),
            "findings": _serialize_findings(findings),
        })
        mechanical += findings

    return {
        "module": module,
        "picks": picks,
        "code_parity": _serialize_findings(code_parity),
        "data_fetch": _data_fetch_summary(module),
        "recompute": recompute,
        "fire_log": _read_fire_log(REPO_ROOT),
        "mechanical_findings": _serialize_findings(mechanical),
    }


def gather_evidence(module, result):
    """Public entry point: collect the deterministic evidence bundle for
    `module` + a fresh `result` (a picks JSON dict, as main.py --json-only
    emits it). Thin wrapper — delegates all work to _collect."""
    return _collect(module, result)
