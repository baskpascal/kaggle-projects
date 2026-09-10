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

### Melon pre-emption: arithmetic clears, the game does not

Static falsification on `agent.economy.price`, melon inventory 9,995 at t250 (price 266).
v006's 72-melon block is worth 17,081 there. Selling N melons first:

    N=40   we take  9,922   it loses  2,966   swing +12,888
    N=60   we take 14,521   it loses  5,286   swing +19,807
    N=100  we take 22,249   it loses 11,528   swing +33,777

Right order of magnitude, so the candidate was built: early melon block plus no sale floor.
Over 40 games against v006: score 0.000, our money 51,088 → 61,640, **v006's money
130,332 → 144,944**. We gained 10,552 and handed it 14,612. `feed_days_of_cover` 1 against
v006 is byte-identical to base.

Fourteen interventions rejected across six layers. Every one that grows our economy grows the
opponent's faster. The candidate explanation for that direction — our input purchases drain
market inventory and lift the price the opponent realises — is **hypothesis only**; the one
knob touching it moved nothing.

### Next

Stop optimising absolute economy; every parameter this planner exposes is an absolute-economy
parameter. Build a market policy that scores each candidate buy and sell by its **differential**
effect: our realised value from `agent.economy.sale_value` at the current inventory, against
the change it causes in the value of the opponent's observable standing production. Prefer
orders positive on `ours - theirs` — the only quantity that has tracked the paired score.
Falsify statically first: replay a lost world, compute the differential of every order we
actually issued, and check whether a differential-aware ordering would have flipped it, before
writing any policy code.

## 2026-09-10 (cont.) — the threshold, and the first mechanism with the right size

### The differential idea, tested in the wrong regime

Placing 40 units ahead of **every one** of v006's 292 executed sales costs it 11,870 in total,
against a deficit of 81,266 — and that ceiling assumes unlimited inventory of every product at
every moment. Rejected at Stage 0. But the reason is informative: below the engine's neutral
inventory the price curve is shallow, and every one of those sales happens below it.

### What the mirror actually showed

    nosso vs v006   STRAWBERRY t719  inventory  9,806  price 237
    v006 vs v006    STRAWBERRY t719  inventory 10,046  price   1

Two v006s flood strawberry past neutral and destroy the market, which is why the v006 mirror
is only 74,619 each. Our weak farm never floods it, so v006 faces a rising price all game and
finishes at 102,139. **We were not losing the market to it. We were preserving it for it.**
That retracts the contention framing from the previous cycle: the curve is not inelastic, it is
shallow below neutral and catastrophic above, and all the leverage sits at that boundary.

### The mechanism, priced on the engine's own function

v006 sells 203 strawberries after t360 at inventories between 9,801 and 9,878, realising
45,479. Adding our units first:

    N=100   price 201   it realises 36,649   loses  8,830   we take 22,046
    N=200   price 108   it realises  7,370   loses 38,109   we take 39,161
    N=300   price   1   it realises    203   loses 45,276   we take 42,324

At N=200 the swing is **77,270 against an 81,266 deficit** — the first mechanism in this
campaign at the right order of magnitude, and it raises ours while lowering theirs.

### Implemented, and the binding constraint is timing

`attack_tiles` reserves tiles for the crop the opponent depends on when that market sits just
under neutral (`neutral_inventory`, `attack_headroom`); defaults off. Results against v006:

    only_crop STRAWBERRY   score 0.000  margin -75,733  ours 35,850  **theirs 114,500**
    attack_tiles 2         score 0.000  margin -73,604  ours 63,620  theirs 138,790
    attack_tiles 5         score 0.000  margin -76,384  ours 57,528  theirs 138,325
    opponent_weight -1.0   score 0.000  margin -76,180  ours 55,318  theirs 136,640
    base                   score 0.000  margin -81,266  ours 51,088  theirs 130,332

`only_crop STRAWBERRY` is the only variant that moved the opponent — **-15,832** — and it is
the only one that produced real volume, at the cost of our own income. A negative
`opponent_weight` does not fire the mechanism at all: it feeds our own supply forecast and
suppresses planting rather than forcing it.

