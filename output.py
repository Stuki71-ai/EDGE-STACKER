import json
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import asdict

logger = logging.getLogger("edge_stacker")


def build_output(placed, skipped, bankroll, peak, modules_run, json_only=False, games_evaluated=0):
    """Build JSON and email output.

    Returns:
        dict with full output structure
    """
    today = datetime.now(timezone(timedelta(hours=-5)))
    date_str = today.strftime("%Y-%m-%d")

    # Determine morning/afternoon
    hour = today.hour
    time_label = "Morning" if hour < 14 else "Afternoon"
    run_time = today.strftime("%-I:%M %p ET")

    daily_limit = bankroll * 0.08
    daily_exposure = sum(p.bet_size for p in placed)

    total_wagered = sum(p.bet_size for p in placed)
    total_potential = sum(p.potential_win for p in placed)

    output = {
        "date": date_str,
        "run_time": run_time,
        "bankroll": bankroll,
        "peak_bankroll": peak,
        "daily_exposure": round(daily_exposure, 2),
        "daily_limit": round(daily_limit, 2),
        "modules_run": modules_run,
        "picks": [_pick_to_dict(p) for p in placed],
        "skipped": [_skipped_to_dict(p) for p in skipped],
        "summary": {
            "games_evaluated": games_evaluated,
            "picks_qualified": len(placed) + len(skipped),
            "picks_placed": len(placed),
            "picks_skipped": len(skipped),
            "total_wagered": round(total_wagered, 2),
            "total_potential_win": round(total_potential, 2),
        },
    }

    # Build email body
    email = build_email(placed, skipped, bankroll, peak, modules_run,
                        date_str, time_label, daily_exposure, daily_limit, output["summary"])
    output["email_body"] = email

    return output


def build_email(placed, skipped, bankroll, peak, modules_run,
                date_str, time_label, daily_exposure, daily_limit, summary):
    """Build the plain text email digest."""
    from staking import KELLY_MULTIPLIER, KELLY_MULTIPLIER_DRAWDOWN

    # Detect drawdown
    dd = (peak - bankroll) / peak if peak > 0 else 0
    km = KELLY_MULTIPLIER_DRAWDOWN if dd >= 0.20 else KELLY_MULTIPLIER
    kelly_label = f"{'Eighth' if km < 0.2 else 'Quarter'} ({km})"

    exposure_pct = (daily_exposure / daily_limit * 100) if daily_limit > 0 else 0

    lines = []
    lines.append(f"EDGE STACKER \u2014 {_format_date(date_str)} ({time_label})")
    lines.append(f"Bankroll: ${bankroll:,.2f} | Peak: ${peak:,.2f} | Kelly: {kelly_label}")
    lines.append(f"Daily Exposure: ${daily_exposure:,.2f} / ${daily_limit:,.2f} limit ({exposure_pct:.1f}%)")
    lines.append(f"Active: {', '.join(modules_run)}")
    lines.append("")

    # Group picks by module
    module_picks = {}
    for p in placed:
        module_picks.setdefault(p.module, []).append(p)

    MODULE_NAMES = {
        "ncaaf_weather": "NCAAF WEATHER UNDERS",
        "nba_props": "NBA PLAYER PROPS",
        "ncaaf_bowls": "NCAAF BOWL UNDERDOGS",
        "ncaab_kenpom": "NCAAB KENPOM DISAGREEMENT",
        "ncaab_conf_tourney": "NCAAB CONFERENCE TOURNAMENT",
    }

    for module, picks in module_picks.items():
        lines.append("\u2501" * 35)
        lines.append(MODULE_NAMES.get(module, module.upper()))
        lines.append("\u2501" * 35)
        lines.append("")

        for p in picks:
            star = "\u2605 " if p.grade.startswith("A") else ""
            lines.append(f"{star}{p.grade} | {_module_context_line(p)} | Edge: {p.edge_pct:.1%} | Kelly raw: {p.kelly_fraction:.1%}")
            lines.append(f"{p.matchup} \u2192 {p.pick_description} ({p.best_odds_book} {_format_odds(p.best_odds_raw)})")
            lines.append(f"Consensus: {_format_odds(p.consensus_odds_raw)} | {_module_detail_line(p)}")

            cap_note = f" ({p.staking_note})" if p.staking_note else ""
            lines.append(f"Bet ${p.bet_size:,.2f} \u2192 Win ${p.potential_win:,.2f}{cap_note}")
            lines.append(f"BET BY: {p.bet_by} | Game: {p.game_time}")
            lines.append("")

    if skipped:
        lines.append("\u2501" * 35)
        lines.append("SKIPPED")
        lines.append("\u2501" * 35)
        for p in skipped:
            lines.append(f"{p.grade} | {p.pick_description} (edge {p.edge_pct:.1%}) \u2192 {p.staking_note or 'Skipped'}")
        lines.append("")

    lines.append("\u2501" * 35)
    lines.append("SUMMARY")
    lines.append("\u2501" * 35)
    lines.append(f"Evaluated: {summary['games_evaluated']} games | "
                 f"Qualified: {summary['picks_qualified']} | "
                 f"Placed: {summary['picks_placed']} | "
                 f"Skipped: {summary['picks_skipped']}")
    lines.append(f"Wagered: ${summary['total_wagered']:,.2f} | "
                 f"Potential win: ${summary['total_potential_win']:,.2f}")

    return "\n".join(lines)


