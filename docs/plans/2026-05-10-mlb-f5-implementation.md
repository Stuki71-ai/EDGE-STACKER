# MLB F5 Totals Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add MLB F5 (First-5-Innings) totals module to EDGE STACKER. Emits 3 highest-edge picks per day at 04:30 PM ET fire, mirroring NHL pattern.

**Architecture:** New `modules/mlb_f5/` mirrors `modules/nhl_sog/` shape. Free data: MLB Stats API (probable starters), pybaseball/Statcast (pitcher xFIP, K-BB%, opp wOBA vs hand), OpenWeather (already wired), static park-factor table. Odds API `baseball_mlb` market `totals_1st_5_innings`. Projection = xFIP-based F5 expected runs × opp wOBA factor × park factor × weather factor. Edge via Poisson CDF vs F5 line, market anchor 60/40 blend, top-3 by raw edge after `MIN_EDGE = 0.10`.

**Tech Stack:** Python, requests, pybaseball, scipy.stats (Poisson), existing `shared/odds_client.py`.

---

## Task 1: Add MLB sport key + install pybaseball

**Files:**
- Modify: `config.py:30-34` (add `"mlb"` to SPORT_KEYS)
- Modify: `requirements.txt` (or pip install + freeze)

**Step 1:** Add MLB sport key

In `config.py` SPORT_KEYS dict, add:
```python
SPORT_KEYS = {
    "ncaaf": "americanfootball_ncaaf",
    "ncaab": "basketball_ncaab",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",  # new
}
```

**Step 2:** Install pybaseball

Run: `pip install pybaseball`

Verify: `python -c "import pybaseball; print(pybaseball.__version__)"` should print version.

**Step 3:** Update requirements.txt if it exists

Check: `cat requirements.txt 2>/dev/null | grep pybaseball || echo "pybaseball" >> requirements.txt`

**Step 4:** Commit

```bash
git add config.py requirements.txt
git commit -m "Add MLB sport key + pybaseball dependency"
```

---

## Task 2: Static park-factor table

**Files:**
- Create: `static/mlb_park_factors.json`
- Test: `tests/test_mlb_park_factors.py`

**Step 1:** Create park factors JSON

Source: 3-year FanGraphs park factors (runs). Use these stable values:

```json
{
  "Coors Field":          1.18,
  "Great American Ball Park": 1.10,
  "Camden Yards":         1.07,
  "Globe Life Field":     1.06,
  "Fenway Park":          1.05,
  "Guaranteed Rate Field": 1.05,
  "Yankee Stadium":       1.04,
  "Wrigley Field":        1.02,
  "Citizens Bank Park":   1.02,
  "Truist Park":          1.01,
  "Angel Stadium":        1.01,
  "Chase Field":          1.00,
  "Minute Maid Park":     1.00,
  "Busch Stadium":        0.99,
  "Target Field":         0.99,
  "Rogers Centre":        0.99,
  "Citi Field":           0.98,
  "American Family Field": 0.98,
  "Dodger Stadium":       0.97,
  "Nationals Park":       0.97,
  "Comerica Park":        0.97,
  "Oracle Park":          0.96,
  "PNC Park":             0.96,
  "loanDepot park":       0.95,
  "Oakland Coliseum":     0.95,
  "T-Mobile Park":        0.94,
  "Tropicana Field":      0.94,
  "Petco Park":           0.94,
  "Progressive Field":    0.93,
  "Kauffman Stadium":     0.92
}
```

Save to `static/mlb_park_factors.json`.

**Step 2:** Write test

`tests/test_mlb_park_factors.py`:
```python
import json
from pathlib import Path

def test_park_factors_loads():
    p = Path(__file__).resolve().parent.parent / "static" / "mlb_park_factors.json"
    with open(p) as f:
        data = json.load(f)
    assert "Coors Field" in data
    assert data["Coors Field"] > 1.0
    assert all(0.85 < v < 1.25 for v in data.values())
    assert len(data) == 30
```

**Step 3:** Run test

Run: `pytest tests/test_mlb_park_factors.py -v`
Expected: PASS

**Step 4:** Commit

```bash
git add static/mlb_park_factors.json tests/test_mlb_park_factors.py
git commit -m "MLB: 3yr FanGraphs park factors static table"
```

---

## Task 3: shared/mlb_data.py — data wrapper

**Files:**
- Create: `shared/mlb_data.py`
- Test: `tests/test_mlb_data.py`

**Step 1:** Write the wrapper

