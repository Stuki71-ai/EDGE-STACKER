"""ESPN-based NHL data client (mirrors espn_nba.py pattern)."""

import logging
import requests
import re

logger = logging.getLogger("edge_stacker")

BASE = "https://site.web.api.espn.com/apis/common/v3/sports/hockey/nhl"
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"

# ESPN gamelog labels: G, A, PTS, +/-, PIM, S, SPCT, PPG, PPA, SHG, SHA, GWG, TOI/G, PROD
GAMELOG_LABELS = ["G", "A", "PTS", "+/-", "PIM", "S", "SPCT", "PPG", "PPA",
                  "SHG", "SHA", "GWG", "TOI/G", "PROD"]


def _toi_to_seconds(toi_str):
    """Parse TOI string like '14:32' to total seconds."""
    if not toi_str or toi_str == "0:00":
        return 0
    try:
        m = re.match(r"^(\d+):(\d+)", str(toi_str))
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
    except (ValueError, AttributeError):
        pass
    return 0


def get_player_gamelog(espn_player_id, last_n=10, include_postseason=True):
    """Get NHL player game log from ESPN.

    Returns:
        list of dicts with S (shots), TOI_SEC (time on ice in seconds),
        G, A, PIM, PPG, GAME_DATE, TEAM_ID
    """
    try:
        resp = requests.get(f"{BASE}/athletes/{espn_player_id}/gamelog", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug(f"ESPN NHL gamelog failed for player {espn_player_id}: {e}")
        return []

    labels = data.get("labels", GAMELOG_LABELS)
    idx = {lbl: i for i, lbl in enumerate(labels)}

    all_games = []
    for season_type in data.get("seasonTypes", []):
        st_name = season_type.get("displayName", "")
        # Include both regular season and postseason (playoffs is when sharps care most)
        if "Regular" not in st_name and not (include_postseason and "Postseason" in st_name):
            continue
        for cat in season_type.get("categories", []):
            for event in cat.get("events", []):
                stats = event.get("stats", [])
                if len(stats) < len(labels):
                    continue

                event_id = event.get("eventId", "") or event.get("id", "")
                event_info = data.get("events", {}).get(str(event_id), {})

                game = {
                    "S": float(stats[idx.get("S", 5)]) if stats[idx.get("S", 5)] else 0.0,
                    "G": float(stats[idx.get("G", 0)]) if stats[idx.get("G", 0)] else 0.0,
                    "A": float(stats[idx.get("A", 1)]) if stats[idx.get("A", 1)] else 0.0,
                    "PIM": float(stats[idx.get("PIM", 4)]) if stats[idx.get("PIM", 4)] else 0.0,
                    "PPG": float(stats[idx.get("PPG", 7)]) if stats[idx.get("PPG", 7)] else 0.0,
                    "TOI_SEC": _toi_to_seconds(stats[idx.get("TOI/G", 12)]),
                    "GAME_DATE": event_info.get("gameDate", ""),
                    "TEAM_ID": str(event_info.get("homeTeamId", "")) if event_info.get("atVs") == "vs" else str(event_info.get("awayTeamId", "")),
                }
                all_games.append(game)

    return all_games[:last_n]


def get_team_defensive_stats():
    """Get NHL team defensive stats — shots against per game.

    Returns:
        dict mapping espn_team_id (str) -> shots_against_per_game (float)
    """
    sa_map = {}
    try:
        resp = requests.get(f"https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for group in data.get("children", []):
            for entry in group.get("standings", {}).get("entries", []):
                team_id = str(entry.get("team", {}).get("id", ""))
                if not team_id:
                    continue
                # NHL standings may not include shots-against directly; fallback to team stats
                sa_map[team_id] = 30.0  # league avg placeholder

        # Fallback: per-team stats endpoint
        for team_id in list(sa_map.keys()):
            try:
                tr = requests.get(f"{SITE_BASE}/teams/{team_id}/statistics", timeout=8)
                if tr.status_code == 200:
                    td = tr.json()
                    cats = td.get("results", {}).get("stats", {}).get("categories", [])
                    for cat in cats:
                        for s in cat.get("stats", []):
                            name = s.get("name", "")
                            if name in ("avgShotsAgainst", "shotsAgainstPerGame"):
                                try:
                                    sa_map[team_id] = float(s.get("value", 30.0))
                                except (ValueError, TypeError):
                                    pass
                                break
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"ESPN NHL standings/SA failed: {e}")

    return sa_map


_player_id_cache = {}
_player_position_cache = {}


def build_player_id_cache(team_ids):
    """Pre-fetch rosters for team IDs, build name -> ID and name -> position caches."""
    global _player_id_cache, _player_position_cache
    for team_id in team_ids:
        try:
            roster = get_team_roster(team_id)
            for player in roster:
                name = player.get("name", "")
                pid = player.get("id", "")
                pos = player.get("position", "")
                if name and pid:
                    _player_id_cache[name] = pid
                    _player_position_cache[name] = pos
        except Exception as e:
            logger.debug(f"NHL roster cache for team {team_id}: {e}")
    logger.info(f"ESPN NHL player cache: {len(_player_id_cache)} players from {len(team_ids)} teams")


def find_espn_player_id(player_name):
    """Look up ESPN player ID from cache."""
    if player_name in _player_id_cache:
        return _player_id_cache[player_name]
    name_lower = player_name.lower()
    for cached, pid in _player_id_cache.items():
        if cached.lower() == name_lower:
            return pid
    return None


def get_player_position(player_name):
    """Returns position abbreviation: C, LW, RW, D, G."""
    if player_name in _player_position_cache:
        return _player_position_cache[player_name]
    name_lower = player_name.lower()
    for cached, pos in _player_position_cache.items():
        if cached.lower() == name_lower:
            return pos
    return ""


def get_team_roster(team_id):
    """Get NHL team roster (handles grouped position format).

    Returns:
        list of {id, name, position} dicts
    """
    try:
        resp = requests.get(f"{SITE_BASE}/teams/{team_id}/roster", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        roster = []
        for group in data.get("athletes", []):
            # NHL groups roster by position type (Centers, LeftWings, etc.)
            if isinstance(group, dict) and "items" in group:
                for player in group.get("items", []):
                    pid = str(player.get("id", ""))
                    name = player.get("fullName", "") or player.get("displayName", "")
                    pos = player.get("position", {}).get("abbreviation", "")
                    if pid and name:
                        roster.append({"id": pid, "name": name, "position": pos})
            elif isinstance(group, dict) and "id" in group:
                # Flat format fallback
                pid = str(group.get("id", ""))
                name = group.get("fullName", "")
                pos = group.get("position", {}).get("abbreviation", "")
                if pid and name:
                    roster.append({"id": pid, "name": name, "position": pos})
        return roster
    except Exception as e:
        logger.debug(f"NHL roster for team {team_id}: {e}")
        return []


def get_injuries():
    """Get NHL injuries from ESPN.

    Returns:
        dict mapping team_id (str) -> list of {player_id, player_name, status}
    """
    injuries = {}
    try:
        resp = requests.get(f"{SITE_BASE}/injuries", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for team_entry in data.get("injuries", []):
            team_id = ""
            team_injuries = []
            for inj in team_entry.get("injuries", []):
                athlete = inj.get("athlete", {}) if isinstance(inj.get("athlete"), dict) else {}
                if not team_id:
                    team_id = str(athlete.get("team", {}).get("id", ""))

                inj_type = inj.get("type", {})
                type_name = inj_type.get("name", "") if isinstance(inj_type, dict) else str(inj_type)
                status_text = inj.get("status", "")

                is_out = ("OUT" in type_name.upper() or "OUT" in status_text.upper()
                          or "DOUBTFUL" in type_name.upper() or "DOUBTFUL" in status_text.upper())

                if is_out:
                    team_injuries.append({
                        "player_id": str(athlete.get("id", "")),
                        "player_name": athlete.get("displayName", ""),
                        "status": status_text or type_name,
                    })

            if team_injuries and team_id:
                injuries[team_id] = team_injuries

    except Exception as e:
        logger.warning(f"ESPN NHL injuries failed: {e}")

    return injuries


def is_back_to_back(team_id, today_iso):
    """Check if team played yesterday (back-to-back fatigue check).

    Returns:
        True if team played yesterday.
    """
    try:
        from datetime import datetime, timedelta
        today = datetime.fromisoformat(today_iso) if isinstance(today_iso, str) else today_iso
        yesterday = today - timedelta(days=1)
        ystr = yesterday.strftime("%Y%m%d")
        resp = requests.get(f"{SITE_BASE}/scoreboard", params={"dates": ystr}, timeout=10)
        resp.raise_for_status()
        for ev in resp.json().get("events", []):
            comp = ev.get("competitions", [{}])[0]
            for c in comp.get("competitors", []):
                if str(c.get("team", {}).get("id", "")) == team_id:
                    return True
    except Exception:
        pass
    return False
