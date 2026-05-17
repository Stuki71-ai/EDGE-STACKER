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

# ── Odds API Budget ──
ODDS_API_CREDIT_WARNING = 50  # Warn if remaining credits below this

# ── Cross-platform time formatting ──
TIME_FMT = "%#I:%M %p ET" if platform.system() == "Windows" else "%-I:%M %p ET"
BET_BY_FMT = "%#I:%M %p CET" if platform.system() == "Windows" else "%-I:%M %p CET"
CET_OFFSET_HOURS = 1  # UTC+1 (CET), use 2 for CEST
ET_OFFSET_HOURS = -5  # UTC-5 (ET)

# ── Default Bankroll ──
DEFAULT_BANKROLL = 500.00


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