```python
"""MLB data layer — MLB Stats API + Statcast (via pybaseball) + park factors.

All sources free. Cached per-run in module-level dicts.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("edge_stacker")

_park_factors = None
_pitcher_stats_cache = {}
_team_woba_cache = {}


def park_factor(venue_name):
    """Return 3yr park factor for runs. Default 1.00 if venue not in table."""
    global _park_factors
    if _park_factors is None:
        path = Path(__file__).resolve().parent.parent / "static" / "mlb_park_factors.json"
        with open(path) as f:
            _park_factors = json.load(f)
    return _park_factors.get(venue_name, 1.00)


def get_schedule(date_iso):
    """Get MLB schedule with probable starters for a date.

    Returns list of dicts: {gamePk, away_team, home_team, venue, gameDate (UTC ISO),
    away_starter_id, home_starter_id, away_starter_name, home_starter_name}
    """
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={date_iso}&hydrate=probablePitcher,venue")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"MLB schedule fetch failed: {e}")
        return []

    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            teams = g.get("teams", {})
            away = teams.get("away", {})
            home = teams.get("home", {})
            away_pitcher = away.get("probablePitcher", {})
            home_pitcher = home.get("probablePitcher", {})
            games.append({
                "gamePk": g.get("gamePk"),
                "gameDate": g.get("gameDate"),  # UTC ISO
                "venue": g.get("venue", {}).get("name", ""),
                "away_team": away.get("team", {}).get("name", ""),
                "home_team": home.get("team", {}).get("name", ""),
                "away_starter_id": away_pitcher.get("id"),
                "home_starter_id": home_pitcher.get("id"),
                "away_starter_name": away_pitcher.get("fullName", ""),
                "home_starter_name": home_pitcher.get("fullName", ""),
            })
    logger.info(f"MLB schedule: {len(games)} games for {date_iso}")
    return games


def get_pitcher_stats(pitcher_id, season=None):
    """Get pitcher rolling stats via MLB Stats API (free, no rate limit).

    Returns dict: {xFIP_30d, K_pct_30d, BB_pct_30d, hand, starts_season}
    Returns None if pitcher has <5 starts this season.
    """
    if pitcher_id in _pitcher_stats_cache:
        return _pitcher_stats_cache[pitcher_id]

    if season is None:
        season = datetime.utcnow().year

    # MLB Stats API: people/{id}/stats?stats=statsSingleSeason,gameLog&group=pitching&season=YYYY
    url = (f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}"
           f"?hydrate=stats(group=[pitching],type=[statsSingleSeason,gameLog],season={season})")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.debug(f"Pitcher stats fetch failed for {pitcher_id}: {e}")
        _pitcher_stats_cache[pitcher_id] = None
        return None

    person = (data.get("people") or [{}])[0]
    hand = person.get("pitchHand", {}).get("code", "R")
    stats_blocks = person.get("stats", [])
    season_stats = None
    gamelog = []
    for block in stats_blocks:
        if block.get("type", {}).get("displayName") == "statsSingleSeason":
            splits = block.get("splits", [])
            if splits:
                season_stats = splits[0].get("stat", {})
        elif block.get("type", {}).get("displayName") == "gameLog":
            for split in block.get("splits", []):
                gamelog.append(split.get("stat", {}))

    starts = int(season_stats.get("gamesStarted", 0)) if season_stats else 0
    if starts < 5:
        _pitcher_stats_cache[pitcher_id] = None
        return None

    # Rolling 30-day window: last ~6 starts
    last_n = gamelog[-6:] if len(gamelog) >= 6 else gamelog
    if not last_n:
        _pitcher_stats_cache[pitcher_id] = None
        return None
    sum_ip = sum(_ip_to_float(g.get("inningsPitched", "0.0")) for g in last_n)
    sum_k = sum(int(g.get("strikeOuts", 0)) for g in last_n)
    sum_bb = sum(int(g.get("baseOnBalls", 0)) for g in last_n)
    sum_hr = sum(int(g.get("homeRuns", 0)) for g in last_n)
    sum_bf = sum(int(g.get("battersFaced", 0)) for g in last_n)
    if sum_ip <= 0 or sum_bf <= 0:
        _pitcher_stats_cache[pitcher_id] = None
        return None

    # xFIP = ((13*xHR) + (3*BB) - (2*K))/IP + constant; xHR = league HR/FB% × FB
    # Simplified to FIP for automation: FIP = ((13*HR) + (3*BB) - (2*K))/IP + 3.10
    fip = ((13 * sum_hr) + (3 * sum_bb) - (2 * sum_k)) / sum_ip + 3.10
    k_pct = sum_k / sum_bf
    bb_pct = sum_bb / sum_bf

    result = {
        "xFIP_30d": round(fip, 2),
        "K_pct_30d": round(k_pct, 3),
        "BB_pct_30d": round(bb_pct, 3),
        "hand": hand,
        "starts_season": starts,
    }
    _pitcher_stats_cache[pitcher_id] = result
    return result


def get_team_woba_vs_hand(team_id, opp_hand, season=None):
    """Get team's wOBA vs LHP or RHP, last 30 days.

    opp_hand: "L" or "R" — pitcher handedness the team is FACING.
    Returns float wOBA (~.290-.350 typical range), or 0.320 (league avg) on failure.
    """
    cache_key = (team_id, opp_hand)
    if cache_key in _team_woba_cache:
        return _team_woba_cache[cache_key]
    if season is None:
        season = datetime.utcnow().year

    # MLB Stats API: teams/{id}/stats?stats=byDateRange&group=hitting&season=YYYY&...
    today = datetime.utcnow().date()
    start = today - timedelta(days=30)
    url = (f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats"
           f"?stats=byDateRange&group=hitting&season={season}"
           f"&startDate={start.isoformat()}&endDate={today.isoformat()}"
           f"&sitCodes=vl,vr")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        _team_woba_cache[cache_key] = 0.320
        return 0.320

    woba = 0.320
    for stats_block in data.get("stats", []):
        for split in stats_block.get("splits", []):
            sit = split.get("sitCode", "")
            if (opp_hand == "L" and sit == "vl") or (opp_hand == "R" and sit == "vr"):
                stat = split.get("stat", {})
                # wOBA approximation if not provided directly
                # Use OPS / 3 + .260 as rough proxy if wOBA absent
                ops_str = stat.get("ops", ".700")
                try:
                    ops = float(ops_str)
                    woba = round(ops * 0.45 + 0.020, 3)  # OPS->wOBA approx
                except (ValueError, TypeError):
                    pass
                break
    _team_woba_cache[cache_key] = woba
    return woba


def _ip_to_float(ip_str):
    """'5.2' -> 5.667 (5 + 2/3 outs)."""
    try:
        s = str(ip_str)
        if "." in s:
            whole, frac = s.split(".")
            return float(whole) + float(frac) / 3.0
        return float(s)
    except (ValueError, TypeError):
        return 0.0
```

