import logging
import config

logger = logging.getLogger("edge_stacker")


def project_player_stat(player_games, stat, opponent_drtg, teammate_out):
    """
    Build per-player projection from last 10 games.

    Args:
        player_games: list of dicts from PlayerGameLog (last 10 games)
        stat: "PTS", "REB", or "AST"
        opponent_drtg: float (opponent's defensive rating)
        teammate_out: bool (top-2 minutes player on same team is OUT)

    Returns:
        dict with projection, minutes_stable, minutes_very_stable, sample_size, avg_minutes
    """
    values = [float(g[stat]) for g in player_games]
    minutes = [float(g["MIN"]) for g in player_games]

    avg = sum(values) / len(values)
    avg_min = sum(minutes) / len(minutes)
    min_std = (sum((m - avg_min) ** 2 for m in minutes) / len(minutes)) ** 0.5

    # Opponent adjustment
    opp_factor = opponent_drtg / config.LEAGUE_AVG_DRTG

    if stat in ("PTS", "AST"):
        adjusted = avg * opp_factor
    elif stat == "REB":
        adjusted = avg * (opp_factor * 0.4 + 0.6)  # rebounds less affected
    else:
        adjusted = avg

    # Teammate absence boost
    if teammate_out and stat in ("PTS", "AST"):
        adjusted *= 1.12  # +12% usage redistribution

    return {
        "projection": round(adjusted, 1),
        "minutes_stable": min_std < 5.0,
        "minutes_very_stable": min_std < 3.0,
        "sample_size": len(player_games),
        "avg_minutes": round(avg_min, 1),
    }
