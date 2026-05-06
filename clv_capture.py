"""Closing Line Value capture — re-query Odds API for pending picks.

Run periodically (e.g., every 30 min from after the 4 PM ET fire until
the latest game starts). Each run updates entries with the most-recent
line/odds, so the LAST successful capture before tip-off becomes the
"closing" snapshot used in CLV grading.

Usage:
    python clv_capture.py [--date YYYY-MM-DD]

If --date is omitted, captures for today's slate (in ET).
"""

import argparse
import logging
import sys
from datetime import datetime, timezone, timedelta

import config
from shared import clv

logger = logging.getLogger("edge_stacker")
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def _key(entry):
    return (
        entry["sport"], entry["player"], entry["stat"],
        entry["direction"], entry["line"], entry["event_id"],
    )


def capture_for_date(slate_date_iso, max_minutes_to_tipoff=180):
    """For each pending pick on this slate, fetch the latest Odds API line.

    Skips picks whose game has already started (commence_time in the past)
    or that are too far in the future (>max_minutes_to_tipoff away from now).
    """
    pending = clv.list_pending_close(slate_date_iso)
    if not pending:
        # Also re-capture entries with an existing close_odds to keep
        # snapshot fresh (the latest snapshot before tip-off is "the close")
        pending = [e for e in clv.list_all(slate_date_iso) if "hit" not in e]
    if not pending:
        logger.info(f"No CLV entries to capture for {slate_date_iso}")
        return

    now = datetime.now(timezone.utc)
    by_sport_event = {}
    for e in pending:
        # Skip if commence_time already past (game started — line is locked)
        ct_str = e.get("commence_time", "")
        if ct_str:
            try:
                ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
                minutes_ahead = (ct - now).total_seconds() / 60.0
                if minutes_ahead < -5:
                    continue  # game already started
                if minutes_ahead > max_minutes_to_tipoff:
                    continue  # too far ahead
            except (ValueError, TypeError):
                pass
        by_sport_event.setdefault((e["sport"], e["event_id"]), []).append(e)

    if not by_sport_event:
        logger.info(f"No CLV entries within capture window for {slate_date_iso}")
        return

    captured = 0
    for (sport, event_id), entries in by_sport_event.items():
        try:
            if sport == "nba":
                from modules.nba_props import odds as sport_odds
                event_odds = sport_odds.get_player_props(event_id)
                props = sport_odds.extract_props(event_odds)
            elif sport == "nhl":
                from modules.nhl_sog import odds as sport_odds
                event_odds = sport_odds.get_player_sog(event_id)
                props = sport_odds.extract_props(event_odds)
            else:
                continue
        except Exception as ex:
            logger.warning(f"CLV: failed to fetch {sport} event {event_id}: {ex}")
            continue

        for entry in entries:
            # NBA props are nested: props[player][stat]
            # NHL props are flat: props[player]  (only SOG market)
            player_data = props.get(entry["player"])
            if not player_data:
                continue
            if sport == "nba":
                sd = player_data.get(entry["stat"])
                if not sd:
                    continue
            else:
                sd = player_data

            if entry["direction"] == "OVER":
                close_odds = sd.get("best_over_odds")
                close_book = sd.get("best_over_book")
                consensus = sd.get("over_odds")
            else:
                close_odds = sd.get("best_under_odds")
                close_book = sd.get("best_under_book")
                consensus = sd.get("under_odds")
            line = sd.get("line")

            if close_odds is None:
                continue

            ok = clv.update_close(
                slate_date_iso,
                _key(entry),
                close_odds=close_odds,
                close_book=close_book,
                close_line=line,
                close_consensus_odds=consensus,
            )
            if ok:
                captured += 1
                logger.info(
                    f"CLV close: {entry['player']} {entry['direction']} {entry['line']} "
                    f"{entry['stat']}: open={entry['open_odds']} -> close={close_odds}"
                )

    logger.info(f"CLV capture for {slate_date_iso}: {captured} updates")


def main():
    parser = argparse.ArgumentParser(description="CLV capture for pending picks")
    parser.add_argument("--date", type=str, help="Slate date YYYY-MM-DD (default: today ET)")
    parser.add_argument("--max-minutes-ahead", type=int, default=180,
                        help="Don't capture for games more than N min away (default 180)")
    args = parser.parse_args()

    if args.date:
        slate = args.date
    else:
        et = timezone(timedelta(hours=config.ET_OFFSET_HOURS))
        slate = datetime.now(et).strftime("%Y-%m-%d")

    logger.info(f"CLV capture run for slate {slate}")
    capture_for_date(slate, max_minutes_to_tipoff=args.max_minutes_ahead)


if __name__ == "__main__":
    main()
