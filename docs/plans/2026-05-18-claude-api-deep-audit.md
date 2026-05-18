# Claude-API Deep Audit — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `pipeline.py`'s every-fire audit identical to the ad-hoc Claude deep audit — the pipeline gathers evidence deterministically, then makes one Claude API call to judge it (GREEN/BUG), falling back to the mechanical `audit_checks.py` audit if the API is unreachable.

**Architecture:** New `shared/deep_audit.py` with `gather_evidence()` (deterministic Python) + `claude_api_audit()` (one Anthropic API call, prompt-cached spec, structured-JSON verdict) + `deep_audit()` (orchestrator → `list[Finding]`, falls back to a passed-in mechanical-audit callable). `pipeline.py:heal_loop` calls `deep_audit` instead of `run_full_audit`. The audit spec is one canonical repo file used by both the pipeline and the ad-hoc routine.

**Tech Stack:** Python 3, `anthropic` SDK, existing `shared/audit_checks.py` / `pipeline.py`.

**Reference:** Design doc `docs/plans/2026-05-18-claude-api-deep-audit-design.md`. REQUIRED SUB-SKILL for Task 3 (the API call): use the `claude-api` skill — include prompt caching, use the current Opus model id.

---

## Task 1: Canonical audit spec — `docs/audit-spec.md`

Not TDD — a documentation artifact. One source of truth for the audit, used by both the pipeline (sent to the API) and the ad-hoc routine.

**Files:**
- Create: `docs/audit-spec.md`
- Modify: `C:\Users\istva\.claude\scheduled-tasks\edge-stacker-2240-audit\SKILL.md`

**Step 1:** Create `docs/audit-spec.md`. Content = the *substance* of the current refreshed `edge-stacker-2240-audit/SKILL.md` — the checks and the GREEN/BUG judgment rules — but reframed as **"judge THIS freshly-generated picks result"** (not "find the last fire"): Phase 0 code/constant parity, Phase 1.5 data-fetch verification + end-to-end pick recompute, Phase 2 per-pick sanity + 0-pick legitimacy, Phase 2.5 design fidelity. Keep the audit PRINCIPLES (never trust logs alone, full data-fetch control, recompute every emitted pick, GREEN-or-BUG-never-INCOMPLETE). Drop the routine-only mechanics (finding the fire via markers, the report template). End with the **output contract** (see Task 3 Step 1) so the spec doc itself defines the JSON the judge must return.

**Step 2:** Update `SKILL.md` so the ad-hoc routine references the canonical spec — replace its phase bodies with: "The canonical audit — checks + GREEN/BUG rules — is `docs/audit-spec.md` in the EDGE STACKER repo. Execute it against the last completed fire (find it via `logs/pipeline_markers/`)." Keep the routine's fire-finding mechanics (markers/logs) and report template; the CHECKS now come from the one spec. This guarantees the pipeline audit and the routine audit cannot drift.

**Step 3:** Commit.
```bash
git add docs/audit-spec.md
git commit -m "Canonical audit spec — single source for pipeline + ad-hoc routine"
```
(`SKILL.md` is outside the repo — save it in place, no commit.)

---

## Task 2: `gather_evidence()` — deterministic evidence bundle

**Files:**
- Create: `shared/deep_audit.py`
- Test: `tests/test_deep_audit.py`

**Step 1: Write failing tests**
```python
# tests/test_deep_audit.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import deep_audit

def test_gather_evidence_has_required_sections(monkeypatch):
    # gather_evidence must return a dict covering every audit phase
    monkeypatch.setattr(deep_audit, "_collect", lambda module, result: {
        "module": module, "picks": result.get("picks", []),
        "code_parity": "clean", "data_fetch": {}, "recompute": [],
        "fire_log": "", "mechanical_findings": [],
    })
    ev = deep_audit.gather_evidence("mlb_f5", {"picks": []})
    for key in ("module", "picks", "code_parity", "data_fetch",
                "recompute", "fire_log", "mechanical_findings"):
        assert key in ev
```

**Step 2:** Run → FAIL (`deep_audit` does not exist).

