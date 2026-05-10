# MLB F5 Totals — Module Design

**Status**: Approved (Approach A — F5 Totals)
**Date**: 2026-05-10
**Module**: `modules/mlb_f5/`

## Summary

Add MLB module that emits 3 highest-conviction First-5-Innings (F5) total picks per
day. F5 markets have the largest documented structural mispricing in MLB betting
because books haven't fully adjusted to opener/bulk pitching strategies. Module
mirrors `nhl_sog/` shape (single-fire, top-3 by raw edge, market anchor blend, 8h
tipoff cap, MIN_EDGE 0.10).

## Why F5 (rejected alternatives)

- **NRFI/YRFI**: smaller edge (~0.2-0.4 runs equivalent), noisier per pick.
- **Player props**: requires lineup feed posted 1-2h pre-game; many lineups TBD at
  our 04:30 PM ET fire time.
- **Full-game totals/ML/RL**: bullpen variance dominates; harder to model well.

F5 wins on: edge magnitude, lineup independence, market depth, free data sources.

## Data sources (all free)

| Source | Used for |
|---|---|
| MLB Stats API `/api/v1/schedule?sportId=1&date=...&hydrate=probablePitcher` | confirmed starters per game |
| `pybaseball.statcast_pitcher` | starter Statcast metrics: K%, BB%, barrel%, xwOBA against, 30-day rolling |
| `pybaseball.team_batting` (split by hand) | opposing team wOBA vs LHP / vs RHP, last 30 days |
| Static park-factor table (3-year, FanGraphs) | run environment multiplier |
| OpenWeather API (already wired) | venue temp + wind speed/direction |
| Odds API `baseball_mlb` market `totals_1st_5_innings` | line + best over/under odds |

## Projection model (per game)

For each starting pitcher P facing opposing team T:

```
xFIP_P                                           # last 30 days
opp_wOBA_T_vs_hand_P                             # last 30 days
park_factor                                      # 3-year FanGraphs (1.00 = neutral)
weather_factor = f(wind_dir, wind_mph, temp_F)   # >1 = scoring up, <1 = down
expected_runs_allowed_P_F5 = (xFIP_P / 9 * 5)
                              * (opp_wOBA / .320 * 0.85 + 0.15)  # blend with .320 base
                              * park_factor
                              * weather_factor
```

Sum both teams' expected F5 runs allowed = projected F5 total.

Compare to F5 line via Poisson CDF:
- `P(over) = 1 - PoissonCDF(line, projected_total)`
- `P(under) = PoissonCDF(line, projected_total)`
- Edge = max(P(over) - implied_over, P(under) - implied_under)

## Filter pipeline (mirrors NHL)

1. `commence_time` in [now+5min, now+8h] (drops already-started + tomorrow's slate)
2. Phantom-game cross-check vs ESPN MLB scoreboard for game date
3. Vig pre-filter (`PROP_MAX_VIG = 0.08`)
4. Confirmed starter (no `probablePitcher = null`)
5. Starter has ≥5 starts this season
6. Line sanity (skip if `|projection - line| / line > 0.25`)
7. Market anchor: blend 60% model + 40% market consensus (de-vigged)
8. `MIN_EDGE = 0.10` after anchor blend
9. Sort by raw edge, take top-3

Tighter line sanity than NBA (35%) because MLB F5 totals cluster narrowly (lines
typically 4.0-5.5, so 25% of 5.0 = 1.25 runs is already a meaningful disagreement).

## Output

Same email format as NHL/NBA picks. Subject: `EDGE STACKER / MLB F5 TOTALS - DD.MM.YYYY - N picks`.

## Error handling

- MLB Stats API down → log warning, skip module (return [])
- pybaseball / Statcast slow or rate-limited → cache per-pitcher data per run (in-memory)
- OpenWeather down → use neutral weather_factor = 1.0
- Odds API returns no `totals_1st_5_innings` for an event → skip event

## Schedule + infra

- Cron entry on VPS: `30 20 * * *` and `30 21 * * *` (DST-safe), runs `run_afternoon_mlb.sh`
- Webhook to existing or new VPS n8n workflow that sends Gmail to `stuki71.alert@gmail.com`
- 8h tipoff cap inherited from runner template

## Testing plan

1. Unit test projection on a known game (manually verify projected F5 against published lines)
2. Smoke test: run module locally with `--verbose --json-only`, verify
   - Starters confirmed
   - Pitcher Statcast retrieved
   - Odds API returns F5 markets
   - Output JSON well-formed
3. Backtest 7 days using historical Odds API + retrospective gamelog → hit rate sanity check
4. Live shadow run for 3 nights (no email, just log) before enabling email

## Risks / known limitations

- **F5 market thinness**: not all books offer F5 totals; if Odds API returns 0 events with this market on a given night, output will be 0 picks (acceptable per "no picks" policy)
- **pybaseball rate limit**: Baseball Savant occasionally rate-limits; mitigation = cache per pitcher_id per run, retry with backoff
- **Park factors update mid-season**: use 3-year average; refresh annually
- **Sample-size discipline**: same as NBA/NHL — no model changes until 30+ live nights

## Out of scope (explicit YAGNI)

- Multi-bet stacks (F5 under + game over) — single market per pick
- Live in-game betting
- Lineup-confirmed batter HR props (would require lineup feed)
- Umpire factor (data scraping required, not stable enough for automation)
- "Reverse line movement" detection (would need historical odds polling)
