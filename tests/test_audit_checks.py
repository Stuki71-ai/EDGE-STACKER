"""Tests for the pure pieces of shared/audit_checks.py.

The five check_*/recompute functions need live deps (git, docker, requests,
the MLB Stats / ESPN APIs) and are smoke-tested on the VPS instead. Only the
pure logic — the Finding type and classify_worst — is unit-tested here.
"""
import sys
import os
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.audit_checks import (Finding, INFRA, DATA, CODE, classify_worst,
                                  check_picks, recompute_pick, check_infra)


def test_classify_worst_prefers_code():
    fs = [Finding(INFRA, "n8n"), Finding(CODE, "bug"), Finding(DATA, "pick")]
    assert classify_worst(fs) == CODE


def test_classify_worst_data_over_infra():
    assert classify_worst([Finding(INFRA, "x"), Finding(DATA, "y")]) == DATA


def test_classify_worst_empty_is_none():
    assert classify_worst([]) is None


def test_classify_worst_single_infra():
    assert classify_worst([Finding(INFRA, "n8n down")]) == INFRA


def test_finding_pick_ref_defaults_empty():
    f = Finding(INFRA, "n8n down")
    assert f.pick_ref == ""


def test_check_picks_malformed_pick_is_data_not_crash():
    """A non-numeric edge_pct must not abort check_picks: the crash-guard
    turns it into a DATA finding (pipeline drops the pick) and other picks
    are still checked. The malformed float() failure happens up front,
    before any live dependency, so this runs offline."""
    bad = {"module": "mlb_f5", "edge_pct": "not-a-number",
           "matchup": "AWAY @ HOME", "pick_description": "Over 4.5",
           "context": {"line": 4.5}}
    findings = check_picks("mlb_f5", [bad])  # must NOT raise
    data = [f for f in findings if f.kind == DATA]
    assert data, "expected a DATA finding for the malformed pick"
    assert any("unprocessable" in f.text for f in data)
    assert any(f.pick_ref == "AWAY @ HOME — Over 4.5" for f in data)


# ── recompute_pick tolerance parameter (issue I1) ──
#
# recompute_pick's MLB branch pulls live data (schedule, pitcher stats, wOBA
# splits, park factor). We mock every upstream call so the test runs offline
# and we fully control the two numbers the drift comparison uses:
#   recomp       — what project_total_f5 returns (mocked to 4.20)
#   logged_proj  — the pick's context["projection"] (set to 5.00)
# That is |4.20 - 5.00| / 5.00 = 16% drift: a CODE finding at the tight 0.03
# default, but clean once audit.py passes its day-after tolerance of 0.30.

_MLB_PICK = {
    "module": "mlb_f5",
    "matchup": "AWAY @ HOME",
    "pick_description": "F5 OVER 4.5",
    "edge_pct": 0.12,
    "context": {"line": 4.5, "projection": 5.00, "game_date": "2026-05-16",
                "weather_factor": 1.0},
}


def _mlb_recompute_offline(pick, tolerance):
    """Run recompute_pick('mlb_f5', ...) with all upstream MLB deps mocked so
    project_total_f5 yields a fixed recomputed total of 4.20."""
    sched_game = {
        "away_team": "AWAY", "home_team": "HOME",
        "away_team_id": 1, "home_team_id": 2,
        "away_starter_id": 11, "home_starter_id": 22,
        "venue": "Some Park",
    }
    with mock.patch("shared.mlb_data.get_schedule",
                    return_value=[sched_game]), \
         mock.patch("shared.mlb_data.get_pitcher_stats",
                    return_value={"xFIP_30d": 4.0, "hand": "R"}), \
         mock.patch("shared.mlb_data.get_team_woba_vs_hand",
                    return_value=0.320), \
         mock.patch("shared.mlb_data.park_factor", return_value=1.0), \
         mock.patch("modules.mlb_f5.projections.project_total_f5",
                    return_value=4.20):
        return recompute_pick("mlb_f5", pick, tolerance=tolerance)


def test_recompute_pick_drift_fails_at_tight_default():
    """A 16% drift IS a CODE finding at the tight 0.03 default — proving the
    check still fires (and that pipeline.py's behavior is unchanged)."""
    findings = _mlb_recompute_offline(_MLB_PICK, tolerance=0.03)
    code = [f for f in findings if f.kind == CODE]
    assert code, "expected a CODE finding for 16% drift at tolerance 0.03"
    assert any("drift" in f.text for f in code)


def test_recompute_pick_respects_passed_tolerance():
    """The same 16% drift produces NO finding when audit.py's 0.30 day-after
    tolerance is passed — the looser threshold suppresses the false positive
    while leaving the 0.03 default (pipeline.py) untouched."""
    findings = _mlb_recompute_offline(_MLB_PICK, tolerance=0.30)
    assert findings == [], (
        f"expected no findings at tolerance 0.30, got {findings}")


# ── check_infra required-files logic ──
#
# check_infra audits two things: the n8n docker container (needs the VPS) and
# the core pipeline files present in the repo. We scope these tests to the
# required-files logic by mocking _sh so the docker probe reports the container
# Up — that leaves only the os.path.exists file checks, which run offline.
# The required files are pipeline.py / deadman.py / main.py; the obsolete
# run_afternoon_*.sh / run_audit.sh scripts (deleted in the cron cutover) must
# NOT produce a finding.


def test_check_infra_no_finding_when_core_files_present(tmp_path):
    """All three core files present -> NO required-files (CODE) finding."""
    for fname in ("pipeline.py", "deadman.py", "main.py"):
        (tmp_path / fname).write_text("# stub\n")
    with mock.patch("shared.audit_checks._sh", return_value=(0, "Up 2 hours")):
        findings = check_infra(str(tmp_path), "n8n-n8n-1")
    assert findings == [], f"expected no findings, got {findings}"


def test_check_infra_missing_core_file_is_code_finding(tmp_path):
    """A missing core file (deadman.py) -> a CODE finding naming it."""
    for fname in ("pipeline.py", "main.py"):   # deadman.py deliberately absent
        (tmp_path / fname).write_text("# stub\n")
    with mock.patch("shared.audit_checks._sh", return_value=(0, "Up 2 hours")):
        findings = check_infra(str(tmp_path), "n8n-n8n-1")
    code = [f for f in findings if f.kind == CODE]
    assert len(code) == 1, f"expected exactly one CODE finding, got {findings}"
    assert "deadman.py" in code[0].text


def test_check_infra_deleted_shell_scripts_do_not_flag(tmp_path):
    """The deleted run_afternoon_*.sh / run_audit.sh scripts must NOT cause a
    finding even when absent — the stale required-files list was the post-
    cutover bug that HELD every pipeline email."""
    for fname in ("pipeline.py", "deadman.py", "main.py"):
        (tmp_path / fname).write_text("# stub\n")
    # the obsolete shell scripts are NOT created — they no longer exist
    with mock.patch("shared.audit_checks._sh", return_value=(0, "Up 2 hours")):
        findings = check_infra(str(tmp_path), "n8n-n8n-1")
    assert not any(
        ".sh" in f.text for f in findings), (
        f"deleted shell scripts must not be flagged, got {findings}")
