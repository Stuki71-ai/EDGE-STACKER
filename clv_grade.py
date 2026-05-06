"""Grade CLV + actual results for a slate.

Pulls actual stats per player from nba_api / ESPN and computes:
  - Pick result (WIN/LOSS/DNP)
  - CLV (closing implied prob - taken implied prob, in %-points)
  - Beat-close rate (fraction of picks where CLV > 0)
  - Hit rate aggregates

Usage:
    python clv_grade.py [--date YYYY-MM-DD]
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import config
from shared import clv

logger = logging.getLogger("edge_stacker")
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def _key(entry):
    return (
        entry["sport"], entry["player"], entry["stat"],
        entry["direction"], entry["line"], entry["event_id"],
    )


def _grade_nba_player(player_name, stat, slate_date_iso):
    """Get actual NBA stat for player on slate date.

    Tries nba_api if USE_NBA_API_FULL is set or available, else ESPN.
    Returns float or None if DNP/unavailable.
    """
    try:
        os.environ.setdefault("USE_NBA_API_FULL", "1")
        from shared import nba_api_full as src
    except Exception:
        from shared import espn_nba as src

    if not src._player_id_cache:
        try:
            src.build_player_id_cache(None)
        except TypeError:
            src.build_player_id_cache([])
    pid = src.find_espn_player_id(player_name)
    if not pid:
        return None
    games = src.get_player_gamelog(pid)
    # Accept slate_date or ±1 day to handle UTC/ET drift in gamelog dates
    target = datetime.strptime(slate_date_iso, "%Y-%m-%d")
    accept = {(target + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)}
    chosen = None
    for g in games:
        gd = g.get("GAME_DATE", "")[:10]
        if gd in accept:
            chosen = g
            if gd == slate_date_iso:
                break
    if not chosen:
        return None
    return float(chosen.get(stat, 0))


def _grade_nhl_player(player_name, slate_date_iso):
    from shared import espn_nhl
    if not espn_nhl._player_id_cache:
        # Need to populate roster cache for all 32 teams
        import requests
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams", timeout=10)
        team_ids = []
        for s in r.json().get("sports", []):
            for l in s.get("leagues", []):
                for t in l.get("teams", []):
                    team_ids.append(str(t["team"]["id"]))
        espn_nhl.build_player_id_cache(team_ids)
    pid = espn_nhl.find_espn_player_id(player_name)
    if not pid:
        return None
    games = espn_nhl.get_player_gamelog(pid, last_n=None)
    target = datetime.strptime(slate_date_iso, "%Y-%m-%d")
    accept = {(target + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)}
    chosen = None
    for g in games:
        gd = g.get("GAME_DATE", "")[:10]
        if gd in accept:
            chosen = g
            if gd == slate_date_iso:
                break
    if not chosen:
        return None
    return float(chosen.get("S", 0))


def grade_date(slate_date_iso):
    entries = clv.list_all(slate_date_iso)
    if not entries:
        print(f"No CLV entries for {slate_date_iso}")
        return

    # Grade actuals where missing
    for e in entries:
        if "hit" in e:
            continue
        if e["sport"] == "nba":
            actual = _grade_nba_player(e["player"], e["stat"], slate_date_iso)
        else:
            actual = _grade_nhl_player(e["player"], slate_date_iso)
        if actual is None:
            continue
        line = e["line"]
        if e["direction"] == "OVER":
            hit = actual > line
        else:
            hit = actual < line
        clv.update_result(slate_date_iso, _key(e), actual, hit)

    # Re-load with grades applied
    entries = clv.list_all(slate_date_iso)

    print(f"\n{'='*98}")
    print(f"  CLV REPORT — slate {slate_date_iso}")
    print(f"{'='*98}\n")
    print(f"{'WF':<5s} {'Player':<22s} {'Pick':<22s} {'Open':>5s}/{'Close':<6s} {'Actual':>7s} {'Result':<6s} {'CLV':>7s}")
    print("-" * 98)

    n_total = 0
    n_graded = 0
    n_wins = 0
    n_with_close = 0
    n_beat_close = 0
    sum_clv = 0.0

    for e in entries:
        n_total += 1
        wf = "NHL" if e["sport"] == "nhl" else ("LOC" if "USE_NBA_API_FULL" in (e.get("env") or "") else "NBA")
        # Better: derive WF from module — LOCAL has no marker, both VPS+LOCAL
        # use module=nba_props. Use the file naming for now or just sport.
        wf = e["sport"].upper()
        pick = f"{e['direction']} {e['line']} {e['stat']}"
        open_o = e.get("open_odds")
        close_o = e.get("close_odds")
        actual = e.get("actual")
        hit = e.get("hit")
        clv_pct = clv.beat_close(e)

        if hit is True:
            n_wins += 1
            n_graded += 1
            res = "WIN"
        elif hit is False:
            n_graded += 1
            res = "LOSS"
        else:
            res = "DNP"

        if clv_pct is not None:
            n_with_close += 1
            sum_clv += clv_pct
            if clv_pct > 0:
                n_beat_close += 1
            clv_str = f"{clv_pct*100:+.1f}%"
        else:
            clv_str = "n/a"

        actual_str = f"{actual:.0f}" if actual is not None else "DNP"
        open_str = f"{open_o:+d}" if open_o is not None else "?"
        close_str = f"{close_o:+d}" if close_o is not None else "?"
        print(f"{wf:<5s} {e['player'][:22]:<22s} {pick:<22s} {open_str:>5s}/{close_str:<6s} {actual_str:>7s} {res:<6s} {clv_str:>7s}")

    print("-" * 98)
    win_rate = (n_wins / n_graded * 100) if n_graded else 0
    beat_rate = (n_beat_close / n_with_close * 100) if n_with_close else 0
    avg_clv = (sum_clv / n_with_close * 100) if n_with_close else 0
    print(f"\nResults: {n_wins}/{n_graded} graded picks won ({win_rate:.0f}%)  ({n_total - n_graded} DNP)")
    print(f"CLV:     {n_beat_close}/{n_with_close} beat the close ({beat_rate:.0f}%)  | avg CLV: {avg_clv:+.2f} pp")
    print()
    if n_with_close >= 5:
        if beat_rate >= 55:
            print("Sharpness signal: STRONG (>= 55% beat-close = sharp)")
        elif beat_rate >= 50:
            print("Sharpness signal: NEUTRAL (50-55% beat-close = noise/break-even)")
        else:
            print("Sharpness signal: WEAK (< 50% beat-close = picks lag the market)")
    else:
        print(f"(Sharpness signal needs >= 5 closed picks; have {n_with_close})")


def main():
    parser = argparse.ArgumentParser(description="CLV grade for a slate")
    parser.add_argument("--date", type=str, help="Slate date YYYY-MM-DD (default: yesterday ET)")
    args = parser.parse_args()

    if args.date:
        slate = args.date
    else:
        et = timezone(timedelta(hours=config.ET_OFFSET_HOURS))
        slate = (datetime.now(et) - timedelta(days=1)).strftime("%Y-%m-%d")

    grade_date(slate)


if __name__ == "__main__":
    main()
