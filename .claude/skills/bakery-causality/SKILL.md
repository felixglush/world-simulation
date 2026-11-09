---
name: bakery-causality
description: "Check that cause and effect in the bakery simulation stay realistic: price, bake quantity, counter and cleaner staffing, baker hours, satisfaction, morale and quits, opening hours, ingredient cost, marketing, rivals, weather, the ledger and score identities, the state machines, and whether firing a baker pays. Use after any change to worlds/bakery/bakery_world/engine.py, params.py, rota.py, catalog.py, exogenous.py, records.py (draw_reviews), score.py, managed.py, actions.py or machines.py, or when asked whether the bakery sim teaches real lessons."
---

# Bakery causality

The bakery world exists so a manager model can learn real lessons from it. A wrong sign (dearer goods selling more, a dirty shop earning stars) or a wrong size (labor at 60% of sales, 10% staff turnover) teaches the wrong lesson without any test failing. These checks run the real engine and say, for each cause and effect a bakery owner would agree with, whether the code gets the sign right and the size into a plausible range.

## Does my change touch this?

Yes if the diff touches any file or function in the coverage record below. Constants count: `BETA`, `CLEAN_PER_HOUR`, `BAKER_HANDS_ON`, `quit_hazard`, `SEASONAL_AMPLITUDE`, `BATCH_LABOR_FACTOR` and the like move magnitudes.

## Run

From the repository root:

```
PYTHONPATH=worlds/bakery uv run --frozen python worlds/bakery/verify/causal.py
```

About 90 s with 4 jobs (the first run also builds seed 1-3 handoffs, about 8 s each, cached in `runs/bakery/verify/` (gitignored) under a hash of the `bakery_world` source, so any code change rebuilds them). Options: `--only P1,S2` for some checks, `--seeds N` (default 6), `--days N` (default 28), `--draws N` (default 40), `--jobs N`. Stdlib `random` with fixed seeds only; no model calls, no extra dependencies. It is not collected by `pytest worlds/bakery`.

To prove the checks can still fail (run this after editing a check):

```
PYTHONPATH=worlds/bakery uv run --frozen python worlds/bakery/verify/mutants.py
```

About 10 minutes. Each mutant in `verify/mutants.py` swaps one exact snippet of a `bakery_world` module's source (flip a sign, drop a term) and re-executes the module in memory, in its own process; nothing is written to disk. Every mutant must print `caught`. `mutants.py NAME` runs one.

## Read the output

Each line is `id  verdict  known  claim`, then the measured number, the expected range and the code that produces it.

- `holds`: sign right, and size in range where the claim gives one.
- `fails`: wrong sign, broken identity or broken state rule.
- `magnitude`: sign right, size outside the range.

`known` is the verdict at the sync commit (`KNOWN` in `causal.py`). The command exits 1 and marks `<-- CHANGED` when any verdict differs from it, whether something broke or a finding got fixed.

When a check changes:

1. Rerun it with `--seeds 12` to rule out noise. Paired runs share the world seed and the day rng, but a cause that changes how many draws a day takes shifts the rng after that point, so small effects are noisy.
2. If it still differs, decide whether the code or the check is wrong. Open the code in the check's `code:` line. A check is wrong only if it measures something other than its claim (for example it reads a constant the engine no longer uses). Never widen a range or loosen a sign test to make a check pass; a finding stays a finding until the code changes.
3. If the change was intended (you fixed a finding or deliberately changed the economics), update `KNOWN`, rerun `mutants.py`, and move the sync commit below.

## Claims and how each is checked

Paired runs use `town.build_world(seed, established=True)` and `engine.simulate_day` with the same seed and day rng in both arms. Generated-input checks call the pure helpers with stdlib `random` draws. The baker check and the ledger checks use a real `ManagedWorld` from `management.load` on a cached handoff.

