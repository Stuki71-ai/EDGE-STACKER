import logging
import config

logger = logging.getLogger("edge_stacker")


def kenpom_model_prob(divergence_abs, is_conference_game):
    """
    Model probability based on KenPom vs market disagreement.
    """
    if divergence_abs >= 7.0:
        base = 0.68
    elif divergence_abs >= 5.0:
        base = 0.62
    elif divergence_abs >= 3.0:
        base = 0.57
    else:
        base = 0.50

    if not is_conference_game:
        base += 0.03

    return min(base, 0.72)


def determine_side(kenpom_margin, market_spread):
    """Determine which side to bet based on KenPom vs market.

    Args:
        kenpom_margin: positive = home favored (from kenpom_predicted_spread)
        market_spread: as returned by odds API (negative = home favored, e.g. -3.5)

    Returns:
        (side, divergence_abs) where side is "home" or "away"
    """
    market_margin = -market_spread  # Convert to same sign convention

    divergence = kenpom_margin - market_margin

    if divergence > 0:
        side = "home"  # KenPom thinks home is stronger than market
    elif divergence < 0:
        side = "away"  # KenPom thinks away is stronger than market
    else:
        return None, 0.0

    return side, abs(divergence)


def passes_filters(divergence_abs, best_odds, csv_age_days, verbose=False):
    """
    Run the NCAAB KenPom filter pipeline.
    Returns: (passes: bool, reason: str or None, warning: str or None)
    """
    warning = None

    # STEP 5: KenPom CSV age check
    if csv_age_days is not None:
        if csv_age_days > config.KENPOM_DISABLE_DAYS:
            return False, f"KenPom CSV {csv_age_days} days old (> {config.KENPOM_DISABLE_DAYS})", None
        if csv_age_days > config.KENPOM_WARN_DAYS:
            warning = f"KenPom CSV is {csv_age_days} days old"

    # STEP 3: Divergence >= 3.0?
    if divergence_abs < config.KENPOM_MIN_DIVERGENCE:
        return False, f"Divergence {divergence_abs:.1f} < {config.KENPOM_MIN_DIVERGENCE}", warning

    # STEP 4: Best odds >= -115?
    if best_odds is None or best_odds < config.KENPOM_MAX_ODDS:
        return False, f"Odds {best_odds} worse than {config.KENPOM_MAX_ODDS}", warning

    return True, None, warning
