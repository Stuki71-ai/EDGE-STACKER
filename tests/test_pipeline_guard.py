"""Tests for the pure pipeline.should_run guard."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


def test_should_run_only_at_target_et_hour():
    # nhl_sog target = 16 (04:30 PM ET); mlb_f5 target = 15 (03:00 PM ET)
    assert pipeline.should_run("nhl_sog", et_hour=16) is True
    assert pipeline.should_run("nhl_sog", et_hour=15) is False
    assert pipeline.should_run("mlb_f5", et_hour=15) is True
    assert pipeline.should_run("mlb_f5", et_hour=21) is False
