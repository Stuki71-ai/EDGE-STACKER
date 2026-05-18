# Claude-API Deep Audit in the Pipeline — Design

**Status**: Approved
**Date**: 2026-05-18
**Scope**: Make `pipeline.py`'s every-fire audit identical to the ad-hoc Claude
audit routine — no discrepancy between the automatic money-gate and the manual
deep audit.

## Goal

Today the pipeline's every-fire gate is the mechanical `shared/audit_checks.py`
(deterministic Python). The ad-hoc routine (`edge-stacker-2240-audit/SKILL.md`)
is deeper — it adds Claude's judgment (reading formulas vs spec, interpreting
anomalies, a GREEN/BUG verdict). That gap means most fires only get the shallow
check; the deep check happens only when the user manually triggers the routine.

Close the gap: **every fire gets the full deep audit.**

## Approved decisions

1. **The pipeline calls the Claude API every fire** to run the full deep audit
   (judgment-level), before sending. This deliberately reverses the earlier
   "no LLM in the pipeline path" stance — accepted with the tradeoff understood.
2. **On Claude-API failure** (down / timeout / network, after retries) the
   pipeline **falls back to the mechanical `audit_checks.py` audit**; if that is
   clean it sends. A blip must not silently miss a night.

## Approach (chosen: #1 of 3)

**Pipeline gathers the evidence deterministically; ONE Claude API call judges
it.** The LLM does judgment only — no shell access, no tools, one bounded call.
(Rejected: #2 an autonomous agentic Claude with VPS shell every fire —
unbounded, many failure modes, overengineered. #3 API-Claude with read-only
tools — more moving parts for no gain; the pipeline already knows exactly what
evidence the audit needs.)

## Architecture

```
heal_loop(module):
    generate()                      # picks JSON (unchanged)
    findings = deep_audit(module, result)   # <-- replaces run_full_audit
    classify -> self-heal / HOLD / SEND     # unchanged
```

### Components

**1. One canonical audit spec — `docs/audit-spec.md` (new, in the repo).**
The audit's checks + GREEN/BUG judgment rules, as one file. The pipeline reads
it and sends it to the API; the ad-hoc `SKILL.md` routine references it. ONE
source of truth → the pipeline audit and the routine audit cannot drift apart.
Content = the substance of the current refreshed `SKILL.md` (Phase 0 parity,
1.5 data-fetch + recompute, 2 pick sanity / 0-pick legitimacy, 2.5 design
fidelity), framed as "judge THIS freshly-generated result".

**2. `gather_evidence(module, result)` — deterministic Python.**
Collects the full evidence bundle, reusing/extending `audit_checks.py`:
- code + constant parity (`git diff HEAD`, the SPEC_CONSTANTS table, magic
  numbers, forbidden-pattern scan)
- re-fetched upstream values (ESPN SA, Odds events, MLB schedule, FIP constant,
  park-factor coverage)
- end-to-end recompute of each emitted pick — tighter than the ad-hoc routine
  (same-data, no morning-after drift)
- the fresh picks JSON, the module fire log, 0-pick context (games processed)
Returns a structured dict.

**3. `claude_api_audit(spec, evidence)` — one Anthropic API call.**
Model: Opus. System prompt = the audit spec (prompt-cached — it is static).
User message = the evidence bundle. Claude returns STRUCTURED JSON:
`{"verdict": "GREEN"|"BUG", "findings": [...], "summary": "..."}`.
Retries on transient failure.

**4. `deep_audit(module, result)` — orchestrator.**
`gather_evidence` -> `claude_api_audit` -> parse the JSON verdict -> return a
`list[Finding]` (the exact shape `heal_loop` already consumes, so `heal_loop`
barely changes). On API failure OR unparseable response after retries → log it
and **fall back to `run_full_audit`** (the existing mechanical audit).

**5. `heal_loop`** calls `deep_audit` instead of `run_full_audit`. Everything
downstream is unchanged: GREEN → `send()`; BUG → HOLD + ntfy (the ntfy body
carries Claude's verdict summary).

### Data flow

```
generate() → result
  → gather_evidence()  [Python, deterministic]
  → claude_api_audit() [1 API call: spec + evidence → JSON verdict]
        success → verdict → findings
        fail (after retries) → run_full_audit()  [mechanical fallback]
  → heal_loop: GREEN → send ;  BUG → HOLD + ntfy
```

## Boundaries (unchanged from prior decisions)

- The LLM **judges only — it never patches code.** A CODE-BUG verdict → HOLD +
  ntfy; the user fixes it. The earlier "no autonomous code patching in the
  money path" decision stands.
- The pipeline's existing infra/data self-heal loop is unchanged.

## Requirements

- `ANTHROPIC_API_KEY` added to the VPS `.env` (user action).
- Model: Claude Opus. Cost ≈ 2 fires/day × 1 call ≈ **$1–3/month**.
- Implementation should use prompt caching for the static spec (the
  `claude-api` skill covers this).

## The ad-hoc routine after this change

`edge-stacker-2240-audit/SKILL.md` references the same `docs/audit-spec.md`.
Same checks, same judgment criteria, same model. The ad-hoc routine and the
pipeline's every-fire audit are the same audit — by construction.

## Failure modes

| Failure | Behaviour |
|---|---|
| Claude API down / timeout (after retries) | fall back to mechanical `audit_checks.py`; clean → send, else HOLD |
| Claude returns unparseable / non-JSON | treat as API failure → mechanical fallback |
| Mechanical fallback finds a problem | HOLD + ntfy (existing behaviour) |
| `ANTHROPIC_API_KEY` missing | every fire falls back to mechanical audit; ntfy once so the user notices |

## Out of scope (YAGNI)

- Agentic / tool-using Claude in the pipeline (rejected approach #2/#3).
- The LLM proposing or applying fixes (judges only).
- Auditing anything other than the fresh in-hand result per fire.
