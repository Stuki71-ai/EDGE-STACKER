import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from modules.ncaab_conf_tourney.schedule import detect_round, ROUND_KEYWORDS
from modules.ncaab_conf_tourney.filters import passes_filters


class TestRoundParsing:
    def test_semifinal(self):
        game = {"notes": ["ACC Tournament - Semifinal"], "type_abbreviation": "CTOURN"}
        result = detect_round(game)
        assert result == "semifinals"

    def test_championship(self):
        game = {"notes": ["NEC Championship"], "type_abbreviation": "CTOURN"}
        result = detect_round(game)
        assert result == "championship"

    def test_first_round(self):
        game = {"notes": ["Big 12 Tournament - First Round"], "type_abbreviation": "CTOURN"}
        result = detect_round(game)
        assert result == "first_round"

    def test_opening_round(self):
        game = {"notes": ["SEC Tournament - Opening Round"], "type_abbreviation": "CTOURN"}
        result = detect_round(game)
        assert result == "opening_round"

    def test_not_tournament(self):
        game = {"notes": ["Regular Season"], "type_abbreviation": ""}
        result = detect_round(game)
        assert result is None


class TestConfTourneyFilters:
    def _rules(self):
        return {
            "ACC": {
                "semifinals": {"dog_ats_pct": 0.781, "sample": 32, "years": "2007-2025", "min_sample": True}
            },
            "NEC": {
                "championship": {"dog_ats_pct": 0.842, "sample": 19, "years": "2006-2025", "min_sample": True}
            },
        }

    def test_acc_semifinal(self):
        game = {"_conference": "ACC", "_round_key": "semifinals"}
        spread_data = {"spread": 7, "best_odds": -108, "consensus_odds": -110, "underdog": "NC State"}
        passes, reason, rule = passes_filters(game, spread_data, self._rules())
        assert passes
        assert rule["dog_ats_pct"] == 0.781

    def test_not_in_rules(self):
        game = {"_conference": "Big Ten", "_round_key": "semifinals"}
        spread_data = {"spread": 7, "best_odds": -108}
        passes, reason, rule = passes_filters(game, spread_data, self._rules())
        assert not passes
        assert "not in rules" in reason

    def test_wrong_round(self):
        game = {"_conference": "ACC", "_round_key": "quarterfinals"}
        spread_data = {"spread": 7, "best_odds": -108}
        passes, reason, rule = passes_filters(game, spread_data, self._rules())
        assert not passes
        assert "Round" in reason

    def test_no_underdog(self):
        game = {"_conference": "ACC", "_round_key": "semifinals"}
        spread_data = {"spread": 0, "best_odds": -108}
        passes, reason, rule = passes_filters(game, spread_data, self._rules())
        assert not passes

    def test_no_spread_data(self):
        game = {"_conference": "ACC", "_round_key": "semifinals"}
        passes, reason, rule = passes_filters(game, None, self._rules())
        assert not passes

    def test_not_tournament(self):
        game = {"_conference": None, "_round_key": None}
        passes, reason, rule = passes_filters(game, {}, self._rules())
        assert not passes
