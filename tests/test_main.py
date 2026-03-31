"""Tests for main.py — module calendar, bankroll state, daily exposure."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from datetime import date
from main import active_modules, load_bankroll, load_daily_exposure


class TestModuleCalendar:
    def test_march_31_tuesday(self):
        # Mar 31, 2026 = Tuesday (dow=1)
        mods = active_modules(date(2026, 3, 31))
        assert "nba_props" in mods
        assert "ncaab_kenpom" in mods
        assert "ncaaf_weather" not in mods
        assert "ncaab_conf_tourney" not in mods  # Mar 31 > 15

    def test_september_saturday(self):
        # Sep 5, 2026 = Saturday
        mods = active_modules(date(2026, 9, 5))
        assert "ncaaf_weather" in mods
        assert "nba_props" not in mods

    def test_september_wednesday(self):
        # Sep 2, 2026 = Wednesday -- no NCAAF on weekdays
        mods = active_modules(date(2026, 9, 2))
        assert "ncaaf_weather" not in mods
        assert "nba_props" not in mods

    def test_december_saturday(self):
        d = date(2026, 12, 19)  # Saturday in Dec
        mods = active_modules(d)
        assert "ncaaf_weather" in mods  # Sep-Jan, Sat
        assert "nba_props" in mods
        assert "ncaaf_bowls" in mods  # Dec 14+
        assert "ncaab_kenpom" in mods  # Nov-Mar

    def test_bowl_season_jan(self):
        d = date(2027, 1, 2)  # Saturday, Jan 2, 2027
        mods = active_modules(d)
        assert "ncaaf_bowls" in mods  # Jan <= 10
        assert "ncaaf_weather" in mods  # Jan <= 15, Sat

    def test_after_bowls(self):
        d = date(2027, 1, 11)
        mods = active_modules(d)
        assert "ncaaf_bowls" not in mods  # Jan 11 > 10

    def test_conf_tourney_active(self):
        d = date(2026, 3, 10)  # Tuesday in March 1-15
        mods = active_modules(d)
        assert "ncaab_conf_tourney" in mods
        assert "ncaab_kenpom" in mods
        assert "nba_props" in mods

    def test_conf_tourney_over(self):
        d = date(2026, 3, 16)
        mods = active_modules(d)
        assert "ncaab_conf_tourney" not in mods

    def test_summer_dead(self):
        d = date(2026, 7, 15)
        assert active_modules(d) == []

    def test_nba_starts_oct15(self):
        assert "nba_props" not in active_modules(date(2026, 10, 14))
        assert "nba_props" in active_modules(date(2026, 10, 15))

    def test_nba_ends_jun20(self):
        assert "nba_props" in active_modules(date(2026, 6, 20))
        assert "nba_props" not in active_modules(date(2026, 6, 21))


class TestBankrollState:
    def test_load_default(self, tmp_path, monkeypatch):
        # Point to nonexistent file
        monkeypatch.setattr("config.BANKROLL_STATE_PATH", str(tmp_path / "nonexistent.json"))
        bankroll, peak, dd = load_bankroll()
        assert bankroll == 5000.00
        assert peak == 5000.00
        assert dd is False

    def test_load_existing(self, tmp_path, monkeypatch):
        state_file = tmp_path / "bankroll.json"
        state_file.write_text(json.dumps({
            "bankroll": 4500.00,
            "peak_bankroll": 5200.00,
            "in_drawdown": True,
        }))
        monkeypatch.setattr("config.BANKROLL_STATE_PATH", str(state_file))
        bankroll, peak, dd = load_bankroll()
        assert bankroll == 4500.00
        assert peak == 5200.00
        assert dd is True


class TestDailyExposure:
    def test_new_day_resets(self, tmp_path, monkeypatch):
        state_file = tmp_path / "daily.json"
        state_file.write_text(json.dumps({
            "date": "2026-01-01",
            "total_exposure": 200.0,
        }))
        monkeypatch.setattr("config.DAILY_STATE_PATH", str(state_file))
        exp = load_daily_exposure(date(2026, 3, 31))
        assert exp == 0.0  # Different date -> reset

    def test_same_day_carries(self, tmp_path, monkeypatch):
        state_file = tmp_path / "daily.json"
        state_file.write_text(json.dumps({
            "date": "2026-03-31",
            "total_exposure": 85.0,
        }))
        monkeypatch.setattr("config.DAILY_STATE_PATH", str(state_file))
        exp = load_daily_exposure(date(2026, 3, 31))
        assert exp == 85.0
