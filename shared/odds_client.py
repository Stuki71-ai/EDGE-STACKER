import logging
import requests
import config

logger = logging.getLogger("edge_stacker")

BASE_URL = "https://api.the-odds-api.com/v4"

_remaining_credits = None


def _get(endpoint, params=None):
    """Make a GET request to the Odds API with credit tracking."""
    global _remaining_credits

    if not config.ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY environment variable not set")

    if params is None:
        params = {}
    params["apiKey"] = config.ODDS_API_KEY

    url = f"{BASE_URL}/{endpoint}"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    # Track remaining credits
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        _remaining_credits = int(remaining)
        logger.info(f"Odds API credits remaining: {_remaining_credits}")
        if _remaining_credits < config.ODDS_API_CREDIT_WARNING:
            logger.warning(f"Odds API credits LOW: {_remaining_credits}")

    return resp.json()


def get_remaining_credits():
    return _remaining_credits


def verify_sport_keys():
    """Call /v4/sports/ to verify sport keys. Returns dict of key -> title."""
    data = _get("sports")
    verified = {}
    for sport in data:
        verified[sport["key"]] = sport["title"]
    return verified


def get_odds(sport_key, markets="spreads", regions="us", odds_format="american"):
    """Get odds for a sport. Returns list of events with odds."""
    params = {
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    }
    return _get(f"sports/{sport_key}/odds/", params)


def get_event_odds(sport_key, event_id, markets, regions="us", odds_format="american"):
    """Get odds for a specific event (used for player props)."""
    params = {
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    }
    return _get(f"sports/{sport_key}/events/{event_id}/odds", params)


def get_events(sport_key):
    """Get upcoming events for a sport."""
    return _get(f"sports/{sport_key}/events")
