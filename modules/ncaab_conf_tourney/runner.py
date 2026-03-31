import logging
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from staking import american_to_prob, assign_grade
import config
from . import schedule, odds, filters

logger = logging.getLogger("edge_stacker")


def run(today):
    """Run the NCAAB Conference Tournament module."""
    rules = config.load_static_json("conf_tourney_rules.json")
    if not rules:
        logger.error("conf_tourney_rules.json not found or empty")
        return []

    # Get tournament games
    games = schedule.get_conf_tourney_games(today)
    if not games:
        logger.info("No conference tournament games today")
        return []

    # Get odds
    try:
        odds_events = odds.get_spreads()
    except Exception as e:
        logger.error(f"Failed to get NCAAB spreads: {e}")
        return []

    picks = []
    for game in games:
        spread_data = odds.find_underdog_spread(odds_events, game)

        passes, reason, rule = filters.passes_filters(game, spread_data, rules)
        if not passes:
            logger.debug(f"Filtered: {game.get('name', '?')} -- {reason}")
            continue

        # Use dog_ats_pct directly as model_prob
        model_prob = rule["dog_ats_pct"]
        implied = american_to_prob(spread_data["best_odds"])
        edge = model_prob - implied

        if edge <= 0:
            continue

        # Bet-by: 30 minutes before tip
        bet_by_str = ""
        game_time_str = ""
        try:
            game_dt = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
            et_offset = timezone(timedelta(hours=-5))
            game_et = game_dt.astimezone(et_offset)
            game_time_str = game_et.strftime(config.TIME_FMT)
            bet_by_et = game_et - timedelta(minutes=30)
            bet_by_str = bet_by_et.strftime(config.TIME_FMT)
        except (ValueError, TypeError):
            pass

        conference = game.get("_conference", "Unknown")
        round_key = game.get("_round_key", "unknown")

        matchup = f"{game['away']['name']} vs {game['home']['name']}"
        pick = Pick(
            module="ncaab_conf_tourney",
            matchup=matchup,
            pick_description=f"{spread_data['underdog']} +{spread_data['spread']}",
            best_odds_raw=spread_data["best_odds"],
            best_odds_book=spread_data["best_book"],
            consensus_odds_raw=spread_data["consensus_odds"],
            implied_prob=implied,
            model_prob=model_prob,
            edge_pct=edge,
            grade=assign_grade(edge),
            context={
                "conference": conference,
                "round": round_key,
                "historical_ats": rule["dog_ats_pct"],
                "sample": rule.get("sample", 0),
                "years": rule.get("years", ""),
            },
            bet_by=bet_by_str,
            game_time=game_time_str,
        )
        picks.append(pick)
        logger.info(f"PICK: {spread_data['underdog']} +{spread_data['spread']} | {conference} {round_key} | edge={edge:.1%}")

    return picks