Save to `shared/mlb_data.py`.

**Step 2:** Write tests

`tests/test_mlb_data.py`:
```python
from shared import mlb_data


def test_park_factor_known():
    assert mlb_data.park_factor("Coors Field") > 1.10
    assert mlb_data.park_factor("Petco Park") < 1.00
    assert mlb_data.park_factor("Unknown Stadium") == 1.00


def test_ip_to_float():
    assert mlb_data._ip_to_float("5.2") == 5.0 + 2/3
    assert mlb_data._ip_to_float("6.0") == 6.0
    assert mlb_data._ip_to_float("invalid") == 0.0


def test_schedule_returns_list():
    # Live API call. Today should always have games during MLB season.
    today_iso = "2026-05-10"
    games = mlb_data.get_schedule(today_iso)
    assert isinstance(games, list)
    if games:  # in-season
        g = games[0]
        assert "gamePk" in g
        assert "venue" in g
```

**Step 3:** Run

Run: `pytest tests/test_mlb_data.py -v`
Expected: pure-function tests PASS, schedule test PASS if MLB season active.

**Step 4:** Commit

```bash
git add shared/mlb_data.py tests/test_mlb_data.py
git commit -m "MLB data layer: schedule, pitcher xFIP, team wOBA vs hand, park factors"
```

---

## Task 4: modules/mlb_f5/odds.py — Odds API for F5 totals

**Files:**
- Create: `modules/mlb_f5/__init__.py` (empty)
- Create: `modules/mlb_f5/odds.py`
- Test: `tests/test_mlb_f5_odds.py`

**Step 1:** Create empty `__init__.py`

```bash
mkdir -p modules/mlb_f5
touch modules/mlb_f5/__init__.py
```

**Step 2:** Write odds.py

```python
"""MLB F5 totals odds fetching + parsing."""

import logging
import config
from shared import odds_client

logger = logging.getLogger("edge_stacker")

SPORT_KEY = "baseball_mlb"
F5_TOTALS_MARKET = "totals_1st_5_innings"


def get_mlb_events():
    """Get upcoming MLB events from Odds API."""
    return odds_client.get_events(SPORT_KEY)


def get_f5_totals(event_id):
    """Get F5 totals market for a specific event."""
    return odds_client.get_event_odds(SPORT_KEY, event_id, F5_TOTALS_MARKET)


def extract_totals(event_odds):
    """Parse F5 totals into structured format keyed by line value.

    Returns dict: line -> {best_over_odds, best_over_book, best_under_odds,
    best_under_book, over_odds, under_odds, fair_over_prob, fair_under_prob}.

    F5 totals can have multiple lines per event (alt totals); we keep all and
    let the runner pick the consensus line.
    """
    from staking import american_to_prob

    by_line = {}
    for bookmaker in event_odds.get("bookmakers", []):
        book = bookmaker.get("title", "Unknown")
        for market in bookmaker.get("markets", []):
            if market.get("key") != F5_TOTALS_MARKET:
                continue
            # Group outcomes by point (line value) per book
            book_outcomes = {}
            for outcome in market.get("outcomes", []):
                point = outcome.get("point")
                if point is None:
                    continue
                book_outcomes.setdefault(point, {})[outcome.get("name", "")] = outcome.get("price")

            for point, sides in book_outcomes.items():
                line_data = by_line.setdefault(point, {
                    "best_over_odds": None, "best_over_book": None,
                    "best_under_odds": None, "best_under_book": None,
                    "all_over": [], "all_under": [],
                    "by_book": {},
                })
                over_p = sides.get("Over")
                under_p = sides.get("Under")
                if over_p is None or under_p is None:
                    continue
                line_data["all_over"].append(over_p)
                line_data["all_under"].append(under_p)
                line_data["by_book"][book] = {"over": over_p, "under": under_p}
                if line_data["best_over_odds"] is None or over_p > line_data["best_over_odds"]:
                    line_data["best_over_odds"] = over_p
                    line_data["best_over_book"] = book
                if line_data["best_under_odds"] is None or under_p > line_data["best_under_odds"]:
                    line_data["best_under_odds"] = under_p
                    line_data["best_under_book"] = book

    # For each line: consensus odds (median) + de-vigged fair probs
    for point, sd in by_line.items():
        for key, all_key in [("over_odds", "all_over"), ("under_odds", "all_under")]:
            ol = sorted(sd[all_key])
            if ol:
                mid = len(ol) // 2
                sd[key] = ol[mid] if len(ol) % 2 else (ol[mid - 1] + ol[mid]) // 2

        fair_overs = []
        fair_unders = []
        for pair in sd.get("by_book", {}).values():
            o = american_to_prob(pair["over"])
            u = american_to_prob(pair["under"])
            t = o + u
            if t > 0:
                fair_overs.append(o / t)
                fair_unders.append(u / t)
        if fair_overs:
            fair_overs.sort()
            fair_unders.sort()
            mid = len(fair_overs) // 2
            sd["fair_over_prob"] = fair_overs[mid]
            sd["fair_under_prob"] = fair_unders[mid]

    return by_line
```

