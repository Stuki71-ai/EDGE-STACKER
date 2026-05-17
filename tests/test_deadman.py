"""Tests for the deadman.py dead-man's-switch.

Mocks pipeline.ntfy and points MARKER_DIR at a temp dir so tests touch no
real files and send no real notifications.
"""
import sys
import os
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deadman


def _write_marker(tmp_path, module, ts):
    (tmp_path / f"{module}.json").write_text(
        json.dumps({"ts": ts.isoformat(), "outcome": "SEND"}))


def test_marker_missing_alerts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deadman, "MARKER_DIR", str(tmp_path))
    monkeypatch.setattr(deadman, "ntfy", lambda title, body: calls.append(title))
    deadman.main(["--module", "nhl_sog"])
    assert len(calls) == 1
    assert "did NOT run" in calls[0]


def test_marker_stale_alerts(tmp_path, monkeypatch):
    calls = []
    _write_marker(tmp_path, "mlb_f5",
                  datetime.now(timezone.utc) - timedelta(days=2))
    monkeypatch.setattr(deadman, "MARKER_DIR", str(tmp_path))
    monkeypatch.setattr(deadman, "ntfy", lambda title, body: calls.append(title))
    deadman.main(["--module", "mlb_f5"])
    assert len(calls) == 1
    assert "did NOT run" in calls[0]


def test_marker_fresh_silent(tmp_path, monkeypatch):
    calls = []
    _write_marker(tmp_path, "nhl_sog", datetime.now(timezone.utc))
    monkeypatch.setattr(deadman, "MARKER_DIR", str(tmp_path))
    monkeypatch.setattr(deadman, "ntfy", lambda title, body: calls.append(title))
    deadman.main(["--module", "nhl_sog"])
    assert calls == []
