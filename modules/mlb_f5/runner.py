"""MLB F5 totals module — projects F5 runs, finds edge vs market line."""

import logging
from datetime import datetime, timezone, timedelta

from shared.pick import Pick
from shared import mlb_data
from staking import american_to_prob, assign_grade
import config
from . import projections, filters, odds

logger = logging.getLogger("edge_stacker")

MAX_HOURS_AHEAD = 8


def run(today):
    """Run MLB F5 module. Returns list of Pick objects."""
    schedule = mlb_data.get_schedule(today.isoformat())
    if not schedule:
        logger.info("No MLB games today")
        return []

    sched_index = {(g["away_team"], g["home_team"]): g for g in schedule}

    try:
        events = odds.get_mlb_events()
    except Exception as e:
        logger.error(f"Failed to get MLB events: {e}")
        return []
    if not events:
        return []

    # Tipoff window: drop already-started + tomorrow's slate
    now_utc = datetime.now(timezone.utc)
    fresh = []
    dropped = []
    for ev in events:
        ct = ev.get("commence_time", "")
        if not ct:
            fresh.append(ev)
            continue
        try:
            game_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            hours = (game_dt - now_utc).total_seconds() / 3600.0
            if hours < (5 / 60.0):
                dropped.append(f"{ev.get('away_team','')} @ {ev.get('home_team','')} (started)")
                continue
            if hours > MAX_HOURS_AHEAD:
                dropped.append(f"{ev.get('away_team','')} @ {ev.get('home_team','')} ({hours:.1f}h)")
                continue
        except (ValueError, TypeError):
            pass
        fresh.append(ev)
    if dropped:
        logger.info(f"MLB: dropped {len(dropped)} outside [5min, {MAX_HOURS_AHEAD}h]: {dropped}")
    events = fresh
    if not events:
        return []
    logger.info(f"MLB F5: Processing {len(events)} games")

    picks = []
    for ev in events:
        sched = sched_index.get((ev.get("away_team", ""), ev.get("home_team", "")))
        if not sched:
            logger.debug(f"MLB: no schedule match for {ev.get('away_team','')}@{ev.get('home_team','')}")
            continue
        # Postponed/cancelled/suspended games must never produce a pick — the
        # Odds API can still list a market briefly after MLB pulls the game.
        status = sched.get("status", "")
        if any(bad in status for bad in ("Postponed", "Cancel", "Suspend")):
            logger.info(f"MLB: skipping {sched['away_team']}@{sched['home_team']} "
                        f"— game status '{status}'")
            continue
        if not sched["away_starter_id"] or not sched["home_starter_id"]:
            logger.debug(f"MLB: starters TBD for {sched['away_team']}@{sched['home_team']}")
            continue

        away_p = mlb_data.get_pitcher_stats(sched["away_starter_id"])
        home_p = mlb_data.get_pitcher_stats(sched["home_starter_id"])
        if not away_p or not home_p:
            logger.debug(f"MLB: insufficient starter sample for {sched['away_team']}@{sched['home_team']}")
            continue

        if not sched.get("away_team_id") or not sched.get("home_team_id"):
            continue

        away_woba = mlb_data.get_team_woba_vs_hand(sched["away_team_id"], home_p["hand"])
        home_woba = mlb_data.get_team_woba_vs_hand(sched["home_team_id"], away_p["hand"])
        pf = mlb_data.park_factor(sched["venue"])

        # Weather: temp-only multiplier from OpenWeather (already wired in config)
        wf = 1.0
        try:
            wf = _get_temp_weather_factor(sched["venue"], sched.get("gameDate", ""))
        except Exception:
            wf = 1.0

        proj_total = projections.project_total_f5(
            home_p["xFIP_30d"], away_woba,
            away_p["xFIP_30d"], home_woba,
            pf, wf,
        )

        try:
            event_odds = odds.get_f5_totals(ev["id"])
        except Exception as e:
            logger.warning(f"Failed F5 odds for {ev['id']}: {e}")
            continue
        by_line = odds.extract_totals(event_odds)
        if not by_line:
            continue

        # Pick consensus line: most common across books
        line_counts = {pt: len(d.get("by_book", {})) for pt, d in by_line.items()}
        consensus_line = max(line_counts, key=line_counts.get)
        line_data = by_line[consensus_line]

        over_odds = line_data.get("best_over_odds")
        under_odds = line_data.get("best_under_odds")
        if over_odds is None or under_odds is None:
            continue

        direction, edge, model_prob, odds_to_bet = projections.f5_edge(
            proj_total, consensus_line, over_odds, under_odds)
        if direction is None:
            continue
        edge = min(edge, filters.MAX_EDGE)

        # Market anchor 60/40
        fair = (line_data.get("fair_over_prob") if direction == "OVER"
                else line_data.get("fair_under_prob"))
        if fair is not None:
            model_prob = 0.6 * model_prob + 0.4 * fair
            edge = min(model_prob - american_to_prob(odds_to_bet), filters.MAX_EDGE)

        ok, reason = filters.passes_filters(line_data, edge, consensus_line, proj_total)
        if not ok:
            logger.debug(f"MLB F5 filter: {sched['away_team']}@{sched['home_team']}: {reason}")
            continue

        implied = american_to_prob(odds_to_bet)
        best_book = (line_data.get("best_over_book") if direction == "OVER"
                     else line_data.get("best_under_book")) or "Unknown"
        consensus = (line_data.get("over_odds") if direction == "OVER"
                     else line_data.get("under_odds")) or odds_to_bet

        # Game time strings (ET for display)
        game_time_str = ""
        game_date_str = ""
        try:
            commence = ev.get("commence_time", "")
            if commence:
                game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                et = timezone(timedelta(hours=config.ET_OFFSET_HOURS))
                game_time_str = game_dt.astimezone(et).strftime(config.TIME_FMT)
                game_date_str = game_dt.astimezone(et).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

        pick = Pick(
            module="mlb_f5",
            matchup=f"{sched['away_team']} @ {sched['home_team']}",
            pick_description=f"F5 {direction} {consensus_line}",
            best_odds_raw=odds_to_bet,
            best_odds_book=best_book,
            consensus_odds_raw=consensus,
            implied_prob=implied,
            model_prob=model_prob,
            edge_pct=edge,
            grade=assign_grade(edge),
            context={
                "stat": "F5_TOTAL",
                "projection": round(proj_total, 2),
                "line": consensus_line,
                "away_starter": sched["away_starter_name"],
                "home_starter": sched["home_starter_name"],
                "away_xfip": away_p["xFIP_30d"],
                "home_xfip": home_p["xFIP_30d"],
                "away_woba_vs_hand": round(away_woba, 3),
                "home_woba_vs_hand": round(home_woba, 3),
                "park_factor": pf,
                "weather_factor": round(wf, 3),
                "venue": sched["venue"],
                "game_date": game_date_str,
            },
            game_time=game_time_str,
        )
        picks.append(pick)
        logger.info(f"MLB PICK: {pick.matchup} F5 {direction} {consensus_line} | "
                    f"proj={proj_total:.2f} | edge={edge:.1%}")

    return picks


def _get_temp_weather_factor(venue, game_date_iso):
    """Best-effort temp-only multiplier via OpenWeather. Returns 1.0 on any failure."""
    if not config.OPENWEATHER_API_KEY:
        return 1.0
    # Skip if no venue or date
    if not venue or not game_date_iso:
        return 1.0
    # Crude geocoding: OpenWeather supports city queries; we'd need venue->city map.
    # For MVP, return neutral. Wire venue-city map in a follow-up.
    return 1.0
