import logging
from shared import odds_client, name_normalizer
import config

logger = logging.getLogger("edge_stacker")


def get_spreads():
    """Get NCAAB spreads from Odds API."""
    return odds_client.get_odds(config.SPORT_KEYS["ncaab"], markets="spreads")


def find_game_spread(odds_events, game):
    """Find spread data for an NCAAB game.

    Returns:
        dict with home_spread, away_spread, and per-team best odds/book/consensus
        or None.
    """
    event = name_normalizer.find_odds_event(
        odds_events,
        game["home"]["name"],
        game["away"]["name"],
        "ncaab"
    )

    if not event:
        return None

    spreads = {}  # team_name -> {spread, best_odds, best_book, all_odds}

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                team = outcome.get("name", "")
                spread = outcome.get("point")
                odds_val = outcome.get("price")
                book = bookmaker.get("title", "Unknown")

                if spread is None or odds_val is None:
                    continue

                if team not in spreads:
                    spreads[team] = {
                        "spread": spread,
                        "best_odds": odds_val,
                        "best_book": book,
                        "all_odds": [],
                    }
                spreads[team]["all_odds"].append(odds_val)
                if odds_val > spreads[team]["best_odds"]:
                    spreads[team]["best_odds"] = odds_val
                    spreads[team]["best_book"] = book
                    spreads[team]["spread"] = spread

    if not spreads:
        return None

    # Calculate consensus for each team
    for team_data in spreads.values():
        sorted_odds = sorted(team_data["all_odds"])
        mid = len(sorted_odds) // 2
        if len(sorted_odds) % 2 == 0:
            team_data["consensus"] = (sorted_odds[mid - 1] + sorted_odds[mid]) // 2
        else:
            team_data["consensus"] = sorted_odds[mid]

    return spreads
