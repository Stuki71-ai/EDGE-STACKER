import logging
import config
from staking import american_to_prob, norm_cdf, calculate_vig

logger = logging.getLogger("edge_stacker")


MAX_EDGE = 0.20  # No real sports betting edge exceeds 20%


def prop_edge(projection, line, stat, over_odds_raw, under_odds_raw, actual_std=None):
    """
    Compare projection to line using normal distribution.
    Uses actual std from player's last 10 games if provided,
    falls back to fixed percentage if not.
    Returns: (direction, edge, model_prob, odds_to_bet) or (None, 0, 0, 0)
    """
    if actual_std is not None and actual_std > 0:
        std = actual_std
    else:
        std = projection * config.STAT_STD_PCT.get(stat, 0.22)
    if std < 0.5:
        std = 0.5

    over_prob = 1.0 - norm_cdf(line, projection, std)
    under_prob = norm_cdf(line, projection, std)

    over_implied = american_to_prob(over_odds_raw)
    under_implied = american_to_prob(under_odds_raw)

    over_edge = over_prob - over_implied
    under_edge = under_prob - under_implied

    # Cap edge at MAX_EDGE — anything higher means model overconfidence
    over_edge = min(over_edge, MAX_EDGE)
    under_edge = min(under_edge, MAX_EDGE)

    if over_edge >= config.PROP_MIN_EDGE and over_edge > under_edge:
        return "OVER", over_edge, over_prob, over_odds_raw
    elif under_edge >= config.PROP_MIN_EDGE and under_edge > over_edge:
        return "UNDER", under_edge, under_prob, under_odds_raw
    else:
        return None, 0.0, 0.0, 0


def passes_filters(player_games, stat_data, edge_pct, minutes_stable):
    """
    Run the NBA Props filter pipeline.
    Returns: (passes: bool, reason: str or None)
    """
    # STEP 1: Player has >= 10 games?
    if len(player_games) < config.PROP_MIN_GAMES:
        return False, f"Only {len(player_games)} games (need {config.PROP_MIN_GAMES})"

    # STEP 2: Average >= 20 minutes?
    avg_min = sum(float(g.get("MIN", 0)) for g in player_games) / len(player_games)
    if avg_min < config.PROP_MIN_MINUTES:
        return False, f"Avg {avg_min:.1f} min < {config.PROP_MIN_MINUTES}"

    # STEP 3: Vig <= 8%?
    over_odds = stat_data.get("best_over_odds") or stat_data.get("over_odds")
    under_odds = stat_data.get("best_under_odds") or stat_data.get("under_odds")
    if over_odds is not None and under_odds is not None:
        vig = calculate_vig(over_odds, under_odds)
        if vig > config.PROP_MAX_VIG:
            return False, f"Vig {vig:.3f} > {config.PROP_MAX_VIG}"

    # STEP 4: Edge >= 6%?
    if edge_pct < config.PROP_MIN_EDGE:
        return False, f"Edge {edge_pct:.1%} < {config.PROP_MIN_EDGE:.1%}"

    # STEP 5: Minutes stability?
    if not minutes_stable and edge_pct < config.PROP_MINUTES_HIGH_EDGE:
        return False, f"Unstable minutes + edge {edge_pct:.1%} < {config.PROP_MINUTES_HIGH_EDGE:.1%}"

    return True, None
