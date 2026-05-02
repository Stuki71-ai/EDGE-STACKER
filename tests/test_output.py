"""Tests for output.py — JSON schema and email format validation."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from shared.pick import Pick
from staking import american_to_prob, assign_grade
from output import build_output, output_empty


def _make_placed_pick():
    return Pick(
        module="ncaab_conf_tourney",
        matchup="Duke vs NC State",
        pick_description="NC STATE +7.5",
        best_odds_raw=-108,
        best_odds_book="FanDuel",
        consensus_odds_raw=-110,
        implied_prob=american_to_prob(-108),
        model_prob=0.781,
        edge_pct=0.262,
        grade="A+",
        kelly_fraction=0.545,
        bet_size=150.00,
        potential_win=138.89,
        context={"conference": "ACC", "round": "semifinals", "historical_ats": 0.781, "sample": 32},
        bet_by="12:00 PM ET",
        game_time="2:30 PM ET",
    )


def _make_skipped_pick():
    return Pick(
        module="ncaab_kenpom",
        matchup="Vermont vs UMBC",
        pick_description="VERMONT +2.5",
        best_odds_raw=-110,
        best_odds_book="DraftKings",
        consensus_odds_raw=-110,
        edge_pct=0.028,
        grade="C",
        staking_note="Skipped: edge too small",
    )


class TestBuildOutput:
    def test_json_schema_fields(self):
        placed = [_make_placed_pick()]
        skipped = [_make_skipped_pick()]
        output = build_output(placed, skipped, 5000, 5200, ["ncaab_kenpom", "ncaab_conf_tourney"])

        assert "date" in output
        assert "run_time" in output
        assert "bankroll" in output
        assert output["bankroll"] == 5000
        assert "peak_bankroll" in output
        assert output["peak_bankroll"] == 5200
        assert "daily_exposure" in output
        assert "daily_limit" in output
        assert "modules_run" in output
        assert "picks" in output
        assert "skipped" in output
        assert "summary" in output
        assert "email_body" in output

    def test_summary_fields(self):
        placed = [_make_placed_pick()]
        skipped = [_make_skipped_pick()]
        output = build_output(placed, skipped, 5000, 5200, ["ncaab_kenpom"])

        summary = output["summary"]
        assert summary["picks_placed"] == 1
        assert summary["picks_skipped"] == 1
        assert summary["picks_qualified"] == 2
        assert summary["total_wagered"] == 150.00
        assert summary["total_potential_win"] == 138.89

    def test_pick_dict_fields(self):
        placed = [_make_placed_pick()]
        output = build_output(placed, [], 5000, 5200, ["ncaab_conf_tourney"])

        pick_dict = output["picks"][0]
        required_fields = [
            "module", "matchup", "pick_description", "best_odds_raw",
            "best_odds_book", "consensus_odds_raw", "model_prob", "implied_prob",
            "edge_pct", "grade", "kelly_fraction", "bet_size", "potential_win",
            "staking_note", "bet_by", "game_time", "context",
        ]
        for field in required_fields:
            assert field in pick_dict, f"Missing field: {field}"

    def test_skipped_dict_fields(self):
        skipped = [_make_skipped_pick()]
        output = build_output([], skipped, 5000, 5200, ["ncaab_kenpom"])

        skip_dict = output["skipped"][0]
        assert "module" in skip_dict
        assert "matchup" in skip_dict
        assert "pick_description" in skip_dict
        assert "edge_pct" in skip_dict
        assert "grade" in skip_dict
        assert "staking_note" in skip_dict

    def test_email_body_contains_key_sections(self):
        placed = [_make_placed_pick()]
        skipped = [_make_skipped_pick()]
        output = build_output(placed, skipped, 5000, 5200, ["ncaab_conf_tourney"])

        email = output["email_body"]
        assert "Duke" in email or "NC State" in email

    def test_empty_picks_no_crash(self):
        output = build_output([], [], 5000, 5000, ["nba_props"])
        assert output["picks"] == []
        assert output["summary"]["picks_placed"] == 0

    def test_daily_limit_calculation(self):
        output = build_output([], [], 5000, 5000, [])
        assert output["daily_limit"] == 400.00  # 8% of 5000

    def test_json_serializable(self):
        placed = [_make_placed_pick()]
        output = build_output(placed, [], 5000, 5200, ["ncaab_conf_tourney"])
        # Should not raise
        json_str = json.dumps(output)
        # Should parse back
        parsed = json.loads(json_str)
        assert parsed["bankroll"] == 5000


    def test_daily_exposure_includes_prior(self):
        placed = [_make_placed_pick()]
        output = build_output(placed, [], 5000, 5200, ["ncaab_conf_tourney"],
                              prior_exposure=85.0)
        # daily_exposure should be prior (85) + current run (150)
        assert output["daily_exposure"] == 235.0


class TestOutputEmpty:
    def test_output_empty_prints_json(self, capsys):
        output_empty("No active modules today.", 5000, 5000, ["nba_props"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["picks"] == []
        assert data["skipped"] == []
        assert "email_body" in data
        assert "No qualifying picks" in data["email_body"]

    def test_output_empty_has_all_schema_fields(self, capsys):
        output_empty("Test.", 5000, 5200, ["nba_props"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "run_time" in data
        assert data["bankroll"] == 5000
        assert data["peak_bankroll"] == 5200
        assert data["daily_exposure"] == 0.0
        assert data["daily_limit"] == 400.0
        assert data["modules_run"] == ["nba_props"]
