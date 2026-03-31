import logging
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from staking import american_to_prob, assign_grade
import config
from . import schedule, weather, odds, filters

logger = logging.getLogger("edge_stacker")


def run(today):
    """Run the NCAAF Weather Unders module.

    Args:
        today: date object

    Returns:
        List of Pick objects
    """
    stadiums = config.load_static_json("ncaaf_stadiums.json")
    if not stadiums:
        logger.error("ncaaf_stadiums.json not found or empty")
        return []

    # Get games
    games = schedule.get_games(today)
    if not games:
        logger.info("No NCAAF games today")
        return []

    # Get odds
    try:
        odds_events = odds.get_totals()
    except Exception as e:
        logger.error(f"Failed to get NCAAF totals: {e}")
        return []

    picks = []
    for game in games:
        # Get weather
        wx = weather.get_weather_for_game(game, stadiums)

        # Get total and odds
        total, best_under_odds, best_book, consensus, _ = odds.find_game_total(odds_events, game)

        # Run filters
        passes, reason = filters.passes_filters(game, wx, best_under_odds, total, stadiums)
        if not passes:
            logger.debug(f"Filtered: {game.get('name', '?')} -- {reason}")
            continue

        # Calculate edge
        model_prob = filters.weather_under_model_prob(
            wx["wind_mph"], wx["temp_f"], wx["precipitation"]
        )
        implied = american_to_prob(best_under_odds)
        edge = model_prob - implied

        if edge <= 0:
            logger.debug(f"No edge: {game.get('name', '?')} model={model_prob:.3f} implied={implied:.3f}")
            continue

        # Calculate bet_by (2 hours before game time)
        bet_by_str = ""
        game_time_str = ""
        try:
            game_dt = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
            # Convert to ET (UTC-5 / UTC-4 depending on DST, approximate with UTC-5)
            et = timezone(timedelta(hours=config.ET_OFFSET_HOURS)); cet = timezone(timedelta(hours=config.CET_OFFSET_HOURS))
            game_time_str = game_dt.astimezone(et).strftime(config.TIME_FMT)
            
            bet_by_cet = game_dt.astimezone(cet) - timedelta(hours=2)
            bet_by_str = bet_by_cet.strftime(config.BET_BY_FMT)
        except (ValueError, TypeError):
            pass

        conf_score = filters.confidence_score(
            wx["wind_mph"], wx["temp_f"], wx["precipitation"],
            best_under_odds, consensus or best_under_odds
        )

        matchup = f"{game['away']['name']} @ {game['home']['name']}"
        pick = Pick(
            module="ncaaf_weather",
            matchup=matchup,
            pick_description=f"UNDER {total}",
            best_odds_raw=best_under_odds,
            best_odds_book=best_book or "Unknown",
            consensus_odds_raw=consensus or best_under_odds,
            implied_prob=implied,
            model_prob=model_prob,
            edge_pct=edge,
            grade=assign_grade(edge),
            context={
                "wind_mph": wx["wind_mph"],
                "temp_f": wx["temp_f"],
                "precipitation": wx["precipitation"],
                "venue": game.get("venue_name", ""),
                "total": total,
                "confidence": conf_score,
            },
            bet_by=bet_by_str,
            game_time=game_time_str,
        )
        picks.append(pick)
        logger.info(f"PICK: {matchup} UNDER {total} | edge={edge:.1%} | wind={wx['wind_mph']}mph")

    return picks
