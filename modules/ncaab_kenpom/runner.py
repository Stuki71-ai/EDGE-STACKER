import logging
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from staking import american_to_prob, assign_grade
import config
from . import kenpom, schedule, odds, filters

logger = logging.getLogger("edge_stacker")


def run(today):
    """Run the NCAAB KenPom Disagreement module."""
    # Load KenPom data
    kenpom_data, csv_age = kenpom.load_kenpom_data()
    if kenpom_data is None:
        logger.error("KenPom data unavailable")
        return []

    # Hard disable if CSV too old
    if csv_age is not None and csv_age > config.KENPOM_DISABLE_DAYS:
        logger.error(f"KenPom CSV is {csv_age} days old — module DISABLED")
        return []

    # Get games
    games = schedule.get_games(today)
    if not games:
        logger.info("No NCAAB games today")
        return []

    # Get odds
    try:
        odds_events = odds.get_spreads()
    except Exception as e:
        logger.error(f"Failed to get NCAAB spreads: {e}")
        return []

    picks = []
    for game in games:
        home_name = game["home"]["name"]
        away_name = game["away"]["name"]

        # STEP 1: Both teams in KenPom data?
        if home_name not in kenpom_data or away_name not in kenpom_data:
            continue

        # STEP 2: Game has market spread?
        spread_data = odds.find_game_spread(odds_events, game)
        if not spread_data:
            continue

        # Determine conference and neutral
        is_neutral = game.get("neutral_site", False)
        is_conf = schedule.is_conference_game(game, kenpom_data)

        # Calculate KenPom predicted spread
        kp_margin = kenpom.kenpom_predicted_spread(
            home_name, away_name, kenpom_data,
            neutral=is_neutral, is_conference=is_conf
        )
        if kp_margin is None:
            continue

        # Match ESPN team names to Odds API names in spread data
        from shared.name_normalizer import espn_to_odds
        h_odds = espn_to_odds(home_name, "ncaab")
        a_odds = espn_to_odds(away_name, "ncaab")

        home_odds_name = None
        away_odds_name = None
        for team_name in spread_data:
            if team_name == h_odds:
                home_odds_name = team_name
            elif team_name == a_odds:
                away_odds_name = team_name

        if not home_odds_name or home_odds_name not in spread_data:
            continue

        market_spread = spread_data[home_odds_name]["spread"]  # negative = home favored

        # Determine side
        side, divergence_abs = filters.determine_side(kp_margin, market_spread)
        if side is None:
            continue

        # Get the best odds for the KenPom side
        if side == "home" and home_odds_name in spread_data:
            side_data = spread_data[home_odds_name]
            side_team = home_name
        elif side == "away" and away_odds_name and away_odds_name in spread_data:
            side_data = spread_data[away_odds_name]
            side_team = away_name
        else:
            continue

        best_odds = side_data["best_odds"]
        best_book = side_data["best_book"]
        consensus = side_data["consensus"]

        # Run filters
        passes, reason, warning = filters.passes_filters(divergence_abs, best_odds, csv_age)
        if not passes:
            logger.debug(f"Filtered: {home_name} vs {away_name} -- {reason}")
            continue

        if warning:
            logger.warning(warning)

        model_prob = filters.kenpom_model_prob(divergence_abs, is_conf)
        implied = american_to_prob(best_odds)
        edge = model_prob - implied

        if edge <= 0:
            continue

        # Bet-by: 1 hour before game time
        bet_by_str = ""
        game_time_str = ""
        try:
            game_dt = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
            et = timezone(timedelta(hours=config.ET_OFFSET_HOURS)); cet = timezone(timedelta(hours=config.CET_OFFSET_HOURS))
            game_time_str = game_dt.astimezone(et).strftime(config.TIME_FMT)
            
            bet_by_cet = game_dt.astimezone(cet) - timedelta(hours=1)
            bet_by_str = bet_by_cet.strftime(config.BET_BY_FMT)
        except (ValueError, TypeError):
            pass

        side_spread = side_data["spread"]
        spread_str = f"+{side_spread}" if side_spread > 0 else str(side_spread)

        pick = Pick(
            module="ncaab_kenpom",
            matchup=f"{away_name} vs {home_name}",
            pick_description=f"{side_team} {spread_str}",
            best_odds_raw=best_odds,
            best_odds_book=best_book,
            consensus_odds_raw=consensus,
            implied_prob=implied,
            model_prob=model_prob,
            edge_pct=edge,
            grade=assign_grade(edge),
            context={
                "kenpom_margin": kp_margin,
                "market_spread": market_spread,
                "divergence": divergence_abs,
                "side": side,
                "is_conference": is_conf,
                "neutral_site": is_neutral,
                "csv_age_days": csv_age,
                "kenpom_warning": warning,
            },
            bet_by=bet_by_str,
            game_time=game_time_str,
        )
        picks.append(pick)
        logger.info(f"PICK: {side_team} {spread_str} | div={divergence_abs:.1f} edge={edge:.1%}")

    return picks
