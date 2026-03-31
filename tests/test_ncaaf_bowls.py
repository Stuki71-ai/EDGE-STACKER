import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from modules.ncaaf_bowls.filters import bowl_underdog_model_prob, passes_filters


class TestBowlModelProb:
    def test_small_dog(self):
        prob = bowl_underdog_model_prob(5)
        assert abs(prob - 0.545) < 0.01

    def test_big_dog(self):
        prob = bowl_underdog_model_prob(12)
        assert abs(prob - 0.563) < 0.01

    def test_acc_fav_penalty(self):
        # Dog +12, ACC favorite: 0.563 + 0.05 = 0.613
        prob = bowl_underdog_model_prob(12, fav_conference="ACC")
        assert abs(prob - 0.613) < 0.01

    def test_mw_dog_bonus(self):
        prob = bowl_underdog_model_prob(7, dog_conference="Mountain West")
        assert abs(prob - 0.595) < 0.01

    def test_max_cap(self):
        prob = bowl_underdog_model_prob(15, fav_conference="ACC", dog_conference="Mountain West")
        assert prob <= 0.68


class TestBowlFilters:
    def test_small_spread_skipped(self):
        spread_data = {"spread": 2.5, "best_odds": -108, "consensus_odds": -110}
        passes, reason = passes_filters(spread_data)
        assert not passes
        assert "Spread" in reason

    def test_huge_spread_skipped(self):
        spread_data = {"spread": 30, "best_odds": -108, "consensus_odds": -110}
        passes, reason = passes_filters(spread_data)
        assert not passes
        assert "Spread" in reason

    def test_bad_odds_skipped(self):
        spread_data = {"spread": 7, "best_odds": -120, "consensus_odds": -120}
        passes, reason = passes_filters(spread_data)
        assert not passes
        assert "Odds" in reason

    def test_no_spread_data(self):
        passes, reason = passes_filters(None)
        assert not passes

    def test_qualifying(self):
        spread_data = {"spread": 7, "best_odds": -108, "consensus_odds": -110}
        passes, reason = passes_filters(spread_data)
        assert passes
        assert reason is None
