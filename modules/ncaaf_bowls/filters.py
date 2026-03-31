import logging
import config

logger = logging.getLogger("edge_stacker")


def bowl_underdog_model_prob(spread, fav_conference=None, dog_conference=None):
    """
    Model probability for bowl underdogs covering.

    Args:
        spread: positive number (points the dog is getting)
        fav_conference: conference name of the favorite
        dog_conference: conference name of the underdog
    """
    base = 0.563 if spread >= 10 else 0.545

    # Boost when the FAVORITE is from a historically bad bowl conference
    fav_penalty = {"ACC": 0.05}
    if fav_conference:
        base += fav_penalty.get(fav_conference, 0.0)

    # Boost when the DOG is from a historically strong bowl conference
    dog_bonus = {"Mountain West": 0.05, "Independent": 0.03}
    if dog_conference:
        base += dog_bonus.get(dog_conference, 0.0)

    return min(base, 0.68)


def passes_filters(spread_data, verbose=False):
    """
    Run the NCAAF Bowl filter pipeline.
    Returns: (passes: bool, reason: str or None)
    """
    if not spread_data:
        return False, "No spread data"

    spread = spread_data.get("spread", 0)
    best_odds = spread_data.get("best_odds")

    # STEP 2: Underdog getting 3+ points?
    if spread < config.BOWL_MIN_SPREAD:
        return False, f"Spread {spread} < {config.BOWL_MIN_SPREAD}"

    # STEP 3: Underdog getting <= 28 points?
    if spread > config.BOWL_MAX_SPREAD:
        return False, f"Spread {spread} > {config.BOWL_MAX_SPREAD}"

    # STEP 4: Best available spread odds >= -115?
    if best_odds is None or best_odds < config.BOWL_MAX_ODDS:
        return False, f"Odds {best_odds} worse than {config.BOWL_MAX_ODDS}"

    return True, None
