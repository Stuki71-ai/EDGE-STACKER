import logging
from datetime import datetime, timezone
from shared.weather_client import get_forecast, get_forecast_at_gametime

logger = logging.getLogger("edge_stacker")


def get_weather_for_game(game, stadiums):
    """Get weather forecast at game time for a given game.

    Args:
        game: game dict with venue_name and date
        stadiums: dict from ncaaf_stadiums.json

    Returns:
        Weather dict or None
    """
    venue = game.get("venue_name", "")
    stadium = stadiums.get(venue)
    if not stadium:
        logger.warning(f"Stadium not in database: {venue}")
        return None

    if stadium.get("dome", False):
        return None

    lat = stadium["lat"]
    lon = stadium["lon"]

    # Parse game time to UTC timestamp
    game_time_str = game.get("date", "")
    if not game_time_str:
        logger.warning(f"No game time for {game.get('name', 'unknown')}")
        return None

    try:
        game_dt = datetime.fromisoformat(game_time_str.replace("Z", "+00:00"))
        game_time_utc = int(game_dt.timestamp())
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse game time '{game_time_str}': {e}")
        return None

    try:
        forecast_list = get_forecast(lat, lon)
        weather = get_forecast_at_gametime(forecast_list, game_time_utc)
        return weather
    except Exception as e:
        logger.error(f"Weather API error for {venue}: {e}")
        return None