**Step 3: Implement `gather_evidence`** in `shared/deep_audit.py`. It collects, for the given `module` + fresh `result`:
- `mechanical_findings` — run every `audit_checks` check (`check_code_parity`, `check_infra`, `check_data_fetch`, `check_picks`, `recompute_pick`) and include their `Finding`s (serialised: kind/text/pick_ref). These are evidence, not the verdict.
- `code_parity` — `git diff HEAD -- modules/ shared/ pipeline.py staking.py config.py`, the SPEC_CONSTANTS values, the magic-number greps, the forbidden-pattern scan.
- `data_fetch` — the re-fetched upstream SUMMARIES (NHL SA: team count / placeholder count / range; MLB FIP constant; MLB park-factor coverage). Do NOT re-fetch the Odds API (it costs credits and `generate()` already did) — read odds/credit info from the fire log instead.
- `recompute` — for each emitted pick, the end-to-end recomputed projection/edge vs the logged value. Same-data (fire time) → expect a tight match.
- `picks` — the fresh `result["picks"]`.
- `fire_log` — contents of the newest `logs/edge-stacker-*.log` (this run's `main.py` module detail).
Keep the heavy work behind a private `_collect(module, result)` so tests can stub it. `gather_evidence` returns the dict.

**Step 4:** Run the test → PASS. Smoke-test on the VPS: `python3 -c "from shared import deep_audit; import json; print(list(deep_audit.gather_evidence('mlb_f5', {'picks':[]}).keys()))"` → prints the section keys, no exception.

**Step 5:** Commit (`shared/deep_audit.py`, `tests/test_deep_audit.py`).

---

## Task 3: `claude_api_audit()` — one Anthropic API call

**REQUIRED SUB-SKILL:** use the `claude-api` skill. Use the current Opus model id, prompt-cache the static spec, follow the SDK best practices.

**Files:**
- Modify: `shared/deep_audit.py`
- Modify: `tests/test_deep_audit.py`
- Modify: `requirements.txt` (add `anthropic`)

**Step 1: Define the output contract.** The judge must return ONLY this JSON:
```json
{"verdict": "GREEN" | "BUG",
 "findings": [{"kind": "INFRA"|"DATA"|"CODE", "text": "...", "pick_ref": ""}],
 "summary": "one-paragraph verdict"}
```
GREEN → `findings` empty. This maps 1:1 onto the existing `audit_checks.Finding`.

**Step 2: Write failing tests** (mock the `anthropic` client — no real API call):
- a mocked client returning a valid GREEN JSON → `claude_api_audit` returns `{"verdict":"GREEN",...}`.
- a mocked client returning BUG JSON → parsed through.
- the request is built with the spec as a cache-controlled system prompt and the evidence as the user message (assert on the captured call args).
- a transient error (mock raises, then succeeds) → retried; persistent error → raises after the retry budget.

**Step 3: Implement `claude_api_audit(spec, evidence)`** in `shared/deep_audit.py`:
- `from anthropic import Anthropic`; client uses `ANTHROPIC_API_KEY` from the env.
- System prompt = `spec` (the `docs/audit-spec.md` text) with `cache_control` set (static → cached across fires).
- User message = the evidence dict, JSON-serialised.
- Model = current Opus id. Ask for the Step-1 JSON only.
- Parse the response as JSON; if it is not valid JSON matching the contract → raise (callers treat this as an API failure → fallback).
- Retry transient failures (timeout / 5xx / rate-limit) a few times with backoff; raise after the budget.

**Step 4:** Run tests → PASS. **Do NOT make a real API call in tests.**

**Step 5:** Commit.

---

## Task 4: `deep_audit()` — orchestrator with mechanical fallback

**Files:** Modify `shared/deep_audit.py`, `tests/test_deep_audit.py`.

**Step 1: Write failing tests** (mock `gather_evidence` + `claude_api_audit`):
- GREEN verdict → `deep_audit` returns `[]`.
- BUG verdict with two findings → returns two `audit_checks.Finding` objects with the right `kind`/`text`/`pick_ref`.
- `claude_api_audit` raises (API unreachable) → `deep_audit` calls the passed-in `fallback` and returns ITS result; assert the fallback was called.
- `claude_api_audit` returns malformed data → also treated as failure → fallback used.

**Step 2: Implement `deep_audit(module, result, fallback)`**:
- Reads the spec from `docs/audit-spec.md` (path relative to the repo root).
- `evidence = gather_evidence(module, result)`.
- `try: verdict = claude_api_audit(spec, evidence)` → map `verdict["findings"]` to `Finding` objects (GREEN → `[]`). 
- `except Exception` → `logger.warning("deep audit unavailable — mechanical fallback")` → `return fallback(module, result)`.
- `fallback` is passed in (avoids a circular import with `pipeline.py`).
- If `ANTHROPIC_API_KEY` is absent, skip the API call entirely and use the fallback (and log it once).

**Step 3:** Run tests → PASS. **Step 4:** Commit.

---

## Task 5: Wire `deep_audit` into `pipeline.py:heal_loop`

**Files:** Modify `pipeline.py`, `tests/test_pipeline_loop.py`.

**Step 1:** In `heal_loop`, replace the `run_full_audit(module, result)` call with
`deep_audit.deep_audit(module, result, fallback=run_full_audit)`
(`from shared import deep_audit` at the top of the function, matching the existing local-import style). `run_full_audit` STAYS in `pipeline.py` — it is the fallback. The returned `list[Finding]` is the same shape, so the rest of `heal_loop` (classify / self-heal / HOLD / SEND) is unchanged.

**Step 2:** Update `tests/test_pipeline_loop.py` — the existing loop tests mock the audit; point the mock at `deep_audit.deep_audit` (or `pipeline`'s reference to it). Keep every existing scenario (clean→SEND, CODE→HELD, DATA→drop, INFRA→autofix, never-clean→HELD). Add one test: `deep_audit` raising internally is impossible (it self-falls-back), but verify `heal_loop` still works when `deep_audit` returns the fallback's findings.

**Step 3:** Run `python -m pytest tests/ -q` — full suite green (3 pre-existing failures are gone since the dead-module purge; expect 0 failures). **Step 4:** Commit.

---

## Task 6: VPS deploy + end-to-end verification

**Step 1:** Add `ANTHROPIC_API_KEY` to the VPS `.env` (`/root/edge-stacker/.env`). **This is a user action** — the plan executor must request the key from the user; do not invent one. If it is not yet available, deploy anyway (the pipeline falls back to the mechanical audit and ntfys once — see Task 4).

**Step 2:** Install the `anthropic` package into the VPS venv: `ssh root@vmi3157940.contaboserver.net 'cd /root/edge-stacker && venv/bin/pip install anthropic'`. Confirm `requirements.txt` lists it.

**Step 3:** Push `origin/main`; `git pull` on the VPS.

**Step 4: E2E** on the VPS — run `pipeline.heal_loop` for both modules (safe — `heal_loop` does not send):
```
ssh root@vmi3157940.contaboserver.net 'cd /root/edge-stacker && source venv/bin/activate && set -a; source .env; set +a && python3 -c "
import pipeline
for m in [\"mlb_f5\",\"nhl_sog\"]:
    o,res,f,af = pipeline.heal_loop(m)
    print(m, \"->\", o, \"| findings:\", [x.kind for x in f])
"'
```
Confirm: both reach `SEND` (or a legitimately-explained `HELD`); the `pipeline.log` shows the deep audit ran (a real API call, not the fallback) when the key is present. If the key is absent, confirm the fallback path ran cleanly.

**Step 5:** Commit any deploy-only changes (`requirements.txt`).

---

## Risks

- **Odds API credits** — `gather_evidence` must NOT re-fetch the Odds API (read odds info from the fire log). Re-fetching ESPN/MLB-Stats (free) is fine.
- **Evidence size** — the evidence bundle (fire log + picks + recompute) should be tens of KB, well within context. If a fire log is huge, truncate it in `gather_evidence`.
- **Verdict trust** — the LLM judges only; a CODE-BUG verdict HOLDs the email (never auto-patches). `heal_loop`'s existing classification routing is unchanged.
- **First live fire** — after deploy, the next scheduled fire is the first real Claude-API-audited send; `deadman.py` + the HOLD/ntfy path are the safety net.
