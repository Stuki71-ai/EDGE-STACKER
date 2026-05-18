"""Tests for shared/deep_audit.py — the Claude-API deep-audit evidence layer.

Task 2 scope: gather_evidence() and its private _collect() helper.
gather_evidence is a thin wrapper that delegates ALL heavy work (git,
subprocess, fetchers, recompute, filesystem) to _collect, so tests stub
_collect and never touch the network or the VPS.
"""
import json
import sys
import os
from unittest import mock

import pytest

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


# --- claude_api_audit (Task 3) -------------------------------------------

class _FakeBlock:
    def __init__(self, text, type="text"):
        self.text = text
        self.type = type


class _FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    """Records every messages.create call; replays a scripted list of
    side-effects: a str returns a text _FakeResponse, an Exception is raised,
    and a _FakeResponse instance is returned verbatim (for non-text / custom
    stop_reason cases)."""
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self._script.pop(0) if self._script else self._script_last
        self._script_last = step
        if isinstance(step, Exception):
            raise step
        if isinstance(step, _FakeResponse):
            return step
        return _FakeResponse(step)


class _FakeClient:
    def __init__(self, script, **kwargs):
        self.init_kwargs = kwargs
        self.messages = _FakeMessages(script)


def _install_fake_client(monkeypatch, script):
    """Patch the Anthropic symbol in deep_audit with a fake; return the
    fake client instance so the test can inspect calls."""
    holder = {}

    def _factory(**kwargs):
        client = _FakeClient(script, **kwargs)
        holder["client"] = client
        return client

    monkeypatch.setattr(deep_audit, "Anthropic", _factory)
    monkeypatch.setattr(deep_audit.time, "sleep", lambda *_: None)
    return holder


_GREEN = json.dumps({"verdict": "GREEN", "findings": [],
                     "summary": "all checks passed"})
_BUG = json.dumps({"verdict": "BUG", "findings": [
    {"kind": "CODE", "text": "constant drift", "pick_ref": ""}],
    "summary": "a constant drifted"})


def test_claude_api_audit_returns_green(monkeypatch):
    """A mocked client returning valid GREEN JSON is parsed through."""
    _install_fake_client(monkeypatch, [_GREEN])
    out = deep_audit.claude_api_audit("SPEC TEXT", {"module": "nhl_sog"})
    assert out["verdict"] == "GREEN"
    assert out["findings"] == []
    assert out["summary"]


def test_claude_api_audit_returns_bug(monkeypatch):
    """A mocked client returning BUG JSON is parsed through with findings."""
    _install_fake_client(monkeypatch, [_BUG])
    out = deep_audit.claude_api_audit("SPEC TEXT", {"module": "mlb_f5"})
    assert out["verdict"] == "BUG"
    assert out["findings"][0]["kind"] == "CODE"
    assert out["findings"][0]["text"] == "constant drift"
    assert out["findings"][0]["pick_ref"] == ""


def test_claude_api_audit_builds_request_correctly(monkeypatch):
    """The request carries the spec as a cache-controlled system block, the
    evidence as the JSON user message, and the Opus model id."""
    holder = _install_fake_client(monkeypatch, [_GREEN])
    evidence = {"module": "nhl_sog", "picks": [{"matchup": "A @ B"}]}
    deep_audit.claude_api_audit("THE SPEC", evidence)

    kwargs = holder["client"].messages.calls[0]
    assert kwargs["model"] == "claude-opus-4-7"
    system = kwargs["system"]
    assert system[0]["text"] == "THE SPEC"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    user_content = kwargs["messages"][0]["content"]
    assert kwargs["messages"][0]["role"] == "user"
    assert json.loads(user_content) == evidence


def test_claude_api_audit_retries_transient_then_succeeds(monkeypatch):
    """A transient error on the first call is retried; the second succeeds."""
    import anthropic
    transient = anthropic.APITimeoutError(request=mock.Mock())
    holder = _install_fake_client(monkeypatch, [transient, _GREEN])
    out = deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})
    assert out["verdict"] == "GREEN"
    assert len(holder["client"].messages.calls) == 2


def test_claude_api_audit_raises_after_retry_budget(monkeypatch):
    """A persistent transient error raises after the retry budget; the call
    count equals the budget."""
    import anthropic
    transient = anthropic.APITimeoutError(request=mock.Mock())
    holder = _install_fake_client(monkeypatch, [transient])
    with pytest.raises(anthropic.APITimeoutError):
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})
    assert len(holder["client"].messages.calls) == deep_audit.AUDIT_MAX_ATTEMPTS


def test_claude_api_audit_does_not_retry_bad_request(monkeypatch):
    """A non-transient 400 raises immediately — no retry."""
    import anthropic
    bad = anthropic.BadRequestError(
        "bad", response=mock.Mock(status_code=400), body=None)
    holder = _install_fake_client(monkeypatch, [bad])
    with pytest.raises(anthropic.BadRequestError):
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})
    assert len(holder["client"].messages.calls) == 1


def test_claude_api_audit_raises_on_invalid_json(monkeypatch):
    """A response that is not valid JSON raises (callers fall back)."""
    _install_fake_client(monkeypatch, ["not json at all"])
    with pytest.raises(Exception):
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})


def test_claude_api_audit_raises_on_wrong_shape(monkeypatch):
    """Valid JSON that does not match the verdict contract raises."""
    _install_fake_client(monkeypatch, [json.dumps({"verdict": "MAYBE"})])
    with pytest.raises(Exception):
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})


