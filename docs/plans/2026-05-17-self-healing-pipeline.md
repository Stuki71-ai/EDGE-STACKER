# Self-Healing Pick Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `pipeline.py`, a VPS-native orchestrator that generates picks, runs a full audit, self-heals infra/data issues, and sends the picks email only when the audit is clean — replacing the current fire-and-forget cron.

**Architecture:** One Python orchestrator per workflow (`nhl_sog`, `mlb_f5`), fired by cron on the VPS. It runs `main.py`, audits the fresh result with importable check functions extracted from `audit.py`, auto-fixes infra and drops bad picks, holds the email on any code/design bug, and POSTs the clean JSON to the existing n8n webhook. No PC dependency, no Claude routine.

**Tech Stack:** Python 3 (stdlib + `requests`), existing `main.py` / `audit.py` / `shared/` / `modules/`, cron, n8n webhooks, ntfy.sh.

**Reference:** Design doc `docs/plans/2026-05-17-self-healing-pipeline-design.md`.

---

## Task 1: Deep-audit `nhl_sog` (pre-launch requirement)

Not a TDD task — an investigation. The pipeline's code-bug HOLD path must stay
dormant, so `nhl_sog` must be as clean as `mlb_f5` already is.

**Files (read, then fix only if a real bug is found):**
- `modules/nhl_sog/runner.py`, `projections.py`, `filters.py`, `odds.py`
- `shared/espn_nhl.py`, `shared/odds_client.py`, `staking.py`
- `NHL.txt` / `NHL_amended.txt` / `NHL_v2.txt` (user spec), `docs/plans/` NHL design if present

**Steps:**
1. Read every file above. For each formula in `projections.py`, diff it against the
   NHL spec docs — EWMA decay, `sog_per_60`, opp factor clamp, back-to-back 0.95,
   line-sanity 0.5, edge via normal CDF, 70/30 market anchor.
2. On the VPS, re-fetch each upstream source (`espn_nhl.get_team_defensive_stats`,
   roster, gamelog, Odds API events) and verify ranges + zero silent fallbacks.
3. Recompute one recent NHL pick end-to-end from raw data; confirm projection/edge.
4. Check the same bug classes found in MLB: integer vs half-point line push handling
   in the edge calc; postponed/cancelled game guard; any market-anchor / devig
   asymmetry; constant drift vs spec.
5. For each REAL bug: fix it meticulously, add/adjust a test, commit with a clear
   message, push, sync the VPS (`git pull`).
6. If zero bugs: record that explicitly in the commit log of Task 2 (no empty commit).

**Done when:** `nhl_sog` has been verified end-to-end and any bug is fixed + deployed.

---

## Task 2: Extract importable audit checks into `shared/audit_checks.py`

DRY: `pipeline.py` and the existing `audit.py` must share one set of check
functions. Extract them; make them operate on a fresh in-hand result.

**Files:**
- Create: `shared/audit_checks.py`
- Test: `tests/test_audit_checks.py`
- Modify later (Task 8): `audit.py` to import from here

**Step 1: Define the Finding type and classification**

```python
# shared/audit_checks.py
"""Importable audit checks — shared by pipeline.py and audit.py.

A Finding has a kind that drives the pipeline's self-heal decision:
  INFRA  — mechanically auto-fixable on the VPS (n8n down, file/perm drift)
  DATA   — scoped to one pick; heal by dropping that pick
  CODE   — code/design bug; never auto-patched -> pipeline HOLDS the email
"""
from dataclasses import dataclass

INFRA, DATA, CODE = "INFRA", "DATA", "CODE"


@dataclass
class Finding:
    kind: str            # INFRA | DATA | CODE
    text: str            # human-readable description
    pick_ref: str = ""   # matchup/player the DATA finding is scoped to ("" if not)
```

**Step 2: Write failing tests for the pure pieces**

```python
# tests/test_audit_checks.py
from shared.audit_checks import Finding, INFRA, DATA, CODE, classify_worst


def test_classify_worst_prefers_code():
    fs = [Finding(INFRA, "n8n"), Finding(CODE, "bug"), Finding(DATA, "pick")]
    assert classify_worst(fs) == CODE

def test_classify_worst_data_over_infra():
    assert classify_worst([Finding(INFRA, "x"), Finding(DATA, "y")]) == DATA

def test_classify_worst_empty_is_none():
    assert classify_worst([]) is None
```

Run: `pytest tests/test_audit_checks.py -v` → Expected: FAIL (no `classify_worst`).

**Step 3: Implement `classify_worst`**

