import logging
from shared import odds_client
import config

logger = logging.getLogger("edge_stacker")


def get_nba_events():
    """Get upcoming NBA events from Odds API."""
    return odds_client.get_events(config.SPORT_KEYS["nba"])


def get_player_props(event_id):
    """Get player props for a specific NBA event.

    Returns:
        dict with bookmaker data for player_points, player_rebounds, player_assists
    """
    markets = "player_points,player_rebounds,player_assists"
    return odds_client.get_event_odds(config.SPORT_KEYS["nba"], event_id, markets)


def extract_props(event_odds):
    """Parse player prop data into a structured format.

    Returns:
        dict: player_name -> stat -> {line, over_odds, under_odds, book, all_books: [...]}
    """
    props = {}

    for bookmaker in event_odds.get("bookmakers", []):
        book_name = bookmaker.get("title", "Unknown")

        for market in bookmaker.get("markets", []):
            market_key = market.get("key", "")
            stat_map = {
                "player_points": "PTS",
                "player_rebounds": "REB",
                "player_assists": "AST",
            }
            stat = stat_map.get(market_key)
            if not stat:
                continue

            for outcome in market.get("outcomes", []):
                player = outcome.get("description", "")
                if not player:
                    continue

                if player not in props:
                    props[player] = {}
                if stat not in props[player]:
                    props[player][stat] = {
                        "line": None,
                        "over_odds": None,
                        "under_odds": None,
                        "best_over_odds": None,
                        "best_over_book": None,
                        "best_under_odds": None,
                        "best_under_book": None,
                        "all_over": [],
                        "all_under": [],
                        "all_lines": [],
                        # Per-book entries for de-vigging: {book_name: {over: x, under: y}}
                        "by_book": {},
                    }

                entry = props[player][stat]
                line = outcome.get("point")
                price = outcome.get("price")
                side = outcome.get("name", "")

                if line is not None:
                    entry["all_lines"].append(line)

                # Track per-book over/under for de-vigging
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

    # Set consensus line (median), consensus odds, and market no-vig probabilities
    from staking import american_to_prob
    for player_props in props.values():
        for stat_data in player_props.values():
            all_lines = sorted(stat_data.get("all_lines", []))
            if all_lines:
                mid = len(all_lines) // 2
                stat_data["line"] = all_lines[mid]

            for key, all_key in [("over_odds", "all_over"), ("under_odds", "all_under")]:
                odds_list = sorted(stat_data[all_key])
                if odds_list:
                    mid = len(odds_list) // 2
                    if len(odds_list) % 2 == 0:
                        stat_data[key] = (odds_list[mid - 1] + odds_list[mid]) // 2
                    else:
                        stat_data[key] = odds_list[mid]

            # Market consensus no-vig probability: median of de-vigged probs across books.
            # This is the "true" implied probability that sharp models target.
            no_vig_overs = []
            no_vig_unders = []
            for book_pair in stat_data.get("by_book", {}).values():
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
                stat_data["fair_over_prob"] = no_vig_overs[mid]
                stat_data["fair_under_prob"] = no_vig_unders[mid]

    return props
