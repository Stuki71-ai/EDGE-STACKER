"""Tests for NHL SOG module."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from modules.nhl_sog.projections import project_player_sog, LEAGUE_AVG_SHOTS_AGAINST
from modules.nhl_sog.filters import sog_edge, passes_filters
from shared.espn_nhl import _toi_to_seconds


class TestTOIParsing:
    def test_basic(self):
        assert _toi_to_seconds("14:32") == 14 * 60 + 32

    def test_hour_format(self):
        assert _toi_to_seconds("23:00") == 23 * 60

    def test_zero(self):
        assert _toi_to_seconds("0:00") == 0

    def test_empty(self):
        assert _toi_to_seconds("") == 0
        assert _toi_to_seconds(None) == 0


class TestProjection:
    def _make_games(self, shots_per_game, toi_min):
        return [{"S": s, "TOI_SEC": toi_min * 60} for s in shots_per_game]

    def test_basic_projection(self):
        games = self._make_games([3, 4, 2, 5, 3, 4, 3, 2, 4, 3], toi_min=18)
        # Total shots = 33, total TOI = 180 min = 10800 sec = 3 hours
        # SOG/60 = 33 / 3 = 11 shots/60 min
        # Avg TOI = 18 min; project = 11 * 18/60 = 3.3 shots
        result = project_player_sog(games, LEAGUE_AVG_SHOTS_AGAINST)
        assert result is not None
        assert abs(result["projection"] - 3.3) < 0.1
        assert result["sample_size"] == 10
        assert abs(result["avg_TOI_min"] - 18.0) < 0.1

    def test_high_volume_shooter(self):
        # 5 shots/game in 22 min — top-line star
        games = self._make_games([5] * 10, toi_min=22)
        result = project_player_sog(games, LEAGUE_AVG_SHOTS_AGAINST)
        # 50 shots / (220 min / 60) = 13.6 SOG/60
        # Project = 13.6 * 22/60 = 5.0
        assert abs(result["projection"] - 5.0) < 0.1

    def test_opp_factor_high_shots_against(self):
        games = self._make_games([3] * 10, toi_min=18)
        # Opp gives up 35 shots/game (above league avg 30)
        result = project_player_sog(games, 35.0)
        # opp_factor = 35/30 = 1.167 capped to 1.15
        assert result["opp_factor"] == 1.15

    def test_opp_factor_low_shots_against(self):
        games = self._make_games([3] * 10, toi_min=18)
        result = project_player_sog(games, 25.0)
        # opp_factor = 25/30 = 0.833 capped to 0.85
        assert result["opp_factor"] == 0.85

    def test_zero_toi_returns_none(self):
        games = [{"S": 0, "TOI_SEC": 0}] * 10
        assert project_player_sog(games, 30) is None

    def test_empty_games(self):
        assert project_player_sog([], 30) is None


class TestEdge:
    def test_over_edge(self):
        # Projection 4.0, line 2.5 → strong OVER signal
        direction, edge, prob, odds = sog_edge(4.0, 2.5, -110, -110)
        assert direction == "OVER"
        assert edge >= 0.06

    def test_under_edge(self):
        # Projection 1.5, line 2.5 → strong UNDER signal
        direction, edge, prob, odds = sog_edge(1.5, 2.5, -110, -110)
        assert direction == "UNDER"
        assert edge >= 0.06

    def test_no_edge_when_close(self):
        direction, edge, prob, odds = sog_edge(2.5, 2.5, -110, -110)
        assert direction is None

    def test_edge_capped_at_20pct(self):
        # Wildly different projection should still cap at 20%
        direction, edge, prob, odds = sog_edge(10.0, 1.5, -110, -110)
        assert edge <= 0.20

    def test_actual_std_used_when_provided(self):
        # If actual_std is huge, edge should be smaller than fixed 30%
        # Compare same projection/line with different stds
        d1, e1, _, _ = sog_edge(4.0, 2.5, -110, -110, actual_std=0.5)
        d2, e2, _, _ = sog_edge(4.0, 2.5, -110, -110, actual_std=3.0)
        # Tighter std -> bigger over edge for projection above line
        assert e1 >= e2


class TestFilters:
    def _games(self, n=10, toi_min=18):
        return [{"S": 3, "TOI_SEC": toi_min * 60} for _ in range(n)]

    def _stat_data(self, vig=0.05):
        # Quick math: -110/-110 has ~0.0476 vig
        return {"best_over_odds": -110, "best_under_odds": -110, "over_odds": -110, "under_odds": -110}

    def test_pass_forward(self):
        passed, reason = passes_filters(self._games(10, 18), "C", self._stat_data(), 0.10)
        assert passed

    def test_fail_few_games(self):
        passed, reason = passes_filters(self._games(5, 18), "C", self._stat_data(), 0.10)
        assert not passed

    def test_fail_low_toi_forward(self):
        passed, reason = passes_filters(self._games(10, 10), "C", self._stat_data(), 0.10)
        assert not passed

    def test_pass_defenseman_high_toi(self):
        passed, reason = passes_filters(self._games(10, 20), "D", self._stat_data(), 0.10)
        assert passed

    def test_fail_defenseman_low_toi(self):
        # 16 min for defenseman is below 18 min minimum
        passed, reason = passes_filters(self._games(10, 16), "D", self._stat_data(), 0.10)
        assert not passed

    def test_fail_low_edge(self):
        passed, reason = passes_filters(self._games(10, 18), "C", self._stat_data(), 0.04)
        assert not passed

    def test_fail_high_vig(self):
        bad_vig = {"best_over_odds": -150, "best_under_odds": -150, "over_odds": -150, "under_odds": -150}
        passed, reason = passes_filters(self._games(10, 18), "C", bad_vig, 0.10)
        assert not passed
