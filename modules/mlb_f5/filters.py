"""MLB F5 filter pipeline."""

from staking import calculate_vig

MAX_VIG = 0.08
MIN_EDGE = 0.10
MAX_EDGE = 0.25
LINE_SANITY_PCT = 0.25  # tighter than NBA's 35% — F5 totals cluster narrow


def passes_filters(line_data, edge_pct, line, projection):
    """Final filters before pick is emitted."""
    if edge_pct < MIN_EDGE:
        return False, f"Edge {edge_pct:.1%} < {MIN_EDGE:.1%}"

    # Use the TRUE single-book vig (hold) computed in odds.extract_totals.
    # calculate_vig(best_over, best_under) would mix prices from two
    # different books and understate the real hold.
    vig = line_data.get("vig")
    if vig is None:
        over = line_data.get("best_over_odds")
        under = line_data.get("best_under_odds")
        if over is not None and under is not None:
            vig = calculate_vig(over, under)
    if vig is not None and vig > MAX_VIG:
        return False, f"Vig {vig:.3f} > {MAX_VIG}"

    if line > 0 and abs(projection - line) / line > LINE_SANITY_PCT:
        return False, (f"Line sanity: |proj-line|/line "
                       f"= {abs(projection-line)/line:.1%} > {LINE_SANITY_PCT:.0%}")

    return True, None
