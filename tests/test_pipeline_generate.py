"""Tests for pipeline.generate() — subprocess is mocked so these run anywhere
(no dependency on the VPS REPO path or a live main.py)."""
import sys
import os
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


def _proc(returncode=0, stdout="", stderr=""):
    m = mock.Mock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_generate_parses_json_on_success():
    with mock.patch("pipeline.subprocess.run",
                    return_value=_proc(0, stdout='{"picks": []}')):
        result = pipeline.generate("nhl_sog")
    assert result == {"picks": []}


def test_generate_raises_on_nonzero_exit():
    with mock.patch("pipeline.subprocess.run",
                    return_value=_proc(2, stdout="", stderr="boom")):
        with pytest.raises(RuntimeError):
            pipeline.generate("mlb_f5")


def test_generate_raises_on_non_json_stdout():
    with mock.patch("pipeline.subprocess.run",
                    return_value=_proc(0, stdout="not json at all")):
        with pytest.raises(RuntimeError):
            pipeline.generate("nhl_sog")
