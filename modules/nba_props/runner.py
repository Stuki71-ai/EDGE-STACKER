import logging
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from staking import american_to_prob, assign_grade
import config
from . import projections, injuries, odds, matchups, filters

logger = logging.getLogger("edge_stacker")


def run(today):
    """Run the NBA Player Props module.

    Args:
        today: date object

    Returns:
        List of Pick objects
    """
    season = _get_season(today)

    # Get team defensive ratings
    try:
        drtg_map = matchups.get_team_defensive_ratings(season)
    except Exception as e:
        logger.error(f"Failed to get defensive ratings: {e}")
        return []

    # Get injury report
    injury_map = injuries.get_injuries()

    # Get NBA events from Odds API
    try:
        events = odds.get_nba_events()
    except Exception as e:
        logger.error(f"Failed to get NBA events: {e}")
        return []

    if not events:
        logger.info("No NBA games today")
        return []

    # Pre-filter to top 5-8 games
    games_to_process = _prioritize_games(events, injury_map, drtg_map)
    logger.info(f"Processing {len(games_to_process)} of {len(events)} NBA games")

    picks = []
    for event in games_to_process:
        event_id = event.get("id")
        if not event_id:
            continue

        # Get player props for this event
        try:
            event_odds = odds.get_player_props(event_id)
        except Exception as e:
            logger.warning(f"Failed to get props for event {event_id}: {e}")
            continue

        player_props = odds.extract_props(event_odds)

        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")

        # Resolve team IDs for DRTG lookup and injury check
        home_team_id = _resolve_team_id(home_team)
        away_team_id = _resolve_team_id(away_team)

        # Get opponent DRTGs for this matchup
        home_opp_drtg = drtg_map.get(away_team_id, config.LEAGUE_AVG_DRTG)  # home faces away defense
        away_opp_drtg = drtg_map.get(home_team_id, config.LEAGUE_AVG_DRTG)  # away faces home defense

        # Check for teammate-out on each team
        home_teammate_out, home_out_name = _check_teammate_out(
            home_team_id, injury_map, season)
        away_teammate_out, away_out_name = _check_teammate_out(
            away_team_id, injury_map, season)

        # Process each player's props
        for player_name, stat_props in player_props.items():
            for stat, stat_data in stat_props.items():
                line = stat_data.get("line")
                over_odds = stat_data.get("best_over_odds") or stat_data.get("over_odds")
                under_odds = stat_data.get("best_under_odds") or stat_data.get("under_odds")

                if line is None or over_odds is None or under_odds is None:
                    continue

                player_games = _get_player_games_safe(player_name, season)
                if not player_games:
                    continue

                # Determine which team the player is on and set opponent DRTG
                player_team_id = _get_player_team_id(player_name)
                if player_team_id == home_team_id:
                    opp_drtg = home_opp_drtg  # home player faces away defense
                    teammate_out = home_teammate_out
                    teammate_name = home_out_name
                elif player_team_id == away_team_id:
                    opp_drtg = away_opp_drtg  # away player faces home defense
                    teammate_out = away_teammate_out
                    teammate_name = away_out_name
                else:
                    # Could not determine team — fall back to league average
                    opp_drtg = config.LEAGUE_AVG_DRTG
                    teammate_out = False
                    teammate_name = None

                proj_result = projections.project_player_stat(
                    player_games, stat, opp_drtg, teammate_out
                )

                projection = proj_result["projection"]

                # Calculate edge
                direction, edge, model_prob, odds_to_bet = filters.prop_edge(
                    projection, line, stat, over_odds, under_odds
                )

                if direction is None:
                    continue

                # Run filters
                passes, reason = filters.passes_filters(
                    player_games, stat_data, edge, proj_result["minutes_stable"]
                )
                if not passes:
                    logger.debug(f"Filtered: {player_name} {stat} -- {reason}")
                    continue

                implied = american_to_prob(odds_to_bet)
                best_book = (stat_data.get("best_over_book") if direction == "OVER"
                             else stat_data.get("best_under_book")) or "Unknown"

                # Consensus odds
                consensus = (stat_data.get("over_odds") if direction == "OVER"
                             else stat_data.get("under_odds")) or odds_to_bet

                # Calculate bet_by (1 hour before game time)
                bet_by_str = ""
                game_time_str = ""
                try:
                    commence = event.get("commence_time", "")
                    if commence:
                        game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                        et_offset = timezone(timedelta(hours=-5))
                        game_et = game_dt.astimezone(et_offset)
                        game_time_str = game_et.strftime(config.TIME_FMT)
                        bet_by_et = game_et - timedelta(hours=1)
                        bet_by_str = bet_by_et.strftime(config.TIME_FMT)
                except (ValueError, TypeError):
                    pass

                vig = None
                if over_odds and under_odds:
                    from staking import calculate_vig
                    vig = round(calculate_vig(over_odds, under_odds), 3)

                pick = Pick(
                    module="nba_props",
                    matchup=f"{player_name} {stat}",
                    pick_description=f"{player_name} {direction} {line} {stat}",
                    best_odds_raw=odds_to_bet,
                    best_odds_book=best_book,
                    consensus_odds_raw=consensus,
                    implied_prob=implied,
                    model_prob=model_prob,
                    edge_pct=edge,
                    grade=assign_grade(edge),
                    context={
                        "player": player_name,
                        "stat": stat,
                        "projection": projection,
                        "line": line,
                        "l10_avg": round(sum(float(g[stat]) for g in player_games) / len(player_games), 1),
                        "opp_drtg": opp_drtg,
                        "teammate_out": teammate_name,
                        "minutes_stable": proj_result["minutes_stable"],
                        "vig": vig,
                    },
                    bet_by=bet_by_str,
                    game_time=game_time_str,
                )
                picks.append(pick)
                logger.info(f"PICK: {player_name} {direction} {line} {stat} | edge={edge:.1%}")

    return picks


