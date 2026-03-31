from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Pick:
    # -- Identity --
    module: str                    # "ncaaf_weather", "nba_props", etc.
    matchup: str                   # "Alabama @ LSU" or "LeBron James PTS"
    pick_description: str          # "UNDER 48.5" or "LeBron OVER 27.5 PTS"

    # -- Odds (raw integers for staking math) --
    best_odds_raw: int             # American odds integer: -108, +140, etc.
    best_odds_book: str            # "FanDuel", "DraftKings", etc.
    consensus_odds_raw: int        # Median across books

    # -- Edge Calculation --
    implied_prob: float = 0.0      # Market implied probability (from best odds)
    model_prob: float = 0.0        # Our estimated probability
    edge_pct: float = 0.0          # model_prob - implied_prob (decimal: 0.06 = 6%)

    # -- Scoring --
    grade: str = "C"               # A+/A/B+/B/C -- for email readability only

    # -- Context (module fills what's relevant) --
    context: dict = field(default_factory=dict)

    # -- Staking (filled by staking.py) --
    kelly_fraction: float = 0.0
    bet_size: float = 0.0
    potential_win: float = 0.0
    staking_note: Optional[str] = None

    # -- Timing --
    bet_by: str = ""               # "10:30 AM ET"
    game_time: str = ""            # "7:00 PM ET"