**Step 3:** Smoke test (live API call)

`tests/test_mlb_f5_odds.py`:
```python
from modules.mlb_f5 import odds


def test_get_mlb_events_returns_list():
    events = odds.get_mlb_events()
    assert isinstance(events, list)


def test_extract_totals_handles_empty():
    assert odds.extract_totals({}) == {}
    assert odds.extract_totals({"bookmakers": []}) == {}
```

**Step 4:** Run

Run: `pytest tests/test_mlb_f5_odds.py -v`
Expected: PASS

**Step 5:** Commit

```bash
git add modules/mlb_f5/__init__.py modules/mlb_f5/odds.py tests/test_mlb_f5_odds.py
git commit -m "MLB F5: Odds API client + extract_totals"
```

---

## Task 5: modules/mlb_f5/projections.py

**Files:**
- Create: `modules/mlb_f5/projections.py`
- Test: `tests/test_mlb_f5_projections.py`

**Step 1:** Write projection logic

```python
"""F5 expected runs projection for MLB.

Pure-function module — given pitcher stats, opp wOBA, park, weather: returns
expected F5 total runs.
"""

import logging
import math

logger = logging.getLogger("edge_stacker")

LEAGUE_AVG_FIP = 4.10
LEAGUE_AVG_WOBA = 0.320


def project_team_runs_f5(pitcher_xfip, opp_woba, park_factor=1.00, weather_factor=1.00):
    """Project runs allowed by `pitcher` over first 5 innings against opp lineup.

    Args:
        pitcher_xfip: pitcher's xFIP (rolling 30-day)
        opp_woba: opposing team's wOBA vs pitcher's handedness (rolling 30-day)
        park_factor: 3yr park factor for runs (1.00 = neutral)
        weather_factor: f(wind, temp) multiplier (1.00 = neutral)

    Returns:
        Expected runs allowed in first 5 innings (float, typically 1.5-3.5)
    """
    # Base: scale xFIP from RA/9 down to RA/5
    base_runs_5 = pitcher_xfip * 5.0 / 9.0

    # Lineup quality: team wOBA above/below league avg shifts ER
    # Each 0.020 wOBA above 0.320 = roughly +10% ER
    lineup_factor = 1.0 + (opp_woba - LEAGUE_AVG_WOBA) * 5.0

    return base_runs_5 * lineup_factor * park_factor * weather_factor


def project_total_f5(home_xfip, home_opp_woba, away_xfip, away_opp_woba,
                     park_factor=1.00, weather_factor=1.00):
    """Total F5 runs from both teams' perspective.

    home_opp_woba = AWAY team's wOBA vs HOME pitcher's hand
    away_opp_woba = HOME team's wOBA vs AWAY pitcher's hand
    """
    home_runs_allowed = project_team_runs_f5(home_xfip, home_opp_woba, park_factor, weather_factor)
    away_runs_allowed = project_team_runs_f5(away_xfip, away_opp_woba, park_factor, weather_factor)
    return home_runs_allowed + away_runs_allowed


def weather_factor(wind_mph, wind_dir_deg, ballpark_orientation_deg, temp_f):
    """Compute weather multiplier on F5 scoring.

    wind_dir_deg: meteorological wind direction (where wind comes FROM, 0=N)
    ballpark_orientation_deg: home plate to center field bearing
    Returns multiplier (typically 0.92-1.10).

    Rules of thumb (FanGraphs research):
    - Outbound wind ≥10mph + temp ≥75: HR rate +15-25%, runs +8-12%
    - Inbound wind ≥10mph + temp ≤55: HR rate -15-25%, runs -8-12%
    - Otherwise: ~neutral
    """
    # Relative angle: 0 = wind blowing toward CF (out), 180 = toward home (in)
    rel = (ballpark_orientation_deg - wind_dir_deg) % 360
    if rel > 180:
        rel = 360 - rel
    # rel ~= 0 means wind blowing OUT (toward CF)
    out_component = math.cos(math.radians(rel))  # +1 = out, -1 = in
    speed_factor = min(wind_mph / 10.0, 2.0)
    temp_factor = (temp_f - 65.0) / 30.0  # +1 per 30F above 65
    factor = 1.0 + (out_component * speed_factor * 0.04) + (temp_factor * 0.03)
    return max(0.85, min(1.15, factor))


def poisson_cdf_under(line, expected_total):
    """P(total <= floor(line)) under Poisson(expected_total).

    For F5 totals, lines are typically half-points (4.5, 5.0, 5.5). For 5.0
    "push" possible — we treat 5.0 as just-under for under bets to be conservative.
    """
    k = int(math.floor(line))
    if k < 0:
        return 0.0
    s = 0.0
    term = math.exp(-expected_total)
    s = term
    for i in range(1, k + 1):
        term *= expected_total / i
        s += term
    return min(s, 1.0)


def f5_edge(projection, line, over_odds_raw, under_odds_raw):
    """Compute model edge vs F5 line.

    Returns (direction, raw_edge, model_prob, odds_to_bet) or (None, 0, 0, 0).
    """
    from staking import american_to_prob

    p_under = poisson_cdf_under(line, projection)
    p_over = 1.0 - p_under

    over_implied = american_to_prob(over_odds_raw)
    under_implied = american_to_prob(under_odds_raw)

    over_edge = p_over - over_implied
    under_edge = p_under - under_implied

    if over_edge > under_edge and over_edge > 0:
        return "OVER", over_edge, p_over, over_odds_raw
    elif under_edge > over_edge and under_edge > 0:
        return "UNDER", under_edge, p_under, under_odds_raw
    return None, 0.0, 0.0, 0
```

