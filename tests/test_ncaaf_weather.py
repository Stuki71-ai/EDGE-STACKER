import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from modules.ncaaf_weather.filters import weather_under_model_prob, passes_filters


class TestWeatherModelProb:
    def test_wind_15_temp_50(self):
        prob = weather_under_model_prob(15, 50, "Clear")
        assert abs(prob - 0.58) < 0.01

    def test_wind_22_temp_28_snow(self):
        # wind >= 20: 0.65, temp 28 < 40: +0.03, snow: +0.03 = 0.71
        prob = weather_under_model_prob(22, 28, "Snow")
        assert abs(prob - 0.71) < 0.01

    def test_wind_22_temp_20_snow(self):
        # wind >= 20: 0.65, temp 20 < 25: +0.05, snow: +0.03 = 0.73
        prob = weather_under_model_prob(22, 20, "Snow")
        assert abs(prob - 0.73) < 0.01

    def test_wind_13_base(self):
        prob = weather_under_model_prob(13, 60, "Clear")
        assert abs(prob - 0.57) < 0.01

    def test_wind_20_cold(self):
        # wind >= 20: 0.65, temp < 40: +0.03 = 0.68
        prob = weather_under_model_prob(20, 35, "Clear")
        assert abs(prob - 0.68) < 0.01

    def test_max_cap(self):
        # Should not exceed 0.75
        prob = weather_under_model_prob(25, 10, "Snow")
        assert prob <= 0.75

    def test_rain_bonus(self):
        prob_clear = weather_under_model_prob(15, 50, "Clear")
        prob_rain = weather_under_model_prob(15, 50, "Rain")
        assert prob_rain - prob_clear == pytest.approx(0.03, abs=0.001)


class TestFilters:
    def _make_game(self, venue="Michigan Stadium", indoor=False):
        return {
            "venue_name": venue,
            "venue_indoor": indoor,
            "name": "Test Game",
        }

    def _stadiums(self):
        return {
            "Michigan Stadium": {"lat": 42.27, "lon": -83.75, "dome": False},
            "Lucas Oil Stadium": {"lat": 39.76, "lon": -86.16, "dome": True},
        }

    def test_dome_game_skipped(self):
        game = self._make_game("Lucas Oil Stadium")
        weather = {"wind_mph": 20, "temp_f": 30, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -108, 45, self._stadiums())
        assert not passes
        assert "Dome" in reason

    def test_low_wind_skipped(self):
        game = self._make_game()
        weather = {"wind_mph": 10, "temp_f": 50, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -108, 45, self._stadiums())
        assert not passes
        assert "Wind" in reason

    def test_low_total_skipped(self):
        game = self._make_game()
        weather = {"wind_mph": 15, "temp_f": 50, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -108, 36, self._stadiums())
        assert not passes
        assert "Total" in reason

    def test_bad_odds_skipped(self):
        game = self._make_game()
        weather = {"wind_mph": 15, "temp_f": 50, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -120, 45, self._stadiums())
        assert not passes
        assert "odds" in reason.lower()

    def test_unknown_stadium_skipped(self):
        game = self._make_game("Unknown Field")
        weather = {"wind_mph": 15, "temp_f": 50, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -108, 45, self._stadiums())
        assert not passes
        assert "Unknown" in reason

    def test_qualifying_pick(self):
        game = self._make_game()
        weather = {"wind_mph": 15, "temp_f": 50, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -108, 45, self._stadiums())
        assert passes
        assert reason is None

    def test_espn_indoor_flag(self):
        game = self._make_game("Some Stadium", indoor=True)
        stadiums = {"Some Stadium": {"lat": 40.0, "lon": -80.0, "dome": False}}
        weather = {"wind_mph": 20, "temp_f": 30, "precipitation": "Clear"}
        passes, reason = passes_filters(game, weather, -108, 45, stadiums)
        assert not passes
