import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from staking import (
    american_to_prob, american_to_decimal_payout, kelly_fraction,
    calculate_vig, norm_cdf, assign_grade, apply_portfolio_limits,
    MIN_KELLY_TO_BET, KELLY_MULTIPLIER, KELLY_MULTIPLIER_DRAWDOWN,
)
from shared.pick import Pick


def _make_pick(module="test", model_prob=0.60, odds=-110, edge=0.06):
    return Pick(
        module=module,
        matchup="Test",
        pick_description="Test pick",
        best_odds_raw=odds,
        best_odds_book="TestBook",
        consensus_odds_raw=odds,
        model_prob=model_prob,
        implied_prob=american_to_prob(odds),
        edge_pct=edge,
    )


class TestAmericanToProb:
    def test_negative_odds(self):
        assert abs(american_to_prob(-110) - 0.5238) < 0.001

    def test_positive_odds(self):
        assert abs(american_to_prob(+140) - 0.4167) < 0.001

    def test_even_odds(self):
        assert abs(american_to_prob(+100) - 0.50) < 0.001


class TestAmericanToDecimalPayout:
    def test_negative_odds(self):
        assert abs(american_to_decimal_payout(-110) - 0.9091) < 0.001

    def test_positive_odds(self):
        assert abs(american_to_decimal_payout(+140) - 1.40) < 0.001


class TestKellyFraction:
    def test_kelly_with_edge(self):
        # model_prob 0.60, odds -110: b=0.909, kelly=(0.909*0.60-0.40)/0.909 = 0.160
        k = kelly_fraction(0.60, -110)
        assert abs(k - 0.160) < 0.005

    def test_kelly_plus_odds(self):
        # model_prob 0.55, odds +140: b=1.40, kelly=(1.40*0.55-0.45)/1.40 = 0.229
        k = kelly_fraction(0.55, +140)
        assert abs(k - 0.229) < 0.005

    def test_kelly_no_edge(self):
        # model_prob 0.52, odds -110: should be ~0 or negative
        k = kelly_fraction(0.52, -110)
        assert k == 0.0 or k < 0.01

    def test_kelly_floor_at_zero(self):
        k = kelly_fraction(0.30, -110)
        assert k == 0.0


class TestVig:
    def test_normal_vig(self):
        # -110/-110: 0.5238 + 0.5238 - 1 = 0.0476
        vig = calculate_vig(-110, -110)
        assert abs(vig - 0.0476) < 0.001

    def test_high_vig(self):
        # -130/-130: 0.565 + 0.565 - 1 = 0.13
        vig = calculate_vig(-130, -130)
        assert abs(vig - 0.13) < 0.01


class TestNormCdf:
    def test_known_value(self):
        # norm_cdf(28.5, 33.0, 7.26) ~ 0.268
        result = norm_cdf(28.5, 33.0, 7.26)
        assert abs(result - 0.268) < 0.01

    def test_at_mean(self):
        assert abs(norm_cdf(0, 0, 1) - 0.5) < 0.001


class TestGrade:
    def test_a_plus(self):
        assert assign_grade(0.12) == "A+"

    def test_a(self):
        assert assign_grade(0.08) == "A"

    def test_b_plus(self):
        assert assign_grade(0.06) == "B+"

    def test_b(self):
        assert assign_grade(0.04) == "B"

    def test_c(self):
        assert assign_grade(0.02) == "C"


class TestPortfolioLimits:
    def test_min_kelly_skip(self):
        pick = _make_pick(model_prob=0.52, odds=-110, edge=0.01)
        picks, dd = apply_portfolio_limits([pick], 5000, 5000)
        assert pick.bet_size == 0
        assert "edge too small" in pick.staking_note

    def test_quarter_kelly_sizing(self):
        # raw kelly ~0.16, quarter = 0.04, on $5000 = $200, capped at $150 (3%)
        pick = _make_pick(model_prob=0.60, odds=-110, edge=0.08)
        picks, dd = apply_portfolio_limits([pick], 5000, 5000)
        assert pick.bet_size == 150.0  # capped at 3% of $5000

    def test_daily_cap(self):
        # Prior exposure $300, daily limit $400 (8% of $5000)
        pick = _make_pick(model_prob=0.60, odds=-110, edge=0.08)
        picks, dd = apply_portfolio_limits([pick], 5000, 5000, prior_exposure=300)
        assert pick.bet_size <= 100.0  # Only $100 remaining

    def test_module_cap(self):
        # Multiple picks from same module
        picks = [
            _make_pick(module="nba_props", model_prob=0.60, odds=-110, edge=0.08),
            _make_pick(module="nba_props", model_prob=0.58, odds=-108, edge=0.06),
            _make_pick(module="nba_props", model_prob=0.57, odds=-106, edge=0.05),
        ]
        result, dd = apply_portfolio_limits(picks, 5000, 5000)
        total_module = sum(p.bet_size for p in result if p.module == "nba_props")
        assert total_module <= 250.0  # 5% of $5000

    def test_drawdown_entry(self):
        # 22% drawdown: enters drawdown
        picks, dd = apply_portfolio_limits([], 3900, 5000, in_drawdown=False)
        assert dd is True

    def test_drawdown_hysteresis(self):
        # 15% drawdown while in_drawdown=True: stays in drawdown
        picks, dd = apply_portfolio_limits([], 4250, 5000, in_drawdown=True)
        assert dd is True

    def test_drawdown_recovery(self):
        # 9% drawdown while in_drawdown=True: exits drawdown
        picks, dd = apply_portfolio_limits([], 4550, 5000, in_drawdown=True)
        assert dd is False

    def test_drawdown_not_entered(self):
        # 19% drawdown while not in drawdown: stays out (threshold is 20%)
        picks, dd = apply_portfolio_limits([], 4050, 5000, in_drawdown=False)
        assert dd is False

    def test_potential_win_negative_odds(self):
        pick = _make_pick(model_prob=0.60, odds=-110, edge=0.08)
        picks, dd = apply_portfolio_limits([pick], 5000, 5000)
        if pick.bet_size > 0:
            expected = round(pick.bet_size * (100.0 / 110.0), 2)
            assert abs(pick.potential_win - expected) < 0.02

    def test_potential_win_positive_odds(self):
        pick = _make_pick(model_prob=0.55, odds=+140, edge=0.08)
        picks, dd = apply_portfolio_limits([pick], 5000, 5000)
        if pick.bet_size > 0:
            expected = round(pick.bet_size * 1.40, 2)
            assert abs(pick.potential_win - expected) < 0.02

    def test_cross_run_exposure(self):
        # Morning placed $85, afternoon sees prior_exposure = $85
        pick = _make_pick(model_prob=0.60, odds=-110, edge=0.08)
        picks, dd = apply_portfolio_limits([pick], 5000, 5000, prior_exposure=85.0)
        # Daily limit is $400, so $315 remaining
        assert pick.bet_size <= 315.0

    def test_drawdown_halves_kelly(self):
        pick = _make_pick(model_prob=0.60, odds=-110, edge=0.08)
        picks_normal, _ = apply_portfolio_limits([pick], 5000, 5000, in_drawdown=False)
        normal_size = pick.bet_size

        pick2 = _make_pick(model_prob=0.60, odds=-110, edge=0.08)
        picks_dd, _ = apply_portfolio_limits([pick2], 3900, 5000, in_drawdown=True)
        dd_size = pick2.bet_size

        # Drawdown should result in smaller bet
        if normal_size > 0 and dd_size > 0:
            assert dd_size < normal_size
