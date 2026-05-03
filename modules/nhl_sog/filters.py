"""NHL SOG filter pipeline + edge calculation."""

import logging
from staking import american_to_prob, norm_cdf, calculate_vig

logger = logging.getLogger("edge_stacker")

MIN_GAMES = 10
MIN_TOI_FORWARD_SEC = 14 * 60  # 14 min average for top-6 forwards
MIN_TOI_DEFENSE_SEC = 18 * 60  # 18 min for top-4 defensemen
MIN_EDGE = 0.06
MAX_VIG = 0.08
MAX_EDGE = 0.20


def sog_edge(projection, line, over_odds_raw, under_odds_raw, actual_std=None):
    """Compute edge using normal distribution.

    Returns (direction, edge, model_prob, odds_to_bet) or (None, 0, 0, 0).
    """
    if actual_std is not None and actual_std > 0:
        std = actual_std
    else:
        # Fallback: SOG std typically ~30% of mean for top-6 forwards
        std = projection * 0.30

    if std < 0.5:
        std = 0.5

    over_prob = 1.0 - norm_cdf(line, projection, std)
    under_prob = norm_cdf(line, projection, std)

    over_implied = american_to_prob(over_odds_raw)
    under_implied = american_to_prob(under_odds_raw)

    over_edge = min(over_prob - over_implied, MAX_EDGE)
    under_edge = min(under_prob - under_implied, MAX_EDGE)

    if over_edge >= MIN_EDGE and over_edge > under_edge:
        return "OVER", over_edge, over_prob, over_odds_raw
    elif under_edge >= MIN_EDGE and under_edge > over_edge:
        return "UNDER", under_edge, under_prob, under_odds_raw
    return None, 0.0, 0.0, 0


def passes_filters(player_games, position, stat_data, edge_pct):
    """Run NHL SOG filter pipeline.

    Returns (passes: bool, reason: str or None).
    """
    if len(player_games) < MIN_GAMES:
        return False, f"Only {len(player_games)} games (need {MIN_GAMES})"

    avg_toi = sum(float(g.get("TOI_SEC", 0)) for g in player_games) / len(player_games)
    is_defenseman = position in ("D",)
    min_toi_required = MIN_TOI_DEFENSE_SEC if is_defenseman else MIN_TOI_FORWARD_SEC

    if avg_toi < min_toi_required:
        return False, f"Avg TOI {avg_toi/60:.1f} min < {min_toi_required/60:.0f} min"

    over = stat_data.get("best_over_odds") or stat_data.get("over_odds")
    under = stat_data.get("best_under_odds") or stat_data.get("under_odds")
    if over is not None and under is not None:
        vig = calculate_vig(over, under)
        if vig > MAX_VIG:
            return False, f"Vig {vig:.3f} > {MAX_VIG}"

    if edge_pct < MIN_EDGE:
        return False, f"Edge {edge_pct:.1%} < {MIN_EDGE:.1%}"

    return True, None
