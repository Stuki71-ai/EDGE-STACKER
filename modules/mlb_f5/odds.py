"""MLB F5 totals odds fetching + parsing."""

import logging
import config
from shared import odds_client

logger = logging.getLogger("edge_stacker")

SPORT_KEY = "baseball_mlb"
F5_TOTALS_MARKET = "totals_1st_5_innings"


def get_mlb_events():
    """Get upcoming MLB events from Odds API."""
    return odds_client.get_events(SPORT_KEY)


def get_f5_totals(event_id):
    """Get F5 totals market for a specific event."""
    return odds_client.get_event_odds(SPORT_KEY, event_id, F5_TOTALS_MARKET)


def extract_totals(event_odds):
    """Parse F5 totals into structured format keyed by line value.

    Returns dict: line(float) -> {best_over_odds, best_over_book,
    best_under_odds, best_under_book, over_odds (consensus), under_odds,
    by_book, fair_over_prob, fair_under_prob}.
    """
    from staking import american_to_prob

    by_line = {}
    for bookmaker in event_odds.get("bookmakers", []):
        book = bookmaker.get("title", "Unknown")
        for market in bookmaker.get("markets", []):
            if market.get("key") != F5_TOTALS_MARKET:
                continue
            book_outcomes = {}
            for outcome in market.get("outcomes", []):
                point = outcome.get("point")
                if point is None:
                    continue
                book_outcomes.setdefault(point, {})[outcome.get("name", "")] = outcome.get("price")

            for point, sides in book_outcomes.items():
                ld = by_line.setdefault(point, {
                    "best_over_odds": None, "best_over_book": None,
                    "best_under_odds": None, "best_under_book": None,
                    "all_over": [], "all_under": [],
                    "by_book": {},
                })
                over_p = sides.get("Over")
                under_p = sides.get("Under")
                if over_p is None or under_p is None:
                    continue
                ld["all_over"].append(over_p)
                ld["all_under"].append(under_p)
                ld["by_book"][book] = {"over": over_p, "under": under_p}
                if ld["best_over_odds"] is None or over_p > ld["best_over_odds"]:
                    ld["best_over_odds"] = over_p
                    ld["best_over_book"] = book
                if ld["best_under_odds"] is None or under_p > ld["best_under_odds"]:
                    ld["best_under_odds"] = under_p
                    ld["best_under_book"] = book

    for point, sd in by_line.items():
        for key, all_key in [("over_odds", "all_over"), ("under_odds", "all_under")]:
            ol = sorted(sd[all_key])
            if ol:
                mid = len(ol) // 2
                sd[key] = ol[mid] if len(ol) % 2 else (ol[mid - 1] + ol[mid]) // 2

        fair_overs = []
        fair_unders = []
        for pair in sd.get("by_book", {}).values():
            o = american_to_prob(pair["over"])
            u = american_to_prob(pair["under"])
            t = o + u
            if t > 0:
                fair_overs.append(o / t)
                fair_unders.append(u / t)
        if fair_overs:
            fair_overs.sort()
            fair_unders.sort()
            mid = len(fair_overs) // 2
            sd["fair_over_prob"] = fair_overs[mid]
            sd["fair_under_prob"] = fair_unders[mid]

    return by_line
