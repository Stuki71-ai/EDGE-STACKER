"""NHL Shots on Goal module — projects player SOG, finds edge vs market line."""

import logging
import requests
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from shared import espn_nhl
from staking import american_to_prob, assign_grade
import config
from . import projections, filters, odds

logger = logging.getLogger("edge_stacker")

_team_id_cache = {}


def run(today):
    """Run NHL SOG module.

    Args:
        today: date object

    Returns:
        list of Pick objects
    """
    # ESPN team defensive stats (shots against per game)
    try:
        sa_map = espn_nhl.get_team_defensive_stats()
        logger.info(f"NHL SOG: ESPN team SA loaded for {len(sa_map)} teams")
    except Exception as e:
        logger.warning(f"NHL team SA unavailable: {e}")
        sa_map = {}

    # Injuries
    try:
        injury_map = espn_nhl.get_injuries()
        logger.info(f"NHL SOG: injuries loaded for {len(injury_map)} teams")
    except Exception as e:
        logger.warning(f"NHL injuries unavailable: {e}")
        injury_map = {}

    # Load ALL 32 NHL teams from ESPN's static teams endpoint. The previous
    # scoreboard-only approach silently skipped teams whose game wasn't in
    # ESPN's scoreboard at fetch time but IS on the Odds API slate today,
    # losing picks for those teams.
    _build_team_id_cache()

    # Get NHL events from Odds API FIRST so we know exactly which teams are
    # playing today, then fetch rosters only for those teams.
    try:
        events = odds.get_nhl_events()
    except Exception as e:
        logger.error(f"Failed to get NHL events: {e}")
        return []

    if not events:
        logger.info("No NHL games today")
        return []

    logger.info(f"NHL SOG: Processing {len(events)} games")

    # Pre-fetch player rosters for the teams actually playing today.
    # Resolve each Odds API team name to ESPN team_id via the (now-complete)
    # _team_id_cache. Skip with warning if a team can't be resolved.
    playing_team_ids = set()
    unresolved = []
    for event in events:
        for name in (event.get("home_team", ""), event.get("away_team", "")):
            tid = _resolve_team_id(name)
            if tid:
                playing_team_ids.add(tid)
            elif name:
                unresolved.append(name)
    if unresolved:
        logger.warning(f"NHL: could not resolve team IDs: {unresolved}")
    if playing_team_ids:
        espn_nhl.build_player_id_cache(list(playing_team_ids))

    picks = []
    gamelog_cache = {}

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        try:
            event_odds = odds.get_player_sog(event_id)
        except Exception as e:
            logger.warning(f"Failed to get SOG props for event {event_id}: {e}")
            continue

        player_props = odds.extract_props(event_odds)
        if not player_props:
            continue

        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        home_team_id = _resolve_team_id(home_team)
        away_team_id = _resolve_team_id(away_team)

        # Process each player
        for player_name, sd in player_props.items():
            line = sd.get("line")
            over_odds = sd.get("best_over_odds") or sd.get("over_odds")
            under_odds = sd.get("best_under_odds") or sd.get("under_odds")

            if line is None or over_odds is None or under_odds is None:
                continue

            # Skip injured/scratched players
            if _is_player_injured(player_name, injury_map):
                continue

            # Must be in roster cache
            espn_id = espn_nhl.find_espn_player_id(player_name)
            if not espn_id:
                continue

            # Skip goalies (their SOG isn't a thing — they SAVE shots, different market)
            position = espn_nhl.get_player_position(player_name)
            if position == "G":
                continue

            # Vig pre-filter (saves API calls)
            from staking import calculate_vig
            if calculate_vig(over_odds, under_odds) > filters.MAX_VIG:
                continue

            # Fetch FULL-SEASON gamelog (cache per-player). EWMA weights recent more.
            if player_name not in gamelog_cache:
                gamelog_cache[player_name] = espn_nhl.get_player_gamelog(espn_id, last_n=None)
            player_games = gamelog_cache[player_name]

            if not player_games:
                continue

            # Recency check (must have played within 14 days)
            if not _played_recently(player_games, max_days=14):
                logger.debug(f"NHL SOG skip {player_name}: not played recently")
                continue

            # Determine player team & opponent
            player_team_id = _get_player_team_id_from_games(player_games)
            if player_team_id == home_team_id:
                opp_sa = sa_map.get(away_team_id, projections.LEAGUE_AVG_SHOTS_AGAINST)
            elif player_team_id == away_team_id:
                opp_sa = sa_map.get(home_team_id, projections.LEAGUE_AVG_SHOTS_AGAINST)
            else:
                opp_sa = projections.LEAGUE_AVG_SHOTS_AGAINST

            proj_result = projections.project_player_sog(player_games, opp_sa)
            if not proj_result:
                continue
            projection = proj_result["projection"]
            actual_std = proj_result["std"]  # EWMA std over full season
            l10_avg = round(sum(float(g["S"]) for g in player_games[:10]) / min(10, len(player_games)), 1)

            # LINE SANITY CHECK: skip if projection diverges >50% from line.
            if line > 0 and abs(projection - line) / line > 0.5:
                logger.debug(f"NHL line sanity skip {player_name}: proj={projection} line={line}")
                continue

            # BACK-TO-BACK FATIGUE: if player played yesterday, -5% projection
            if _played_yesterday(player_games):
                projection *= 0.95

            # Edge
            direction, edge, model_prob, odds_to_bet = filters.sog_edge(
                projection, line, over_odds, under_odds, actual_std=actual_std
            )
            if direction is None:
                continue

            # MARKET ANCHOR: always blend 70% model + 30% market consensus.
            fair_over = sd.get("fair_over_prob")
            fair_under = sd.get("fair_under_prob")
            fair_market = fair_over if direction == "OVER" else fair_under
            if fair_market is not None:
                model_prob = 0.7 * model_prob + 0.3 * fair_market
                edge = min(model_prob - american_to_prob(odds_to_bet), filters.MAX_EDGE)
                if edge < filters.MIN_EDGE:
                    logger.debug(f"NHL market anchor: {player_name} edge below threshold")
                    continue

            # Filter pipeline
            passes, reason = filters.passes_filters(player_games, position, sd, edge)
            if not passes:
                logger.debug(f"NHL SOG filtered {player_name}: {reason}")
                continue

            implied = american_to_prob(odds_to_bet)
            best_book = (sd.get("best_over_book") if direction == "OVER" else sd.get("best_under_book")) or "Unknown"
            consensus = (sd.get("over_odds") if direction == "OVER" else sd.get("under_odds")) or odds_to_bet

            # Game time
            game_time_str = ""
            try:
                commence = event.get("commence_time", "")
                if commence:
                    game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                    et = timezone(timedelta(hours=config.ET_OFFSET_HOURS))
                    game_time_str = game_dt.astimezone(et).strftime(config.TIME_FMT)
                    game_date_str = game_dt.astimezone(et).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                game_date_str = ""

            pick = Pick(
                module="nhl_sog",
                matchup=f"{away_team} @ {home_team}",
                pick_description=f"{player_name} {direction} {line} SOG",
                best_odds_raw=odds_to_bet,
                best_odds_book=best_book,
                consensus_odds_raw=consensus,
                implied_prob=implied,
                model_prob=model_prob,
                edge_pct=edge,
                grade=assign_grade(edge),
                context={
                    "player": player_name,
                    "stat": "S",  # Shots
                    "projection": projection,
                    "line": line,
                    "l10_avg": l10_avg,
                    "position": position,
                    "avg_TOI_min": proj_result["avg_TOI_min"],
                    "sog_per_60": proj_result["sog_per_60"],
                    "opp_sa": round(opp_sa, 1),
                    "game_date": game_date_str,
                },
                game_time=game_time_str,
            )
            picks.append(pick)
            logger.info(f"NHL PICK: {player_name} {direction} {line} SOG | edge={edge:.1%}")

    return picks


