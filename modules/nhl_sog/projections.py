"""NHL Shots on Goal projection model."""

import logging

logger = logging.getLogger("edge_stacker")

LEAGUE_AVG_SHOTS_AGAINST = 30.0  # NHL teams average ~30 shots against per game


def project_player_sog(player_games, opp_shots_against_per_game):
    """Build SOG projection from last N games + opponent factor.

    Args:
        player_games: list of dicts with S, TOI_SEC, GAME_DATE keys
        opp_shots_against_per_game: float

    Returns:
        dict: projection, sample_size, avg_TOI_min, sog_per_60
    """
    if not player_games:
        return None

    shots = [float(g.get("S", 0)) for g in player_games]
    toi_secs = [float(g.get("TOI_SEC", 0)) for g in player_games]

    total_shots = sum(shots)
    total_toi_sec = sum(toi_secs)

    if total_toi_sec <= 0:
        return None

    sog_per_60 = total_shots / (total_toi_sec / 3600.0)
    avg_toi_sec = total_toi_sec / len(player_games)

    # Opponent factor — normalize against league avg
    opp_factor = opp_shots_against_per_game / LEAGUE_AVG_SHOTS_AGAINST if opp_shots_against_per_game else 1.0
    # Soften extreme factors (cap between 0.85 and 1.15)
    opp_factor = max(0.85, min(1.15, opp_factor))

    # Project shots for tonight = SOG_per_60 × expected_TOI/60 × opp_factor
    projection = sog_per_60 * (avg_toi_sec / 3600.0) * opp_factor

    return {
        "projection": round(projection, 2),
        "sog_per_60": round(sog_per_60, 2),
        "avg_TOI_min": round(avg_toi_sec / 60.0, 1),
        "sample_size": len(player_games),
        "opp_factor": round(opp_factor, 3),
    }
