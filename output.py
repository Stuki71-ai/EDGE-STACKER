import json
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
import config

logger = logging.getLogger("edge_stacker")


def build_output(placed, skipped, bankroll, peak, modules_run,
                 games_evaluated=0, prior_exposure=0.0):
    """Build JSON and email output.

    Returns:
        dict with full output structure
    """
    today = datetime.now(timezone(timedelta(hours=-5)))
    # Use game date from picks if available (games may be tomorrow)
    game_date = _extract_game_date(placed)
    date_str = game_date if game_date else today.strftime("%Y-%m-%d")

    # Determine morning/afternoon
    hour = today.hour
    time_label = "Morning" if hour < 14 else "Afternoon"
    run_time = today.strftime(config.TIME_FMT)

    daily_limit = bankroll * 0.08
    current_run_exposure = sum(p.bet_size for p in placed)
    daily_exposure = prior_exposure + current_run_exposure

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
    """Build HTML email digest for Gmail."""
    from staking import KELLY_MULTIPLIER, KELLY_MULTIPLIER_DRAWDOWN

    dd = (peak - bankroll) / peak if peak > 0 else 0
    km = KELLY_MULTIPLIER_DRAWDOWN if dd >= 0.20 else KELLY_MULTIPLIER
    kelly_label = f"{'Eighth' if km == KELLY_MULTIPLIER_DRAWDOWN else 'Quarter'} ({km})"
    exposure_pct = (daily_exposure / daily_limit * 100) if daily_limit > 0 else 0

    h = []
    date_eu = f"{date_str[8:10]}.{date_str[5:7]}.{date_str[:4]}"
    h.append("<div style='font-family:Consolas,monospace;font-size:13px;line-height:1.6;color:#222'>")

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
        h.append(f"<h3 style='margin:4px 0'>{MODULE_NAMES.get(module, module.upper())}</h3>")

        STAT_FULL = {"PTS": "Points", "REB": "Rebounds", "AST": "Assists"}
        for p in picks:
            star = "&#9733; " if p.grade.startswith("A") else ""
            edge_color = "#0a7e0a" if p.edge_pct >= 0.10 else "#b8860b" if p.edge_pct >= 0.06 else "#555"
            cap_note = ""
            ctx = p.context
            stat_abbr = ctx.get("stat", "")
            stat_full = STAT_FULL.get(stat_abbr, stat_abbr)
            player = ctx.get("player", "")
            line = ctx.get("line", "")
            # Determine direction from pick_description
            direction = "OVER" if "OVER" in p.pick_description else "UNDER"
            h.append(f"<div style='margin:10px 0;padding:8px;background:#f5f5f5;border-left:4px solid {edge_color}'>")
            h.append(f"<b>{star}{p.matchup}: {player} - {stat_full} - {direction} {line}</b> | <b style='color:{edge_color}'>Edge: {p.edge_pct:.1%}</b><br>")
            h.append(f"<b>Bet ${p.bet_size:,.2f} &rarr; Win ${p.potential_win:,.2f}</b>{cap_note} | BET BY: {p.bet_by} | Game: {p.game_time}")
            h.append("</div>")

    h.append("</div>")

    return "".join(h)


def output_empty(message, bankroll=0.0, peak=0.0, modules_run=None):
    """Output for no-picks scenario."""
    now = datetime.now(timezone(timedelta(hours=-5)))
    output = {
        "date": now.strftime("%Y-%m-%d"),
        "run_time": now.strftime(config.TIME_FMT),
        "bankroll": bankroll,
        "peak_bankroll": peak,
        "daily_exposure": 0.0,
        "daily_limit": round(bankroll * 0.08, 2),
        "modules_run": modules_run or [],
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


def _extract_game_date(picks):
    """Get the game date from the first pick's context."""
    for p in picks:
        gd = p.context.get("game_date", "")
        if gd:
            return gd
    return ""


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
        return f"{ctx.get('player', '?')} {ctx.get('stat', '?')} proj {ctx.get('projection', '?')} vs line {ctx.get('line', '?')}"
    elif p.module == "ncaaf_bowls":
        return f"Bowl +{ctx.get('spread', '?')}"
    elif p.module == "ncaab_kenpom":
        div = ctx.get('divergence', 0)
        return f"KenPom +{div:.1f} divergence"
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
