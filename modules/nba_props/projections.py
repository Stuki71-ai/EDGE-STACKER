import logging
import config

logger = logging.getLogger("edge_stacker")

# EWMA decay factor — most recent weight=1.0, n games ago weight=DECAY^n
# 0.92 = half-life ~8-9 games — less reactive to short hot/cold streaks,
# more anchored to season-long performance. Matches what sharps do when
# they don't have position-vs-position defense data: regularize harder.
EWMA_DECAY = 0.92


def ewma(values, decay=EWMA_DECAY):
    """Exponentially weighted moving average.

    Assumes values[0] is most recent, values[-1] is oldest.
    Recent games get more weight; older games stabilize the estimate.
    """
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** i for i in range(n)]
    weight_sum = sum(weights)
    if weight_sum == 0:
        return sum(values) / n
    return sum(v * w for v, w in zip(values, weights)) / weight_sum


def weighted_std(values, mean, decay=EWMA_DECAY):
    """Exponentially weighted standard deviation."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** i for i in range(n)]
    weight_sum = sum(weights)
    if weight_sum == 0:
        return 0.0
    var = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / weight_sum
    return var ** 0.5


def project_player_stat(player_games, stat, opponent_drtg, teammate_out):
    """
    Build per-player projection using EWMA over full season.

    Args:
        player_games: list of dicts from gamelog (full season, most recent first)
        stat: "PTS", "REB", or "AST"
        opponent_drtg: float (opponent's defensive rating)
        teammate_out: bool (top-2 minutes player on same team is OUT)

    Returns:
        dict with projection, std, minutes_stable, sample_size, avg_minutes
    """
    if not player_games:
        return None

    values = [float(g[stat]) for g in player_games]
    minutes = [float(g["MIN"]) for g in player_games]

    # EWMA-based projection (recent games weighted heavier, but full season stabilizes)
    avg = ewma(values)
    avg_min = ewma(minutes)
    std = weighted_std(values, avg)
    min_std = weighted_std(minutes, avg_min)

    # Opponent adjustment (same as spec)
    opp_factor = opponent_drtg / config.LEAGUE_AVG_DRTG

    if stat in ("PTS", "AST"):
        adjusted = avg * opp_factor
    elif stat == "REB":
        adjusted = avg * (opp_factor * 0.4 + 0.6)  # rebounds less affected
    else:
        adjusted = avg

    # Teammate absence boost
    if teammate_out and stat in ("PTS", "AST"):
        adjusted *= 1.12

    return {
        "projection": round(adjusted, 1),
        "std": round(std, 2),
        "raw_avg": round(avg, 1),
        "minutes_stable": min_std < 5.0,
        "minutes_very_stable": min_std < 3.0,
        "sample_size": len(player_games),
        "avg_minutes": round(avg_min, 1),
    }
