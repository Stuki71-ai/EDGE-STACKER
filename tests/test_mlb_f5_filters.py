"""Tests for MLB F5 totals filters — vig (hold) gate."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.mlb_f5.filters import passes_filters, MAX_VIG, MIN_EDGE
from modules.mlb_f5.odds import extract_totals


class TestVigFilter:
    """The vig gate must use the TRUE single-book hold, not a cross-book figure."""

    def test_vig_uses_true_single_book_not_cross_book(self):
        # best_over/-under come from two different books and look like a low
        # cross-book vig, but the real single-book hold (stored in "vig") is
        # above MAX_VIG. The filter must reject on the true single-book hold.
        line_data = {
            "best_over_odds": -105, "best_under_odds": -105,  # cross-book ~4.6%
            "over_odds": -110, "under_odds": -110,
            "vig": 0.10,  # true min single-book hold = 10% > MAX_VIG 0.08
        }
        # edge above MIN_EDGE, projection == line so line-sanity passes
        passed, reason = passes_filters(line_data, 0.12, 8.0, 8.0)
        assert not passed
        assert "Vig" in reason

    def test_vig_field_passes_when_low(self):
        line_data = {
            "best_over_odds": -110, "best_under_odds": -110,
            "over_odds": -110, "under_odds": -110,
            "vig": 0.045,  # genuinely low single-book hold
        }
        passed, reason = passes_filters(line_data, 0.12, 8.0, 8.0)
        assert passed

    def test_vig_falls_back_to_cross_book_when_no_field(self):
        # No "vig" field (no single book quoted both sides) -> fall back to
        # calculate_vig(best_over, best_under). -150/-150 is a high hold.
        line_data = {
            "best_over_odds": -150, "best_under_odds": -150,
        }
        passed, reason = passes_filters(line_data, 0.12, 8.0, 8.0)
        assert not passed
        assert "Vig" in reason


class TestExtractTotalsVig:
    """extract_totals must store the MINIMUM true single-book hold."""

    def test_stores_min_single_book_vig(self):
        # Two books on the same line. Book A: -110/-110 (~4.5% hold).
        # Book B: -130/-130 (~13% hold). sd["vig"] must be the minimum.
        event_odds = {
            "bookmakers": [
                {"title": "BookA", "markets": [{
                    "key": "totals_1st_5_innings",
                    "outcomes": [
                        {"name": "Over", "point": 4.5, "price": -110},
                        {"name": "Under", "point": 4.5, "price": -110},
                    ],
                }]},
                {"title": "BookB", "markets": [{
                    "key": "totals_1st_5_innings",
                    "outcomes": [
                        {"name": "Over", "point": 4.5, "price": -130},
                        {"name": "Under", "point": 4.5, "price": -130},
                    ],
                }]},
            ]
        }
        by_line = extract_totals(event_odds)
        sd = by_line[4.5]
        assert "vig" in sd
        # min() must select BookA (-110/-110, hold = 2*(110/210) - 1 ~= 0.0476)
        # over BookB (-130/-130, hold ~= 0.1304), proving the minimum was taken.
        assert abs(sd["vig"] - 0.0476) < 1e-3