The diagnostic names the constraint exactly. With `attack_tiles 2` we **deliver four
strawberries** against the ~200 the attack needs. We hold 25 standing at t480, but strawberry
needs ten days to first harvest, so anything planted after about day 8 never reaches the market
in time. The attack fails on **timing**, not on tiles.

### Next

Make the attack allocation early and unconditional rather than opportunistic: reserve its tiles
from day 0 through about day 8, ahead of pasture construction, which is what currently occupies
the opening. Falsify cheaply first — with the reservation forced from turn 0, measure delivered
units before v006's selling window opens at t360, and require at least 150 before running a
single paired game.

### Attack implementation: a seed bug, and the opening is still the wall

`attack_tiles` generated its PLANT jobs and every one was refused: the assignment loop drops a
PLANT whose seed is not in the private store, and the seed order is built from `best_crop`,
which is our own scorer's choice and never the attack crop. Found by tracing a replay where
twenty tiles were free, an attack crop was correctly selected (wheat and melon both had
headroom under neutral), and all units still passed. Fixed by adding the attack crop to the
seed order. No paired game was spent on the broken version.

With the fix, seed 1000, `attack_tiles` 6 and `attack_until_day` 8: crops finally appear —
12 melon standing at t192 — and **v006 finishes at 93,338 against its 102,139 in the base
run**. The mechanism moves the opponent. Our own money is 32,587, so the trade is still bad.

Zero crops stand at t96 even with the reservation outranking construction, and the reason is
cash: the first days hold three to seven hundred coins while six melon seeds cost 480. The
attack lands at the end of its window rather than the start.

### Next

The blocker is opening liquidity, not allocation. Before any further paired game, measure the
first eight days' cash trajectory against the seed cost of the reserved tiles, and find what
the opening actually spends its three hundred coins on. Gate unchanged: at least 150 units of
the target crop delivered before t360.

## Adaptive Option Selector v0

One macro decision at turn 336, three continuations per world, fitness is win probability
because the final tournament is Bradley-Terry over wins — a win by one coin and a win by
thirty thousand count the same.

Against v006, 40 seeds × both seats = 80 worlds, zero failures:

    continue     0.000
    cow_heavy    0.000
    crop_heavy   0.000
    ORACLE       0.000

The oracle is the ceiling of any selector — it picks the best of the three knowing the
outcome in advance — and it is zero. There is nothing for a state-conditioned rule to choose
between: no combination of state and option produces a single win against v006 from our own
planner's base.

Measuring the oracle alongside the fixed baselines is what makes this cheap and safe. Without
it the reading would have been "adaptive ties fixed", and the next step would have been fitting
a value model to a signal that does not exist.

**The result does not say option-level adaptation fails.** It says adaptation is not the
bottleneck: a selector only has value when at least one option wins in at least some worlds,
and our owned base is 81,000 behind. The architecture question it settles is which body the
selector belongs on — not the owned planner.

Caveat on the design: `continue`, `cow_heavy` and `crop_heavy` are parameter variations, not
genuinely distinct economic regimes. A zero oracle against v006 is conclusive regardless, since
none of them wins. In the self-play control, a small oracle-minus-fixed gap would mean these
options are weak rather than that options do not work.

### Self-play control: the value is in the regime, not in conditioning on state

Same decision at turn 336, opponent is our own base planner, 30 seeds × both seats = 60
worlds, zero failures:

    continue     0.500
    cow_heavy    0.400
    crop_heavy   0.883
    ORACLE       0.917

The oracle beats the best fixed option by **0.033**. Even where outcomes vary freely, choosing
the option from state is worth about three points, while choosing the right *fixed* option is
worth **0.383** — `crop_heavy` (`animal_cap` 10, `plant_until_hour` 22, from t336) against
`continue`.

That answers the caveat from the v006 run. The options are not merely weak against a strong
opponent: state-conditioning itself has almost no headroom over a well-chosen constant. An
Adaptive Option Selector is not the next thing to build; finding the right regime is.

