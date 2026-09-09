# Phase 1: where we stand, what the elite economy is, and what planner_v1 should be

Investigation only. Nothing in `agent/` or `versions/` changes in this document; it exists to
decide what to build and to record the measurements that decide it. Dated 2026-09-09,
21 days before the deadline.

## 1. Where we are on the ladder

| | |
|---|---|
| team | Uncle Scooge, **rank 402 of 8407**, 2554.3 |
| active submissions | `v006` 2554.3 (2026-09-09), `v004` 2536.9 |
| top-10 cut | **2915.6** (was ~2870 on 2026-09-09 morning) |
| top-30 cut | 2855.5 |
| gap to a paid finish | **≈ 360 rating points** |

`v006` converged at 2554.3. The prediction in `docs/SHOP_ROUTER_STACK.md` was that it would
land near `yhay81`'s 2838.5, and it did not. Two of the three candidate explanations are
already excluded by evidence in this repository, and the third is the one that matters:

* not a bug - the artifact plays 3,200 validation games with zero callback failures;
* not decay alone - `v004` fell 2611.7 → 2536.9 over the same window, about 75 points;
* **the public notebook is not the submission.** `docs/PANEL_SNAPSHOT.md` says a team rating
  bounds a published notebook *from above*. We measured 0.840 against `shop-router-0909` and
  read that as "near 2838". The 2838 belongs to `yhay81`'s live submission, which is not the
  notebook they published.

So the public-artifact panel measures a ceiling that is roughly 300 points below the live
top, and the plateau `v006` hit is the plateau of that whole approach. That is the
quantitative case for the pivot this task asks for.

## 2. What the current architecture actually is

Two lineages exist in this repository and only one of them is a policy.

**The submitted lineage (`versions/v003`-`v006`) has no planner and no executor.** It is a
tape player: 13 recorded 719-turn action streams, a routing table read once at step 144, and
three repair layers (weed DIG, sale advance, terminal liquidation) plus the two layers `v006`
adds. It cannot express a plan it does not already carry. `docs/TOP_PANEL_2800.md` measured
what that is worth: against elite recorded worlds `v006` scores 0.7930 and the unmodified
parent scores 0.7930, differing on **2 of 314** assignments. Everything we added to it is a
tie-breaker.

**The policy lineage (`agent/`, 819 lines) is a real executor with a static plan.** The split
is much cleaner than expected:

| module | lines | what it does |
|---|---:|---|
| `agent/planner.py` | 230 | per-turn: build a job list from tiles, score jobs by `value / (1 + travel)`, assign units, then buy |
| `agent/economy.py` | 182 | **the engine's market model, mirrored exactly**: `CURVES` = `MARKET_PARAMS`, `price()`, `sale_value()` marginal pricing, `daily_demand()` from the unlocked shops, `score_crops()` marginal value per tile-day |
| `agent/market.py` | 105 | sale scheduling against a price-recovery threshold, shed pressure, order slots |
| `agent/state.py` | 90 | observation wrapper, risk posture |
| `agent/params.py` | 27 | **the entire strategy**, as 22 static constants |
| `agent/routing.py` | 25 | Manhattan movement |

The executor is not the problem. `agent/economy.py` already carries the correct market
curves, the correct shop-driven demand function, marginal (not average) sale pricing, and a
forecast that includes our own unsold stock. What is missing is a layer above it: **every
strategic quantity is a constant in `params.py`, chosen once, offline, identical in every
world.** `animal_target: 0`, `animal_type: 'GOOSE'`, `max_quadrants: 2`, `expand_day: 7`,
`max_hands: 8`. That is the frozen plan, and it is where a planner belongs.

## 3. The elite economy, measured

From the 2026-09-08 official dump, 1,289 distinct streams, tiered by the 2026-09-09
leaderboard: **Tier A ≥2900** (SpaTaro r2, Otter Vibe r3, Mengfei Li r6, binghua r12; 435
streams), Tier B 2800-2900 (212), Tier C 2700-2800 (72).

Tier A per game, medians: **270-280 HIRE orders**, **2 land purchases** with the first at
**turn 97**, **16 BUILD_PASTURE and 2 BUILD_COOP**, **~9 COW + ~9 SHEEP + ~2 GOOSE**, ~1030
WATER, ~465 HARVEST, ~160 FERTILIZE, final money p50 **≈ 102k**.

Our executor in the same recorded worlds: **money p50 41,948**, elite seat 136,944, score
**0.0064** (2 of 314). It is not an order of magnitude away - it is a factor of three, and
the three axes where it differs from Tier A are exactly the three constants above.

### The economic families at the top are real and different

