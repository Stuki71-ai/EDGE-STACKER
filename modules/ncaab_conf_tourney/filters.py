import logging
import config

logger = logging.getLogger("edge_stacker")


def passes_filters(game, spread_data, rules):
    """
    Run the NCAAB Conference Tournament filter pipeline.
    Returns: (passes: bool, reason: str or None, rule: dict or None)
    """
    conference = game.get("_conference")
    round_key = game.get("_round_key")

    # STEP 1: Is this a conf tournament game?
    if round_key is None:
        return False, "Not a conference tournament game", None

    # STEP 2: Match conference + round to rules?
    if conference not in rules:
        return False, f"Conference '{conference}' not in rules", None

    conf_rules = rules[conference]
    if round_key not in conf_rules:
        return False, f"Round '{round_key}' not in rules for {conference}", None

    rule = conf_rules[round_key]

    # STEP 3: Underdog getting points?
    if not spread_data:
        return False, "No spread data", None
    if spread_data.get("spread", 0) <= 0:
        return False, "No underdog spread", None

    # STEP 4: Historical ATS >= 60%?
    ats_pct = rule.get("dog_ats_pct", 0)
    if ats_pct < config.CONF_TOURNEY_MIN_ATS:
        return False, f"ATS {ats_pct:.1%} < {config.CONF_TOURNEY_MIN_ATS:.1%}", None

    return True, None, rule
