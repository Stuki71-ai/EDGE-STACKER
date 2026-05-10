"""F5 expected runs projection — pure functions."""

import math

LEAGUE_AVG_FIP = 4.10
LEAGUE_AVG_WOBA = 0.320


def project_team_runs_f5(pitcher_xfip, opp_woba, park_factor=1.00, weather_factor=1.00):
    """Project runs allowed by `pitcher` over first 5 innings.

    Args:
        pitcher_xfip: rolling 30-day xFIP
        opp_woba: opposing team's wOBA vs pitcher's hand (30d)
        park_factor: 3yr park factor for runs (1.00 neutral)
        weather_factor: temp/wind multiplier (1.00 neutral)
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

    Returns (direction, raw_edge, model_prob, odds_to_bet) or (None, 0, 0, 0).
    """
    from staking import american_to_prob

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
