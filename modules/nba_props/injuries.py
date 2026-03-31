import logging
import requests

logger = logging.getLogger("edge_stacker")

INJURIES_URL = "https://cdn.nba.com/static/json/liveData/injuries/injuries_todaysGames.json"


def get_injuries():
    """Fetch today's injury report from NBA.com.

    Returns:
        dict mapping team_id -> list of {player, status, reason}
    """
    try:
        resp = requests.get(INJURIES_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Could not fetch injury report: {e}")
        return {}

    injuries = {}
    # The structure may vary; handle the common format
    league_injuries = data.get("leagueInjuries", {})
    for team_data in league_injuries.get("teams", []):
        team_id = str(team_data.get("teamId", ""))
        team_injuries = []
        for player in team_data.get("players", []):
            status = player.get("injuryStatus", "")
            if status in ("Out", "Doubtful"):
                team_injuries.append({
                    "player_id": str(player.get("playerId", "")),
                    "player_name": f"{player.get('firstName', '')} {player.get('lastName', '')}".strip(),
                    "status": status,
                    "reason": player.get("injuryType", ""),
                })
        if team_injuries:
            injuries[team_id] = team_injuries

    return injuries


def is_top_minutes_player_out(injuries_for_team, team_roster_minutes):
    """Check if a top-2 minutes player on a team is OUT.

    Args:
        injuries_for_team: list of injury dicts for the team
        team_roster_minutes: list of (player_id, avg_minutes) sorted desc

    Returns:
        (bool, player_name or None)
    """
    if not injuries_for_team or not team_roster_minutes:
        return False, None

    top2_ids = {str(pid) for pid, _ in team_roster_minutes[:2]}
    for inj in injuries_for_team:
        if str(inj.get("player_id", "")) in top2_ids:
            return True, inj.get("player_name")

    return False, None
