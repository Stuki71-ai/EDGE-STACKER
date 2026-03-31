import logging
from shared import espn_client

logger = logging.getLogger("edge_stacker")


def get_bowl_games(target_date):
    """Get NCAAF bowl games for a date.

    Uses seasontype=3 for postseason.
    """
    date_str = target_date.strftime("%Y%m%d")
    data = espn_client.get_ncaaf_scoreboard(date_str, seasontype=3)
    games = espn_client.parse_events(data)
    logger.info(f"NCAAF Bowls: found {len(games)} postseason games on {date_str}")
    return games


def get_team_conference(team_id):
    """Get conference name for a team."""
    try:
        data = espn_client.get_team_info("football/college-football", team_id)
        team = data.get("team", {})
        groups = team.get("groups", {})
        return groups.get("name")
    except Exception as e:
        logger.warning(f"Could not get conference for team {team_id}: {e}")
        return None
