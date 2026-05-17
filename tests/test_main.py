"""Tests for main.py — module calendar, bankroll state, daily exposure."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from datetime import date
from main import active_modules, load_bankroll, load_daily_exposure


class TestModuleCalendar:
    def test_summer_mlb_only(self):
        # Deep summer: only the MLB F5 module is active
        d = date(2026, 7, 15)
        assert active_modules(d) == ["mlb_f5"]


class TestBankrollState:
    def test_load_default(self, tmp_path, monkeypatch):
        # Point to nonexistent file
        monkeypatch.setattr("config.BANKROLL_STATE_PATH", str(tmp_path / "nonexistent.json"))
        bankroll, peak, dd = load_bankroll()
        assert bankroll == 500.00
        assert peak == 500.00
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
