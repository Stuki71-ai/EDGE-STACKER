"""MLB data layer — MLB Stats API + park factors.

All sources free, no external Python deps beyond requests. Cached per-run.
MLB Stats API has no documented rate limit but be courteous with caching.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger("edge_stacker")

_park_factors = None
_pitcher_stats_cache = {}
_team_woba_cache = {}
_fip_constant_cache = {}


def park_factor(venue_name):
    """3yr FanGraphs park factor for runs. 1.00 = neutral. Default 1.00 if unknown."""
    global _park_factors
    if _park_factors is None:
        path = Path(__file__).resolve().parent.parent / "static" / "mlb_park_factors.json"
        try:
            with open(path) as f:
                _park_factors = json.load(f)
        except Exception as e:
            logger.warning(f"Park factors load failed: {e}")
            _park_factors = {}
    return _park_factors.get(venue_name, 1.00)


def get_schedule(date_iso):
    """MLB schedule with probable starters + venue + team_ids for a date.

    Returns list of dicts: gamePk, gameDate (UTC ISO), venue,
    away_team, home_team, away_team_id, home_team_id,
    away_starter_id, home_starter_id, away_starter_name, home_starter_name.
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
                "gameDate": g.get("gameDate"),
                "venue": g.get("venue", {}).get("name", ""),
                "away_team": away.get("team", {}).get("name", ""),
                "home_team": home.get("team", {}).get("name", ""),
                "away_team_id": away.get("team", {}).get("id"),
                "home_team_id": home.get("team", {}).get("id"),
                "away_starter_id": away_pitcher.get("id"),
                "home_starter_id": home_pitcher.get("id"),
                "away_starter_name": away_pitcher.get("fullName", ""),
                "home_starter_name": home_pitcher.get("fullName", ""),
            })
    logger.info(f"MLB schedule: {len(games)} games for {date_iso}")
    return games


def get_pitcher_stats(pitcher_id, season=None):
    """Pitcher rolling stats via MLB Stats API.

    Returns dict {xFIP_30d, K_pct_30d, BB_pct_30d, hand, starts_season}
    or None if pitcher has fewer than 5 starts this season.

    xFIP approximated as FIP (free, no need for league HR/FB%):
        FIP = (13*HR + 3*BB - 2*K) / IP + 3.10
    """
    if pitcher_id in _pitcher_stats_cache:
        return _pitcher_stats_cache[pitcher_id]

    if season is None:
        season = datetime.utcnow().year

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
        type_name = block.get("type", {}).get("displayName", "")
        if type_name == "statsSingleSeason":
            splits = block.get("splits", [])
            if splits:
                season_stats = splits[0].get("stat", {})
        elif type_name == "gameLog":
            for split in block.get("splits", []):
                gamelog.append(split.get("stat", {}))

    starts = int(season_stats.get("gamesStarted", 0)) if season_stats else 0
    if starts < 5:
        _pitcher_stats_cache[pitcher_id] = None
        return None

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

    fip = ((13 * sum_hr) + (3 * sum_bb) - (2 * sum_k)) / sum_ip + fip_constant(season)
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
    """Team's wOBA vs LHP or RHP (season-to-date).

    opp_hand: 'L' or 'R' — handedness of pitcher the team is FACING.
    Returns float (typically .280-.360) or 0.320 (league avg) on failure.

    OPS->wOBA conversion: wOBA ~= 0.45 * OPS + 0.020 (rough but stable).
    """
    cache_key = (team_id, opp_hand)
    if cache_key in _team_woba_cache:
        return _team_woba_cache[cache_key]
    if season is None:
        season = datetime.utcnow().year

    url = (f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats"
           f"?stats=statSplits&group=hitting&season={season}"
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
            sit = split.get("split", {}).get("code", "")
            if (opp_hand == "L" and sit == "vl") or (opp_hand == "R" and sit == "vr"):
                stat = split.get("stat", {})
                ops_str = stat.get("ops", ".700")
                try:
                    ops = float(ops_str)
                    woba = round(ops * 0.45 + 0.020, 3)
                except (ValueError, TypeError):
                    pass
                break
    _team_woba_cache[cache_key] = woba
    return woba


def fip_constant(season=None):
    """Live league FIP constant for the given season.

    FIP = (13*HR + 3*BB - 2*K) / IP + constant, where `constant` is calibrated
    so league-avg FIP == league-avg ERA. Modern values range ~2.95-3.30.
    Hardcoding 3.10 is OK most years but can be off by ~0.10 in extremes.

    Returns float; falls back to 3.10 if API/parse fails.
    """
    if season is None:
        season = datetime.utcnow().year
    if season in _fip_constant_cache:
        return _fip_constant_cache[season]

    # The /stats endpoint returns the top-50 pitchers by default — NOT a league
    # aggregate. Instead query /teams/stats and sum across all 30 teams.
    url = (f"https://statsapi.mlb.com/api/v1/teams/stats"
           f"?stats=season&group=pitching&sportIds=1&season={season}")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"FIP constant fetch failed: {e} — using 3.10")
        _fip_constant_cache[season] = 3.10
        return 3.10

    splits = []
    for block in data.get("stats", []):
        splits.extend(block.get("splits", []))
    if not splits:
        _fip_constant_cache[season] = 3.10
        return 3.10

    # Sum across all teams to get true league aggregates.
    lg_ip = lg_hr = lg_bb = lg_k = 0.0
    lg_er = 0.0  # back-derived from each team's ERA × IP / 9
    for s in splits:
        stat = s.get("stat", {})
        try:
            ip = _ip_to_float(stat.get("inningsPitched", "0"))
            era = float(stat.get("era", "0"))
        except (ValueError, TypeError):
            continue
        lg_ip += ip
        lg_er += era * ip / 9.0
        lg_hr += int(stat.get("homeRuns", 0))
        lg_bb += int(stat.get("baseOnBalls", 0))
        lg_k += int(stat.get("strikeOuts", 0))
    if lg_ip <= 0:
        _fip_constant_cache[season] = 3.10
        return 3.10
    lg_era = lg_er * 9.0 / lg_ip

    constant = lg_era - ((13 * lg_hr + 3 * lg_bb - 2 * lg_k) / lg_ip)
    constant = round(constant, 3)
    # Sanity guard: known historical range is ~2.85-3.40
    if constant < 2.5 or constant > 3.6:
        logger.warning(f"FIP constant {constant} out of expected range — using 3.10")
        constant = 3.10
    logger.info(f"MLB FIP constant for {season}: {constant} (lgERA={lg_era}, lgIP={lg_ip:.1f})")
    _fip_constant_cache[season] = constant
    return constant


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