| team | rank | first BUY_LAND | land buys | animals | builds | plant mix | sell turns |
|---|---:|---:|---:|---|---|---|---:|
| SpaTaro | 2 | **turn 49** | 5 orders | COW 10.6, SHEEP 10.3, GOOSE 0.1 | 17.9 pasture, 0.1 coop | WHEAT 171, CARROT 63, **no TOMATO** | 234 |
| Otter Vibe | 3 | turn 120 | 2 | COW 8.3, SHEEP 6.4, **GOOSE 5.3** | 14.7 pasture, **5.3 coop** | TOMATO 19 | 89 |
| Mengfei Li | 6 | turn 150 | 2 | COW 9.2, SHEEP 6.2 | 15.5 pasture | STRAWBERRY 35, TOMATO 12 | 137 |
| binghua | 12 | turn 87 | 2 | COW 7.8, SHEEP 7.0 | 14.6 pasture | WHEAT 136 | 168 |

Three distinguishable strategies: an early land rush with maximum pasture and no birds
(SpaTaro, the most consistent top team), a coop/goose hybrid that sells in concentrated
bursts (Otter Vibe), and a later-expanding crop-mix economy (Mengfei). Every one of them runs
livestock at scale, and every one buys land.

## 4. Why livestock is not a preference but the whole economy

From the engine, verified in `kaggriculture.py` rather than assumed: animals yield **1 unit
per interval** (`GOOSE` 1 day, `COW` 2, `SHEEP` 3), plus **1 extra unit on a production day
when fed and cared the day before**, plus **1 FERTILIZER every day, from every animal**.

| asset, per tile-day at base price | revenue |
|---|---:|
| WHEAT tile | ~25 |
| CARROT tile | ~26 |
| GOOSE + its fertilizer | ~200 |
| SHEEP + its fertilizer | ~234 |
| COW + its fertilizer | **~260** |

An animal tile is worth about **ten crop tiles**, and our default plan sets `animal_target`
to zero. `score_crops()` scores crops only; there is no function in the repository that
prices an animal, a pasture, a hire or a quadrant.

The second half of the model is the constraint that stops "buy 40 cows" from being the
answer. The market is shared with the opponent, prices move on *inventory*, and the shops are
the only thing that removes inventory:

| shop instance | daily consumption it creates |
|---|---|
| YARN_STORE | WOOL 12 |
| PET_CAFE | CARROT 12 |
| BAKERY / BRUNCH / PIZZA / ICE_CREAM / SMOOTHIE / FARMERS_MARKET | 6 per listed product |
| town centre | +1 per product per day |

and the premium goods are exactly the shallow ones: `T` is 105 for WOOL, 122 for MILK, 100
for STRAWBERRY, 300 for MELON against 400 for WHEAT and 450 for CARROT, with WOOL's glut
target 3.2 and MELON's 3.6. Dumping ~105 wool puts wool on the price floor; dumping 400 wheat
costs 20% of 25.

So the sustainable revenue of a product is bounded by **what the town consumes**, and that is
a function of which shops unlocked - the thing that differs between worlds. One yarn store
absorbs 18 sheep, one milk shop 6 cows, one egg shop 3 geese. Tier A holds 9 cows and 9 sheep,
which is what a town with one or two of each shop can absorb. **The elite are not maximising
production; they are matching production to the town's demand vector.** That is the same
quantity `agent/economy.py::daily_demand` already computes and that nothing in the repository
uses to decide what to build.

## 5. The decisive experiment already run

If the gap were execution, changing the static plan would not move money. It does, and it
moves non-monotonically, which is the signature of a missing planner rather than a missing
constant. Elite recorded worlds, 60 seat assignments each, same executor:

| plan | money p50 | vs base | score |
|---|---:|---:|---:|
| `DEFAULTS` (baseline) | 40,476 | - | 0.000 |
| `animal_target=8, animal_type=COW` | **55,484** | **+37%** | 0.050 |
| `max_quadrants=4, expand_day=2` | 40,886 | +1% | 0.000 |
| `max_hands=12` | 37,630 | -7% | 0.000 |
| all three together | **28,244** | **-30%** | 0.017 |

Livestock alone is worth +37%. Land alone buys nothing, because the executor cannot fill it.
Hands alone lose money, because hire cost is fibonacci per day and the greedy scorer spreads
units thinner. All three together are *worse than any of them*, because land and hires drain
the capital the animal chain needs first. A constant cannot fix this; a sequencing decision
can.

## 6. What the field says, and what it changes

* `busyaprime/what-actually-wins-on-the-kaggriculture-ladder` (2026-09-09): winners and
  losers plant, hire, harvest and fertilise within one per cent of each other; **the only
  action column that separates them is selling**. The daily median has been falling since
  2026-08-10 and no agent in the current tier list appeared in the 2026-08-15 one. Reading:
  copying the meta is worth less than it was, and the remaining edge is not in doing more.
