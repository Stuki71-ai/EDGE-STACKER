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
    param_names = ["measure_type_detailed", "measure_type_detailed_defense"]

    for param_name in param_names:
        delay = 1.0
        for attempt in range(1):  # Single attempt — nba_api often blocked from VPS
            try:
                stats = LeagueDashTeamStats(season=season, timeout=10, **{param_name: "Advanced"})
                time.sleep(config.NBA_API_DELAY)

                df = stats.get_data_frames()[0]
                records = df.to_dict("records")

                for row in records:
                    team_id = str(row.get("TEAM_ID", ""))
                    drtg = row.get("DEF_RATING")
                    if team_id and drtg is not None:
                        drtg_map[team_id] = float(drtg)

                if drtg_map:
                    return drtg_map
            except Exception as e:
                if attempt < config.NBA_API_RETRIES - 1:
                    logger.debug(f"DRTG retry {attempt+1} ({param_name}): {e}")
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.warning(f"DRTG failed with {param_name}: {e}")
                    break  # Try next param name

    logger.error("Could not get team defensive ratings with any parameter variant")
    return drtg_map


def get_player_game_log(player_id, season="2025-26", last_n=10):
    """Get last N games for a player.

    Returns:
        list of dicts with PTS, REB, AST, MIN, FGA, FTA, TEAM_ID, etc.
    """
    from nba_api.stats.endpoints import PlayerGameLog

    retries = 1  # Single attempt — nba_api often blocked from VPS IPs
    delay = 1.0

    for attempt in range(retries):
        try:
            log = PlayerGameLog(
                player_id=player_id,
                season=season,
                timeout=5,
            )
            time.sleep(config.NBA_API_DELAY)

            df = log.get_data_frames()[0]
            records = df.to_dict("records")
            return records[:last_n]  # Slice to last N (df is sorted most recent first)
        except Exception as e:
            if attempt < retries - 1:
                logger.debug(f"Retry {attempt+1} for player {player_id}: {e}")
                time.sleep(delay)
                delay *= 2  # Exponential backoff: 1s, 2s, 4s
            else:
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
