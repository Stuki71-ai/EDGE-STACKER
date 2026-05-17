"""Tests for pipeline.py's SEND / HELD paths and main() (Task 6).

`requests` and `heal_loop` are mocked so the SEND/HELD/CRASH branching is
exercised with no network and no live audit. The marker directory is
redirected to a tmp path so tests never write into the real repo.
"""
import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline
from shared.audit_checks import Finding, INFRA, DATA, CODE


# ── fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def marker_dir(tmp_path, monkeypatch):
    """Redirect MARKER_DIR to a tmp path for the duration of a test."""
    d = tmp_path / "markers"
    monkeypatch.setattr(pipeline, "MARKER_DIR", str(d))
    return d


def _marker(marker_dir, module):
    """Read back the marker JSON written for `module` (None if absent)."""
    p = marker_dir / f"{module}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _ok_response():
    """A webhook response that acknowledges the trigger."""
    r = mock.Mock()
    r.text = "Workflow was started"
    return r


# ── send() ───────────────────────────────────────────────────────────
def test_send_posts_webhook():
    result = {"picks": [{"matchup": "BOS @ NYR"}]}
    with mock.patch("requests.post", return_value=_ok_response()) as post:
        pipeline.send("nhl_sog", result)
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == pipeline.WEBHOOK["nhl_sog"]
    assert kwargs["json"] == result          # JSON forwarded byte-for-byte


def test_send_raises_when_webhook_not_acknowledged():
    bad = mock.Mock()
    bad.text = "404 Not Found"
    with mock.patch("requests.post", return_value=bad):
        with pytest.raises(RuntimeError, match="webhook did not accept"):
            pipeline.send("mlb_f5", {"picks": []})


# ── write_marker() ───────────────────────────────────────────────────
def test_write_marker_writes_outcome(marker_dir):
    pipeline.write_marker("nhl_sog", "SEND")
    m = _marker(marker_dir, "nhl_sog")
    assert m["outcome"] == "SEND"
    assert "ts" in m and m["ts"]             # ISO timestamp present


# ── ntfy() never raises ──────────────────────────────────────────────
def test_ntfy_swallows_failure():
    """A failed ntfy push must never crash the pipeline — it is logged."""
    with mock.patch("requests.post", side_effect=Exception("network down")):
        pipeline.ntfy("EDGE STACKER - test", "body")   # must not raise


# ── main(): SEND branch ──────────────────────────────────────────────
def test_main_send_posts_webhook_and_writes_marker(marker_dir):
    result = {"picks": [{"matchup": "BOS @ NYR"}]}
    with mock.patch("pipeline.heal_loop",
                    return_value=("SEND", result, [], [])), \
         mock.patch("pipeline._current_et_hour", return_value=16), \
         mock.patch("pipeline.load_env"), \
         mock.patch("pipeline.sync") as sync, \
         mock.patch("pipeline.send") as send, \
         mock.patch("pipeline.ntfy") as ntfy:
        pipeline.main(["--module", "nhl_sog"])

    send.assert_called_once_with("nhl_sog", result)
    sync.assert_called_once()
    ntfy.assert_not_called()                 # no auto-fix -> silent
    assert _marker(marker_dir, "nhl_sog")["outcome"] == "SEND"


def test_main_send_after_autofix_ntfys(marker_dir):
    """A SEND that involved a self-heal action emits a transparency ntfy."""
    result = {"picks": []}
    with mock.patch("pipeline.heal_loop",
                    return_value=("SEND", result, [],
                                  ["infra fix: n8n container down"])), \
         mock.patch("pipeline._current_et_hour", return_value=15), \
         mock.patch("pipeline.load_env"), \
         mock.patch("pipeline.sync"), \
         mock.patch("pipeline.send") as send, \
         mock.patch("pipeline.ntfy") as ntfy:
        pipeline.main(["--module", "mlb_f5"])

    send.assert_called_once()
    ntfy.assert_called_once()                # transparency note sent
    title = ntfy.call_args[0][0]
    assert title.isascii()                   # Title header must be ASCII
    assert _marker(marker_dir, "mlb_f5")["outcome"] == "SEND"


# ── main(): HELD branch ──────────────────────────────────────────────
def test_main_held_does_not_post_webhook(marker_dir):
    findings = [Finding(CODE, "Constant drift: MIN_EDGE")]
    with mock.patch("pipeline.heal_loop",
                    return_value=("HELD", {"picks": []}, findings, [])), \
         mock.patch("pipeline._current_et_hour", return_value=16), \
         mock.patch("pipeline.load_env"), \
         mock.patch("pipeline.send") as send, \
         mock.patch("pipeline.ntfy") as ntfy:
        pipeline.main(["--module", "nhl_sog"])

    send.assert_not_called()                 # HELD: NO email
    ntfy.assert_called_once()
    title = ntfy.call_args[0][0]
    assert title == "EDGE STACKER - picks HELD"
    assert title.isascii()
    assert _marker(marker_dir, "nhl_sog")["outcome"] == "HELD"


# ── main(): CRASH guard ──────────────────────────────────────────────
def test_main_crash_writes_crash_marker_and_ntfys(marker_dir):
    with mock.patch("pipeline.heal_loop",
                    side_effect=RuntimeError("main.py timed out")), \
         mock.patch("pipeline._current_et_hour", return_value=16), \
         mock.patch("pipeline.load_env"), \
         mock.patch("pipeline.send") as send, \
         mock.patch("pipeline.ntfy") as ntfy:
        pipeline.main(["--module", "nhl_sog"])   # must NOT propagate

    send.assert_not_called()
    ntfy.assert_called_once()
    title = ntfy.call_args[0][0]
    assert title == "EDGE STACKER - pipeline CRASHED"
    assert title.isascii()
    assert _marker(marker_dir, "nhl_sog")["outcome"] == "CRASH"


# ── main(): DST guard ────────────────────────────────────────────────
def test_main_skips_when_not_target_et_hour(marker_dir):
    """Off-hour cron fire: should_run is False -> exit early, no work."""
    with mock.patch("pipeline.heal_loop") as heal, \
         mock.patch("pipeline._current_et_hour", return_value=9), \
         mock.patch("pipeline.load_env"), \
         mock.patch("pipeline.send") as send, \
         mock.patch("pipeline.ntfy") as ntfy:
        pipeline.main(["--module", "nhl_sog"])

    heal.assert_not_called()
    send.assert_not_called()
    ntfy.assert_not_called()
    assert _marker(marker_dir, "nhl_sog") is None   # no marker on a skip
