"""Tests for the pure pieces of shared/audit_checks.py.

The five check_*/recompute functions need live deps (git, docker, requests,
the MLB Stats / ESPN APIs) and are smoke-tested on the VPS instead. Only the
pure logic — the Finding type and classify_worst — is unit-tested here.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.audit_checks import (Finding, INFRA, DATA, CODE, classify_worst,
                                  check_picks)


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