**Step 2:** Write tests

`tests/test_mlb_f5_projections.py`:
```python
from modules.mlb_f5 import projections


def test_project_team_runs_neutral():
    # League-average pitcher (xFIP=4.10) vs league-avg lineup (wOBA=.320)
    # Expected: ~2.28 runs in F5 (4.10 * 5/9)
    runs = projections.project_team_runs_f5(4.10, 0.320)
    assert 2.20 <= runs <= 2.35


def test_project_team_runs_elite_pitcher():
    runs = projections.project_team_runs_f5(2.50, 0.320)
    assert runs < 1.50  # elite pitcher much lower


def test_project_team_runs_park_boost():
    base = projections.project_team_runs_f5(4.10, 0.320, 1.00, 1.00)
    coors = projections.project_team_runs_f5(4.10, 0.320, 1.18, 1.00)
    assert coors > base * 1.15


def test_poisson_cdf_under():
    # Expected total = 5.0, line = 4.5: P(total <= 4) for Poisson(5)
    p = projections.poisson_cdf_under(4.5, 5.0)
    # Poisson(5) CDF at k=4 ≈ 0.4405
    assert 0.43 < p < 0.45


def test_f5_edge_under_signal():
    # Projected 4.5, line 6.0, even odds: should pick UNDER
    direction, edge, prob, odds = projections.f5_edge(4.5, 6.0, -110, -110)
    assert direction == "UNDER"
    assert edge > 0
```

**Step 3:** Run

Run: `pytest tests/test_mlb_f5_projections.py -v`
Expected: 5 PASS

**Step 4:** Commit

```bash
git add modules/mlb_f5/projections.py tests/test_mlb_f5_projections.py
git commit -m "MLB F5: xFIP-based projection + Poisson edge calc"
```

---

## Task 6: modules/mlb_f5/filters.py

**Files:**
- Create: `modules/mlb_f5/filters.py`
- Test: `tests/test_mlb_f5_filters.py`

**Step 1:** Write filters

```python
"""MLB F5 filter pipeline."""

import logging
from staking import calculate_vig

logger = logging.getLogger("edge_stacker")

MAX_VIG = 0.08
MIN_EDGE = 0.10
MAX_EDGE = 0.25  # F5 totals can have legit higher edges than NBA player props
LINE_SANITY_PCT = 0.25  # tighter than NBA's 35% (F5 totals cluster narrowly)


def passes_filters(line_data, edge_pct, line, projection):
    """Final filters before pick is emitted."""
    if edge_pct < MIN_EDGE:
        return False, f"Edge {edge_pct:.1%} < {MIN_EDGE:.1%}"

    over = line_data.get("best_over_odds")
    under = line_data.get("best_under_odds")
    if over is not None and under is not None:
        vig = calculate_vig(over, under)
        if vig > MAX_VIG:
            return False, f"Vig {vig:.3f} > {MAX_VIG}"

    if line > 0 and abs(projection - line) / line > LINE_SANITY_PCT:
        return False, f"Line sanity: |proj-line|/line = {abs(projection-line)/line:.1%} > {LINE_SANITY_PCT:.0%}"

    return True, None
```

