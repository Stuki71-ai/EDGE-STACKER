"""Closing Line Value (CLV) tracking — the only real model-quality test.

CLV measures whether the lines we bet on moved in our favor by game time.
A sharp model will see lines move toward our projection ~55%+ of the time.
A weak model will be on the wrong side of line movement.

Storage: clv_history/{YYYY-MM-DD}.json — one file per slate date.
Each entry tracks: open snapshot (taken at pick gen), close snapshot
(taken right before tip-off), and final result (graded after game).

Workflow:
  1. main.py calls save_open() for each emitted pick
  2. clv_capture.py runs ~5 min before each game, calls capture_close()
  3. clv_grade.py runs after games, calls grade_results() and reports
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("edge_stacker")

CLV_DIR = Path(__file__).resolve().parent.parent / "clv_history"
CLV_DIR.mkdir(exist_ok=True)


def _path_for(date_iso):
    return CLV_DIR / f"{date_iso}.json"


def _load(date_iso):
    p = _path_for(date_iso)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(date_iso, entries):
    p = _path_for(date_iso)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _key(entry):
    """Unique key for matching open/close/result of the same pick."""
    return (
        entry.get("sport"),
        entry.get("player"),
        entry.get("stat"),
        entry.get("direction"),
        entry.get("line"),
        entry.get("event_id"),
    )


def save_open(picks, slate_date_iso):
    """Save the line/odds taken at pick generation time.

    Args:
        picks: list of Pick objects (the ones actually emitted in the email)
        slate_date_iso: "YYYY-MM-DD" — the date the slate is for (game date,
            not run date — they can differ when a run produces picks for
            tomorrow's slate).
    """
    if not picks:
        return
    entries = _load(slate_date_iso)
    existing_keys = {_key(e) for e in entries}
    now_iso = datetime.now(timezone.utc).isoformat()

    for p in picks:
        ctx = p.context or {}
        sport = "nhl" if p.module == "nhl_sog" else "nba"
        stat = ctx.get("stat") or ("SOG" if sport == "nhl" else "")
        # Extract direction from pick_description (last word per emit format)
        direction = "OVER" if "OVER" in p.pick_description else "UNDER"

        new = {
            "sport": sport,
            "module": p.module,
            "player": ctx.get("player", ""),
            "stat": stat,
            "direction": direction,
            "line": ctx.get("line", 0.0),
            "event_id": ctx.get("event_id", ""),
            "matchup": p.matchup,
            "commence_time": ctx.get("commence_time", ""),
            "open_odds": p.best_odds_raw,
            "open_book": p.best_odds_book,
            "open_consensus_odds": p.consensus_odds_raw,
            "model_prob": p.model_prob,
            "implied_prob": p.implied_prob,
            "edge_pct": p.edge_pct,
            "open_at": now_iso,
        }
        if _key(new) in existing_keys:
            continue  # idempotent on re-runs
        entries.append(new)
    _save(slate_date_iso, entries)
    logger.info(f"CLV: saved {len(picks)} open snapshots to {slate_date_iso}.json")


def update_close(slate_date_iso, key, close_odds, close_book, close_line, close_consensus_odds):
    """Update an entry with closing snapshot."""
    entries = _load(slate_date_iso)
    for e in entries:
        if _key(e) == key:
            e["close_odds"] = close_odds
            e["close_book"] = close_book
            e["close_line"] = close_line
            e["close_consensus_odds"] = close_consensus_odds
            e["close_at"] = datetime.now(timezone.utc).isoformat()
            _save(slate_date_iso, entries)
            return True
    return False


def update_result(slate_date_iso, key, actual_value, hit):
    """Update an entry with final game result."""
    entries = _load(slate_date_iso)
    for e in entries:
        if _key(e) == key:
            e["actual"] = actual_value
            e["hit"] = hit
            e["graded_at"] = datetime.now(timezone.utc).isoformat()
            _save(slate_date_iso, entries)
            return True
    return False


def list_pending_close(slate_date_iso):
    """Entries that have an open snapshot but no close snapshot yet."""
    return [e for e in _load(slate_date_iso) if "close_odds" not in e]


def list_pending_grade(slate_date_iso):
    """Entries with both open and close, but no result graded yet."""
    return [
        e for e in _load(slate_date_iso)
        if "close_odds" in e and "hit" not in e
    ]


def list_all(slate_date_iso):
    return _load(slate_date_iso)


def beat_close(entry):
    """Did we get value vs the closing line/odds?

    The classic CLV test: did the implied probability of our taken odds
    end up better than the implied probability of the closing odds?
    If we took +200 (33% implied) and close was +150 (40% implied),
    the line moved toward us → +CLV.

    Returns: float or None if close not captured. Positive = beat close.
    """
    from staking import american_to_prob
    open_odds = entry.get("open_odds")
    close_odds = entry.get("close_odds")
    if open_odds is None or close_odds is None:
        return None
    open_implied = american_to_prob(open_odds)
    close_implied = american_to_prob(close_odds)
    # We took at lower implied prob than close = we got better-than-close odds
    # = +CLV. Difference is in implied-probability percentage points.
    return close_implied - open_implied
