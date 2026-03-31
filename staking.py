import math

# ── Constants ──
KELLY_MULTIPLIER = 0.25            # Quarter-Kelly
KELLY_MULTIPLIER_DRAWDOWN = 0.125  # Eighth-Kelly during drawdown
MIN_KELLY_TO_BET = 0.005           # Skip if raw Kelly < 0.5%
MIN_BET = 5.00                     # Minimum bet size in dollars
MAX_DAILY_EXPOSURE_PCT = 0.08      # 8% of bankroll across ALL modules per day
MAX_SINGLE_BET_PCT = 0.03          # 3% of bankroll per pick
MAX_MODULE_DAILY_PCT = 0.05        # 5% per module per day
DRAWDOWN_THRESHOLD_PCT = 0.20      # 20% from peak -> halve Kelly
DRAWDOWN_RECOVERY_PCT = 0.10       # Restore when within 10% of peak


# ── Utility Functions ──

def american_to_prob(odds: int) -> float:
    """American odds -> implied probability (includes vig)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return abs(odds) / (abs(odds) + 100.0)


def american_to_decimal_payout(odds: int) -> float:
    """American odds -> decimal payout per $1 wagered (NOT including stake)."""
    if odds == 0:
        return 0.0
    elif odds > 0:
        return odds / 100.0
    else:
        return 100.0 / abs(odds)


def kelly_fraction(model_prob: float, odds_raw: int) -> float:
    """
    Kelly % = (b*p - q) / b
    b = decimal payout per $1 wagered
    p = model probability, q = 1-p
    Returns 0 if no edge or invalid odds.
    """
    b = american_to_decimal_payout(odds_raw)
    if b <= 0:
        return 0.0
    p = model_prob
    q = 1.0 - p
    k = (b * p - q) / b
    return max(k, 0.0)


def calculate_vig(over_odds: int, under_odds: int) -> float:
    """Calculate hold/vig from two-way odds."""
    return american_to_prob(over_odds) + american_to_prob(under_odds) - 1.0


def norm_cdf(x: float, mu: float, sigma: float) -> float:
    """Normal CDF using math.erf. No scipy needed."""
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def assign_grade(edge_pct: float) -> str:
    if edge_pct >= 0.10:
        return "A+"
    elif edge_pct >= 0.07:
        return "A"
    elif edge_pct >= 0.05:
        return "B+"
    elif edge_pct >= 0.03:
        return "B"
    else:
        return "C"


# ── Portfolio Staking ──

def apply_portfolio_limits(all_picks: list, bankroll: float, peak_bankroll: float,
                           prior_exposure: float = 0.0, in_drawdown: bool = False) -> tuple:
    """
    Size all picks via Kelly, then enforce portfolio limits.
    prior_exposure: amount already committed in earlier runs today (from daily_state.json).
    in_drawdown: persisted state from bankroll_state.json.
    Returns: (picks_list, new_in_drawdown_bool)
    """
    # Drawdown with hysteresis: enter at 20% down, exit at 10% down
    dd = (peak_bankroll - bankroll) / peak_bankroll if peak_bankroll > 0 else 0
    if in_drawdown:
        if dd < DRAWDOWN_RECOVERY_PCT:
            in_drawdown = False
    else:
        if dd >= DRAWDOWN_THRESHOLD_PCT:
            in_drawdown = True

    km = KELLY_MULTIPLIER_DRAWDOWN if in_drawdown else KELLY_MULTIPLIER

    max_total = bankroll * MAX_DAILY_EXPOSURE_PCT
    max_single = bankroll * MAX_SINGLE_BET_PCT
    max_module = bankroll * MAX_MODULE_DAILY_PCT

    # Calculate raw Kelly for each pick
    for pick in all_picks:
        raw_k = kelly_fraction(pick.model_prob, pick.best_odds_raw)
        pick.kelly_fraction = raw_k

        if raw_k < MIN_KELLY_TO_BET:
            pick.bet_size = 0.0
            pick.staking_note = "Skipped: edge too small"
            continue

        bet = round(bankroll * raw_k * km, 2)
        bet = min(bet, max_single)
        bet = max(bet, MIN_BET)
        pick.bet_size = bet

    # Sort by edge descending -- best picks get priority for caps
    all_picks.sort(key=lambda p: p.edge_pct, reverse=True)

    # Enforce caps
    total_exposure = prior_exposure
    module_exposure = {}

    for pick in all_picks:
        if pick.bet_size == 0:
            continue

        mod = pick.module
        mod_exp = module_exposure.get(mod, 0.0)

        # Module cap
        if mod_exp + pick.bet_size > max_module:
            remaining = max_module - mod_exp
            if remaining >= MIN_BET:
                pick.bet_size = round(remaining, 2)
                pick.staking_note = "Reduced: module cap"
            else:
                pick.bet_size = 0.0
                pick.staking_note = "Skipped: module cap"
                continue

        # Daily cap
        if total_exposure + pick.bet_size > max_total:
            remaining = max_total - total_exposure
            if remaining >= MIN_BET:
                pick.bet_size = round(remaining, 2)
                pick.staking_note = "Reduced: daily cap"
            else:
                pick.bet_size = 0.0
                pick.staking_note = "Skipped: daily cap"
                continue

        # Calculate potential win
        pick.potential_win = round(pick.bet_size * american_to_decimal_payout(pick.best_odds_raw), 2)
        pick.grade = assign_grade(pick.edge_pct)

        total_exposure += pick.bet_size
        module_exposure[mod] = mod_exp + pick.bet_size

    return all_picks, in_drawdown
