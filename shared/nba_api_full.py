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


# Static map: NBA Stats team_id (10-digit) -> ESPN team_id (1-30).
# runner.py keys all team lookups by ESPN team_id (from ESPN scoreboard),
# so we re-key our nba_api dicts here to match. Without this translation,
# every drtg_map.get() and pace_map.get() falls back to defaults and the
# entire nba_api edge is silently lost.
NBA_STATS_TO_ESPN_TEAM_ID = {
    "1610612737": "1",   # Atlanta Hawks
    "1610612738": "2",   # Boston Celtics
    "1610612751": "17",  # Brooklyn Nets
    "1610612766": "30",  # Charlotte Hornets
    "1610612741": "4",   # Chicago Bulls
    "1610612739": "5",   # Cleveland Cavaliers
    "1610612742": "6",   # Dallas Mavericks
    "1610612743": "7",   # Denver Nuggets
    "1610612765": "8",   # Detroit Pistons
    "1610612744": "9",   # Golden State Warriors
    "1610612745": "10",  # Houston Rockets
    "1610612754": "11",  # Indiana Pacers
    "1610612746": "12",  # LA Clippers
    "1610612747": "13",  # Los Angeles Lakers
    "1610612763": "29",  # Memphis Grizzlies
    "1610612748": "14",  # Miami Heat
    "1610612749": "15",  # Milwaukee Bucks
    "1610612750": "16",  # Minnesota Timberwolves
    "1610612740": "3",   # New Orleans Pelicans
    "1610612752": "18",  # New York Knicks
    "1610612760": "25",  # Oklahoma City Thunder
    "1610612753": "19",  # Orlando Magic
    "1610612755": "20",  # Philadelphia 76ers
    "1610612756": "21",  # Phoenix Suns
    "1610612757": "22",  # Portland Trail Blazers
    "1610612758": "23",  # Sacramento Kings
    "1610612759": "24",  # San Antonio Spurs
    "1610612761": "28",  # Toronto Raptors
    "1610612762": "26",  # Utah Jazz
    "1610612764": "27",  # Washington Wizards
}

# Team abbreviation (from PlayerGameLog MATCHUP, e.g. "LAL @ BOS") -> ESPN team_id
TEAM_ABBREV_TO_ESPN_TEAM_ID = {
    "ATL": "1", "BOS": "2", "BKN": "17", "CHA": "30", "CHI": "4",
    "CLE": "5", "DAL": "6", "DEN": "7", "DET": "8", "GSW": "9",
    "HOU": "10", "IND": "11", "LAC": "12", "LAL": "13", "MEM": "29",
    "MIA": "14", "MIL": "15", "MIN": "16", "NOP": "3", "NYK": "18",
    "OKC": "25", "ORL": "19", "PHI": "20", "PHX": "21", "POR": "22",
    "SAC": "23", "SAS": "24", "TOR": "28", "UTA": "26", "WAS": "27",
}


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
    skipped_unmapped = 0
    for row in df.to_dict("records"):
        nba_stats_team_id = str(row.get("TEAM_ID", ""))
        drtg = row.get("DEF_RATING")
        # Translate NBA Stats team_id -> ESPN team_id so runner.py lookups hit
        espn_team_id = NBA_STATS_TO_ESPN_TEAM_ID.get(nba_stats_team_id)
        if espn_team_id and drtg is not None:
            drtg_map[espn_team_id] = float(drtg)
        elif drtg is not None:
            skipped_unmapped += 1

    if skipped_unmapped:
        logger.warning(f"nba_api DRTG: {skipped_unmapped} teams had no ESPN ID mapping")
    _team_stats_cache = drtg_map
    logger.info(f"nba_api DRTG loaded for {len(drtg_map)} teams (keyed by ESPN id)")
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
        nba_stats_team_id = str(row.get("TEAM_ID", ""))
        pace = row.get("PACE")
        # Translate NBA Stats team_id -> ESPN team_id for runner.py compatibility
        espn_team_id = NBA_STATS_TO_ESPN_TEAM_ID.get(nba_stats_team_id)
        if espn_team_id and pace is not None:
            pace_map[espn_team_id] = float(pace)

    _pace_cache = pace_map
    logger.info(f"nba_api PACE loaded for {len(pace_map)} teams (keyed by ESPN id)")
    return pace_map


def get_player_gamelog(player_id, last_n=None):
    """Get full season player gamelog (Regular Season + Playoffs combined).

    During playoffs, regular season ended weeks ago, so the recent-games
    filter (14-day window) would skip everyone if we only fetched Regular
    Season. ESPN's gamelog includes both season types by default; nba_api
    requires explicit calls for each, so we fetch both and merge.

    Returns list of dicts (most recent first): PTS, REB, AST, MIN, GAME_DATE, TEAM_ID
    Compatible with espn_nba interface.
    """
    from nba_api.stats.endpoints import PlayerGameLog
    import pandas as pd

    season = _current_season_str()
    frames = []
    for season_type in ("Regular Season", "Playoffs"):
        result = _safe_call(PlayerGameLog, player_id=player_id, season=season,
                            season_type_all_star=season_type)
        if result is None:
            continue
        df = result.get_data_frames()[0]
        if not df.empty:
            frames.append(df)

    if not frames:
        return []

    df = pd.concat(frames, ignore_index=True)
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

        # Derive player's team from MATCHUP field (e.g., "LAL @ BOS" or "LAL vs. BOS")
        # First 3 chars = player's team abbreviation. Translate to ESPN team_id.
        matchup = str(row.get("MATCHUP", ""))
        espn_team_id = ""
        if matchup:
            abbrev = matchup[:3].upper().strip()
            espn_team_id = TEAM_ABBREV_TO_ESPN_TEAM_ID.get(abbrev, "")

        games.append({
            "PTS": float(row.get("PTS", 0)),
            "REB": float(row.get("REB", 0)),
            "AST": float(row.get("AST", 0)),
            "MIN": float(row.get("MIN", 0)),
            "TEAM_ID": espn_team_id,  # ESPN team_id, matches runner.py home/away_team_id schema
            "GAME_DATE": iso_date,
        })

    # Sort most-recent-first across both season types (regular season + playoffs merged)
    games.sort(key=lambda g: g["GAME_DATE"], reverse=True)

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
