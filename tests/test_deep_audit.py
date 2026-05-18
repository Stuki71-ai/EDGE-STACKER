"""Tests for shared/deep_audit.py — the Claude-API deep-audit evidence layer.

Task 2 scope: gather_evidence() and its private _collect() helper.
gather_evidence is a thin wrapper that delegates ALL heavy work (git,
subprocess, fetchers, recompute, filesystem) to _collect, so tests stub
_collect and never touch the network or the VPS.
"""
import sys
import os
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import deep_audit


def test_gather_evidence_has_required_sections(monkeypatch):
    # gather_evidence must return a dict covering every audit phase
    monkeypatch.setattr(deep_audit, "_collect", lambda module, result: {
        "module": module, "picks": result.get("picks", []),
        "code_parity": "clean", "data_fetch": {}, "recompute": [],
        "fire_log": "", "mechanical_findings": [],
    })
    ev = deep_audit.gather_evidence("mlb_f5", {"picks": []})
    for key in ("module", "picks", "code_parity", "data_fetch",
                "recompute", "fire_log", "mechanical_findings"):
        assert key in ev


def test_gather_evidence_delegates_to_collect(monkeypatch):
    """gather_evidence must call _collect with the same args and return
    exactly what _collect produced — no extra processing."""
    sentinel = {"sentinel": object()}
    called = {}

    def fake_collect(module, result):
        called["module"] = module
        called["result"] = result
        return sentinel

    monkeypatch.setattr(deep_audit, "_collect", fake_collect)
    out = deep_audit.gather_evidence("nhl_sog", {"picks": [1, 2]})
    assert out is sentinel
    assert called["module"] == "nhl_sog"
    assert called["result"] == {"picks": [1, 2]}


def test_serialize_findings_produces_plain_dicts():
    """Finding objects must serialise to JSON-safe dicts so the evidence
    bundle can later be JSON-encoded."""
    from shared.audit_checks import Finding, CODE, DATA
    findings = [Finding(CODE, "constant drift"),
                Finding(DATA, "bad line", pick_ref="AWAY @ HOME")]
    out = deep_audit._serialize_findings(findings)
    assert out == [
        {"kind": "CODE", "text": "constant drift", "pick_ref": ""},
        {"kind": "DATA", "text": "bad line", "pick_ref": "AWAY @ HOME"},
    ]


def test_read_fire_log_missing_returns_empty(tmp_path):
    """No fire log present -> empty string, no crash."""
    assert deep_audit._read_fire_log(str(tmp_path)) == ""


def test_read_fire_log_picks_newest_and_truncates(tmp_path):
    """The newest edge-stacker-*.log is read; an oversized log is truncated
    to the last MAX_FIRE_LOG_BYTES so the evidence bundle stays small."""
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "edge-stacker-old.log"
    new = logs / "edge-stacker-new.log"
    old.write_text("OLD CONTENT")
    big = "X" * (deep_audit.MAX_FIRE_LOG_BYTES + 5000) + "TAIL-MARKER"
    new.write_text(big)
    # make `new` the most-recently-modified
    os.utime(old, (1, 1))
    out = deep_audit._read_fire_log(str(tmp_path))
    assert out.endswith("TAIL-MARKER")
    assert "OLD CONTENT" not in out
    assert len(out) <= deep_audit.MAX_FIRE_LOG_BYTES


