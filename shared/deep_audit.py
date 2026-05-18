"""Claude-API deep audit — evidence-gathering layer.

Task 2 of docs/plans/2026-05-18-claude-api-deep-audit.md: gather_evidence()
collects a deterministic evidence bundle (picks, re-fetched upstream
summaries, code/constant parity, end-to-end recompute, fire log, mechanical
audit findings) for a freshly-generated picks result. A later task feeds the
bundle to the Claude API for judgment.

gather_evidence() is a thin public wrapper around the private _collect():
ALL heavy work (git, subprocess, fetchers, recompute, filesystem) lives in
_collect so tests can monkeypatch it and never touch the network or the VPS.
"""
import glob
import json
import logging
import os
import time
from datetime import datetime, timezone

import anthropic
import requests
from anthropic import Anthropic

from shared import audit_checks as ac

logger = logging.getLogger(__name__)

# Repo root is the parent of shared/ — resolve relative to this file so the
# evidence layer works regardless of the pipeline's cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cap the fire log embedded in the bundle so the JSON stays tens of KB.
MAX_FIRE_LOG_BYTES = 40_000


def _serialize_findings(findings):
    """Render a list[Finding] as plain JSON-safe dicts."""
    return [{"kind": f.kind, "text": f.text, "pick_ref": f.pick_ref}
            for f in findings]


def _read_fire_log(repo):
    """Return the newest logs/edge-stacker-*.log contents, truncated to the
    last MAX_FIRE_LOG_BYTES. Empty string if no fire log exists."""
    matches = glob.glob(os.path.join(repo, "logs", "edge-stacker-*.log"))
    if not matches:
        return ""
    newest = max(matches, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return text[-MAX_FIRE_LOG_BYTES:]


def _data_fetch_summary(module):
    """Re-fetch the upstream SUMMARIES a deep audit needs (ESPN / MLB-StatsAPI
    only — never the Odds API, which costs credits). Returns a plain dict.

    Per-source failures are captured as an `error` string rather than raised:
    a missing section is itself audit evidence."""
    summary = {}

    if module == "nhl_sog":
        try:
            from shared import espn_nhl
            sa = espn_nhl.get_team_defensive_stats()
            vals = sorted(sa.values())
            summary["nhl_shots_against"] = {
                "team_count": len(sa),
                "placeholder_count": sum(1 for x in vals if x == 30.0),
                "min": vals[0] if vals else None,
                "max": vals[-1] if vals else None,
            }
        except Exception as e:
            summary["nhl_shots_against"] = {"error": str(e)}

    elif module == "mlb_f5":
        year = datetime.now(timezone.utc).year
        try:
            from shared import mlb_data
            summary["mlb_fip_constant"] = {
                "season": year,
                "value": mlb_data.fip_constant(year),
            }
        except Exception as e:
            summary["mlb_fip_constant"] = {"error": str(e)}

        try:
            r = requests.get(
                f"https://statsapi.mlb.com/api/v1/teams?sportId=1"
                f"&season={year}", timeout=20)
            r.raise_for_status()
            venues = {t.get("venue", {}).get("name", "")
                      for t in r.json().get("teams", [])
                      if t.get("venue", {}).get("name")}
            pf_path = os.path.join(REPO_ROOT, "static",
                                   "mlb_park_factors.json")
            with open(pf_path, encoding="utf-8") as f:
                pf = json.load(f)
            summary["mlb_park_factors"] = {
                "venue_count": len(venues),
                "keyed_count": len(pf),
                "missing_venues": sorted(v for v in venues if v not in pf),
            }
        except Exception as e:
            summary["mlb_park_factors"] = {"error": str(e)}

    return summary


def _collect(module, result):
    """Heavy evidence-gathering: git/subprocess, upstream re-fetches, the
    end-to-end recompute, the fire log, and the mechanical audit. Kept private
    so tests can stub it. Returns the evidence dict."""
    picks = result.get("picks", [])

    code_parity = ac.check_code_parity(REPO_ROOT)
    mechanical = list(code_parity)
    mechanical += ac.check_infra(REPO_ROOT, "n8n-n8n-1")
    mechanical += ac.check_data_fetch(module)
    mechanical += ac.check_picks(module, picks)

    recompute = []
    for p in picks:
        # Recompute the bare projection ONCE per pick (recompute_value is a
        # thin passthrough to the shared _recompute_projection core). Stored
        # so the Claude judge can run audit-spec Check 5's tolerance
        # comparison even on a clean pick, where recompute_pick's findings
        # fire only on drift beyond its internal threshold and yield no
        # number. recompute_pick still fetches independently for its
        # findings/comparison logic — eliminating that second fetch would
        # require changing its public (module, pick, tolerance) contract,
        # which pipeline.py depends on, so it is left as-is.
        recomputed_projection = ac.recompute_value(module, p)
        findings = ac.recompute_pick(module, p)
        recompute.append({
            "pick_ref": ac._pick_ref(p),
            "logged_projection": p.get("context", {}).get("projection"),
            "recomputed_projection": recomputed_projection,
            "findings": _serialize_findings(findings),
        })
        mechanical += findings

    return {
        "module": module,
        "picks": picks,
        "code_parity": _serialize_findings(code_parity),
        "data_fetch": _data_fetch_summary(module),
        "recompute": recompute,
        "fire_log": _read_fire_log(REPO_ROOT),
        "mechanical_findings": _serialize_findings(mechanical),
    }


def gather_evidence(module, result):
    """Public entry point: collect the deterministic evidence bundle for
    `module` + a fresh `result` (a picks JSON dict, as main.py --json-only
    emits it). Thin wrapper — delegates all work to _collect."""
    return _collect(module, result)


# --- Claude-API judgment layer -------------------------------------------

AUDIT_MODEL = "claude-opus-4-7"
AUDIT_MAX_TOKENS = 16_000
AUDIT_MAX_ATTEMPTS = 3
AUDIT_BACKOFF_BASE = 2.0

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["GREEN", "BUG"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["INFRA", "DATA", "CODE"]},
                    "text": {"type": "string"},
                    "pick_ref": {"type": "string"},
                },
                "required": ["kind", "text", "pick_ref"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["verdict", "findings", "summary"],
    "additionalProperties": False,
}