| id | claim | method |
|---|---|---|
| P1 | +10% on every price cuts units, elasticity -0.5 to -1.2 | paired, 56 days |
| P2 | revenue changes by (1+dQ)(1.1)-1, less than +10% | paired, 56 days |
| P3 | +20% on croissants cuts their units (-1 to -3), not the rest | paired, 28 days |
| Q1 | baking more raises waste and lowers unmet requests | plan x0.7/x1/x1.3 |
| Q2 | waste is 5-15% of units baked | 28 days |
| S1 | wait rises with arrivals, falls with capacity; walkaways rise with wait | generated, `_wait_minutes`, `_walkaway_probability` |
| S2 | one counter worker fewer means longer waits and more walkaways | paired |
| S3 | walkaways are 1-10% of arrivals | 28 days |
| K1 | more cleaner hours keep the shop cleaner, more visits make it dirtier | generated, `_tidy` |
| K2 | no cleaner: dirtier shop, fewer stars | paired, `records.draw_reviews` |
| K3 | dirty shop: lower reputation, fewer visits | paired, 56 days |
| B1 | more baker hours never bake fewer units or strain more | generated, `_bake` |
| B2 | strain lowers quality, down to a floor | sweep, `_build_offers` |
| B3 | short baker shifts: less baked, more strain, fewer stars | paired |
| R1 | happier customers visit more and raise reputation (every seed), lapse less (all seeds together, starting customers) | paired |
| H1 | hours cut far below contract lower morale and raise quits | paired, 90 days |
| W1 | pay below market lowers morale and raises quits | paired, 90 days |
| W2 | turnover at market pay is 30-60% a year | engine hazard read by bisection |
| W3 | quit elasticity to pay is -0.5 to -4 | same |
| AB1 | sickness takes 2-4% of shifts, leave about 4 weeks a year, absences uncovered | 365 days |
| O1 | closing at 14:00 loses afternoon sales only | paired |
| I1 | dearer flour raises cost by grams x price, moves no sale | paired, exact |
| I2 | food and packaging are 25-35% of sales | 28 days |
| L1 | labor is 25-35% of sales | 28 days |
| ST1 | better stars bring more newcomers and word of mouth | paired |
| M1 | marketing raises awareness, concave, and brings visits | generated `_update_awareness` + paired |
| C1 | a rival opening takes 5-30% of visits | paired |
| E1 | rain and extreme temperatures lower visits | `_visit_factors` over a year |
| E2 | best vs worst month differ by 10-40%, December peak | `_visit_factors` with the seeded weather |
| E3 | cold weather moves the mix toward hot drinks | paired |
| A1 | cash change = that day's ledger; each shop's P&L = what it posted | 40 managed days with random actions, exact |
| A2 | net worth moves one for one with cash, monotone in operating profit | generated amounts |
| A3 | revenue = sale lines, wages = shifts, on-costs = 12% | exact |
| SM1 | no exit from a final state, no unreachable or dead state | exhaustive over `machines.MACHINES` |
| SM2 | only employed or on-notice staff work and are paid | random staff states every day |
| SM3 | a quit is notice, then leaving 14 days later | raised hazard |
| SM4 | a shop that is not trading sells and pays nothing | exhaustive over `LocationState` |
| BK1 | firing either of two bakers does not pay over 52 weeks | `management.manage`, seed 3, freeze vs fire |
| BK2 | a baker on 48 hours (8 over contract) loses morale | paired, 90 days |
| FX1-FX5 | fixing the habits (with an afternoon hand), one more counter worker, the cleaner on four days instead of two, a new item every 8 weeks, $500 of flyers every 4 weeks each raise last-13-week sales and operating profit | 26-week managed runs, seeds 1-3, vs Freeze, 2 of 3 seeds |
| FX6 | the oracle grows year-on-year sales | 52-week oracle run, last 13 weeks vs the same weeks before handover |
| MX1-MX9 | +25% on every price, firing the cleaner, a counter worker or a baker, hours cut to 09-13, baking 40% less, paying 15% below market (52 weeks) and dropping the 3 best sellers cut sales; baking 40% more cuts profit | managed runs vs Freeze, strictly down on 2 of 3 seeds (MX9: sales down by more than 4%) |

The state machines are checked by enumeration, not TLA+: each machine is a small table, and the engine steps entities one at a time inside one single-threaded day loop, so there are no interleavings to explore.

## Coverage record

Sync commit: `2bb9516` (branch `bakery-unit9`). Move it forward only after running both commands at the new commit and confirming every check still measures what its claim says.

- `worlds/bakery/bakery_world/engine.py`: `simulate_day`, `_run_location`, `_roster`, `_effort`, `_counter_capacity`, `_wait_minutes`, `_walkaway_probability`, `_shifts`, `ingredient_cost`, `post`, `prices_paid`, `_age_stock`, `_bake`, `_build_offers`, `_visit_factors`, `_visitors`, `_note_habit`, `_missing_share`, `_tidy`, `_update_awareness`, `_update_satisfaction`, `_staff_day`, `_advance_customers`, `market_wage`, and the constants they read
- `worlds/bakery/bakery_world/records.py`: `draw_reviews`
- `worlds/bakery/bakery_world/params.py`: `ELASTICITY`, `SCALARS`, `draw_hidden_params`
- `worlds/bakery/bakery_world/rota.py`: `worked`, `assign_rota`, `shift_window`, `ON_ROTA`
- `worlds/bakery/bakery_world/catalog.py`: `CATALOG`, `BATCH_LABOR_FACTOR`
- `worlds/bakery/bakery_world/exogenous.py`: `SeasonalWeather`, `SeasonalFootTraffic`
- `worlds/bakery/bakery_world/town.py`: `build_world`, `STARTING_STAFF`, `RIVAL_ID`; `decay.py`: `RIVAL_APPEAL`
- `worlds/bakery/bakery_world/machines.py`: `MACHINES`, `advance`, `step`
- `worlds/bakery/bakery_world/score.py`: `net_worth`, `going_concern`, `operating_profit`
- `worlds/bakery/bakery_world/managed.py`: `let_go`, `depart`, `retire_founder`; `management.py`: `load`, `manage`, `advance_day`; `actions.py`: `perform` and the actions `_random_action` draws (`set_price`, `spend_marketing`, `draw_credit`, `repay_credit`, `run_promotion`, `post_job`, `buy_market_research`, `set_hours`, `set_wage`, `fire`) plus `set_rota` in BK1; `baselines.py`: `Freeze`, `Heuristic.staff_up` (FX2), `Oracle` (FX6), and in the FX/MX claims `set_hours`, `set_bake_plan`, `choose_supplier`, `add_menu_item`, `remove_menu_item`, `hire`, `set_wage`

Not covered: curveballs (they run inside the managed claims but are not checked on their own), the founder policy's own decisions, history generation and calibration, the records database, the manager and analyst, expansion (second shops), loans and credit interest beyond the ledger identity, `hire_casual` and the heuristic's relief cover.

## Findings at the sync commit

All 54 checks hold. Weak but holding effects worth watching: MX1 (+25% prices: sales -1 to -11% by seed, profit mixed, because the shop often sells out), FX3 (profit up on 2 of 3 seeds) and MX8 (one seed carries most of the effect). The previous sync commit's findings (P1, S3, W2, W3, L1, E2 magnitude; B1, BK1, BK2 fails) were fixed in unit 9. MX9 now measures -9.4%, -8.8%, -8.5% (seeds 1-3): customers whose habit leaned on the dropped items visit less, and recover only as they settle on substitutes; the `favourites_forgotten` mutant (no visit penalty) measures -1.8%, +1.0%, -1.1%, the old MX9 numbers, and fails the 4% floor.
