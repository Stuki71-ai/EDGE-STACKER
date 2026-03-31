import logging
from shared import odds_client, name_normalizer
import config

logger = logging.getLogger("edge_stacker")


def get_spreads():
    """Get NCAAB spreads from Odds API."""
    return odds_client.get_odds(config.SPORT_KEYS["ncaab"], markets="spreads")


def find_underdog_spread(odds_events, game):
    """Find the underdog spread data for a conf tournament game.

    Returns:
        dict with underdog, spread, best_odds, best_book, consensus_odds
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

    best_by_team = {}
    all_odds_by_team = {}

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                team = outcome.get("name", "")
                spread = outcome.get("point")
                odds_val = outcome.get("price")
                book = bookmaker.get("title", "Unknown")

                if spread is not None and odds_val is not None:
                    if team not in all_odds_by_team:
                        all_odds_by_team[team] = []
                    all_odds_by_team[team].append(odds_val)

                    if team not in best_by_team or odds_val > best_by_team[team][1]:
                        best_by_team[team] = (spread, odds_val, book)

    # Find the underdog (positive spread)
    for team, (spread, odds_val, book) in best_by_team.items():
        if spread > 0:
            sorted_odds = sorted(all_odds_by_team.get(team, [odds_val]))
            mid = len(sorted_odds) // 2
            consensus = sorted_odds[mid] if len(sorted_odds) % 2 else (sorted_odds[mid - 1] + sorted_odds[mid]) // 2

            return {
                "underdog": team,
                "spread": spread,
                "best_odds": odds_val,
                "best_book": book,
                "consensus_odds": consensus,
            }

    return None
