import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from modules.ncaab_kenpom.filters import kenpom_model_prob, determine_side, passes_filters
from modules.ncaab_kenpom.kenpom import kenpom_predicted_spread


class TestKenPomModelProb:
    def test_divergence_2_5_skipped(self):
        # Should return base 0.50 (below 3.0 threshold)
        prob = kenpom_model_prob(2.5, True)
        assert abs(prob - 0.50) < 0.01

    def test_divergence_5(self):
        prob = kenpom_model_prob(5.0, True)
        assert abs(prob - 0.62) < 0.01

    def test_non_conference_bonus(self):
        prob_conf = kenpom_model_prob(5.0, True)
        prob_nonconf = kenpom_model_prob(5.0, False)
        assert prob_nonconf - prob_conf == pytest.approx(0.03, abs=0.001)

    def test_max_cap(self):
        prob = kenpom_model_prob(10.0, False)
        assert prob <= 0.72


class TestKenPomSpread:
    def _kenpom_data(self):
        return {
            "TeamA": {"AdjEM": 20.5},
            "TeamB": {"AdjEM": 15.3},
        }

    def test_home_favored_nonconf(self):
        # home AdjEM 20.5, away AdjEM 15.3, non-conf HCA 3.5 -> margin +8.7
        margin = kenpom_predicted_spread("TeamA", "TeamB", self._kenpom_data(),
                                         neutral=False, is_conference=False)
        assert abs(margin - 8.7) < 0.1

    def test_conference_hca(self):
        margin = kenpom_predicted_spread("TeamA", "TeamB", self._kenpom_data(),
                                         neutral=False, is_conference=True)
        assert abs(margin - 8.0) < 0.1  # 20.5 - 15.3 + 2.8

    def test_neutral_site(self):
        margin = kenpom_predicted_spread("TeamA", "TeamB", self._kenpom_data(),
                                         neutral=True)
        assert abs(margin - 5.2) < 0.1  # 20.5 - 15.3 + 0

    def test_missing_team(self):
        result = kenpom_predicted_spread("TeamA", "Unknown", self._kenpom_data())
        assert result is None


class TestSideSelection:
    def test_bet_home(self):
        # KenPom says home +3, market says home -2 -> bet home
        side, div = determine_side(3.0, -2.0)
        assert side == "home"
        assert abs(div - 1.0) < 0.01

    def test_bet_away(self):
        # KenPom says home -1, market says home -6 -> bet away
        # kenpom_margin = -1, market_spread = -6, market_margin = 6
        # divergence = -1 - 6 = -7 -> side=away, abs=7
        side, div = determine_side(-1.0, -6.0)
        assert side == "away"
        assert abs(div - 7.0) < 0.01

    def test_bet_away_corrected(self):
        # Recalculate: kenpom=-1, market_spread=-6, market_margin=6
        # divergence = -1 - 6 = -7 -> away, |div|=7
        side, div = determine_side(-1.0, -6.0)
        assert side == "away"
        assert abs(div - 7.0) < 0.01


class TestKenPomFilters:
    def test_low_divergence(self):
        passes, reason, warn = passes_filters(2.5, -108, 3)
        assert not passes
        assert "Divergence" in reason

    def test_bad_odds(self):
        passes, reason, warn = passes_filters(5.0, -120, 3)
        assert not passes
        assert "Odds" in reason

    def test_csv_too_old(self):
        passes, reason, warn = passes_filters(5.0, -108, 15)
        assert not passes
        assert "days old" in reason

    def test_csv_warning(self):
        passes, reason, warn = passes_filters(5.0, -108, 10)
        assert passes
        assert warn is not None
        assert "10 days" in warn

    def test_qualifying(self):
        passes, reason, warn = passes_filters(5.0, -108, 3)
        assert passes
        assert reason is None
