import logging
from shared import espn_client

logger = logging.getLogger("edge_stacker")

ROUND_KEYWORDS = {
    "first round": "first_round",
    "opening round": "opening_round",
    "second round": "second_round",
    "quarterfinal": "quarterfinals",
    "quarterfinals": "quarterfinals",
    "semifinal": "semifinals",
    "semifinals": "semifinals",
    "championship": "championship",
    "final": "championship",
    "finals": "championship",
}


def get_conf_tourney_games(target_date):
    """Get NCAAB conference tournament games for a date."""
    date_str = target_date.strftime("%Y%m%d")
    data = espn_client.get_ncaab_scoreboard(date_str)
    games = espn_client.parse_events(data)

    tourney_games = []
    for game in games:
        round_key = detect_round(game)
        if round_key:
            game["_round_key"] = round_key
            game["_conference"] = detect_conference(game)
            tourney_games.append(game)

    logger.info(f"NCAAB Conf Tourney: found {len(tourney_games)} tournament games on {date_str}")
    return tourney_games


def detect_round(game):
    """Detect the tournament round from ESPN notes and type fields.

    Returns:
        round key (e.g. "semifinals") or None if not a conf tournament game
    """
    # Check type abbreviation
    type_abbr = game.get("type_abbreviation", "").upper()
    if type_abbr not in ("CTOURN", ""):
        # Not a conference tournament game (might be regular season or NIT)
        pass

    # Check notes for round info
    notes = game.get("notes", [])
    for note in notes:
        note_lower = note.lower()
        # Check if this looks like a conference tournament game
        if "tournament" not in note_lower and "tourney" not in note_lower:
            continue

        for keyword, round_key in ROUND_KEYWORDS.items():
            if keyword in note_lower:
                return round_key

    # If type is CTOURN but no round detected, try to infer
    if type_abbr == "CTOURN":
        for note in notes:
            note_lower = note.lower()
            for keyword, round_key in ROUND_KEYWORDS.items():
                if keyword in note_lower:
                    return round_key
        # Default to unknown round if it's confirmed conf tournament
        return "unknown"

    return None


def detect_conference(game):
    """Detect which conference tournament this game belongs to.

    Both teams should be in the same conference.
    """
    # Check if both teams have conference data
    home_conf_id = game.get("home", {}).get("conference_id")
    away_conf_id = game.get("away", {}).get("conference_id")

    # Try to get from notes
    notes = game.get("notes", [])
    for note in notes:
        # Conference tournament notes often include the conference name
        # e.g., "ACC Tournament - Semifinal"
        note_upper = note.upper()
        conferences = [
            "ACC", "Big 12", "Big Ten", "SEC", "Big East",
            "NEC", "OVC", "Patriot League", "MAAC", "Southland",
            "America East", "Horizon League", "Summit League",
            "Mountain West", "WCC", "MVC", "AAC", "Sun Belt",
            "MAC", "WAC", "SWAC", "MEAC", "Big Sky", "Big West",
            "Big South", "CAA", "Ivy League", "SoCon", "A-10",
        ]
        for conf in conferences:
            if conf.upper() in note_upper:
                return conf

    return None
