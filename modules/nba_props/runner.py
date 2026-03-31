import logging
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from shared import espn_nba
from staking import american_to_prob, assign_grade
import config
from . import projections, odds, filters

logger = logging.getLogger("edge_stacker")


def run(today):
    """Run the NBA Player Props module.

    Args:
        today: date object

    Returns:
        List of Pick objects
    """
    season = _get_season(today)

    # Get team defensive stats from ESPN (works from VPS, unlike nba_api)
    try:
        drtg_map = espn_nba.get_team_defensive_stats()
        logger.info(f"ESPN DRTG loaded for {len(drtg_map)} teams")
    except Exception as e:
        logger.warning(f"DRTG unavailable, using league average: {e}")
        drtg_map = {}

    # Get injury report from ESPN
    try:
        injury_map = espn_nba.get_injuries()
        logger.info(f"ESPN injuries loaded for {len(injury_map)} teams")
    except Exception as e:
        logger.warning(f"Injury report unavailable: {e}")
        injury_map = {}

    # Build ESPN team ID cache for team resolution
    _build_team_id_cache(None)

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

                # Get player game log from ESPN
                player_games = _get_player_games_espn(player_name)

                if player_games:
                    # Full projection with game log data
                    player_team_id = _get_player_team_id_from_games(player_games)
                    if player_team_id == home_team_id:
                        opp_drtg = home_opp_drtg
                        teammate_out = home_teammate_out
                        teammate_name = home_out_name
                    elif player_team_id == away_team_id:
                        opp_drtg = away_opp_drtg
                        teammate_out = away_teammate_out
                        teammate_name = away_out_name
                    else:
                        opp_drtg = config.LEAGUE_AVG_DRTG
                        teammate_out = False
                        teammate_name = None

                    proj_result = projections.project_player_stat(
                        player_games, stat, opp_drtg, teammate_out
                    )
                    projection = proj_result["projection"]
                    minutes_stable = proj_result["minutes_stable"]
                    l10_avg = round(sum(float(g[stat]) for g in player_games) / len(player_games), 1)
                else:
                    # Fallback: use odds-implied projection from line
                    # If best odds favor OVER, the line is likely below true value
                    projection = _odds_implied_projection(line, over_odds, under_odds)
                    minutes_stable = True  # Assume stable when we lack data
                    l10_avg = line  # Best guess
                    opp_drtg = config.LEAGUE_AVG_DRTG
                    teammate_out = False
                    teammate_name = None

                # Calculate edge
                direction, edge, model_prob, odds_to_bet = filters.prop_edge(
                    projection, line, stat, over_odds, under_odds
                )

                if direction is None:
                    continue

                # Run filters (use dummy 10-game list if no game log)
                filter_games = player_games if player_games else [{"MIN": 30, stat: line}] * 10
                passes, reason = filters.passes_filters(
                    filter_games, stat_data, edge, minutes_stable
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

                game_matchup = f"{away_team} @ {home_team}"
                pick = Pick(
                    module="nba_props",
                    matchup=game_matchup,
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
                        "l10_avg": l10_avg,
                        "opp_drtg": opp_drtg,
                        "teammate_out": teammate_name,
                        "minutes_stable": minutes_stable,
                        "vig": vig,
                        "data_source": "game_log" if player_games else "odds_implied",
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


def _odds_implied_projection(line, over_odds, under_odds):
    """Derive a projection from the line and odds skew."""
    from staking import american_to_prob
    over_prob = american_to_prob(over_odds)
    under_prob = american_to_prob(under_odds)

    total = over_prob + under_prob
    fair_over = over_prob / total

    skew = fair_over - 0.5
    std = line * config.STAT_STD_PCT.get("PTS", 0.22)
    projection = line + skew * std * 2
    return round(projection, 1)


def _get_player_games_espn(player_name):
    """Get player game log via ESPN API."""
    espn_id = espn_nba.find_espn_player_id(player_name)
    if not espn_id:
        return []
    return espn_nba.get_player_gamelog(espn_id, last_n=10)


def _resolve_team_id(odds_api_team_name):
    """Resolve Odds API team name to ESPN team ID via scoreboard matching."""
    # ESPN scoreboard already loaded in events — use a cached lookup
    return _team_id_cache.get(odds_api_team_name, "")


_team_id_cache = {}


def _build_team_id_cache(events):
    """Build team name -> ESPN team ID cache from Odds API events + ESPN scoreboard."""
    global _team_id_cache
    try:
        import requests
        resp = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        for event in data.get("events", []):
            comp = event.get("competitions", [{}])[0]
            for c in comp.get("competitors", []):
                team = c.get("team", {})
                name = team.get("displayName", "")
                tid = str(team.get("id", ""))
                if name and tid:
                    _team_id_cache[name] = tid
    except Exception as e:
        logger.debug(f"Could not build team ID cache: {e}")


def _get_player_team_id_from_games(player_games):
    """Get a player's team ID from their most recent game log entry."""
    if player_games:
        return str(player_games[0].get("TEAM_ID", ""))
    return ""


def _check_teammate_out(team_id, injury_map, season):
    """Check if a top-2 minutes player on a team is OUT."""
    if not team_id:
        return False, None

    team_injuries = injury_map.get(team_id, [])
    if not team_injuries:
        return False, None

    try:
        roster = espn_nba.get_team_roster(team_id)
        if not roster:
            return False, None
        top2_ids = {r["id"] for r in roster[:2]}
        for inj in team_injuries:
            if inj.get("player_id") in top2_ids:
                return True, inj.get("player_name")
    except Exception as e:
        logger.debug(f"Could not check teammate injuries for team {team_id}: {e}")
    return False, None