def output_empty(message):
    """Output for no-picks scenario."""
    output = {
        "date": datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d"),
        "picks": [],
        "skipped": [],
        "summary": {
            "games_evaluated": 0,
            "picks_qualified": 0,
            "picks_placed": 0,
            "picks_skipped": 0,
            "total_wagered": 0,
            "total_potential_win": 0,
        },
        "email_body": f"EDGE STACKER \u2014 {message}\nNo qualifying picks \u2014 sitting out.",
    }
    print(json.dumps(output, indent=2))


def _pick_to_dict(p):
    return {
        "module": p.module,
        "matchup": p.matchup,
        "pick_description": p.pick_description,
        "best_odds_raw": p.best_odds_raw,
        "best_odds_book": p.best_odds_book,
        "consensus_odds_raw": p.consensus_odds_raw,
        "model_prob": round(p.model_prob, 3),
        "implied_prob": round(p.implied_prob, 3),
        "edge_pct": round(p.edge_pct, 3),
        "grade": p.grade,
        "kelly_fraction": round(p.kelly_fraction, 3),
        "bet_size": p.bet_size,
        "potential_win": p.potential_win,
        "staking_note": p.staking_note,
        "bet_by": p.bet_by,
        "game_time": p.game_time,
        "context": p.context,
    }


def _skipped_to_dict(p):
    return {
        "module": p.module,
        "matchup": p.matchup,
        "pick_description": p.pick_description,
        "edge_pct": round(p.edge_pct, 3),
        "grade": p.grade,
        "staking_note": p.staking_note,
    }


def _format_odds(odds):
    if odds > 0:
        return f"+{odds}"
    return str(odds)


def _format_date(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except ValueError:
        return date_str


def _module_context_line(p):
    ctx = p.context
    if p.module == "ncaaf_weather":
        return f"Wind {ctx.get('wind_mph', '?')}mph, {ctx.get('temp_f', '?')}\u00b0F"
    elif p.module == "nba_props":
        return f"{ctx.get('stat', '?')} proj {ctx.get('projection', '?')} vs line {ctx.get('line', '?')}"
    elif p.module == "ncaaf_bowls":
        return f"Bowl +{ctx.get('spread', '?')}"
    elif p.module == "ncaab_kenpom":
        return f"KenPom +{ctx.get('divergence', '?'):.1f} divergence"
    elif p.module == "ncaab_conf_tourney":
        return f"{ctx.get('conference', '?')} {ctx.get('round', '?').replace('_', ' ').title()}"
    return ""


def _module_detail_line(p):
    ctx = p.context
    if p.module == "ncaaf_weather":
        return f"Precip: {ctx.get('precipitation', 'Clear')} | Venue: {ctx.get('venue', '?')}"
    elif p.module == "nba_props":
        out = ctx.get("teammate_out")
        out_str = f"OUT: {out}" if out else "No key injuries"
        return f"L10 avg: {ctx.get('l10_avg', '?')} | Opp DRTG: {ctx.get('opp_drtg', '?')} | {out_str}"
    elif p.module == "ncaaf_bowls":
        return f"Fav conf: {ctx.get('fav_conference', '?')} | Dog conf: {ctx.get('dog_conference', '?')}"
    elif p.module == "ncaab_kenpom":
        return f"KenPom margin: {ctx.get('kenpom_margin', '?')} | Market: {ctx.get('market_spread', '?')} | Conf: {'Yes' if ctx.get('is_conference') else 'No'}"
    elif p.module == "ncaab_conf_tourney":
        return f"Historical: {ctx.get('conference', '?')} {ctx.get('round', '?')} {ctx.get('historical_ats', 0):.1%} ATS ({ctx.get('sample', 0)} games)"
    return ""