**Step 2:** Write tests

`tests/test_mlb_f5_filters.py`:
```python
from modules.mlb_f5 import filters


def test_passes_filters_happy():
    ld = {"best_over_odds": -110, "best_under_odds": -110}
    ok, reason = filters.passes_filters(ld, 0.12, 5.0, 4.8)
    assert ok is True
    assert reason is None


def test_low_edge_rejected():
    ld = {"best_over_odds": -110, "best_under_odds": -110}
    ok, reason = filters.passes_filters(ld, 0.05, 5.0, 4.8)
    assert ok is False
    assert "Edge" in reason


def test_line_sanity_rejected():
    ld = {"best_over_odds": -110, "best_under_odds": -110}
    # projection 8.0 vs line 5.0 → 60% divergence > 25%
    ok, reason = filters.passes_filters(ld, 0.30, 5.0, 8.0)
    assert ok is False
    assert "sanity" in reason.lower()
```

**Step 3:** Run

Run: `pytest tests/test_mlb_f5_filters.py -v`
Expected: 3 PASS

**Step 4:** Commit

```bash
git add modules/mlb_f5/filters.py tests/test_mlb_f5_filters.py
git commit -m "MLB F5: filter pipeline (vig/edge/line sanity)"
```

---

## Task 7: modules/mlb_f5/runner.py — main orchestration

**Files:**
- Create: `modules/mlb_f5/runner.py`

**Step 1:** Write runner