def _build_team_id_cache():
    """Build a complete map of NHL team_displayName -> ESPN team_id.

    Uses ESPN's static /teams endpoint (returns all 32 NHL teams) instead of
    /scoreboard (only returns games ESPN happens to know about RIGHT NOW,
    which is often a subset of today's actual slate, especially during
    playoffs when ESPN can lag).

    Falls back to scoreboard if /teams fails.
    """
    global _team_id_cache
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        for sport in data.get("sports", []):
            for league in sport.get("leagues", []):
                for entry in league.get("teams", []):
                    team = entry.get("team", {})
                    name = team.get("displayName", "")
                    tid = str(team.get("id", ""))
                    if name and tid:
                        _team_id_cache[name] = tid
        logger.info(f"NHL team cache: {len(_team_id_cache)} teams loaded from ESPN /teams")
    except Exception as e:
        logger.warning(f"NHL /teams failed, falling back to scoreboard: {e}")
        try:
            resp = requests.get(
                "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
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
        except Exception as e2:
            logger.error(f"NHL team cache also failed via scoreboard: {e2}")


def _resolve_team_id(odds_api_team_name):
    """Map Odds API team name to ESPN team ID via cache.

    Odds API uses accented names ("Montréal Canadiens") while ESPN's /teams
    endpoint stores plain ASCII ("Montreal Canadiens"). Bare bytes-encode
    drops the accented char (Montréal -> Montral) instead of mapping it to
    its base letter (Montréal -> Montreal). Use unicodedata.normalize NFKD
    to decompose accented chars into base + combining mark, then strip
    combining marks to get the true base spelling.
    """
    import unicodedata

    def _normalize(s):
        return "".join(
            c for c in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(c)
        ).lower().strip()

    if odds_api_team_name in _team_id_cache:
        return _team_id_cache[odds_api_team_name]
    norm_name = _normalize(odds_api_team_name)
    for cached, tid in _team_id_cache.items():
        if _normalize(cached) == norm_name:
            return tid
    return ""


def _get_player_team_id_from_games(player_games):
    if player_games:
        return str(player_games[0].get("TEAM_ID", ""))
    return ""


def _is_player_injured(player_name, injury_map):
    name_lower = player_name.lower().strip()
    name_normalized = name_lower.replace(" jr.", "").replace(" sr.", "").replace(" ii", "").replace(" iii", "").strip()
    for team_injuries in injury_map.values():
        for inj in team_injuries:
            inj_name = inj.get("player_name", "").lower().strip()
            inj_normalized = inj_name.replace(" jr.", "").replace(" sr.", "").replace(" ii", "").replace(" iii", "").strip()
            if inj_name == name_lower or inj_normalized == name_normalized:
                return True
    return False


def _played_yesterday(player_games):
    """Back-to-back: did player play yesterday with 15+ min TOI?"""
    if not player_games:
        return False
    last = player_games[0].get("GAME_DATE", "")
    if not last:
        return False
    try:
        last_game = datetime.fromisoformat(last.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - last_game).days
        # 15+ min TOI = 900 seconds
        return days == 1 and float(player_games[0].get("TOI_SEC", 0)) >= 900
    except (ValueError, TypeError):
        return False


def _played_recently(player_games, max_days=14):
    if not player_games:
        return False
    last_date_str = player_games[0].get("GAME_DATE", "")
    if not last_date_str:
        return True
    try:
        last_game = datetime.fromisoformat(last_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - last_game).days <= max_days
    except (ValueError, TypeError):
        return True