def _get_season(today):
    """Determine NBA season string from date."""
    year = today.year
    month = today.month
    if month >= 10:
        return f"{year}-{str(year + 1)[2:]}"
    else:
        return f"{year - 1}-{str(year)[2:]}"


def _prioritize_games(events, injury_map, drtg_map):
    """Pre-filter to top 5-8 games based on injury impact and matchup asymmetry."""
    scored = []
    for event in events:
        score = 0
        # Games with injuries get priority
        home_team_id = event.get("home_team_id", "")
        away_team_id = event.get("away_team_id", "")

        if str(home_team_id) in injury_map or str(away_team_id) in injury_map:
            score += 10

        # Extreme matchup asymmetry
        home_drtg = drtg_map.get(str(home_team_id), config.LEAGUE_AVG_DRTG)
        away_drtg = drtg_map.get(str(away_team_id), config.LEAGUE_AVG_DRTG)
        drtg_diff = abs(home_drtg - away_drtg)
        if drtg_diff > 5:
            score += 5

        scored.append((score, event))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [event for _, event in scored[:config.PROP_MAX_GAMES_PER_RUN]]


def _get_player_games_safe(player_name, season):
    """Safely get player game log by name. Returns empty list on failure."""
    try:
        from nba_api.stats.static import players
        matches = players.find_players_by_full_name(player_name)
        if not matches:
            return []

        player_id = matches[0]["id"]
        return matchups.get_player_game_log(player_id, season)
    except Exception as e:
        logger.debug(f"Could not get games for {player_name}: {e}")
        return []


def _resolve_team_id(odds_api_team_name):
    """Resolve Odds API team name to nba_api team ID string."""
    try:
        from nba_api.stats.static import teams
        from shared.name_normalizer import odds_to_nba_api

        nba_name = odds_to_nba_api(odds_api_team_name) or odds_api_team_name
        all_teams = teams.get_teams()
        for t in all_teams:
            if t["full_name"] == nba_name:
                return str(t["id"])
        # Fuzzy fallback: check if city+nickname matches
        for t in all_teams:
            if nba_name in t["full_name"] or t["full_name"] in nba_name:
                return str(t["id"])
    except Exception as e:
        logger.debug(f"Could not resolve team ID for '{odds_api_team_name}': {e}")
    return ""


def _get_player_team_id(player_name):
    """Get a player's current team ID."""
    try:
        from nba_api.stats.static import players
        matches = players.find_players_by_full_name(player_name)
        if matches:
            # The static data includes team_id for active players
            return str(matches[0].get("team_id", ""))
    except Exception:
        pass
    return ""


def _check_teammate_out(team_id, injury_map, season):
    """Check if a top-2 minutes player on a team is OUT.

    Returns (is_out: bool, player_name: str or None)
    """
    if not team_id:
        return False, None

    team_injuries = injury_map.get(team_id, [])
    if not team_injuries:
        return False, None

    try:
        roster_minutes = matchups.get_team_roster_minutes(team_id, season)
        return injuries.is_top_minutes_player_out(team_injuries, roster_minutes)
    except Exception as e:
        logger.debug(f"Could not check teammate injuries for team {team_id}: {e}")
        return False, None
