import logging
from shared import espn_client

logger = logging.getLogger("edge_stacker")


def get_games(target_date):
    """Get NCAAF games for a date with venue info.

    Args:
        target_date: date object

    Returns:
        List of game dicts from ESPN parser.
    """
    date_str = target_date.strftime("%Y%m%d")
    data = espn_client.get_ncaaf_scoreboard(date_str)
    games = espn_client.parse_events(data)
    logger.info(f"NCAAF Weather: found {len(games)} games on {date_str}")
    return games
