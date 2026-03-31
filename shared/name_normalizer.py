import logging
import config

logger = logging.getLogger("edge_stacker")

_team_maps = {}


def _load_map(filename):
    """Load and cache a team map JSON."""
    if filename not in _team_maps:
        _team_maps[filename] = config.load_static_json(filename)
    return _team_maps[filename]


def espn_to_odds(name, sport):
    """Convert ESPN team name to Odds API name.

    sport: 'ncaaf', 'ncaab', or 'nba'
    """
    filename = f"team_map_{sport}.json"
    data = _load_map(filename)
    mapping = data.get("espn_to_odds", {})
    result = mapping.get(name)
    if result is None:
        logger.warning(f"No Odds API mapping for ESPN name '{name}' ({sport})")
    return result


def odds_to_espn(name, sport):
    """Convert Odds API team name to ESPN name."""
    filename = f"team_map_{sport}.json"
    data = _load_map(filename)
    mapping = data.get("odds_to_espn", {})
    result = mapping.get(name)
    if result is None:
        logger.warning(f"No ESPN mapping for Odds API name '{name}' ({sport})")
    return result


def nba_api_to_odds(name):
    """Convert nba_api team name to Odds API name."""
    data = _load_map("team_map_nba.json")
    mapping = data.get("nba_api_to_odds", {})
    return mapping.get(name)


def odds_to_nba_api(name):
    """Convert Odds API team name to nba_api name."""
    data = _load_map("team_map_nba.json")
    mapping = data.get("odds_to_nba_api", {})
    return mapping.get(name)


def find_odds_event(odds_events, home_name, away_name, sport):
    """Find an Odds API event matching ESPN home/away teams.

    Returns the matching event dict or None.
    """
    home_odds = espn_to_odds(home_name, sport)
    away_odds = espn_to_odds(away_name, sport)

    if not home_odds or not away_odds:
        return None

    for event in odds_events:
        event_home = event.get("home_team", "")
        event_away = event.get("away_team", "")
        if event_home == home_odds and event_away == away_odds:
            return event
        if event_home == away_odds and event_away == home_odds:
            return event

    logger.warning(f"No Odds API event for {away_name} @ {home_name}")
    return None
