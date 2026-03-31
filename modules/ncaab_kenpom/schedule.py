import logging
from shared import espn_client

logger = logging.getLogger("edge_stacker")


def get_games(target_date):
    """Get NCAAB games for a date."""
    date_str = target_date.strftime("%Y%m%d")
    data = espn_client.get_ncaab_scoreboard(date_str)
    games = espn_client.parse_events(data)

    # Check if we need pagination
    total_count = data.get("count", 0)
    if total_count > 300:
        logger.info(f"NCAAB: {total_count} games, fetching page 2")
        data2 = espn_client.get_ncaab_scoreboard(date_str, page=2)
        games.extend(espn_client.parse_events(data2))

    logger.info(f"NCAAB KenPom: found {len(games)} games on {date_str}")
    return games


def is_conference_game(game, kenpom_data=None):
    """Determine if a game is a conference game.

    First checks ESPN's conferenceCompetition flag.
    Falls back to comparing team conferences from KenPom data.
    """
    # Check ESPN flag first
    if game.get("conference_competition") is not None:
        return game["conference_competition"]

    # Fallback: compare conferences from KenPom data
    if kenpom_data:
        home_name = game.get("home", {}).get("name", "")
        away_name = game.get("away", {}).get("name", "")
        home_data = kenpom_data.get(home_name, {})
        away_data = kenpom_data.get(away_name, {})
        home_conf = home_data.get("Conf")
        away_conf = away_data.get("Conf")
        if home_conf and away_conf:
            return home_conf == away_conf

    return True  # default to conference game (more conservative HCA)
