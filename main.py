#!/usr/bin/env python3
"""EDGE STACKER — Year-Round Quantitative Sports Betting System"""

import sys
import os
import json
import argparse
import logging
from datetime import date, datetime
from importlib import import_module

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from staking import apply_portfolio_limits
from output import build_output, output_empty

logger = logging.getLogger("edge_stacker")


# ── Module Activation Calendar ──

def active_modules(today: date) -> list:
    month = today.month
    day = today.day
    dow = today.weekday()  # 0=Mon, 5=Sat

    active = []

    # Module 1: NCAAF Weather -- Sep-Jan, Thu/Fri/Sat
    if month in (9, 10, 11, 12) or (month == 1 and day <= 15):
        if dow in (3, 4, 5):  # Thu, Fri, Sat
            active.append("ncaaf_weather")

    # Module 2: NBA Props -- Oct 15 through Jun 20, daily
    if (month == 10 and day >= 15) or month in (11, 12, 1, 2, 3, 4, 5) or (month == 6 and day <= 20):
        active.append("nba_props")

    # Module 3: NCAAF Bowls -- Dec 14 through Jan 10
    if (month == 12 and day >= 14) or (month == 1 and day <= 10):
        active.append("ncaaf_bowls")

    # Module 4: NCAAB KenPom -- Nov 1 through Mar 31, daily
    if month in (11, 12, 1, 2, 3):
        active.append("ncaab_kenpom")

    # Module 5: NCAAB Conf Tournament -- Mar 1-15
    if month == 3 and day <= 15:
        active.append("ncaab_conf_tourney")

    return active


# ── Bankroll State ──

def load_bankroll():
    """Load bankroll state. Returns (bankroll, peak, in_drawdown)."""
    try:
        with open(config.BANKROLL_STATE_PATH, "r") as f:
            state = json.load(f)
        return (
            state.get("bankroll", config.DEFAULT_BANKROLL),
            state.get("peak_bankroll", config.DEFAULT_BANKROLL),
            state.get("in_drawdown", False),
        )
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("bankroll_state.json missing or corrupt -- using defaults")
        save_bankroll(config.DEFAULT_BANKROLL, config.DEFAULT_BANKROLL, False)
        return config.DEFAULT_BANKROLL, config.DEFAULT_BANKROLL, False