`crop_heavy` is a late-game switch, beats our base decisively in self-play, and still scores
0.000 against v006 — the same pattern as every other own-planner improvement.

## Elite macro genomes, and the first gradient that moves the opponent

`experiments/economic_fingerprint.py` compresses an elite economy into the dimensions a macro
plan would have to carry, all read from engine state. Seven teams at or above 2950, 36 worlds,
zero failures:

| team | rank | quad | land1 | cows | sheep | geese | pasture | crops t288 | cash t192 | final |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SpaTaro | 1 | 3 | 151 | 5 | 9 | 0 | 14 | 53 (WHEAT) | 52 | 94,336 |
| Otter Vibe | 2 | 3 | 121 | 4 | 6 | 9 | 11 | 56 | 772 | 88,260 |
| Himanshu | 4 | 3 | 151 | 8 | 6 | 3 | 14 | 53 | 573 | 97,929 |
| binghua | 5 | 3 | 88 | 6 | 5 | 0 | 14 | 61 | 788 | 102,889 |
| Mengfei Li | 6 | 3 | 151 | 12 | 5 | 0 | 11 | 53 | 270 | 112,069 |
| kanno | 8 | 3 | 151 | 8 | 6 | 3 | 14 | 53 | 567 | 110,365 |
| Yusuke Hayashi | 9 | 3 | 151 | 6 | 10 | 0 | 17 | 53 | 118,218 | |

The shared base is uniform: **three quadrants, 11–17 pasture, 53–61 crops, and a daily crew
peaking at 10–11 hands** (measured as the daily maximum; the snapshot at an exact turn reads
zero and is not usable). Cash at t192 is 52–788 for every one of them — the elite are as poor as
we are on day eight, so **liquidity is not what separates us**. The regimes differ on herd
composition, land timing and crop mix, not on scale.

Our planner against that: two quadrants, 6–13 crops, 12 pasture, 8 hands. Roughly five times
fewer crops and one quadrant short.

Seed cost is not the constraint either: `only_crop WHEAT`, seed 10 against strawberry's 100 and
910 coins at t192, still holds only 13 crops at t192 and 8 at t288.

### Five warm starts, and a direction

Each elite regime expressed in our own parameters over the shared base (3 quadrants, 11 hands,
planting to hour 22), against v006, 20 worlds:

    hayashi  (sheep-max, cap 16)   margin -64,098   ours 43,686
    mengfei  (cow-heavy, cap 17)   margin -65,934   ours 46,203
    otter    (poultry, cap 19)     margin -77,883   ours 42,902
    spataro  (sheep+wheat)         margin -88,464   ours 46,280
    binghua  (early land, cap 11)  margin -106,562  ours 44,318
    base                           margin -81,266   ours 51,088

Every one still scores 0.000, and the two best **lower our own money while lowering v006's much
further**: under `hayashi` the opponent falls from 130,332 to about 107,800, a suppression of
22,500 — the largest yet achieved. That is the differential direction the campaign has been
looking for, and it is the first gradient in it.

### Local search around the gradient, and why the warm starts fail

Coordinate search on the sheep-max regime against v006, 20 worlds each:

    animal_cap 14   margin -71,222   ours 60,200   v006 118,654
    animal_cap 16   margin -64,098   ours 43,686   v006 117,914   <- local optimum
    animal_cap 18   margin -71,600   ours 43,177   v006 114,449
    animal_cap 22   margin -76,732   ours 42,836   v006 112,909
    max_hands 13    identical to 11 - hands are not binding

Synthesis with the deferred opening, which had previously fixed the crop start:

    ramp day 6      margin -101,755  ours 47,913   v006 140,013
    ramp day 9      margin  -76,646  ours 57,938   v006 133,335
    ramp day 12     margin  -83,943  ours 52,102   v006 145,142

Deferring the herd raises our money to 57,938 and raises v006's to 133,335. The rule that has
now held across roughly twenty-five experiments: **anything that grows our economy grows the
opponent's faster, and the configurations that improve the margin are the ones that suppress
the opponent while our own money falls.**

