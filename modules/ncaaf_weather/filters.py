import logging
import config

logger = logging.getLogger("edge_stacker")


def weather_under_model_prob(wind_mph, temp_f, precipitation):
    """
    Returns model probability of UNDER hitting.
    Sources: Action Network 1000+ game database, Salaga & Howley 2024.
    """
    if wind_mph >= 20:
        base = 0.65
    elif wind_mph >= 15:
        base = 0.58
    elif wind_mph >= 13:
        base = 0.57
    else:
        base = 0.50

    if temp_f < 25:
        base += 0.05
    elif temp_f < 40:
        base += 0.03

    if precipitation in ("Rain", "Snow", "Drizzle", "Thunderstorm"):
        base += 0.03

    return min(base, 0.75)


def confidence_score(wind_mph, temp_f, precipitation, best_odds, consensus_odds):
    """Confidence scoring for context (not used in sizing)."""
    score = 0
    if wind_mph >= 15:
        score += 1
    if wind_mph >= 20:
        score += 1
    if temp_f < 40:
        score += 1
    if precipitation in ("Rain", "Snow", "Drizzle", "Thunderstorm"):
        score += 1
    # Best odds 5+ cents better than consensus
    from staking import american_to_prob
    if american_to_prob(best_odds) < american_to_prob(consensus_odds) - 0.05:
        score += 1
    return score


def passes_filters(game, weather, best_under_odds, total, stadiums, verbose=False):
    """
    Run the NCAAF Weather filter pipeline.
    Returns (passes: bool, reason: str or None)
    """
    venue = game.get("venue_name", "")

    # STEP 1: Is venue outdoor?
    stadium_info = stadiums.get(venue)
    if stadium_info is None:
        if game.get("venue_indoor", False):
            return False, "Indoor venue (ESPN flag)"
        logger.warning(f"Unknown stadium: {venue} -- skipping")
        return False, f"Unknown stadium: {venue}"
    if stadium_info.get("dome", False) or game.get("venue_indoor", False):
        return False, "Dome/indoor venue"

    if weather is None:
        return False, "No weather forecast available"

    wind_mph = weather.get("wind_mph", 0)
    temp_f = weather.get("temp_f", 70)

    # STEP 2: Wind >= 13 mph?
    if wind_mph < config.WEATHER_MIN_WIND_MPH:
        return False, f"Wind {wind_mph:.0f} mph < {config.WEATHER_MIN_WIND_MPH}"

    # STEP 3: Posted total >= 38?
    if total is None or total < config.WEATHER_MIN_TOTAL:
        return False, f"Total {total} < {config.WEATHER_MIN_TOTAL}"

    # STEP 4: Best UNDER odds >= -115?
    if best_under_odds is None or best_under_odds < config.WEATHER_MAX_UNDER_ODDS:
        return False, f"Under odds {best_under_odds} worse than {config.WEATHER_MAX_UNDER_ODDS}"

    return True, None