def save_bankroll(bankroll, peak, in_drawdown):
    """Save bankroll state."""
    try:
        with open(config.BANKROLL_STATE_PATH, "r") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {
            "module_pnl": {},
            "total_bets": 0,
            "total_wagered": 0.0,
        }

    state["bankroll"] = bankroll
    state["peak_bankroll"] = peak
    state["in_drawdown"] = in_drawdown
    state["last_updated"] = date.today().isoformat()

    with open(config.BANKROLL_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def save_drawdown_state(in_drawdown):
    """Update just the drawdown flag."""
    try:
        with open(config.BANKROLL_STATE_PATH, "r") as f:
            state = json.load(f)
        state["in_drawdown"] = in_drawdown
        with open(config.BANKROLL_STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save drawdown state: {e}")


def update_bankroll(new_amount):
    """Update bankroll to a new amount. Update peak if higher."""
    bankroll, peak, in_drawdown = load_bankroll()
    new_peak = max(peak, new_amount)
    save_bankroll(new_amount, new_peak, in_drawdown)
    print(f"Bankroll updated: ${new_amount:,.2f} (peak: ${new_peak:,.2f})")


def print_bankroll_status():
    """Print current bankroll state."""
    try:
        with open(config.BANKROLL_STATE_PATH, "r") as f:
            state = json.load(f)
        bankroll = state.get("bankroll", 0)
        peak = state.get("peak_bankroll", 0)
        dd = (peak - bankroll) / peak * 100 if peak > 0 else 0
        print(f"Bankroll: ${bankroll:,.2f}")
        print(f"Peak:     ${peak:,.2f}")
        print(f"Drawdown: {dd:.1f}%")
        print(f"In drawdown mode: {state.get('in_drawdown', False)}")
        print(f"Last updated: {state.get('last_updated', 'never')}")
        pnl = state.get("module_pnl", {})
        if pnl:
            print("\nModule P&L:")
            for mod, val in pnl.items():
                print(f"  {mod}: ${val:+,.2f}")
        print(f"\nTotal bets: {state.get('total_bets', 0)}")
        print(f"Total wagered: ${state.get('total_wagered', 0):,.2f}")
    except (FileNotFoundError, json.JSONDecodeError):
        print("No bankroll state found. Run the system to initialize.")


# ── Daily Exposure Tracking ──

def load_daily_exposure(today):
    """Load today's exposure from daily_state.json. Reset if different date."""
    try:
        with open(config.DAILY_STATE_PATH, "r") as f:
            state = json.load(f)
        if state.get("date") != today.isoformat():
            return 0.0
        return state.get("total_exposure", 0.0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0.0


def save_daily_exposure(today, total_exposure, modules):
    """Save updated daily exposure."""
    try:
        with open(config.DAILY_STATE_PATH, "r") as f:
            state = json.load(f)
        if state.get("date") != today.isoformat():
            state = {"date": today.isoformat(), "total_exposure": 0.0, "module_exposure": {}, "picks_placed": 0}
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"date": today.isoformat(), "total_exposure": 0.0, "module_exposure": {}, "picks_placed": 0}

    state["total_exposure"] = round(total_exposure, 2)
    state["date"] = today.isoformat()

    with open(config.DAILY_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── CLI ──

def parse_args():
    parser = argparse.ArgumentParser(description="EDGE STACKER Sports Betting System")
    parser.add_argument("--date", type=str, help="Override date (YYYY-MM-DD)")
    parser.add_argument("--modules", type=str, help="Comma-separated module names")
    parser.add_argument("--json-only", action="store_true", help="JSON output only (for n8n)")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging to stderr")
    parser.add_argument("--update-bankroll", type=float, help="Update bankroll to amount")
    parser.add_argument("--bankroll-status", action="store_true", help="Print bankroll status")
    parser.add_argument("--list-modules", action="store_true", help="List active modules for today")
    return parser.parse_args()


# ── Main ──

def main():
    args = parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(log_level)

    # Also log to file
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    now = datetime.now()
    log_file = os.path.join(config.LOGS_DIR, f"edge-stacker-{now.strftime('%Y-%m-%d-%H%M')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(file_handler)

    today = date.today()
    if args.date:
        try:
            today = date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"Invalid date format: {args.date}")
            sys.exit(1)

    if args.update_bankroll is not None:
        update_bankroll(args.update_bankroll)
        return

    if args.bankroll_status:
        print_bankroll_status()
        return

    if args.list_modules:
        mods = active_modules(today)
        print(json.dumps(mods, indent=2))
        return

    # Determine modules
    if args.modules:
        modules = [m.strip() for m in args.modules.split(",")]
    else:
        modules = active_modules(today)

    if not modules:
        output_empty("No active modules today.")
        sys.exit(0)

    logger.info(f"EDGE STACKER run: {today.isoformat()} | modules: {modules}")

    bankroll, peak, in_drawdown = load_bankroll()
    prior_exposure = load_daily_exposure(today)
    all_picks = []
    had_errors = False

    for mod_name in modules:
        try:
            runner = import_module(f"modules.{mod_name}.runner")
            picks = runner.run(today)
            all_picks.extend(picks)
            logger.info(f"{mod_name}: {len(picks)} qualifying picks")
        except Exception as e:
            logger.error(f"{mod_name}: FAILED -- {e}")
            had_errors = True
            continue

    if not all_picks:
        output_empty("Active modules ran but no qualifying picks found.")
        sys.exit(2 if had_errors else 0)

    # Staking
    all_picks, in_drawdown = apply_portfolio_limits(all_picks, bankroll, peak, prior_exposure, in_drawdown)
    save_drawdown_state(in_drawdown)

    # Separate placed vs skipped
    placed = [p for p in all_picks if p.bet_size > 0]
    skipped = [p for p in all_picks if p.bet_size == 0]

    # Update daily exposure
    new_exposure = sum(p.bet_size for p in placed)
    save_daily_exposure(today, prior_exposure + new_exposure, modules)

    # Output
    output = build_output(placed, skipped, bankroll, peak, modules)
    print(json.dumps(output, indent=2))

    exit_code = 2 if had_errors else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