### Why the warm starts cannot reach the elite base

Tracing the best regime shows it never builds the economy it was warm-started from:

    turn   quadrants  free  crops  pasture  sheep    cash
      96       1        20     0       4      2       519
     192       1         9     0      12      6       604
     288       2        24     9      12      6       269
     480       3        35    20      14      8     4,402
     576       3        17    38      14      8    12,326

**Zero crops before t288**, where the elite stand 53. The whole opening goes into twelve pasture
tiles and the herd, the second quadrant arrives at t288 against the elite's t88–151, and the
third at t480. Free tiles sit unplanted for the rest of the game. The macro genome is expressed
in the parameters, and the executor does not deliver the economy those parameters describe.

That is the wall: a reactive per-turn planner that prices each job independently cannot run
construction and planting in parallel the way a compiled schedule does.

### Next

Build the schedule compiler the mission specifies: take a macro genome and emit a phase plan -
land at fixed turns, pasture built in parallel with a planting quota per day, crew sized per
phase - and let the executor serve that plan rather than re-deriving priorities each turn. The
falsification gate is the elite base itself, measured from state and before any paired game:
three quadrants by t480, 40 or more crops standing at t288, and pasture complete without
starving the crop quota.

## 2026-09-10 — state-conditioned Option counterfactual, corrected

The v0 note above was too broad: `animal_type=COW` was inert under
`economic_planner=True`, no sheep Option existed, internal callback failures were not
checked, and no raw evidence survived. It did not reject the requested hypothesis.

The corrected two-PC Ray experiment selected 50 baseline losses from a 60-world screen:
46 actual v006 losses in strong recorded elite worlds (20 binghua, 11 Otter Vibe, 8
Mengfei Li, 7 Matthew Huang) and 4 reactive v006 worlds. Checkpoints were the day boundary
before sustained gap acceleration. Each branch replayed the identical prefix, then committed
for eight days to `FOLLOW_CURRENT`, `COW_CAPACITY`, or `SHEEP_CAPACITY` using the existing
planner/executor.

All three fixed continuations and the per-state oracle scored **0/50**. Another Option beat
current in **0/50** states. This was not an inert mechanism: cow branches bought 1,145 cows
and grew the herd by 460; sheep branches bought 1,167 sheep and grew it by 377. Both built
more than 740 pastures and both worsened mean margin versus current.

Decision: **reject at Phase 2**. Do not fit a selector and do not integrate a checkpoint
layer. State conditioning cannot recover a reward signal absent from every tested Option.
Full summary and hashes: `docs/STATE_OPTION_COUNTERFACTUAL.md` and
`docs/state-option-counterfactual-20260910.json`.

## 2026-09-10 — o ladder real: a execução está limpa e a perda é estreita

Primeira medição fora do painel local. `experiments/ladder_ground_truth.py` lê os episódios
que a submissão `v006` (56125200) realmente jogou no Kaggle. 204 episódios atribuíveis,
2026-09-09T14:36Z a 2026-09-10T18:32Z, sem gastar nenhuma view de episódio — o `ListEpisodes`
é gratuito e já traz reward dos dois lados, seat, adversário e o par
`initialScore`/`updatedScore` de cada partida. Detalhes em `docs/LADDER_GROUND_TRUTH.md`.

A queda que os snapshots de leaderboard mostravam é do próprio `v006`, e é convergência, não
falta dela: cold start em 600, pico de **2644,4** no episódio 117, e **2513,3** agora, 131,1
abaixo do pico e ainda caindo.

**Execução está limpa.** 204 de 204 episódios em `COMPLETED`, zero rewards ausentes, e o corte
por seat dá 0,593 contra 0,594 em 108 e 96 episódios. Não há assimetria de seat, o que refuta
para este agente a preocupação de que `obs["step"]` ausente no seat 1 faria repetir a lógica do
turno 0.

