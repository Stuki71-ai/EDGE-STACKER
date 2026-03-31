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
    """Get defensive stats for all NBA teams.

    ESPN doesn't provide DEF_RATING directly, but we can approximate
    from points allowed per game (oppPtsPerGame) via team statistics.

    Returns:
        dict mapping espn_team_id (str) -> approx defensive rating (float)
    """
    drtg_map = {}

    try:
        # Get all teams
        resp = requests.get(f"{SITE_BASE}/teams", timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for team_entry in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
            team = team_entry.get("team", {})
            team_id = str(team.get("id", ""))
            if not team_id:
                continue

            # Fetch team stats
            try:
                abbr = team.get("abbreviation", "").lower()
                stats_resp = requests.get(
                    f"{SITE_BASE}/teams/{team_id}/statistics", timeout=10
                )
                stats_resp.raise_for_status()
                stats_data = stats_resp.json()

                # Look for defensive stats
                for cat in stats_data.get("splitCategories", []):
                    if cat.get("name") == "defensive":
                        for stat in cat.get("stats", []):
                            # avgPointsAgainst approximates DRTG
                            if stat.get("name") in ("avgPointsAgainst", "avgPoints"):
                                drtg_map[team_id] = float(stat.get("value", 112.0))
                                break

                # Fallback: look in general stats for opponent points
                if team_id not in drtg_map:
                    for cat in stats_data.get("splitCategories", []):
                        for stat in cat.get("stats", []):
                            if "opponent" in stat.get("name", "").lower() and "point" in stat.get("name", "").lower():
                                drtg_map[team_id] = float(stat.get("value", 112.0))
                                break

            except Exception as e:
                logger.debug(f"Stats for team {team_id}: {e}")

    except Exception as e:
        logger.warning(f"ESPN team stats failed: {e}")

    return drtg_map


def find_espn_player_id(player_name):
    """Search ESPN for a player by name, return their ESPN ID.

    Returns:
        str ESPN player ID or None
    """
    try:
        resp = requests.get(
            f"{SITE_BASE}/athletes",
            params={"search": player_name, "limit": 5},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        athletes = data.get("athletes", data.get("items", []))
        if athletes:
            return str(athletes[0].get("id", ""))
    except Exception:
        pass

    # Fallback: search via site API
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/common/v3/search",
            params={"query": player_name, "limit": 3, "type": "player", "sport": "basketball", "league": "nba"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if items:
            return str(items[0].get("id", ""))
    except Exception:
        pass

    return None


def get_team_roster(team_id):
    """Get team roster with player IDs.

    Returns:
        list of {id, name, minutes} dicts sorted by minutes desc
    """
    try:
        resp = requests.get(f"{SITE_BASE}/teams/{team_id}/roster", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        roster = []
        for group in data.get("athletes", []):
            for player in group.get("items", []):
                pid = str(player.get("id", ""))
                name = player.get("fullName", "")
                # Try to get minutes from stats
                stats = player.get("statistics", {})
                mins = 0
                if stats:
                    for cat in stats.get("splits", {}).get("categories", []):
                        for s in cat.get("stats", []):
                            if s.get("name") == "minutes":
                                mins = float(s.get("value", 0))
                roster.append({"id": pid, "name": name, "minutes": mins})

        roster.sort(key=lambda x: x["minutes"], reverse=True)
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
