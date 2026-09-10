# Kaggriculture research journal

## 2026-09-10 — the contention asymmetry

Reality: SpaTaro leads at 3101.2, top-ten cut 2954.6, we sit at 2525.5, rank 489.

### The measurement that reframes the campaign

Terminal cash, seed 1000, same engine, read from the observation:

| pairing | ours | theirs |
|---|---:|---:|
| own planner vs own planner | **87,104** | 87,104 |
| v006 vs v006 | **74,619** | 74,619 |
| own planner vs v006 | **35,199** | **102,139** |

**Our owned planner's mirror economy is larger than v006's** (87,104 against 74,619). It is
not economically weaker. What happens when the two meet is a swing of roughly 52,000 driven
entirely by contention for the town's shared demand: v006 gains +27,520 over its mirror while
we lose −51,905 against ours.

MEASURED FACT. The interpretation that follows — that v006 is competitively dominant rather
than economically strong, and our planner competitively fragile rather than economically weak
— is consistent with every earlier failure in this campaign, because every knob tried so far
adjusts the size or efficiency of our own economy and none of them touches contention.

### Eight interventions, four layers, all rejected

Paired, 20 dev worlds, both seats, zero failures. Against the same base unless noted.

| layer | intervention | result |
|---|---|---|
| macro genome | `animal_cap` 22→12 / 22→6 | 0.300 / 0.450 |
| macro genome | `plant_until_hour` 18→20/22/23 | 0.550 / 0.625 / 0.650; **0.560 [0.480, 0.635]** at 200 games |
| crop economics | `demand_horizon` 10 / 20 | 0.350 / 0.200 |
| crop economics | `demand_horizon` 10 + `max_hands` 12 | 0.150 |
| executor | `reposition_idle` | 0.575; PASS 21.2%→15.9%, margin vs v006 −81,266→−74,753 |
| sale policy | `sale_floor_fraction` 0.5 / 0.0 | 0.000 vs v006; margin −73,763 / −72,728 |
| forecast | `opponent_weight` 0.0 | 0.000 vs v006; margin −79,368 |
| forecast | `opponent_weight` 2.5 / 5.0 | 0.000 vs v006; margin −80,202 / −85,183 |

The best of these recovers 8,500 of an 81,000 deficit and converts none of it into a win.

### Executor idleness, instrumented and sized

With `diagnostics` on, over 719 turns: 38.32 jobs generated per turn for 8.56 units — work is
4.5× units, there is no shortage. Idle units 1.74 per turn, and the cause is
**jobs_unreachable_or_gated 987 of 1,254 events (79%)**, `no_job_generated` 243, and
`all_jobs_taken` only 24. Units have work and cannot reach it before the day ends.
`reposition_idle` fixes exactly that — PASS falls 5.3 points — and is worth almost nothing.
Executor idleness is therefore real, understood, and not the constraint.

### Intraday sale timing is not the differentiator

Median first sale of the day is hour 1 for both agents. Order counts differ (414 against 240)
but orders are requests, not executions, and may not be used for magnitude.

### Next

The contention swing is the only mechanism measured at the right order of magnitude
(~52,000). Nothing in the current parameter surface expresses it. The next architectural
level is a policy that reasons about the opponent's claim on daily demand rather than about
its own forecast.

### Second batch, same run — eleven interventions now rejected

| intervention (all vs v006 unless noted) | score | median margin |
|---|---:|---:|
| base `{economic_planner, land_reservation}` | 0.000 | −81,266 |
| `land_reserve_holds` False | 0.000 | −81,266 (identical) |
| `animal_cap` 8 | 0.000 | −77,918 |
| `animal_cap` 4 | 0.000 | −112,229 |
| `sale_floor_fraction` 0.0 | 0.000 | −72,728 |
| `reposition_idle` | 0.000 | −74,753 |

Nothing recovers more than about 8,500 of 81,000.

### Static arithmetic that matters, seed 1000 at t360

Town demand per day against price, same town for both agents:

    STRAWBERRY  19/day @ 206      EGG     19/day @  54
    WHEAT       25/day @  44      CARROT  19/day @  46
    TOMATO       7/day @  64      MELON    1/day @ 140
    MILK         1/day @  26      WOOL     1/day @  37

v006 stands 8 cows into a town that buys one milk a day at 26 coins, and still finishes at
102,139 against our 35,199. **Allocation quality is therefore not what separates us.** Crop
scores in our planner are strongly positive throughout (STRAWBERRY 35–47), so the crop
economics are not gating either, yet we hold 6 crops on 25 free tiles at t360 while v006 holds
57 on none.

The binding constraint visible in the trace is cash: 628 coins at t240 cannot buy twelve
strawberry seeds at 100 each. We are in a capital trap — too poor to buy the seed that would
capture the 19-a-day, 206-coin demand — while v006 has 2,253 by turn 150 and 24,160 by t360.

### Next

Money-first reverse engineering of the trap: find the first turn our cash trajectory diverges
from v006's in the same world and attribute the spend by category from executed state. Do not
test another parameter until that first controllable cause is identified.

### The first controllable cause, and why "improving our economy" cannot work

Cash trajectories in the same world (seed 1000) diverge sharply between t240 and t264: v006
goes from 2,111 to 15,755 in a single day. The trace names the event exactly — it sells melon,
in blocks of 6, 24, 12, 6, 6, 6 and 12 across turns 250–265, for roughly +16,000. Melon seed
costs 80, yields 6, and sells near 250, so one tile returns about eighteen times its seed. It
is the single best object in the game and it must be planted by about day 4 to harvest on
day 10.

Our planner is not choosing the wrong crop: it holds 13 melon tiles at t144 against v006's 12.
It is **two days late**. At t96 we have zero crops standing while v006 has 7 wheat and 12
melon, because we spend the opening building twelve pasture tiles and staging a herd —
`BUILD_` outranks `PLANT` in the job loop — so the harvest that funds the mid-game lands late
and small.

Deferring the herd (`animal_bootstrap_cap` 2, `animal_ramp_day` 9) reproduces the mechanism
exactly: 19 melon tiles standing at t96 instead of none, and terminal cash rises from 51,088
to 62,710 across 40 games. **And the paired score stays 0.000**, because in the same worlds
v006's own money rises from 130,332 to 150,443. We gained 11,600; the opponent gained 20,100.

That is the structural fact this campaign was missing. The two farms share one town, and our
choices move the opponent's income by tens of thousands. Every intervention tried so far
optimises our own economy, and in a shared market a larger own economy can hand the opponent
more than it keeps. It explains why eleven interventions improved margins slightly and won
nothing, and why our mirror economy (87,104) exceeds v006's (74,619) while we lose 40-0.

The fitness that matters is `ours - theirs`, which is what the paired score already measures.
The intervention that matters must **raise ours while lowering theirs**.

### Next

Opponent-aware market pre-emption. v006's decisive income is a melon block harvested around
day 10-11, and melon's price curve collapses hard above the neutral inventory (`sq`, target
3.6, base 250, demand about 1 a day). Selling melon into the market immediately before that
harvest should crash the price it realises on roughly 72 units. Falsify statically first:
compute, from `agent.economy.price`, the realised value of 72 melons at the inventory v006
faces at t250 against the inventory it would face after we add N melons, and check whether the
loss to it exceeds the revenue we give up. Only then build the overlay.