**A perda é estratégica e é estreita:**

    oponente < 2400    n= 36   win 0,917   soma dos deltas +1.802,0
    oponente 2400-2600 n=101   win 0,614   soma dos deltas   +138,5
    oponente 2600-2800 n= 67   win 0,388   soma dos deltas    -27,2
    oponente > 2800    n=  0

Nunca enfrentamos ninguém acima de 2800; o corte do top-10 (2946,6) é uma faixa que não
alcançamos, não um adversário que perdemos. Todo o rating acumulado veio da faixa abaixo de
2400, atravessada uma vez na subida. Interpolando as duas faixas centrais, **estimativa** de
equilíbrio em torno de 2600 — extrapolação, não medição.

O número mais surpreendente é a escala. Vitória mediana **+794**, derrota mediana **−1.109**,
sobre rewards de 90.000 a 120.000: menos de 1%. O painel local mede o mesmo agente contra
margens de ±81.000. Isso não prova que o painel é espelho, e a hipótese fica na forma fraca —
`H1: local_eval is conditionally biased relative to live ladder` — mas estabelece o suficiente
para decidir prioridade: **uma função objetivo calibrada em 80.000 não resolve diferenças de
800.**

### Próximo

Ligar o tier 2 (`GetEpisode`, com credencial, sob o `ViewLedger` de 3.600 views/24h já
implementado) para obter seed, engine version, statuses por agente e o stream de ações; e então
reproduzir cada episódio real localmente com mesma seed, seat e adversário, comparando
`predicted_local_outcome` contra `actual_ladder_outcome`. Até esse resultado existir, nenhuma
CPU vai para busca, GA, option selector ou RL: não sabemos qual objetivo correlaciona com o
ladder.

## 2026-09-10 — tier 2: o motor é idêntico, e a faixa decisiva é decidida por 0,1%

`experiments/ladder_reproduction.py`, sobre 20 replays da faixa 2600–2800 baixados pela rota
pública `GET /api/v1/competitions/episodes/{id}/replay`. 3.580 das 3.600 views ainda
disponíveis. Detalhes em `docs/LADDER_GROUND_TRUTH.md`.

**Level A, fidelidade do motor: 20 de 20 exatos.** Replayando os dois streams gravados na seed
gravada, o dinheiro terminal local bate exatamente com o reward publicado pelo Kaggle, nos dois
backends e com zero falhas. Não há discrepância de ambiente; o ramo "execução/ambiente" da H1
está fechado.

**Level B: agreement 20/20, e vale menos do que parece.** O `v006` é determinístico, então
contra o stream gravado do oponente na mesma seed ele refaz a própria partida. O que isso
estabelece é que o `v006` daqui é bit a bit o que rodou no Kaggle — não que o setup local
preveja o ladder. Validação preditiva de verdade exige oponente reativo, que um tape fixo não é.

**O achado é a escala.** As 20 partidas, por margem:

    -1.119  -1.099   -350    -93     -5     -2     -2     +3     +5    +15
       +18    +25    +55   +111   +155   +225   +679   +745  +3.983 +5.115

|margem| mediana de **102 moedas** sobre dinheiro típico de 91.813 — **0,111%**. Dez das vinte
decididas por menos de 100 moedas. O painel local mede o mesmo agente contra ±81.000: uma régua
800 vezes mais grossa do que a diferença que o ladder mede.

Ressalva: esses 20 são os mais antigos dos 67 da faixa, não amostra aleatória. Somam 13/20,
contra 0,388 nos 67. A distribuição de margens é o achado; a win rate desta amostra não é
representativa.

### O que a H1 virou

`H1: local_eval is conditionally biased relative to live ladder` sobrevive com dois ramos
eliminados por medição: não é ambiente (Level A 20/20) e não é execução nem seat (tier 1,
204/204 `COMPLETED`, 0,593 contra 0,594). Resta o setup de avaliação — quais adversários o
painel contém e em que escala ele mede.

### Próximo

Reconstruir o evaluator na escala real: adversários de 2600–2800 e um objetivo que resolva
centenas de moedas. Só depois disso volta a fazer sentido gastar CPU em busca, GA, option
selector ou RL.
