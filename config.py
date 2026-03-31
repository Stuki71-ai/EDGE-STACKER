import os
import json
import logging
import platform

logger = logging.getLogger("edge_stacker")

# ── Load .env file if present ──
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

# ── API Keys ──
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")

# ── Paths ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
BANKROLL_STATE_PATH = os.path.join(BASE_DIR, "bankroll_state.json")
DAILY_STATE_PATH = os.path.join(BASE_DIR, "daily_state.json")

# ── Odds API Sport Keys (verified via /v4/sports/) ──
SPORT_KEYS = {
    "ncaaf": "americanfootball_ncaaf",
    "ncaab": "basketball_ncaab",
    "nba": "basketball_nba",
}

# ── Odds API Budget ──
ODDS_API_CREDIT_WARNING = 50  # Warn if remaining credits below this

# ── Module Priority (lowest priority disabled first when credits low) ──
MODULE_PRIORITY = [
    "nba_props",
    "ncaab_kenpom",
    "ncaaf_weather",
    "ncaaf_bowls",
    "ncaab_conf_tourney",
]

# ── NCAAF Weather Thresholds ──
WEATHER_MIN_WIND_MPH = 13
WEATHER_MIN_TOTAL = 38
WEATHER_MAX_UNDER_ODDS = -115  # Best under odds must be >= this (less negative)

# ── NBA Props Thresholds ──
PROP_MIN_GAMES = 10
PROP_MIN_MINUTES = 20
PROP_MAX_VIG = 0.08
PROP_MIN_EDGE = 0.06
PROP_MINUTES_STD_CAP = 5.0
PROP_MINUTES_HIGH_EDGE = 0.08
PROP_MAX_GAMES_PER_RUN = 8
LEAGUE_AVG_DRTG = 112.0
STAT_STD_PCT = {
    "PTS": 0.22,
    "REB": 0.30,
    "AST": 0.28,
}

# ── NBA API Rate Limiting ──
NBA_API_DELAY = 0.7
NBA_API_RETRIES = 3

# ── NCAAF Bowls Thresholds ──
BOWL_MIN_SPREAD = 3.0
BOWL_MAX_SPREAD = 28.0
BOWL_MAX_ODDS = -115

# ── NCAAB KenPom Thresholds ──
KENPOM_MIN_DIVERGENCE = 3.0
KENPOM_MAX_ODDS = -115
KENPOM_WARN_DAYS = 7
KENPOM_DISABLE_DAYS = 14
KENPOM_HCA_CONFERENCE = 2.8
KENPOM_HCA_NON_CONFERENCE = 3.5

# ── NCAAB Conf Tournament ──
CONF_TOURNEY_MIN_ATS = 0.60

# ── Cross-platform time formatting ──
TIME_FMT = "%#I:%M %p ET" if platform.system() == "Windows" else "%-I:%M %p ET"

# ── Default Bankroll ──
DEFAULT_BANKROLL = 5000.00


def load_static_json(filename):
    """Load a JSON file from the static directory."""
    path = os.path.join(STATIC_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Static file not found: {path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
        return {}