```python
def classify_worst(findings):
    """Return the most severe finding kind present, or None if clean."""
    for kind in (CODE, DATA, INFRA):   # severity order
        if any(f.kind == kind for f in findings):
            return kind
    return None
```

Run the tests → Expected: PASS.

**Step 4: Port the check functions from `audit.py`**

Move/adapt these from `audit.py` into `audit_checks.py`, each returning
`list[Finding]` (no global state, no ntfy, no log-scraping):

- `check_code_parity(repo) -> list[Finding]` — `git diff HEAD` on `modules/ shared/
  staking.py config.py` (non-empty → `CODE`); `SPEC_CONSTANTS` table check
  (drift → `CODE`); `MAX_HOURS_AHEAD` regex `^\s*MAX_HOURS_AHEAD` (→ `CODE`);
  forbidden-pattern grep `TODO|FIXME|XXX|HACK|mock|stub` whole-word (→ `CODE`).
- `check_infra(repo, n8n_container) -> list[Finding]` — n8n container `Up`?
  (down → `INFRA`); cron scripts / `pipeline.py` present (missing → `CODE`).
- `check_data_fetch(module) -> list[Finding]` — module-scoped:
  - `nhl_sog`: `espn_nhl.get_team_defensive_stats()` → 32 teams, 0 placeholders,
    plausible range (else `CODE`).
  - `mlb_f5`: `mlb_data.fip_constant()` in 2.8–3.5 (else `CODE`); park-factor
    coverage — every active venue keyed (missing → `CODE`).
- `check_picks(module, picks) -> list[Finding]` — per pick in the fresh result:
  edge within `[MIN_EDGE, MAX_EDGE]`, line sane, 8h window, postponed-game guard
  (`mlb_f5`). A pick that fails → `DATA` with `pick_ref` set.
- `recompute_pick(module, pick) -> list[Finding]` — recompute the first pick
  end-to-end from raw upstream data. **Tolerance is TIGHT (≤3%)** — the pipeline
  audits immediately after generation, so there is no next-day data drift.
  Mismatch beyond tolerance → `CODE` (formula bug) unless the cause is a
  pick-specific bad upstream value → `DATA` with `pick_ref`.

Reuse the exact logic already in `audit.py` (regexes, `SPEC_CONSTANTS`, range
asserts). The only change is: return `Finding` objects instead of calling
`decision()`/`autofixed()`, and accept `picks` as an argument instead of
scraping logs.

**Step 5: Smoke-test the ported checks on the VPS**

Run on the VPS (live deps):
`python -c "from shared import audit_checks; print(audit_checks.check_data_fetch('mlb_f5'))"`
Expected: `[]` (clean) or a list of Findings — no exception.

**Step 6: Commit**

```bash
git add shared/audit_checks.py tests/test_audit_checks.py
git commit -m "Extract importable audit checks into shared/audit_checks.py"
```

---

## Task 3: `pipeline.py` skeleton — env, DST guard, logging, arg parse

**Files:**
- Create: `pipeline.py`
- Test: `tests/test_pipeline_guard.py`

**Step 1: Failing test for the DST guard**

```python
# tests/test_pipeline_guard.py
import pipeline

def test_should_run_only_at_target_et_hour():
    # nhl_sog target = 16 (04:30 PM ET); mlb_f5 target = 15 (03:00 PM ET)
    assert pipeline.should_run("nhl_sog", et_hour=16) is True
    assert pipeline.should_run("nhl_sog", et_hour=15) is False
    assert pipeline.should_run("mlb_f5", et_hour=15) is True
    assert pipeline.should_run("mlb_f5", et_hour=21) is False
```

Run → Expected: FAIL.

**Step 2: Implement skeleton**

```python
#!/usr/bin/env python3
"""EDGE STACKER self-healing pick pipeline — runs entirely on the VPS.

generate -> audit -> self-heal infra/data -> iterate -> sync -> send.
Holds the email (ntfy only) on any code/design bug or unresolved finding.
See docs/plans/2026-05-17-self-healing-pipeline-design.md
"""
import argparse, json, logging, os, subprocess, sys
from datetime import datetime
from pathlib import Path

REPO = "/root/edge-stacker"
MAX_ATTEMPTS = 3
TARGET_ET_HOUR = {"nhl_sog": 16, "mlb_f5": 15}
WEBHOOK = {
    "nhl_sog": "https://vmi3157940.contaboserver.net/webhook/edge-stacker-nhl",
    "mlb_f5":  "https://vmi3157940.contaboserver.net/webhook/edge-stacker-mlb",
}
NTFY_URL = "https://ntfy.sh/Stuki71-Findings"
MARKER_DIR = os.path.join(REPO, "logs", "pipeline_markers")


def should_run(module, et_hour):
    """DST-proof guard: cron fires at two UTC times; only the run landing on the
    module's target ET hour proceeds."""
    return et_hour == TARGET_ET_HOUR[module]


def load_env(path=os.path.join(REPO, ".env")):
    """Load KEY=VALUE lines from .env into os.environ (so the main.py subprocess
    inherits ODDS_API_KEY etc.)."""
    if not os.path.exists(path):
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
```

