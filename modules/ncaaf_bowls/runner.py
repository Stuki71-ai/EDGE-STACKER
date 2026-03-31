import logging
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from staking import american_to_prob, assign_grade
from . import schedule, odds, filters

logger = logging.getLogger("edge_stacker")


def run(today):
    """Run the NCAAF Bowl Underdogs module."""
    games = schedule.get_bowl_games(today)
    if not games:
        logger.info("No bowl games today")
        return []

    try:
        odds_events = odds.get_spreads()
    except Exception as e:
        logger.error(f"Failed to get NCAAF spreads: {e}")
        return []

    picks = []
    for game in games:
        # STEP 1: Is this a bowl game?
        if game.get("season_type") not in (3, "3"):
            continue

        spread_data = odds.find_game_spread(odds_events, game)
        passes, reason = filters.passes_filters(spread_data)
        if not passes:
            logger.debug(f"Filtered: {game.get('name', '?')} -- {reason}")
            continue

        # Get conference info
        fav_conf = None
        dog_conf = None
        try:
            home_id = game["home"]["id"]
            away_id = game["away"]["id"]
            home_conf = schedule.get_team_conference(home_id)
            away_conf = schedule.get_team_conference(away_id)

            underdog_name = spread_data["underdog"]
            if underdog_name == game["home"]["name"]:
                dog_conf = home_conf
                fav_conf = away_conf
            else:
                dog_conf = away_conf
                fav_conf = home_conf
        except Exception:
            pass

        spread = spread_data["spread"]
        model_prob = filters.bowl_underdog_model_prob(spread, fav_conf, dog_conf)
        implied = american_to_prob(spread_data["best_odds"])
        edge = model_prob - implied

        if edge <= 0:
            continue

        # Bet-by: 2 hours before kickoff
        bet_by_str = ""
        game_time_str = ""
        try:
            game_dt = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
            et = timezone(timedelta(hours=config.ET_OFFSET_HOURS)); cet = timezone(timedelta(hours=config.CET_OFFSET_HOURS))
            game_time_str = game_dt.astimezone(et).strftime(config.TIME_FMT)
            
            bet_by_cet = game_dt.astimezone(cet) - timedelta(hours=2)
            bet_by_str = bet_by_cet.strftime(config.BET_BY_FMT)
        except (ValueError, TypeError):
            pass

        matchup = f"{game['away']['name']} vs {game['home']['name']}"
        pick = Pick(
            module="ncaaf_bowls",
            matchup=matchup,
            pick_description=f"{spread_data['underdog']} +{spread}",
            best_odds_raw=spread_data["best_odds"],
            best_odds_book=spread_data["best_book"],
            consensus_odds_raw=spread_data["consensus_odds"],
            implied_prob=implied,
            model_prob=model_prob,
            edge_pct=edge,
            grade=assign_grade(edge),
            context={
                "spread": spread,
                "fav_conference": fav_conf,
                "dog_conference": dog_conf,
                "underdog": spread_data["underdog"],
            },
            bet_by=bet_by_str,
            game_time=game_time_str,
        )
        picks.append(pick)
        logger.info(f"PICK: {spread_data['underdog']} +{spread} | edge={edge:.1%}")

    return picks
