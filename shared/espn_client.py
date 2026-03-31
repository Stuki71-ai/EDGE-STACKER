import logging
import requests

logger = logging.getLogger("edge_stacker")

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"


def get_ncaaf_scoreboard(date_str, limit=100, seasontype=None):
    """Get NCAAF scoreboard for a date (YYYYMMDD format)."""
    url = f"{BASE_URL}/football/college-football/scoreboard"
    params = {"dates": date_str, "limit": limit}
    if seasontype is not None:
        params["seasontype"] = seasontype
    return _get(url, params)


def get_ncaab_scoreboard(date_str, limit=300, groups=50, page=None):
    """Get NCAAB scoreboard for a date. groups=50 = Division I."""
    url = f"{BASE_URL}/basketball/mens-college-basketball/scoreboard"
    params = {"dates": date_str, "limit": limit, "groups": groups}
    if page is not None:
        params["page"] = page
    return _get(url, params)


def get_nba_scoreboard(date_str):
    """Get NBA scoreboard for a date."""
    url = f"{BASE_URL}/basketball/nba/scoreboard"
    params = {"dates": date_str}
    return _get(url, params)


def get_team_info(sport, team_id):
    """Get team details including conference.

    sport: 'football/college-football' or 'basketball/mens-college-basketball'
    """
    url = f"{BASE_URL}/{sport}/teams/{team_id}"
    return _get(url)


def _get(url, params=None):
    """Make a GET request to ESPN API."""
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_events(data):
    """Parse ESPN scoreboard response into a list of game dicts."""
    events = data.get("events", [])
    games = []
    for event in events:
        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])
        venue = competition.get("venue", {})

        home = None
        away = None
        for c in competitors:
            team_data = {
                "id": c.get("team", {}).get("id"),
                "name": c.get("team", {}).get("displayName", ""),
                "abbreviation": c.get("team", {}).get("abbreviation", ""),
                "conference_id": c.get("team", {}).get("conferenceId"),
            }
            if c.get("homeAway") == "home":
                home = team_data
            else:
                away = team_data

        game = {
            "id": event.get("id"),
            "date": event.get("date", ""),
            "name": event.get("name", ""),
            "home": home,
            "away": away,
            "venue_name": venue.get("fullName", ""),
            "venue_indoor": venue.get("indoor", False),
            "neutral_site": competition.get("neutralSite", False),
            "conference_competition": competition.get("conferenceCompetition"),
            "season_type": event.get("season", {}).get("type"),
            "notes": [n.get("headline", "") for n in competition.get("notes", [])],
            "type_abbreviation": competition.get("type", {}).get("abbreviation", ""),
        }
        games.append(game)
    return games
