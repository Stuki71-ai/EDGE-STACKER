"""Tests for pipeline.generate() — subprocess is mocked so these run anywhere
(no dependency on the VPS REPO path or a live main.py)."""
import sys
import os
import subprocess
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
        with pytest.raises(RuntimeError, match=r"rc=2.*boom"):
            pipeline.generate("mlb_f5")


def test_generate_raises_on_non_json_stdout():
    with mock.patch("pipeline.subprocess.run",
                    return_value=_proc(0, stdout="not json at all")):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            pipeline.generate("nhl_sog")


def test_generate_raises_on_empty_stdout():
    # main.py exiting 0 with EMPTY stdout is a known silent-failure mode;
    # json.loads("") raises JSONDecodeError -> generate converts to RuntimeError.
    with mock.patch("pipeline.subprocess.run",
                    return_value=_proc(0, stdout="")):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            pipeline.generate("nhl_sog")


def test_generate_raises_on_timeout():
    with mock.patch("pipeline.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="main.py", timeout=600)):
        with pytest.raises(RuntimeError, match="timed out"):
            pipeline.generate("mlb_f5")
