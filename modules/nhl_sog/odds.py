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
                        "by_book": {},
                    }

                entry = props[player]
                line = outcome.get("point")
                price = outcome.get("price")
                side = outcome.get("name", "")

                if line is not None:
                    entry["all_lines"].append(line)

                if book_name not in entry["by_book"]:
                    entry["by_book"][book_name] = {}

                if side == "Over" and price is not None:
                    entry["all_over"].append(price)
                    entry["by_book"][book_name]["over"] = price
                    if entry["best_over_odds"] is None or price > entry["best_over_odds"]:
                        entry["best_over_odds"] = price
                        entry["best_over_book"] = book_name
                elif side == "Under" and price is not None:
                    entry["all_under"].append(price)
                    entry["by_book"][book_name]["under"] = price
                    if entry["best_under_odds"] is None or price > entry["best_under_odds"]:
                        entry["best_under_odds"] = price
                        entry["best_under_book"] = book_name

    # Set median consensus line, median consensus odds, market no-vig fair probabilities
    from staking import american_to_prob
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

        # Market no-vig fair probability (median across books with both sides)
        no_vig_overs = []
        no_vig_unders = []
        for book_pair in sd.get("by_book", {}).values():
            if "over" in book_pair and "under" in book_pair:
                over_p = american_to_prob(book_pair["over"])
                under_p = american_to_prob(book_pair["under"])
                total = over_p + under_p
                if total > 0:
                    no_vig_overs.append(over_p / total)
                    no_vig_unders.append(under_p / total)
        if no_vig_overs:
            no_vig_overs.sort()
            no_vig_unders.sort()
            mid = len(no_vig_overs) // 2
            sd["fair_over_prob"] = no_vig_overs[mid]
            sd["fair_under_prob"] = no_vig_unders[mid]

    return props