Add a `logging` setup writing to `logs/pipeline.log` + stdout, and an
`argparse` `--module` (choices `nhl_sog`, `mlb_f5`).

**Step 3: Run tests → PASS. Commit.**

```bash
git add pipeline.py tests/test_pipeline_guard.py
git commit -m "pipeline.py skeleton: DST guard, env loader, logging"
```

---

## Task 4: `pipeline.py` — `generate()`

**Files:** Modify `pipeline.py`. Test: `tests/test_pipeline_generate.py` (mock subprocess).

**Step 1: Implement**

```python
def generate(module):
    """Run main.py for one module, return the parsed picks JSON dict.
    Raises RuntimeError on failure (caught by the top-level guard)."""
    proc = subprocess.run(
        [sys.executable, "main.py", "--modules", module, "--json-only"],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"main.py failed (rc={proc.returncode}): {proc.stderr[-500:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"main.py output not valid JSON: {e}")
```

**Step 2: Test** that a mocked zero-exit subprocess returning `{"picks": []}` is
parsed, and a non-zero exit raises `RuntimeError`.

**Step 3: Commit** `"pipeline.py: generate() — run main.py, parse picks JSON"`.

---

## Task 5: `pipeline.py` — the self-heal loop

**Files:** Modify `pipeline.py`. Test: `tests/test_pipeline_loop.py` (mock
`generate` / audit / fixers).

**Step 1: Implement the heal helpers**

```python
def autofix_infra(finding):
    """Apply a mechanical infra fix. Return True on success."""
    if "n8n" in finding.text:
        rc = subprocess.run("docker start n8n-n8n-1", shell=True).returncode
        return rc == 0
    # add other infra fixes here as the audit grows
    return False


def drop_picks(result, pick_refs):
    """Return a copy of result with the named picks removed."""
    kept = [p for p in result.get("picks", [])
            if p.get("matchup", "") not in pick_refs]
    out = dict(result)
    out["picks"] = kept
    return out
```

**Step 2: Implement the loop** — pure control flow, returns
`(outcome, result, findings)` where outcome ∈ `{"SEND", "HELD"}`:

```python
def heal_loop(module):
    from shared import audit_checks as ac
    attempt = 1
    while attempt <= MAX_ATTEMPTS:
        result = generate(module)
        findings = run_full_audit(module, result)   # Task 2 checks, aggregated
        worst = ac.classify_worst(findings)
        if worst is None:
            return "SEND", result, []
        if worst == ac.CODE:
            return "HELD", result, findings          # never auto-patch
        if worst == ac.DATA:
            refs = {f.pick_ref for f in findings if f.kind == ac.DATA and f.pick_ref}
            result = drop_picks(result, refs)
            findings = run_full_audit(module, result)  # re-audit remainder
            if ac.classify_worst(findings) is None:
                return "SEND", result, []
            # remainder still dirty -> fall through to another attempt
        if worst == ac.INFRA:
            for f in (x for x in findings if x.kind == ac.INFRA):
                if not autofix_infra(f):
                    return "HELD", result, findings
        attempt += 1
    return "HELD", result, findings
```

`run_full_audit(module, result)` = call all Task-2 check functions and
concatenate their `Finding` lists.

**Step 3: Tests** (mock `generate` + `run_full_audit`):
- clean first pass → `("SEND", ...)`
- one CODE finding → `("HELD", ...)`
- one DATA finding on pick X → X dropped, remainder clean → `("SEND", ...)` with X gone
- INFRA finding, autofix succeeds, second pass clean → `("SEND", ...)`
- never-clean → `("HELD", ...)` after `MAX_ATTEMPTS`

**Step 4: Commit** `"pipeline.py: self-heal loop (infra autofix, data drop, code hold)"`.

---

## Task 6: `pipeline.py` — SEND / HELD paths + `main()`

**Files:** Modify `pipeline.py`. Test: `tests/test_pipeline_send.py`.

**Step 1: Implement**