def _is_transient(exc):
    """True for failures worth retrying — network/timeout/rate-limit and any
    5xx server error. 4xx (bad request / auth / permission) are permanent."""
    if isinstance(exc, (anthropic.APIConnectionError,
                        anthropic.APITimeoutError,
                        anthropic.RateLimitError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", 0) >= 500
    return False


def _parse_verdict(text):
    """Parse the model's text block as the contract JSON, validating shape.
    Raises ValueError on bad JSON or a wrong shape (callers fall back)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("verdict is not a JSON object")
    if data.get("verdict") not in ("GREEN", "BUG"):
        raise ValueError(f"bad verdict: {data.get('verdict')!r}")
    if not isinstance(data.get("summary"), str):
        raise ValueError("missing or non-string summary")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings is not a list")
    for f in findings:
        if not isinstance(f, dict) or {"kind", "text", "pick_ref"} - f.keys():
            raise ValueError(f"malformed finding: {f!r}")
    return data


def claude_api_audit(spec, evidence):
    """Judge `evidence` against the audit `spec` via one Anthropic API call.

    `spec` is the docs/audit-spec.md text — sent as a cache-controlled system
    prompt so the static spec is cached across fires. `evidence` is the
    gather_evidence bundle, sent JSON-serialised as the user message. Returns
    the parsed verdict dict ({"verdict","findings","summary"}).

    Transient failures are retried with exponential backoff up to
    AUDIT_MAX_ATTEMPTS; any other failure (bad JSON, wrong shape, a 4xx, or an
    exhausted retry budget) raises — Task 4's orchestrator catches that and
    falls back to the mechanical audit."""
    client = Anthropic(max_retries=0)
    last_exc = None
    for attempt in range(AUDIT_MAX_ATTEMPTS):
        try:
            response = client.messages.create(
                model=AUDIT_MODEL,
                max_tokens=AUDIT_MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=[{
                    "type": "text",
                    "text": spec,
                    "cache_control": {"type": "ephemeral"},
                }],
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema",
                               "schema": VERDICT_SCHEMA},
                },
                messages=[{
                    "role": "user",
                    "content": json.dumps(evidence, sort_keys=True,
                                          default=str),
                }],
            )
            break
        except Exception as exc:
            if not _is_transient(exc):
                raise
            last_exc = exc
            if attempt == AUDIT_MAX_ATTEMPTS - 1:
                raise
            time.sleep(AUDIT_BACKOFF_BASE ** attempt)

    if response.stop_reason == "max_tokens":
        raise ValueError("Claude audit response truncated "
                         "(stop_reason=max_tokens) — evidence bundle or "
                         "max_tokens needs tuning")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError("Claude audit response had no text block; "
                         f"stop_reason={response.stop_reason}")
    return _parse_verdict(text)


# --- deep_audit orchestrator ---------------------------------------------

AUDIT_SPEC_PATH = os.path.join(REPO_ROOT, "docs", "audit-spec.md")

# Set the first time deep_audit runs without an API key, so a missing key
# logs a warning once per process instead of spamming every fire.
_warned_no_key = False


def deep_audit(module, result, fallback):
    """Audit a fresh picks `result` for `module` and return list[Finding].

    Gathers the evidence bundle, sends it to the Claude API for a GREEN/BUG
    verdict, and maps the verdict's findings to Finding objects. Any failure
    — no API key, an unreachable API, a malformed verdict, a missing spec
    file — falls back to the mechanical `fallback(module, result)` audit so
    the pipeline never misses a night.

    `fallback` is passed in (rather than imported) to avoid a circular
    import with pipeline.py."""
    global _warned_no_key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if not _warned_no_key:
            logger.warning("ANTHROPIC_API_KEY unset — deep audit disabled, "
                           "mechanical fallback")
            _warned_no_key = True
        return fallback(module, result)

    try:
        with open(AUDIT_SPEC_PATH, encoding="utf-8") as f:
            spec = f.read()
        evidence = gather_evidence(module, result)
        verdict = claude_api_audit(spec, evidence)
        return [ac.Finding(kind=f["kind"], text=f["text"],
                           pick_ref=f["pick_ref"])
                for f in verdict["findings"]]
    except Exception:
        logger.warning("deep audit unavailable — mechanical fallback")
        return fallback(module, result)
