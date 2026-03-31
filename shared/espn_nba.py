"""ESPN-based NBA data client — replaces nba_api (which is blocked from VPS IPs)."""

import logging
import requests

logger = logging.getLogger("edge_stacker")

BASE = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

# ESPN gamelog labels: MIN, FG, FG%, 3PT, 3P%, FT, FT%, REB, AST, BLK, STL, PF, TO, PTS
GAMELOG_LABELS = ["MIN", "FG", "FG%", "3PT", "3P%", "FT", "FT%", "REB", "AST", "BLK", "STL", "PF", "TO", "PTS"]


def get_player_gamelog(espn_player_id, last_n=10):
    """Get player game log from ESPN.

    Returns:
        list of dicts with PTS, REB, AST, MIN, TEAM_ID keys (matching nba_api format)
    """
    try:
        resp = requests.get(f"{BASE}/athletes/{espn_player_id}/gamelog", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug(f"ESPN gamelog failed for player {espn_player_id}: {e}")
        return []

    labels = data.get("labels", GAMELOG_LABELS)

    # Find label indices
    idx = {}
    for i, lbl in enumerate(labels):
        idx[lbl] = i

    games = []
    for season_type in data.get("seasonTypes", []):
        if "Regular" not in season_type.get("displayName", ""):
            continue
        for cat in season_type.get("categories", []):
            for event in cat.get("events", []):
                stats = event.get("stats", [])
                if len(stats) < len(labels):
                    continue

                # Get team ID from event data
                event_id = event.get("eventId", "") or event.get("id", "")
                event_info = data.get("events", {}).get(str(event_id), {})
                team_id = event_info.get("team", {}).get("id", "")

                game = {
                    "PTS": float(stats[idx.get("PTS", 13)]),
                    "REB": float(stats[idx.get("REB", 7)]),
                    "AST": float(stats[idx.get("AST", 8)]),
                    "MIN": float(stats[idx.get("MIN", 0)]),
                    "TEAM_ID": str(team_id),
                }
                games.append(game)

    return games[:last_n]


def get_team_defensive_stats():
    """Get defensive stats for all NBA teams via standings (one API call).

    Uses avgPointsAgainst as a proxy for defensive rating.

    Returns:
        dict mapping espn_team_id (str) -> defensive rating approx (float)
    """
    drtg_map = {}

    try:
        resp = requests.get(f"{SITE_BASE.replace('/site/', '/v2/')}/standings".replace("site/v2", "v2"), timeout=15)
        if resp.status_code != 200:
            resp = requests.get("https://site.api.espn.com/apis/v2/sports/basketball/nba/standings", timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for group in data.get("children", []):
            for entry in group.get("standings", {}).get("entries", []):
                team_id = str(entry.get("team", {}).get("id", ""))
                if not team_id:
                    continue
                for stat in entry.get("stats", []):
                    if stat.get("name") == "avgPointsAgainst":
                        drtg_map[team_id] = float(stat.get("value", 112.0))
                        break

    except Exception as e:
        logger.warning(f"ESPN standings/DRTG failed: {e}")

    return drtg_map


_player_id_cache = {}


def build_player_id_cache(team_ids):
    """Pre-fetch rosters for a list of team IDs, building a name -> ESPN player ID cache.

    Call once at the start of a run with all team IDs for today's games.
    This avoids per-player API calls.
    """
    global _player_id_cache
    for team_id in team_ids:
        try:
            roster = get_team_roster(team_id)
            for player in roster:
                name = player.get("name", "")
                pid = player.get("id", "")
                if name and pid:
                    _player_id_cache[name] = pid
        except Exception as e:
            logger.debug(f"Roster cache for team {team_id}: {e}")

    logger.info(f"ESPN player cache: {len(_player_id_cache)} players from {len(team_ids)} teams")


def find_espn_player_id(player_name):
    """Look up ESPN player ID from cache (built by build_player_id_cache).

    Falls back to search API if not in cache.

    Returns:
        str ESPN player ID or None
    """
    # Check cache first
    if player_name in _player_id_cache:
        return _player_id_cache[player_name]

    # Fuzzy match: "LeBron James" might be listed slightly differently
    name_lower = player_name.lower()
    for cached_name, pid in _player_id_cache.items():
        if cached_name.lower() == name_lower:
            return pid

    # Not in cache — skip (don't do per-player API calls)
    return None


def get_team_roster(team_id):
    """Get team roster with player IDs.

    Returns:
        list of {id, name} dicts
    """
    try:
        resp = requests.get(f"{SITE_BASE}/teams/{team_id}/roster", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        roster = []
        for player in data.get("athletes", []):
            # Athletes are directly in the list (not nested in groups/items)
            pid = str(player.get("id", ""))
            name = player.get("fullName", "") or player.get("displayName", "")
            if pid and name:
                roster.append({"id": pid, "name": name})

        return roster
    except Exception as e:
        logger.debug(f"Roster for team {team_id}: {e}")
        return []


def get_injuries():
    """Get NBA injuries from ESPN.

    Returns:
        dict mapping team_id -> list of {player_id, player_name, status}
    """
    injuries = {}
    try:
        resp = requests.get(f"{SITE_BASE}/injuries", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for team_data in data.get("injuries", []):
            team_id = str(team_data.get("team", {}).get("id", ""))
            team_injuries = []
            for inj in team_data.get("injuries", []):
                status = inj.get("status", "")
                if status in ("Out", "Doubtful"):
                    athlete = inj.get("athlete", {})
                    team_injuries.append({
                        "player_id": str(athlete.get("id", "")),
                        "player_name": athlete.get("displayName", ""),
                        "status": status,
                    })
            if team_injuries:
                injuries[team_id] = team_injuries

    except Exception as e:
        logger.warning(f"ESPN injuries failed: {e}")

    return injuries