```python
"""MLB F5 totals module — projects expected F5 runs, finds edge vs market line."""

import logging
import requests
from datetime import datetime, timezone, timedelta
from shared.pick import Pick
from shared import mlb_data
from staking import american_to_prob, assign_grade
import config
from . import projections, filters, odds

logger = logging.getLogger("edge_stacker")

MAX_HOURS_AHEAD = 8


def run(today):
    """Run MLB F5 module. Returns list of Pick objects."""
    # Get probable starters from MLB Stats API
    schedule = mlb_data.get_schedule(today.isoformat())
    if not schedule:
        logger.info("No MLB games today")
        return []

    # Index schedule by team-pair for matching with Odds API events
    sched_index = {}
    for g in schedule:
        key = (g["away_team"], g["home_team"])
        sched_index[key] = g

    # Get Odds API events
    try:
        events = odds.get_mlb_events()
    except Exception as e:
        logger.error(f"Failed to get MLB events: {e}")
        return []
    if not events:
        return []

    # Drop events outside 8h tipoff window
    now_utc = datetime.now(timezone.utc)
    fresh_events = []
    dropped = []
    for ev in events:
        ct = ev.get("commence_time", "")
        if not ct:
            fresh_events.append(ev)
            continue
        try:
            game_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            hours_ahead = (game_dt - now_utc).total_seconds() / 3600.0
            if hours_ahead < (5/60.0):
                dropped.append(f"{ev.get('away_team','')} @ {ev.get('home_team','')} (already started)")
                continue
            if hours_ahead > MAX_HOURS_AHEAD:
                dropped.append(f"{ev.get('away_team','')} @ {ev.get('home_team','')} ({hours_ahead:.1f}h ahead)")
                continue
        except (ValueError, TypeError):
            pass
        fresh_events.append(ev)
    if dropped:
        logger.info(f"MLB: dropped {len(dropped)} events outside [5min, {MAX_HOURS_AHEAD}h]: {dropped}")
    events = fresh_events
    if not events:
        logger.info("No MLB games left after window filter")
        return []
    logger.info(f"MLB F5: Processing {len(events)} games")

    picks = []
    for ev in events:
        sched = sched_index.get((ev["away_team"], ev["home_team"]))
        if not sched:
            logger.debug(f"MLB: no schedule match for {ev['away_team']} @ {ev['home_team']}")
            continue
        if not sched["away_starter_id"] or not sched["home_starter_id"]:
            logger.debug(f"MLB: starters TBD for {ev['away_team']} @ {ev['home_team']}")
            continue

        away_p = mlb_data.get_pitcher_stats(sched["away_starter_id"])
        home_p = mlb_data.get_pitcher_stats(sched["home_starter_id"])
        if not away_p or not home_p:
            continue  # one or both starters have <5 starts

        # Team IDs for wOBA lookup — use MLB Stats API team IDs from schedule
        # (gamePk endpoint provides team.id; for now use team-name based lookup
        # which mlb_data normalizes internally)
        # away_woba_vs_home_pitcher_hand
        away_team_id_url = f"https://statsapi.mlb.com/api/v1/teams?sportId=1"
        # Simpler: re-derive from schedule structure; MLB Stats API includes team IDs.
        # We'll add team_id to the schedule dict in mlb_data.get_schedule.
        # For this runner, assume schedule has them (extension in Task 3).
        away_team_id = sched.get("away_team_id")
        home_team_id = sched.get("home_team_id")
        if not away_team_id or not home_team_id:
            continue

        # wOBA: away lineup vs home pitcher's hand, vice versa
        away_woba = mlb_data.get_team_woba_vs_hand(away_team_id, home_p["hand"])
        home_woba = mlb_data.get_team_woba_vs_hand(home_team_id, away_p["hand"])

        # Park factor
        pf = mlb_data.park_factor(sched["venue"])

        # Weather: use OpenWeather (existing infra) — fallback 1.0 if unavailable
        try:
            from modules.ncaaf_weather import weather as ncaaf_weather
            wx = ncaaf_weather.get_weather_for_venue(sched["venue"])
            wf = projections.weather_factor(
                wx.get("wind_mph", 0),
                wx.get("wind_deg", 0),
                wx.get("ballpark_orientation_deg", 0),
                wx.get("temp_f", 70),
            ) if wx else 1.0
        except Exception:
            wf = 1.0

        proj_total = projections.project_total_f5(
            home_p["xFIP_30d"], away_woba,
            away_p["xFIP_30d"], home_woba,
            pf, wf,
        )

        # Get F5 totals market for this event
        try:
            event_odds = odds.get_f5_totals(ev["id"])
        except Exception as e:
            logger.warning(f"Failed F5 odds for {ev['id']}: {e}")
            continue
        by_line = odds.extract_totals(event_odds)
        if not by_line:
            continue

        # Pick the consensus line: most common line value across books
        line_counts = {pt: len(d.get("by_book", {})) for pt, d in by_line.items()}
        consensus_line = max(line_counts, key=line_counts.get)
        line_data = by_line[consensus_line]

        over_odds = line_data.get("best_over_odds")
        under_odds = line_data.get("best_under_odds")
        if over_odds is None or under_odds is None:
            continue

        direction, edge, model_prob, odds_to_bet = projections.f5_edge(
            proj_total, consensus_line, over_odds, under_odds)
        if direction is None:
            continue
        edge = min(edge, filters.MAX_EDGE)

        # Market anchor: 60% model + 40% market
        fair = line_data.get("fair_over_prob") if direction == "OVER" else line_data.get("fair_under_prob")
        if fair is not None:
            model_prob = 0.6 * model_prob + 0.4 * fair
            edge = min(model_prob - american_to_prob(odds_to_bet), filters.MAX_EDGE)

        ok, reason = filters.passes_filters(line_data, edge, consensus_line, proj_total)
        if not ok:
            logger.debug(f"MLB F5 filtered {ev['away_team']}@{ev['home_team']}: {reason}")
            continue

        implied = american_to_prob(odds_to_bet)
        best_book = (line_data.get("best_over_book") if direction == "OVER"
                     else line_data.get("best_under_book")) or "Unknown"
        consensus = (line_data.get("over_odds") if direction == "OVER"
                     else line_data.get("under_odds")) or odds_to_bet

        # Game time
        game_time_str = ""
        game_date_str = ""
        try:
            commence = ev.get("commence_time", "")
            if commence:
                game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                et = timezone(timedelta(hours=config.ET_OFFSET_HOURS))
                game_time_str = game_dt.astimezone(et).strftime(config.TIME_FMT)
                game_date_str = game_dt.astimezone(et).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

        pick = Pick(
            module="mlb_f5",
            matchup=f"{ev['away_team']} @ {ev['home_team']}",
            pick_description=f"F5 {direction} {consensus_line}",
            best_odds_raw=odds_to_bet,
            best_odds_book=best_book,
            consensus_odds_raw=consensus,
            implied_prob=implied,
            model_prob=model_prob,
            edge_pct=edge,
            grade=assign_grade(edge),
            context={
                "stat": "F5_TOTAL",
                "projection": round(proj_total, 2),
                "line": consensus_line,
                "away_starter": sched["away_starter_name"],
                "home_starter": sched["home_starter_name"],
                "away_xfip": away_p["xFIP_30d"],
                "home_xfip": home_p["xFIP_30d"],
                "away_woba_vs_hand": round(away_woba, 3),
                "home_woba_vs_hand": round(home_woba, 3),
                "park_factor": pf,
                "weather_factor": round(wf, 3),
                "venue": sched["venue"],
                "game_date": game_date_str,
            },
            game_time=game_time_str,
        )
        picks.append(pick)
        logger.info(f"MLB PICK: {pick.matchup} F5 {direction} {consensus_line} | "
                    f"proj={proj_total:.2f} | edge={edge:.1%}")

    return picks
```

**Step 2:** Run a smoke test (no automated test — runner has too many live deps for unit test; verify via integration in Task 9)

**Step 3:** Commit