```python
def ntfy(title, body):
    import requests
    requests.post(NTFY_URL, data=body.encode("utf-8"),
                  headers={"Title": title, "Priority": "high", "Tags": "warning"},
                  timeout=15)

def sync():
    """Commit + push any auto-fix that touched tracked files (usually a no-op)."""
    rc, out = _sh(f"cd {REPO} && git status --porcelain")
    if not out.strip():
        return
    _sh(f"cd {REPO} && git add -A && git commit -m 'pipeline: auto-fix' && git push origin main")

def send(module, result):
    import requests
    r = requests.post(WEBHOOK[module], json=result, timeout=30)
    if "Workflow was started" not in r.text:
        raise RuntimeError(f"webhook did not accept: {r.text[:200]}")

def write_marker(module, outcome):
    os.makedirs(MARKER_DIR, exist_ok=True)
    Path(os.path.join(MARKER_DIR, f"{module}.json")).write_text(
        json.dumps({"ts": datetime.utcnow().isoformat(), "outcome": outcome}))

def main():
    # parse --module, load_env, DST guard via should_run(...)
    # heal_loop -> outcome
    #   SEND: sync(); send(module, result);
    #         ntfy only if any auto-fix happened; write_marker(module,"SEND")
    #   HELD: ntfy("EDGE STACKER - picks HELD", findings text);
    #         write_marker(module,"HELD"); NO send
    # wrap the whole body in try/except -> ntfy on crash + write_marker "CRASH"
```

**Step 2: Tests** — mock `requests`; assert SEND posts the webhook and HELD does
not; assert a marker is written in every branch.

**Step 3: Commit** `"pipeline.py: SEND/HELD paths, ntfy, marker, crash guard"`.

---

## Task 7: Dead-man's-switch

**Files:** Create `deadman.py`.

**Step 1: Implement** — given `--module`, read its marker; if missing or older
than today's fire, ntfy `"EDGE STACKER - <module> pipeline did NOT run"`.

**Step 2: Commit** `"deadman.py: alert if a pipeline fire never completed"`.

---

## Task 8: Cron migration + slim `audit.py`

**Files:** VPS crontab; remove `run_afternoon_nhl.sh`, `run_afternoon_mlb.sh`,
`run_audit.sh`; modify `audit.py`.

**Step 1:** Repoint `audit.py` to import its checks from `shared/audit_checks.py`
(delete the now-duplicated bodies). Keep its CLI for ad-hoc use.

**Step 2:** New crontab (DST pattern — two UTC times, `should_run` guards inside):
```
0 19 * * *  cd /root/edge-stacker && venv/bin/python pipeline.py --module mlb_f5  >> logs/pipeline.log 2>&1
0 20 * * *  cd /root/edge-stacker && venv/bin/python pipeline.py --module mlb_f5  >> logs/pipeline.log 2>&1
30 20 * * * cd /root/edge-stacker && venv/bin/python pipeline.py --module nhl_sog >> logs/pipeline.log 2>&1
30 21 * * * cd /root/edge-stacker && venv/bin/python pipeline.py --module nhl_sog >> logs/pipeline.log 2>&1
0 23 * * *  cd /root/edge-stacker && venv/bin/python deadman.py --module mlb_f5   >> logs/pipeline.log 2>&1
0 1 * * *   cd /root/edge-stacker && venv/bin/python deadman.py --module nhl_sog  >> logs/pipeline.log 2>&1
```
Remove the old `run_afternoon_*` and `run_audit.sh` cron lines. `git rm` the
three shell scripts.

**Step 3: Commit** `"Migrate to pipeline.py cron; remove legacy scripts"`.

---

## Task 9: VPS dry-run E2E + go-live

**Step 1:** On the VPS, run `pipeline.py --module mlb_f5` manually (outside the
DST hour `should_run` will exit early — temporarily call `heal_loop` directly, or
add a `--force` test flag) with the webhook URL pointed at the n8n **test**
webhook. Verify: generate → audit → SEND, test email lands.

**Step 2:** Force a HELD path (e.g. stop the n8n container) → verify ntfy fires,
no email, marker says HELD.

**Step 3:** Restore n8n, remove `--force`, confirm crontab, push everything,
`git pull` on the VPS. Pipeline is live for the next scheduled fire.

**Step 4: Commit** any test-flag cleanup.

---

## Risks

- `main.py --json-only` must print ONLY JSON to stdout (logging goes to stderr/file).
  Verify in Task 4; if it prints anything else, fix the stream separation.
- n8n webhook expects the exact JSON shape `main.py` produces — `pipeline.py`
  forwards it byte-for-byte (POST `json=result`), so no contract change.
- `MAX_ATTEMPTS` re-runs re-fetch the Odds API (credits ~10-20 each). 3 attempts
  worst-case ~60 credits — fine against the ~11k balance.
