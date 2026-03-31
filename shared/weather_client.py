import logging
import requests
import config

logger = logging.getLogger("edge_stacker")

FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


def get_forecast(lat, lon):
    """Get 5-day/3-hour forecast for coordinates. Returns list of forecast blocks."""
    if not config.OPENWEATHER_API_KEY:
        raise RuntimeError("OPENWEATHER_API_KEY environment variable not set")

    params = {
        "lat": lat,
        "lon": lon,
        "appid": config.OPENWEATHER_API_KEY,
        "units": "imperial",
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("list", [])


def get_forecast_at_gametime(forecast_list, game_time_utc):
    """Find the 3-hour block closest to game time.

    Args:
        forecast_list: list of forecast blocks from get_forecast()
        game_time_utc: Unix timestamp of game time
    """
    if not forecast_list:
        return None

    closest = min(forecast_list, key=lambda f: abs(f["dt"] - game_time_utc))
    return {
        "wind_mph": closest["wind"]["speed"],
        "wind_deg": closest["wind"].get("deg", 0),
        "temp_f": closest["main"]["temp"],
        "precipitation": closest["weather"][0]["main"],  # Rain, Snow, Clear, etc.
    }