def test_claude_api_audit_raises_on_no_text_block(monkeypatch):
    """A response with no text block (e.g. a refusal with only a thinking
    block) raises a diagnosable ValueError naming the cause — and does NOT
    retry, since a refusal is not a transient failure."""
    refusal = _FakeResponse("", stop_reason="refusal")
    refusal.content = [_FakeBlock("internal reasoning", type="thinking")]
    holder = _install_fake_client(monkeypatch, [refusal])
    with pytest.raises(ValueError) as exc:
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})
    msg = str(exc.value)
    assert "no text block" in msg and "refusal" in msg
    assert len(holder["client"].messages.calls) == 1


def test_claude_api_audit_raises_on_empty_content(monkeypatch):
    """A response with an empty content list also raises ValueError."""
    empty = _FakeResponse("", stop_reason="end_turn")
    empty.content = []
    _install_fake_client(monkeypatch, [empty])
    with pytest.raises(ValueError):
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})


def test_claude_api_audit_raises_on_max_tokens_truncation(monkeypatch):
    """A stop_reason=max_tokens truncation raises a diagnosable ValueError
    before any parsing, and does NOT retry — truncation is not transient."""
    truncated = _FakeResponse(_GREEN, stop_reason="max_tokens")
    holder = _install_fake_client(monkeypatch, [truncated])
    with pytest.raises(ValueError) as exc:
        deep_audit.claude_api_audit("SPEC", {"module": "nhl_sog"})
    assert "max_tokens" in str(exc.value)
    assert len(holder["client"].messages.calls) == 1


# --- deep_audit orchestrator (Task 4) ------------------------------------

from shared.audit_checks import Finding, CODE, DATA


def _fallback_factory(result_findings):
    """Build a fallback callable that records its call args and returns a
    fixed list[Finding]."""
    calls = []

    def fallback(module, result):
        calls.append((module, result))
        return result_findings

    fallback.calls = calls
    return fallback


def test_deep_audit_green_returns_empty(monkeypatch):
    """A GREEN verdict from the API → deep_audit returns []."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(deep_audit, "gather_evidence",
                        lambda module, result: {"module": module})
    monkeypatch.setattr(deep_audit, "claude_api_audit",
                        lambda spec, evidence: {
                            "verdict": "GREEN", "findings": [],
                            "summary": "all good"})
    fallback = _fallback_factory([Finding(CODE, "should not be used")])
    out = deep_audit.deep_audit("nhl_sog", {"picks": []}, fallback)
    assert out == []
    assert fallback.calls == []


def test_deep_audit_bug_maps_findings(monkeypatch):
    """A BUG verdict with two findings → two Finding objects with matching
    kind/text/pick_ref."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(deep_audit, "gather_evidence",
                        lambda module, result: {"module": module})
    monkeypatch.setattr(deep_audit, "claude_api_audit",
                        lambda spec, evidence: {
                            "verdict": "BUG",
                            "findings": [
                                {"kind": "CODE", "text": "constant drift",
                                 "pick_ref": ""},
                                {"kind": "DATA", "text": "bad line",
                                 "pick_ref": "A @ B"},
                            ],
                            "summary": "two problems"})
    fallback = _fallback_factory([Finding(CODE, "unused")])
    out = deep_audit.deep_audit("mlb_f5", {"picks": []}, fallback)
    assert fallback.calls == []
    assert len(out) == 2
    assert all(isinstance(f, Finding) for f in out)
    assert out[0].kind == CODE
    assert out[0].text == "constant drift"
    assert out[0].pick_ref == ""
    assert out[1].kind == DATA
    assert out[1].text == "bad line"
    assert out[1].pick_ref == "A @ B"


def test_deep_audit_api_raises_uses_fallback(monkeypatch):
    """claude_api_audit raising (API unreachable) → deep_audit calls the
    passed-in fallback and returns ITS result."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(deep_audit, "gather_evidence",
                        lambda module, result: {"module": module})

    def boom(spec, evidence):
        raise RuntimeError("API unreachable")

    monkeypatch.setattr(deep_audit, "claude_api_audit", boom)
    sentinel = [Finding(DATA, "mechanical finding", pick_ref="A @ B")]
    fallback = _fallback_factory(sentinel)
    result = {"picks": [1]}
    out = deep_audit.deep_audit("nhl_sog", result, fallback)
    assert out is sentinel
    assert fallback.calls == [("nhl_sog", result)]


def test_deep_audit_malformed_verdict_uses_fallback(monkeypatch):
    """claude_api_audit returns a structurally broken verdict (a finding with
    an invalid kind that breaks verdict→Finding mapping) → fallback used."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(deep_audit, "gather_evidence",
                        lambda module, result: {"module": module})
    monkeypatch.setattr(deep_audit, "claude_api_audit",
                        lambda spec, evidence: {
                            "verdict": "BUG",
                            "findings": ["not-a-dict"],
                            "summary": "broken"})
    sentinel = [Finding(CODE, "mechanical")]
    fallback = _fallback_factory(sentinel)
    result = {"picks": []}
    out = deep_audit.deep_audit("mlb_f5", result, fallback)
    assert out is sentinel
    assert fallback.calls == [("mlb_f5", result)]


def test_deep_audit_missing_key_uses_fallback(monkeypatch):
    """ANTHROPIC_API_KEY absent → deep_audit returns the fallback's result
    WITHOUT calling gather_evidence/claude_api_audit."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gather_called = []
    api_called = []
    monkeypatch.setattr(deep_audit, "gather_evidence",
                        lambda module, result: gather_called.append(1))
    monkeypatch.setattr(deep_audit, "claude_api_audit",
                        lambda spec, evidence: api_called.append(1))
    sentinel = [Finding(CODE, "mechanical")]
    fallback = _fallback_factory(sentinel)
    result = {"picks": []}
    out = deep_audit.deep_audit("nhl_sog", result, fallback)
    assert out is sentinel
    assert fallback.calls == [("nhl_sog", result)]
    assert gather_called == []
    assert api_called == []