def test_collect_runs_offline_with_mocked_externals(monkeypatch):
    """_collect end-to-end with every external (git/docker/fetchers/recompute)
    mocked: it must return a dict with all required keys and never hit the
    network."""
    from shared import audit_checks as ac

    monkeypatch.setattr(ac, "check_code_parity",
                        lambda repo: [ac.Finding(ac.CODE, "diff")])
    monkeypatch.setattr(ac, "check_infra",
                        lambda repo, c: [])
    monkeypatch.setattr(ac, "check_data_fetch",
                        lambda module: [])
    monkeypatch.setattr(ac, "check_picks",
                        lambda module, picks: [])
    monkeypatch.setattr(ac, "recompute_pick",
                        lambda module, pick: [])
    monkeypatch.setattr(deep_audit, "_data_fetch_summary",
                        lambda module: {"ok": True})
    monkeypatch.setattr(deep_audit, "_read_fire_log",
                        lambda repo: "log text")

    pick = {"module": "mlb_f5", "matchup": "A @ B",
            "pick_description": "Over 4.5", "context": {"projection": 4.5}}
    ev = deep_audit._collect("mlb_f5", {"picks": [pick]})

    for key in ("module", "picks", "code_parity", "data_fetch",
                "recompute", "fire_log", "mechanical_findings"):
        assert key in ev
    assert ev["module"] == "mlb_f5"
    assert ev["picks"] == [pick]
    assert ev["fire_log"] == "log text"
    assert ev["data_fetch"] == {"ok": True}
    # mechanical_findings are serialised plain dicts, not Finding objects
    assert all(isinstance(f, dict) for f in ev["mechanical_findings"])
    assert {"kind": "CODE", "text": "diff", "pick_ref": ""} \
        in ev["mechanical_findings"]
    # one pick -> one recompute entry
    assert len(ev["recompute"]) == 1


def test_collect_recompute_entry_carries_recomputed_value(monkeypatch):
    """Each recompute entry must carry BOTH the logged/emitted projection
    AND the independently recomputed projection, so the Claude judge can
    perform audit-spec Check 5's numeric-tolerance comparison even when the
    pick is clean (recompute_pick emits no findings)."""
    from shared import audit_checks as ac

    monkeypatch.setattr(ac, "check_code_parity", lambda repo: [])
    monkeypatch.setattr(ac, "check_infra", lambda repo, c: [])
    monkeypatch.setattr(ac, "check_data_fetch", lambda module: [])
    monkeypatch.setattr(ac, "check_picks", lambda module, picks: [])
    # clean pick -> recompute_pick emits no findings
    monkeypatch.setattr(ac, "recompute_pick", lambda module, pick: [])
    # the recompute helper independently produces a number
    monkeypatch.setattr(ac, "recompute_value", lambda module, pick: 4.62)
    monkeypatch.setattr(deep_audit, "_data_fetch_summary",
                        lambda module: {})
    monkeypatch.setattr(deep_audit, "_read_fire_log", lambda repo: "")

    pick = {"module": "mlb_f5", "matchup": "A @ B",
            "pick_description": "Over 4.5", "context": {"projection": 4.5}}
    ev = deep_audit._collect("mlb_f5", {"picks": [pick]})

    entry = ev["recompute"][0]
    assert entry["logged_projection"] == 4.5
    assert entry["recomputed_projection"] == 4.62
    assert entry["findings"] == []


def test_collect_recompute_entry_handles_missing_recompute(monkeypatch):
    """If the recompute genuinely cannot produce a value, the entry stores
    None for recomputed_projection rather than crashing."""
    from shared import audit_checks as ac

    monkeypatch.setattr(ac, "check_code_parity", lambda repo: [])
    monkeypatch.setattr(ac, "check_infra", lambda repo, c: [])
    monkeypatch.setattr(ac, "check_data_fetch", lambda module: [])
    monkeypatch.setattr(ac, "check_picks", lambda module, picks: [])
    monkeypatch.setattr(ac, "recompute_pick", lambda module, pick: [])
    monkeypatch.setattr(ac, "recompute_value", lambda module, pick: None)
    monkeypatch.setattr(deep_audit, "_data_fetch_summary", lambda module: {})
    monkeypatch.setattr(deep_audit, "_read_fire_log", lambda repo: "")

    pick = {"module": "mlb_f5", "matchup": "A @ B",
            "pick_description": "Over 4.5", "context": {}}
    ev = deep_audit._collect("mlb_f5", {"picks": [pick]})

    assert ev["recompute"][0]["recomputed_projection"] is None
