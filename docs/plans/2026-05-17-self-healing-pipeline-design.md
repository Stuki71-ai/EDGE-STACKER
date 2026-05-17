# Self-Healing Pick Pipeline — Design

**Status**: Approved
**Date**: 2026-05-17
**Scope**: New VPS-native orchestrator that gates the picks email behind a full audit.

## Goal

Replace the current fire-and-forget flow (cron generates picks → curls n8n webhook →
email sent immediately, audited only afterwards) with an **autonomous, iterative,
self-healing pipeline** that runs entirely on the VPS:

> scheduled run → keep result → full audit → self-heal what can be safely healed →
> iterate until clean → sync → only then send the email.

The picks email **only ever contains picks that passed the full audit**. Doubtful
picks never reach subscribers.

## Hard constraints (from the user)

- Runs **entirely on the VPS**. Pure Python. No dependency on the user's PC.
- **No Claude Code routine** anywhere in the live path.
- Robust, not overengineered.
- One pipeline **per workflow** (NHL `nhl_sog`, MLB `mlb_f5`), independent.

## One honest limitation

A pure-Python VPS script can mechanically heal **infra** (restart n8n, fix a
file/permission) and **data anomalies** (drop a bad pick). It **cannot** rewrite
model logic — analysing a logic bug and writing a correct patch needs a human
(with a backtest). Autonomously patching a betting model and shipping it
unreviewed is the "try-and-see on real money" the project explicitly bans.

Therefore self-healing is split:

| Finding class | Action |
|---|---|
| Infra (n8n down, file/permission drift) | auto-fix on the VPS, re-loop |
| Data anomaly scoped to one pick (recompute mismatch, postponed game, bad upstream value) | drop that pick, re-audit the rest |
| Code / design bug (constant drift, logic error, design non-compliance) | **HOLD** the email, ntfy the user — never auto-patched |

The code-bug HOLD path is a safety net expected to stay dormant: both modules are
deep-audited and clean before the pipeline goes live. If it ever fires, the module
was not audited thoroughly enough.

## Architecture

**New component:** `pipeline.py` on the VPS — the orchestrator. Invoked
`python pipeline.py --module {nhl_sog|mlb_f5}`.

**Replaces:** `run_afternoon_nhl.sh`, `run_afternoon_mlb.sh`, `run_audit.sh`
(no more direct-send, no separate post-hoc audit).

**Unchanged:** the n8n email workflows + webhooks (`edge-stacker-nhl`,
`edge-stacker-mlb`) stay as the email sender. `audit.py`'s check logic is
refactored to be importable by `pipeline.py` (and keeps its CLI for ad-hoc use).

**Cron** (per workflow, existing DST-guard pattern — two UTC times + `HOUR_ET`
guard inside the script):
- NHL: `pipeline.py --module nhl_sog` at 04:30 PM ET
- MLB: `pipeline.py --module mlb_f5` at 03:00 PM ET

## The loop (per fire)

```
MAX_ATTEMPTS = 3
attempt = 1
while attempt <= MAX_ATTEMPTS:
    1. GENERATE — run main.py for the module; keep the picks JSON (the "result").
    2. AUDIT — full audit of THAT fresh result (not a past log):
         - git / constant parity vs HEAD, forbidden-pattern scan
         - upstream data-fetch re-verification (re-fetch sources, diff)
         - one emitted pick recomputed end-to-end
         - per-pick sanity: edge/line bounds, 8h window, postponed-game guard
    3. CLEAN (zero findings) -> break -> SEND
    4. classify findings:
         - INFRA            -> auto-fix -> attempt++ -> continue
         - DATA (per pick)  -> drop the pick(s); re-audit remainder;
                               clean remainder -> SEND ; else attempt++
         - CODE / DESIGN    -> HELD ; break
    attempt++
else: HELD            # attempts exhausted, still not clean

SEND:
    - "sync": commit + push any auto-fix that touched tracked files
      (usually a no-op — infra fixes are not git changes)
    - POST the final clean JSON to the n8n webhook -> email sends
    - ntfy ONLY if something was auto-fixed (transparency); else silent
    - write completion marker

HELD:
    - NO webhook POST, NO email
    - ntfy 'Stuki71-Findings' with the exact findings
    - write completion marker
```

## Robustness

- `pipeline.py` wraps the whole run in a top-level `try/except` → ntfy on any
  unhandled crash.
- A tiny separate cron (~2 h after fire time) checks for the completion marker
  written by that fire; if absent → ntfy "pipeline did not run/complete — no
  email sent". A silent miss is impossible.
- `MAX_ATTEMPTS = 3` bounds the loop; each attempt re-fetches odds (fresh lines).
  No clock deadline (per user decision) — bounded by attempt count only.

## Failure behaviour (per user decisions)

- Loop cannot reach a clean audit within `MAX_ATTEMPTS`, OR a code/design bug is
  found → **HOLD the entire email**, ntfy. Nothing sends until the user resolves
  it. No partial/caveated send.
- Fire times stay (MLB 03:00 PM ET, NHL 04:30 PM ET). On a slow night the email
  may land later; acceptable.

## Out of scope (explicit YAGNI)

- Autonomous code/model patching (Claude API in the loop) — deliberately excluded;
  unsafe for a money model, violates "no try-and-see".
- Per-pick caveated sends — excluded; hold-all is the chosen failure mode.
- Clock-based deadline / earlier fire times — excluded by user decision.

## Pre-launch requirement

Deep-audit `nhl_sog` to the same depth already applied to `mlb_f5`, so the
pipeline launches on two clean modules and the code-bug HOLD path stays dormant.
