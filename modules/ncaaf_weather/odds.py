import logging
from shared import odds_client, name_normalizer
import config

logger = logging.getLogger("edge_stacker")


def get_totals():
    """Get NCAAF game totals from Odds API.

    Returns:
        List of event dicts with totals odds.
    """
    return odds_client.get_odds(config.SPORT_KEYS["ncaaf"], markets="totals")


def find_game_total(odds_events, game):
    """Find the total and best under odds for a game.

    Returns:
        (total, best_under_odds, best_book, consensus_odds, all_under_odds) or (None, None, None, None, [])
    """
    event = name_normalizer.find_odds_event(
        odds_events,
        game["home"]["name"],
        game["away"]["name"],
        "ncaaf"
    )

    if not event:
        return None, None, None, None, []

    total = None
    all_under_odds = []
    best_under_odds = None
    best_book = None

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name") == "Under":
                    odds = outcome.get("price")
                    point = outcome.get("point")
                    if total is None and point is not None:
                        total = point
                    if odds is not None:
                        all_under_odds.append(odds)
                        if best_under_odds is None or odds > best_under_odds:
                            best_under_odds = odds
                            best_book = bookmaker.get("title", "Unknown")

    # Consensus = median
    consensus = None
    if all_under_odds:
        sorted_odds = sorted(all_under_odds)
        mid = len(sorted_odds) // 2
        if len(sorted_odds) % 2 == 0:
            consensus = (sorted_odds[mid - 1] + sorted_odds[mid]) // 2
        else:
            consensus = sorted_odds[mid]

    return total, best_under_odds, best_book, consensus, all_under_odds
