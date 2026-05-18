"""Tests for the pure pipeline.should_run guard."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


def test_should_run_only_at_target_et_hour():
    # mlb_f5: weekday target = 15 (03:00 PM ET); weekend target = 11 (11:30 AM ET)
    assert pipeline.should_run("mlb_f5", 15, False) is True    # weekday on-target
    assert pipeline.should_run("mlb_f5", 11, True) is True     # weekend on-target
    assert pipeline.should_run("mlb_f5", 15, True) is False    # weekday hour, weekend day
    assert pipeline.should_run("mlb_f5", 11, False) is False   # weekend hour, weekday day
    # nhl_sog: target = 16 (04:30 PM ET) for both day-types
    assert pipeline.should_run("nhl_sog", 16, False) is True
    assert pipeline.should_run("nhl_sog", 16, True) is True
    assert pipeline.should_run("nhl_sog", 15, False) is False
