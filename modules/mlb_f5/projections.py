"""F5 expected runs projection — pure functions."""

import math

LEAGUE_AVG_FIP = 4.10
LEAGUE_AVG_WOBA = 0.320


def project_team_runs_f5(pitcher_xfip, opp_woba, park_factor=1.00, weather_factor=1.00):
    """Project runs allowed by `pitcher` over first 5 innings.

    Args:
        pitcher_xfip: rolling xFIP (last ~6 starts)
        opp_woba: opposing team's wOBA vs pitcher's hand
        park_factor: 3yr park factor for runs (1.00 neutral)
        weather_factor: temp/wind multiplier (1.00 neutral)

    ┌─ CALIBRATION NOTE — read before "fixing" either factor ─────────────────┐
    │ This projection carries TWO offsetting ~8% scale quirks that currently  │
    │ CANCEL, leaving the total ~correct (verified: two league-avg starters   │
    │ -> ~4.9 F5 runs vs ~5.0 real-world average):                            │
    │                                                                         │
    │  (1) `opp_woba` comes from an OPS->wOBA proxy whose league mean is       │
    │      ~0.336, while LEAGUE_AVG_WOBA here is 0.320 -> lineup_factor runs   │
    │      ~+8% high for an average team.                                     │
    │  (2) xFIP/FIP is an EARNED-run (ERA) scale stat, but the F5 market is    │
    │      TOTAL runs. Unearned runs are ~8% of all runs, so the base         │
    │      `xFIP*5/9` runs ~8% LOW vs a total-runs line.                       │
    │                                                                         │
    │ (1) and (2) offset to within ~1-2% across the realistic team range.     │
    │ DO NOT patch one without the other — that re-introduces a full ~8%      │
    │ directional bias. A proper recalibration of BOTH must be backtested     │
    │ before deployment; it is deliberately deferred (not a "try-and-see"     │
    │ constant tweak). See the MLB deep-audit report.                         │
    └─────────────────────────────────────────────────────────────────────────┘
    """
    base_runs_5 = pitcher_xfip * 5.0 / 9.0
    lineup_factor = 1.0 + (opp_woba - LEAGUE_AVG_WOBA) * 5.0
    return base_runs_5 * lineup_factor * park_factor * weather_factor


def project_total_f5(home_xfip, home_opp_woba, away_xfip, away_opp_woba,
                     park_factor=1.00, weather_factor=1.00):
    """Total F5 runs from both teams.

    home_opp_woba = AWAY team's wOBA vs HOME pitcher's hand
    away_opp_woba = HOME team's wOBA vs AWAY pitcher's hand
    """
    h = project_team_runs_f5(home_xfip, home_opp_woba, park_factor, weather_factor)
    a = project_team_runs_f5(away_xfip, away_opp_woba, park_factor, weather_factor)
    return h + a


def temp_weather_factor(temp_f):
    """Temperature-only weather adjustment. Hot air = ball carries further.

    Conservative: ±5% across realistic temp range. Wind/orientation NOT modeled
    because ballpark CF bearing isn't reliably automatable from free sources.

    Returns multiplier in [0.95, 1.05].
    """
    if temp_f is None:
        return 1.0
    delta = (temp_f - 70.0) / 20.0  # +1 per 20F above 70
    return max(0.95, min(1.05, 1.0 + delta * 0.03))


def poisson_cdf_at(line, expected):
    """P(X <= floor(line)) for X ~ Poisson(expected)."""
    k = int(math.floor(line))
    if k < 0:
        return 0.0
    term = math.exp(-expected)
    s = term
    for i in range(1, k + 1):
        term *= expected / i
        s += term
    return min(s, 1.0)


def f5_edge(projection, line, over_odds_raw, under_odds_raw):
    """Compute model edge vs F5 line.

    Push handling: integer F5 lines (4.0, 5.0, 6.0 — confirmed common in live
    markets) PUSH when the total lands exactly on the line. A push refunds the
    stake; it is NOT a win for either side. So p_under / p_over are each the
    probability that side wins OUTRIGHT — on an integer line the exact-line
    outcome is excluded from both, and they do not sum to 1. Counting the push
    as an under-win (the old behaviour) overstated every integer-line UNDER edge
    by P(X = line) ~= 0.15-0.18 — large enough to manufacture phantom picks.

    Returns (direction, raw_edge, model_prob, odds_to_bet) or (None, 0, 0, 0).
    """
    from staking import american_to_prob

    k = int(math.floor(line))
    if line == k:
        # integer line — a total of exactly k runs is a push
        p_under = poisson_cdf_at(k - 1, projection)   # P(X <= k-1)
        p_over = 1.0 - poisson_cdf_at(k, projection)  # P(X >= k+1)
    else:
        # half-point line — no push possible
        p_under = poisson_cdf_at(line, projection)
        p_over = 1.0 - p_under

    over_implied = american_to_prob(over_odds_raw)
    under_implied = american_to_prob(under_odds_raw)

    over_edge = p_over - over_implied
    under_edge = p_under - under_implied

    if over_edge > under_edge and over_edge > 0:
        return "OVER", over_edge, p_over, over_odds_raw
    elif under_edge > over_edge and under_edge > 0:
        return "UNDER", under_edge, p_under, under_odds_raw
    return None, 0.0, 0.0, 0