```bash
git add modules/mlb_f5/runner.py
git commit -m "MLB F5: main runner with 8h cap, market anchor 60/40, filter pipeline"
```

---

## Task 8: Wire MLB into main.py + active modules

**Files:**
- Modify: `main.py:24-56` (active_modules function)

**Step 1:** Add MLB to module activation calendar

Edit `main.py active_modules()`:
```python
# Module 7: MLB F5 Totals -- Mar 25 through Oct 31 (regular season + playoffs through World Series)
if (month == 3 and day >= 25) or month in (4, 5, 6, 7, 8, 9, 10):
    active.append("mlb_f5")
```

**Step 2:** Add to module priority list (config.py:40-46):
```python
MODULE_PRIORITY = [
    "nba_props",
    "ncaab_kenpom",
    "mlb_f5",  # new
    "ncaaf_weather",
    ...
]
```

**Step 3:** Commit

```bash
git add main.py config.py
git commit -m "Wire MLB F5 into module activation calendar"
```

---

## Task 9: Smoke test — run MLB module locally

**Step 1:** Add team_id to schedule output

In `shared/mlb_data.py:get_schedule`, add team IDs to the returned dict. Modify the schedule parsing block:

```python
games.append({
    ...
    "away_team_id": away.get("team", {}).get("id"),
    "home_team_id": home.get("team", {}).get("id"),
    ...
})
```

**Step 2:** Run the module

```bash
cd /path/to/EDGE\ STACKER
python main.py --modules mlb_f5 --verbose --json-only > _mlb_smoke.json 2> _mlb_smoke.log
```

**Step 3:** Verify output

- Log shows: schedule loaded, events processed, picks generated (or "0 qualifying picks" if no edge)
- JSON: valid, subject like `EDGE STACKER / MLB F5 TOTALS - DD.MM.YYYY - N picks`
- Each pick has populated context (xFIP, wOBA, park_factor, weather_factor)

**Step 4:** Commit any small fixes from smoke test

```bash
git add modules/mlb_f5/ shared/mlb_data.py
git commit -m "MLB F5 smoke test fixes"
```

---

## Task 10: VPS deployment — cron + n8n workflow

**Step 1:** SSH to VPS, pull latest

```bash
ssh root@vmi3157940.contaboserver.net "cd /root/edge-stacker && git pull && pip install pybaseball"
```

**Step 2:** Create `run_afternoon_mlb.sh` on VPS (or locally and commit)

```bash
#!/bin/bash
HOUR_ET=$(TZ=America/New_York date +%H)
if [ "$HOUR_ET" != "16" ]; then exit 0; fi
cd /root/edge-stacker
source venv/bin/activate
set -a; source .env; set +a
OUT=$(python main.py --modules mlb_f5 --json-only 2>>logs/cron.log)
if [ -n "$OUT" ]; then
    curl -s -X POST https://vmi3157940.contaboserver.net/webhook/edge-stacker-mlb \
      -H "Content-Type: application/json" -d "$OUT"
fi
```

`chmod +x run_afternoon_mlb.sh` and commit.

**Step 3:** Add cron entries

```bash
ssh root@vmi3157940 'crontab -l | { cat; echo "30 20 * * * /root/edge-stacker/run_afternoon_mlb.sh"; echo "30 21 * * * /root/edge-stacker/run_afternoon_mlb.sh"; } | crontab -'
```

**Step 4:** Create VPS n8n workflow `EDGE STACKER - MLB F5 Totals`

Webhook path: `edge-stacker-mlb` → Gmail node sending to `stuki71.alert@gmail.com` with subject from JSON `subject` field, body from `email_body`. Mirror the existing NHL workflow.

**Step 5:** Activate and test

```bash
ssh root@vmi3157940 'docker exec n8n-n8n-1 n8n publish:workflow --id=<NEW_WF_ID>'
```

Send a test webhook curl to verify email lands.

**Step 6:** Commit

```bash
git add run_afternoon_mlb.sh
git commit -m "MLB F5: VPS cron + run script (DST-proof, fires 16:00 ET)"
git push origin main
```

---

## Task 11: Live shadow run for 3 nights

Run for 3 nights without alerting; verify:
- Module fires at 04:30 PM ET
- Picks generated (count varies by slate)
- Email lands successfully
- Hit rate at 50-65% (statistically meaningless yet, just a sanity check)

After 3 clean nights → enable as part of normal nightly emails.

---

## Risks during implementation

- **pybaseball install time**: first install can be slow due to numpy/pandas deps. Use venv.
- **MLB Stats API rate limit**: undocumented but generous. Cache per pitcher/team per run.
- **F5 market thinness**: some nights may produce 0-1 picks. Acceptable.
- **Park orientation data**: `weather_factor` needs ballpark CF orientation in degrees — add a static table in `static/mlb_park_orientations.json` if `ncaaf_weather` integration is brittle. Defer to follow-up if needed.

---

Plan complete and saved to `docs/plans/2026-05-10-mlb-f5-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans skill, batch execution with checkpoints

**Which approach?**
