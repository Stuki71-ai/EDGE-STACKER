"""Tests for pipeline.heal_loop() — the self-heal loop.

pipeline.generate and pipeline.run_full_audit are mocked so the loop logic
is exercised in isolation (no VPS, no network, no live audit deps).
drop_picks is NOT mocked: it runs the real audit_checks._pick_ref, so the
DATA scenario proves the dropped pick is matched via the genuine ref.
"""
import sys
import os
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline
from shared.audit_checks import Finding, INFRA, DATA, CODE, _pick_ref


# ── helpers ──────────────────────────────────────────────────────────
def _nhl_pick(player, matchup="BOS @ NYR"):
    """An nhl_sog pick: the player lives in pick_description, so _pick_ref
    combines matchup + description into a unique ref."""
    return {"module": "nhl_sog", "matchup": matchup,
            "pick_description": f"{player} Over 2.5 SOG",
            "edge_pct": 0.12, "context": {"line": 2.5}}


# ── Scenario 1: clean first pass -> SEND ─────────────────────────────
def test_clean_first_pass_sends():
    result = {"picks": [_nhl_pick("Pastrnak")]}
    with mock.patch("pipeline.generate", return_value=result) as gen, \
         mock.patch("pipeline.run_full_audit", return_value=[]) as aud:
        outcome, res, findings = pipeline.heal_loop("nhl_sog")
    assert outcome == "SEND"
    assert res == result
    assert findings == []
    assert gen.call_count == 1
    assert aud.call_count == 1


# ── Scenario 2: a CODE finding -> HELD immediately ───────────────────
def test_code_finding_holds():
    result = {"picks": [_nhl_pick("Pastrnak")]}
    code = [Finding(CODE, "Constant drift: MIN_EDGE")]
    with mock.patch("pipeline.generate", return_value=result), \
         mock.patch("pipeline.run_full_audit", return_value=code) as aud:
        outcome, res, findings = pipeline.heal_loop("nhl_sog")
    assert outcome == "HELD"
    assert findings == code
    # CODE is terminal: audited once, no second pass.
    assert aud.call_count == 1


# ── Scenario 3: a DATA finding on pick X -> X dropped, rest clean ────
def test_data_finding_drops_pick_then_sends():
    bad = _nhl_pick("Bad Player")
    good = _nhl_pick("Good Player")
    bad_ref = _pick_ref(bad)          # the genuine ref the Finding carries
    result = {"picks": [bad, good], "email_body": "x"}

    # 1st audit flags `bad`; 2nd audit (post-drop) is clean.
    audits = [[Finding(DATA, "edge out of range", pick_ref=bad_ref)], []]
    with mock.patch("pipeline.generate", return_value=result), \
         mock.patch("pipeline.run_full_audit", side_effect=audits) as aud:
        outcome, res, findings = pipeline.heal_loop("nhl_sog")

    assert outcome == "SEND"
    assert findings == []
    # the bad pick is gone, the good one survives
    assert res["picks"] == [good]
    assert bad not in res["picks"]
    # other top-level keys are preserved by drop_picks' dict copy
    assert res["email_body"] == "x"
    # drop_picks matched via the real _pick_ref (the 2nd audit saw the
    # trimmed result), so two audits ran.
    assert aud.call_count == 2


def test_drop_picks_matches_via_real_pick_ref():
    """Direct proof: drop_picks uses audit_checks._pick_ref, not p['matchup']
    — critical for nhl_sog whose picks share a matchup but differ by player."""
    a = _nhl_pick("Player A")
    b = _nhl_pick("Player B")
    result = {"picks": [a, b]}
    out = pipeline.drop_picks(result, {_pick_ref(a)})
    assert out["picks"] == [b]
    # both picks share matchup "BOS @ NYR" — matching by matchup alone would
    # wrongly drop BOTH; _pick_ref keeps them distinct.
    assert _pick_ref(a) != _pick_ref(b)


# ── Scenario 4: INFRA finding, autofix succeeds, 2nd pass clean ──────
def test_infra_finding_autofix_then_sends():
    result = {"picks": [_nhl_pick("Pastrnak")]}
    infra = [Finding(INFRA, "n8n container 'n8n-n8n-1' is not Up")]
    audits = [infra, []]   # attempt 1 flags infra; attempt 2 clean
    with mock.patch("pipeline.generate", return_value=result) as gen, \
         mock.patch("pipeline.run_full_audit", side_effect=audits) as aud, \
         mock.patch("pipeline.autofix_infra", return_value=True) as fix:
        outcome, res, findings = pipeline.heal_loop("nhl_sog")
    assert outcome == "SEND"
    assert findings == []
    fix.assert_called_once()
    # attempt 1 (fix) + attempt 2 (clean) -> two generate/audit cycles
    assert gen.call_count == 2
    assert aud.call_count == 2


def test_infra_finding_autofix_fails_holds():
    result = {"picks": [_nhl_pick("Pastrnak")]}
    infra = [Finding(INFRA, "n8n container 'n8n-n8n-1' is not Up")]
    with mock.patch("pipeline.generate", return_value=result), \
         mock.patch("pipeline.run_full_audit", return_value=infra), \
         mock.patch("pipeline.autofix_infra", return_value=False):
        outcome, res, findings = pipeline.heal_loop("nhl_sog")
    assert outcome == "HELD"
    assert findings == infra


# ── Scenario 5: never clean -> HELD after MAX_ATTEMPTS ───────────────
def test_never_clean_holds_after_max_attempts():
    result = {"picks": [_nhl_pick("Pastrnak")]}
    # INFRA every pass, autofix always "succeeds" so the loop keeps iterating
    # until it exhausts MAX_ATTEMPTS.
    infra = [Finding(INFRA, "n8n container 'n8n-n8n-1' is not Up")]
    with mock.patch("pipeline.generate", return_value=result) as gen, \
         mock.patch("pipeline.run_full_audit", return_value=infra) as aud, \
         mock.patch("pipeline.autofix_infra", return_value=True):
        outcome, res, findings = pipeline.heal_loop("nhl_sog")
    assert outcome == "HELD"
    assert findings == infra
    assert gen.call_count == pipeline.MAX_ATTEMPTS
    assert aud.call_count == pipeline.MAX_ATTEMPTS
