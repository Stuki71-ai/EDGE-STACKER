"""Tests for the pure pieces of shared/audit_checks.py.

The five check_*/recompute functions need live deps (git, docker, requests,
the MLB Stats / ESPN APIs) and are smoke-tested on the VPS instead. Only the
pure logic — the Finding type and classify_worst — is unit-tested here.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.audit_checks import Finding, INFRA, DATA, CODE, classify_worst


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
