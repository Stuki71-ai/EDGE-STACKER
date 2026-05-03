"""NHL SOG odds fetching + parsing."""

import logging
from shared import odds_client

logger = logging.getLogger("edge_stacker")

SPORT_KEY = "icehockey_nhl"


def get_nhl_events():
    """Get upcoming NHL events from Odds API."""
    return odds_client.get_events(SPORT_KEY)


def get_player_sog(event_id):
    """Get player_shots_on_goal market for a specific event."""
    return odds_client.get_event_odds(SPORT_KEY, event_id, "player_shots_on_goal")


def extract_props(event_odds):
    """Parse SOG props.

    Returns dict: player_name -> {line, best_over_odds, best_over_book,
                                  best_under_odds, best_under_book,
                                  over_odds, under_odds, all_lines}
    """
    props = {}

    for bookmaker in event_odds.get("bookmakers", []):
        book_name = bookmaker.get("title", "Unknown")

        for market in bookmaker.get("markets", []):
            if market.get("key") != "player_shots_on_goal":
                continue
            for outcome in market.get("outcomes", []):
                player = outcome.get("description", "")
                if not player:
                    continue

                if player not in props:
                    props[player] = {
                        "line": None,
                        "best_over_odds": None,
                        "best_over_book": None,
                        "best_under_odds": None,
                        "best_under_book": None,
                        "over_odds": None,
                        "under_odds": None,
                        "all_over": [],
                        "all_under": [],
                        "all_lines": [],
                    }

                entry = props[player]
                line = outcome.get("point")
                price = outcome.get("price")
                side = outcome.get("name", "")

                if line is not None:
                    entry["all_lines"].append(line)

                if side == "Over" and price is not None:
                    entry["all_over"].append(price)
                    if entry["best_over_odds"] is None or price > entry["best_over_odds"]:
                        entry["best_over_odds"] = price
                        entry["best_over_book"] = book_name
                elif side == "Under" and price is not None:
                    entry["all_under"].append(price)
                    if entry["best_under_odds"] is None or price > entry["best_under_odds"]:
                        entry["best_under_odds"] = price
                        entry["best_under_book"] = book_name

    # Set median consensus line + median consensus odds
    for sd in props.values():
        all_lines = sorted(sd["all_lines"])
        if all_lines:
            mid = len(all_lines) // 2
            sd["line"] = all_lines[mid]
        for key, all_key in [("over_odds", "all_over"), ("under_odds", "all_under")]:
            ol = sorted(sd[all_key])
            if ol:
                mid = len(ol) // 2
                sd[key] = ol[mid] if len(ol) % 2 else (ol[mid - 1] + ol[mid]) // 2

    return props
