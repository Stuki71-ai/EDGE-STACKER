"""NHL Shots on Goal projection model — EWMA over full season."""

import logging

logger = logging.getLogger("edge_stacker")

LEAGUE_AVG_SHOTS_AGAINST = 30.0
EWMA_DECAY = 0.85  # most recent game weight=1.0, n games ago weight=0.85^n


def ewma(values, decay=EWMA_DECAY):
    """Exponentially weighted moving average. values[0] is most recent."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** i for i in range(n)]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def weighted_std(values, mean, decay=EWMA_DECAY):
    """Exponentially weighted standard deviation."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** i for i in range(n)]
    var = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / sum(weights)
    return var ** 0.5


def project_player_sog(player_games, opp_shots_against_per_game):
    """Build SOG projection using EWMA over full season + opponent factor.

    Args:
        player_games: list of dicts with S, TOI_SEC, GAME_DATE keys (full season, most recent first)
        opp_shots_against_per_game: float

    Returns:
        dict: projection, std, sample_size, avg_TOI_min, sog_per_60
    """
    if not player_games:
        return None

    shots = [float(g.get("S", 0)) for g in player_games]
    toi_secs = [float(g.get("TOI_SEC", 0)) for g in player_games]

    # EWMA on per-game shots and TOI separately
    avg_shots = ewma(shots)
    avg_toi_sec = ewma(toi_secs)
    std_shots = weighted_std(shots, avg_shots)

    if avg_toi_sec <= 0:
        return None

    # SOG per 60 = avg_shots / (avg_toi/60). This is a per-minute rate adjusted to per-60.
    sog_per_60 = (avg_shots / avg_toi_sec) * 3600.0

    # Opponent factor — normalize against league avg, capped between 0.85 and 1.15
    opp_factor = opp_shots_against_per_game / LEAGUE_AVG_SHOTS_AGAINST if opp_shots_against_per_game else 1.0
    opp_factor = max(0.85, min(1.15, opp_factor))

    # Project shots = SOG_per_60 × expected_TOI/60 × opp_factor
    projection = sog_per_60 * (avg_toi_sec / 3600.0) * opp_factor

    return {
        "projection": round(projection, 2),
        "std": round(std_shots, 2),
        "sog_per_60": round(sog_per_60, 2),
        "avg_TOI_min": round(avg_toi_sec / 60.0, 1),
        "sample_size": len(player_games),
        "opp_factor": round(opp_factor, 3),
    }
