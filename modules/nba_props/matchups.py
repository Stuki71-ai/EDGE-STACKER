import logging
import time
import config

logger = logging.getLogger("edge_stacker")


def get_team_defensive_ratings(season="2025-26"):
    """Get defensive rating for all NBA teams.

    Returns:
        dict mapping team_id (str) -> defensive_rating (float)
    """
    from nba_api.stats.endpoints import LeagueDashTeamStats

    drtg_map = {}

    try:
        stats = LeagueDashTeamStats(
            season=season,
            measure_type_detailed="Advanced"
        )
        time.sleep(config.NBA_API_DELAY)

        df = stats.get_data_frames()[0]
        records = df.to_dict("records")

        for row in records:
            team_id = str(row.get("TEAM_ID", ""))
            drtg = row.get("DEF_RATING")
            if team_id and drtg is not None:
                drtg_map[team_id] = float(drtg)

    except Exception as e:
        logger.error(f"Failed to get team defensive ratings: {e}")
        # Try alternate parameter name
        try:
            stats = LeagueDashTeamStats(
                season=season,
                measure_type_detailed_defense="Advanced"
            )
            time.sleep(config.NBA_API_DELAY)
            df = stats.get_data_frames()[0]
            records = df.to_dict("records")
            for row in records:
                team_id = str(row.get("TEAM_ID", ""))
                drtg = row.get("DEF_RATING")
                if team_id and drtg is not None:
                    drtg_map[team_id] = float(drtg)
        except Exception as e2:
            logger.error(f"Alternate parameter also failed: {e2}")

    return drtg_map


def get_player_game_log(player_id, season="2025-26", last_n=10):
    """Get last N games for a player.

    Returns:
        list of dicts with PTS, REB, AST, MIN, FGA, FTA
    """
    from nba_api.stats.endpoints import PlayerGameLog

    try:
        log = PlayerGameLog(
            player_id=player_id,
            season=season,
            last_n_games_nullable=last_n,
        )
        time.sleep(config.NBA_API_DELAY)

        df = log.get_data_frames()[0]
        return df.to_dict("records")
    except Exception as e:
        logger.warning(f"Failed to get game log for player {player_id}: {e}")
        return []


def get_team_roster_minutes(team_id, season="2025-26"):
    """Get roster sorted by average minutes (for injury impact detection).

    Returns:
        list of (player_id, avg_minutes) sorted descending
    """
    from nba_api.stats.endpoints import TeamPlayerDashboard

    try:
        dash = TeamPlayerDashboard(
            team_id=team_id,
            season=season,
        )
        time.sleep(config.NBA_API_DELAY)

        df = dash.get_data_frames()[1]  # Player stats (index 0 is team)
        records = df.to_dict("records")

        roster = []
        for r in records:
            pid = r.get("PLAYER_ID")
            mins = r.get("MIN", 0)
            if pid and mins:
                roster.append((str(pid), float(mins)))

        roster.sort(key=lambda x: x[1], reverse=True)
        return roster
    except Exception as e:
        logger.warning(f"Failed to get roster minutes for team {team_id}: {e}")
        return []
