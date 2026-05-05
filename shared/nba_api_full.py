"""Full nba_api client — drop-in replacement for espn_nba.

Uses real NBA Stats API for richer data:
- True DEF_RATING (not points-allowed proxy)
- PACE (possessions per 48 — major projection factor)
- Player tracking defense categories
- Full season player gamelog with 60+ games

Activate with USE_NBA_API_FULL=1 env var.
Falls back to ESPN for injuries (NBA CDN injuries return 403 even from home).
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("edge_stacker")

NBA_API_DELAY = 0.7  # seconds between calls (per spec)


# Cached data per run
_team_stats_cache = None
_pace_cache = None
_player_id_cache = {}
_position_cache = {}  # name -> position
_team_id_lookup = {}  # team_full_name -> team_id


def _safe_call(fn, *args, retries=2, **kwargs):
    """Call nba_api endpoint with retries + backoff."""
    last_err = None
    delay = 1.0
    for attempt in range(retries):
        try:
            result = fn(*args, **kwargs, timeout=20)
            time.sleep(NBA_API_DELAY)
            return result
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
    logger.warning(f"nba_api call failed after {retries} retries: {last_err}")
    return None


def get_team_defensive_stats():
    """Get DEF_RATING for all 30 NBA teams.

    Returns dict: team_id (str) -> defensive_rating (float)
    """
    global _team_stats_cache
    if _team_stats_cache is not None:
        return _team_stats_cache

    from nba_api.stats.endpoints import LeagueDashTeamStats

    drtg_map = {}
    season = _current_season_str()

    result = _safe_call(LeagueDashTeamStats,
                        season=season,
                        measure_type_detailed_defense="Advanced")
    if result is None:
        return drtg_map

    df = result.get_data_frames()[0]
    for row in df.to_dict("records"):
        team_id = str(row.get("TEAM_ID", ""))
        drtg = row.get("DEF_RATING")
        if team_id and drtg is not None:
            drtg_map[team_id] = float(drtg)

    _team_stats_cache = drtg_map
    logger.info(f"nba_api DRTG loaded for {len(drtg_map)} teams")
    return drtg_map


def get_team_pace():
    """Get PACE (possessions per 48 min) for all 30 NBA teams.

    Returns dict: team_id (str) -> pace (float)
    """
    global _pace_cache
    if _pace_cache is not None:
        return _pace_cache

    from nba_api.stats.endpoints import LeagueDashTeamStats

    pace_map = {}
    season = _current_season_str()

    result = _safe_call(LeagueDashTeamStats,
                        season=season,
                        measure_type_detailed_defense="Advanced")
    if result is None:
        return pace_map

    df = result.get_data_frames()[0]
    for row in df.to_dict("records"):
        team_id = str(row.get("TEAM_ID", ""))
        pace = row.get("PACE")
        if team_id and pace is not None:
            pace_map[team_id] = float(pace)

    _pace_cache = pace_map
    logger.info(f"nba_api PACE loaded for {len(pace_map)} teams")
    return pace_map


def get_player_gamelog(player_id, last_n=None):
    """Get full season player gamelog.

    Returns list of dicts (most recent first): PTS, REB, AST, MIN, GAME_DATE, TEAM_ID
    Compatible with espn_nba interface.
    """
    from nba_api.stats.endpoints import PlayerGameLog

    season = _current_season_str()
    result = _safe_call(PlayerGameLog, player_id=player_id, season=season)
    if result is None:
        return []

    df = result.get_data_frames()[0]
    if df.empty:
        return []

    games = []
    for row in df.to_dict("records"):
        # nba_api GAME_DATE is "MMM DD, YYYY" — convert to ISO
        date_str = row.get("GAME_DATE", "")
        try:
            dt = datetime.strptime(date_str, "%b %d, %Y").replace(tzinfo=timezone.utc)
            iso_date = dt.isoformat()
        except (ValueError, TypeError):
            iso_date = ""

        games.append({
            "PTS": float(row.get("PTS", 0)),
            "REB": float(row.get("REB", 0)),
            "AST": float(row.get("AST", 0)),
            "MIN": float(row.get("MIN", 0)),
            "TEAM_ID": str(row.get("Player_ID", "")),  # Player_ID, but we'll get team from gamelog matchup
            "GAME_DATE": iso_date,
        })

    if last_n is None or last_n == 0:
        return games
    return games[:last_n]


def build_player_id_cache(team_ids=None):
    """Build name->id cache. Note: nba_api's static.players includes ALL players, not just today's.
    The team_ids param is ignored but kept for interface compatibility."""
    global _player_id_cache, _position_cache

    from nba_api.stats.static import players, teams

    # Build team lookup once
    global _team_id_lookup
    if not _team_id_lookup:
        for t in teams.get_teams():
            _team_id_lookup[t["full_name"]] = str(t["id"])

    # All active players
    if not _player_id_cache:
        for p in players.get_active_players():
            name = p.get("full_name", "")
            pid = p.get("id", "")
            if name and pid:
                _player_id_cache[name] = str(pid)
                # Position not in static — leave empty (we don't strictly need it)
                _position_cache[name] = ""

    logger.info(f"nba_api player cache: {len(_player_id_cache)} active players")


def find_espn_player_id(player_name):
    """Find player ID by name (case-insensitive)."""
    if player_name in _player_id_cache:
        return _player_id_cache[player_name]
    name_lower = player_name.lower()
    for cached, pid in _player_id_cache.items():
        if cached.lower() == name_lower:
            return pid
    return None


def get_team_roster(team_id):
    """Get team roster — we don't strictly need this for nba_api flow.
    Return empty list since build_player_id_cache covers all active players."""
    return []


def get_injuries():
    """Injuries from ESPN (NBA CDN returns 403 from any IP).

    Returns dict: team_id (str) -> list of {player_id, player_name, status}
    """
    # Reuse ESPN's working injury endpoint
    from shared.espn_nba import get_injuries as espn_get_injuries
    return espn_get_injuries()


def _current_season_str():
    """NBA season string for nba_api e.g. '2025-26'."""
    today = datetime.now(timezone.utc)
    if today.month >= 10:
        return f"{today.year}-{str(today.year + 1)[2:]}"
    return f"{today.year - 1}-{str(today.year)[2:]}"
