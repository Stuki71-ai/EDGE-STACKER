import csv
import os
import logging
from datetime import date, datetime
import config

logger = logging.getLogger("edge_stacker")


def load_kenpom_data():
    """Parse KenPom CSV into a dict keyed by team name.

    Returns:
        (data_dict, csv_age_days) or (None, None) on error
    """
    path = os.path.join(config.STATIC_DIR, "kenpom_data.csv")

    if not os.path.exists(path):
        logger.error(f"KenPom CSV not found: {path}")
        return None, None

    # Check CSV age
    mtime = os.path.getmtime(path)
    csv_date = datetime.fromtimestamp(mtime).date()
    age_days = (date.today() - csv_date).days

    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            columns = reader.fieldnames
            if not columns:
                logger.error("KenPom CSV has no columns")
                return None, age_days

            logger.info(f"KenPom CSV columns: {columns}")

            # Map expected column names (handle variations)
            col_map = {}
            for col in columns:
                cl = col.strip().lower()
                if cl in ("team", "teamname"):
                    col_map["team"] = col
                elif cl == "adjem":
                    col_map["adjem"] = col
                elif cl == "adjo":
                    col_map["adjo"] = col
                elif cl == "adjd":
                    col_map["adjd"] = col
                elif cl == "adjt":
                    col_map["adjt"] = col
                elif cl == "conf":
                    col_map["conf"] = col

            if "team" not in col_map or "adjem" not in col_map:
                logger.error(f"KenPom CSV missing required columns. Found: {columns}")
                logger.error("Expected at least 'Team' and 'AdjEM' columns")
                return None, age_days

            for row in reader:
                team_name = row.get(col_map["team"], "").strip()
                if not team_name:
                    continue

                try:
                    adjem_str = row.get(col_map.get("adjem", ""), "0")
                    adjem = float(adjem_str.replace("+", ""))
                except (ValueError, TypeError):
                    continue

                entry = {"AdjEM": adjem, "team": team_name}

                if "adjo" in col_map:
                    try:
                        entry["AdjO"] = float(row[col_map["adjo"]])
                    except (ValueError, TypeError):
                        pass
                if "adjd" in col_map:
                    try:
                        entry["AdjD"] = float(row[col_map["adjd"]])
                    except (ValueError, TypeError):
                        pass
                if "conf" in col_map:
                    entry["Conf"] = row[col_map["conf"]].strip()

                data[team_name] = entry

    except Exception as e:
        logger.error(f"Failed to parse KenPom CSV: {e}")
        return None, None

    logger.info(f"Loaded {len(data)} teams from KenPom (CSV age: {age_days} days)")
    return data, age_days


def kenpom_predicted_spread(home_team, away_team, kenpom_data, neutral=False, is_conference=True):
    """
    Predicted margin = home_AdjEM - away_AdjEM + HCA
    """
    home = kenpom_data.get(home_team)
    away = kenpom_data.get(away_team)

    if not home or not away:
        return None

    if neutral:
        hca = 0.0
    elif is_conference:
        hca = config.KENPOM_HCA_CONFERENCE
    else:
        hca = config.KENPOM_HCA_NON_CONFERENCE

    margin = home["AdjEM"] - away["AdjEM"] + hca
    return round(margin, 1)