* `zhincez/ten-ppo-runs-one-fake-win-zero-real-ones` (2026-09-09): ten PPO configurations,
  none beat their own anchor, with a documented false positive caused by a handicapped
  baseline. Separately measured: **playing one policy's farm actions with another's market
  orders earns 0 coins** where the pair earns ~108,000. Reading: RL end-to-end is not the
  first move, and any plan we build must keep production and selling consistent - a planner
  that changes the economy without changing the sale schedule will destroy both.
* Host rulings already in `docs/LADDER_META.md` (episodes are public, ties count as half a
  win, the final fit is Bradley-Terry over active pairs) are unchanged.

## 7. Hypothesis for planner_v1

> The elite advantage is an **allocation** advantage, not an execution advantage: capital and
> tiles go to the assets whose product the town actually consumes, in the order that keeps the
> chain funded. Our executor already prices crops and sales correctly against the real market
> curves; extending the same marginal-value arithmetic to animals, structures, hires and land,
> and letting a small planner allocate them against `daily_demand`, closes most of the 3x money
> gap without touching the execution layer.

Concretely, planner_v1 is a function that runs at a handful of decision points and emits a
**plan** - not actions:

```
WorldObserver (existing State)
  -> features: unlocked shops -> per-product daily demand; capital; tiles free per quadrant;
     hands; days left; own and opponent stock; market inventory vs I0
  -> EconomicPlanner: allocate capacity to products against demand, priced with the
     existing sale_value() at forecast inventory
  -> Plan: {product targets, animal chain and count, structures to build, land purchase
     trigger, hire target, cash floor, sale posture}
  -> existing executor (planner.py job scoring, market.py sale scheduling) unchanged
  -> replan on: new shop, demand shift, capital off plan, capacity off plan, terminal phase
```

The planner decides *what the farm should consist of*; the executor keeps deciding which unit
walks where. Replanning is event-driven with hysteresis, not per turn.

Falsifiable predictions, in order of how much they would change the direction if wrong:

1. **A demand-matched livestock plan beats the best constant plan** by more than the +37% that
   `animal_target=8` alone bought, on the same 60 elite worlds.
2. **Sequencing beats simultaneity**: hire and land purchases gated on funded capacity beat
   the -30% that turning all three constants on together produced.
3. **Shop-conditioning pays**: a plan built from the world's own `daily_demand` beats the
   single best world-independent plan, measured on worlds grouped by shop set.

If (1) fails, the executor - not the plan - is the bottleneck and the direction changes to
fixing throughput. If (1) holds and (3) fails, we ship a better constant plan and drop the
adaptive layer.

## 8. What this deliberately does not do

We are not mining the elite replays for imitation targets, and that is a change of direction
from the task's suggestion. The reason is that the corpus answered a different question than
expected: the macro decisions it exposes (16 pastures, 2 land buys, 270 hires, mix by shop)
are already *implied* by the engine's own demand and price arithmetic, which we can compute
exactly rather than infer statistically. Bifurcation mining stays in the plan as a
**validation** tool - if our planner's allocation disagrees with what four top-ten teams do in
the same shop world, one of us is wrong and it is worth knowing which - not as the source of
the policy. `experiments/elite_macro` output is retained for that.

## 9. Files this will touch

| file | change |
|---|---|
| `agent/economy.py` | **add** asset pricing: `animal_value()`, `structure_value()`, `hire_value()`, `land_value()` next to the existing crop scoring. Another session is editing this file for the watering-bonus fix; the additions are append-only and must not collide. |
| `agent/plan.py` | **new**: the planner - features, allocation, plan object, replan triggers |
| `agent/params.py` | plan-shaped defaults and the ablation switches (`planner: on/off` must reproduce today's behaviour byte for byte when off) |
| `agent/planner.py` | **minimal**: read targets from the plan instead of the constants (`want_animals`, `animal_type`, expansion trigger, hire target) |
| `experiments/plan_ablation.py` | **new**: one-switch-at-a-time ablation over the elite worlds |
| `docs/PLANNER_V1.md` + evidence JSON | the promotion report |

`versions/v006` stays frozen as the submitted baseline. No submission is part of this work.

## 10. The gate

planner_v1 is promoted only with a report carrying: elite ≥2900 win rate and delta over
baseline, 2800-2900 and 2700-2800 win rates, worst opponent, median and quartile gold delta,
**large-loss conversion rate** (share of worlds where the baseline loses by more than 5k and
10k where the planner reduces the deficit materially or wins), CI, game count and dataset
revision. Both seats, paired worlds, zero callback failures. A planner that only converts
mirror draws is not a success.

## Provenance

Leaderboard `kaggriculture-publicleaderboard-2026-09-09T13:22:21.csv`; dump
`kaggriculture-episodes-2026-09-08.zip` sha256 `e24aa237…765da`, 663 episodes, engine 1.32.7;
elite subset `data/kaggle/official/top2800-2026-09-08.zip`, 157 episodes, both teams ≥2800.
Numbers in sections 3, 5 and the tier tables are in `docs/planner-v1-investigation.json`.
