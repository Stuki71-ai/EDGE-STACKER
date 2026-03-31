import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from modules.nba_props.projections import project_player_stat
from modules.nba_props.filters import prop_edge, passes_filters
from staking import norm_cdf, calculate_vig


class TestProjections:
    def _make_games(self, stat_values, minutes=None):
        if minutes is None:
            minutes = [32.0] * len(stat_values)
        games = []
        for i, val in enumerate(stat_values):
            games.append({
                "PTS": val if True else 0,
                "REB": val,
                "AST": val,
                "MIN": minutes[i],
            })
        return games

    def test_basic_projection(self):
        games = self._make_games([30, 28, 32, 26, 34, 29, 31, 27, 33, 30])
        result = project_player_stat(games, "PTS", 112.0, False)
        assert abs(result["projection"] - 30.0) < 1.0
        assert result["sample_size"] == 10

    def test_opponent_adjustment(self):
        games = self._make_games([30] * 10)
        # High DRTG opponent (bad defense) -> higher projection
        result_high = project_player_stat(games, "PTS", 118.0, False)
        result_avg = project_player_stat(games, "PTS", 112.0, False)
        assert result_high["projection"] > result_avg["projection"]

    def test_teammate_out_boost(self):
        games = self._make_games([30] * 10)
        result_normal = project_player_stat(games, "PTS", 112.0, False)
        result_out = project_player_stat(games, "PTS", 112.0, True)
        # +12% boost
        assert abs(result_out["projection"] - result_normal["projection"] * 1.12) < 0.5

    def test_rebounds_less_affected(self):
        games = self._make_games([10] * 10)
        result = project_player_stat(games, "REB", 120.0, False)
        # REB uses 0.4 factor: 10 * (120/112 * 0.4 + 0.6) = 10 * (0.429 + 0.6) = 10.29
        assert result["projection"] < 11.0  # Much less than PTS adjustment

    def test_minutes_stability(self):
        games = self._make_games([30] * 10, minutes=[32, 33, 31, 32, 33, 31, 32, 33, 31, 32])
        result = project_player_stat(games, "PTS", 112.0, False)
        assert result["minutes_stable"] is True

    def test_minutes_unstable(self):
        games = self._make_games([30] * 10, minutes=[38, 20, 35, 22, 40, 18, 36, 24, 38, 29])
        result = project_player_stat(games, "PTS", 112.0, False)
        assert result["minutes_stable"] is False


class TestPropEdge:
    def test_over_edge(self):
        # Projection well above line
        direction, edge, prob, odds = prop_edge(33.0, 28.5, "PTS", -110, -110)
        assert direction == "OVER"
        assert edge > 0.06

    def test_under_edge(self):
        # Projection well below line
        direction, edge, prob, odds = prop_edge(24.0, 28.5, "PTS", -110, -110)
        assert direction == "UNDER"
        assert edge > 0.06

    def test_no_edge(self):
        # Projection close to line
        direction, edge, prob, odds = prop_edge(28.5, 28.5, "PTS", -110, -110)
        assert direction is None

    def test_norm_cdf_accuracy(self):
        # Verify norm_cdf(28.5, 33.0, 7.26) matches expected
        result = norm_cdf(28.5, 33.0, 7.26)
        assert abs(result - 0.268) < 0.01

    def test_stat_specific_std(self):
        # PTS std = 22% of projection
        # 28 pt projection -> std = 6.16
        direction, edge, prob, odds = prop_edge(28.0, 22.0, "PTS", -110, -110)
        # std = 28 * 0.22 = 6.16, should find over edge
        assert direction == "OVER"


class TestPropFilters:
    def test_insufficient_games(self):
        games = [{"PTS": 30, "MIN": 32}] * 5
        stat_data = {"best_over_odds": -110, "best_under_odds": -110, "over_odds": -110, "under_odds": -110}
        passes, reason = passes_filters(games, stat_data, 0.08, True)
        assert not passes
        assert "games" in reason.lower()

    def test_low_minutes(self):
        games = [{"PTS": 10, "MIN": 15}] * 12
        stat_data = {"best_over_odds": -110, "best_under_odds": -110, "over_odds": -110, "under_odds": -110}
        passes, reason = passes_filters(games, stat_data, 0.08, True)
        assert not passes
        assert "min" in reason.lower()

    def test_high_vig(self):
        games = [{"PTS": 30, "MIN": 32}] * 12
        stat_data = {"best_over_odds": -130, "best_under_odds": -130, "over_odds": -130, "under_odds": -130}
        passes, reason = passes_filters(games, stat_data, 0.08, True)
        assert not passes
        assert "Vig" in reason

    def test_low_edge(self):
        games = [{"PTS": 30, "MIN": 32}] * 12
        stat_data = {"best_over_odds": -110, "best_under_odds": -110, "over_odds": -110, "under_odds": -110}
        passes, reason = passes_filters(games, stat_data, 0.04, True)
        assert not passes
        assert "Edge" in reason

    def test_unstable_minutes_low_edge(self):
        games = [{"PTS": 30, "MIN": 32}] * 12
        stat_data = {"best_over_odds": -110, "best_under_odds": -110, "over_odds": -110, "under_odds": -110}
        passes, reason = passes_filters(games, stat_data, 0.07, False)  # unstable + 7% < 8%
        assert not passes

    def test_qualifying(self):
        games = [{"PTS": 30, "MIN": 32}] * 12
        stat_data = {"best_over_odds": -110, "best_under_odds": -110, "over_odds": -110, "under_odds": -110}
        passes, reason = passes_filters(games, stat_data, 0.09, True)
        assert passes
