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

## 2026-09-10 — o evaluator na escala certa, e a perda se parte em duas populações

`experiments/ladder_cohort.py` substitui a população do painel. O painel antigo organiza
adversários por rank de leaderboard; o ladder pareia por proximidade de rating, e nunca
enfrentamos ninguém acima de 2800. O cohort novo são os 67 mundos reais da faixa 2600–2800:
seed do episódio, nosso assento, o stream gravado do adversário como replayer, e o resultado
que o ladder registrou. Detalhes e limitações em `docs/LADDER_COHORT.md`.

Calibração exata: `v006` marca 0,3880597 no cohort contra 0,3880597 no ladder, 0 ganhos,
0 regredidos, margem mediana −95,0 contra −95, zero falhas. Isso era esperado por construção —
o agente é determinístico contra tape fixo — e serve como verificação de encanamento, não como
validação. Bootstrap por bloco de seed: **[0,269, 0,507]**, então o déficit é real.

O número de manchete deixou de ser margem e passou a ser mundos ganhos menos mundos
regredidos, com os episódios nomeados. Um teste fixa o motivo: duas vitórias de blowout e três
derrotas por dez moedas dão mediana de margem positiva e net de −3.

**E a régua nova já mostrou uma coisa que a antiga não podia:**

    decididos por < 1.000    n=40   win 0,550   |margem| mediana    124
    decididos por >= 1.000   n=27   win 0,148   |margem| mediana  2.510

Não estamos perdendo os cara-e-coroa — nos apertados ficamos acima de 0,5. O déficit inteiro
está nos 27 mundos realmente decididos, onde perdemos 23. As derrotas largas têm mediana de
−1.932, só uma passa de −10.000, e se espalham por times distintos, nenhum com mais de duas.
Não é um modo catastrófico nem um adversário específico.

Virar 12 dos 23 leva o cohort a 38/67 = 0,567.

### Próximo

A pergunta é o que acontece nos 23 mundos de derrota larga e não acontece nos 40 apertados.
Os episódios estão nomeados no relatório, e `elite_events.py` e `planner_milestones.py` já leem
esse formato de replay.

## 2026-09-10 — o 2x2 causal: CASE 3, e a chave de roteamento está errada

`experiments/regime_counterfactual.py` trocou exatamente uma linha do `router_parent.py` — a
que atribui `state.plan` no turno 144 — e rodou as duas células que faltavam, nos mesmos
mundos, seeds, assentos e streams gravados. Detalhes em `docs/REGIME_COUNTERFACTUAL.md`.

| contexto \ regime | ovelha/pasto | coop |
|---|---:|---:|
| YARN cedo (n=13) | 0,769 observado | **0,077** forçado |
| sem YARN (n=54) | **0,185** forçado | 0,296 observado |

**CASE 3.** Célula A: 10/44, net −6 mundos, Δ margem −9.286. Célula B: 1/12, net −9,
Δ margem −17.564. Ramo verificado por transição de estado: 53/54 ovelha e 13/13 coop, zero
falhas. Ressalva: o adversário é tape fixo, então a direção é confiável e a magnitude não.

**Mecanismo.** Preço da lã no d15: mediana 1 e colapso em 37/54 sem a loja; mediana 241 e zero
colapsos com ela. A `YARN_STORE` é o escoadouro. Isso corrige o relatório anterior, que tratou
o glut de lã como regime ubíquo de 55% dos mundos — os 37 de 67 são exatamente os mundos sem
loja.

**Biblioteca de regimes.** `regime_library.py` varreu 1.325 episódios públicos, 2.650 assentos,
zero falhas, e ficou com os 1.264 de times ≥2900. Das oito lojas, **só a `YARN_STORE` muda o
build da elite** (ovelha 11 contra 5, ganso 0 contra 3, regime dominante `sheep_led`); as
outras sete deixam o rebanho idêntico. Não existem 2–3 regimes independentes para rotear, e
portanto a condição declarada para reabrir roteamento aprendido ou RL não foi atingida.

**O achado que move o alvo.** Entre os fortes sem YARN cedo, `sheep_led` ganha 0,671 (n=76)
contra 0,556 do cow e 0,579 do mixed — enquanto o nosso forçado deu 0,185. A diferença é que
naqueles 76 assentos a lã estava sã em **76 de 76**. A elite não é melhor com ovelhas; ela
nunca compromete com ovelha sem comprador para a lã.

Separando a célula A pelo mercado: lã sã (n=17) dá 0,471 contra 0,294 do `v006`, net **+3**;
lã colapsada (n=37) dá 0,054 contra 0,297, net **−9**. A regra atual tem sensibilidade 1,000
para o colapso e especificidade 0,433 — ela nunca erra para o lado perigoso, mas nega ovelha a
17 mundos que a sustentariam. Trocar a chave por "lã vendável" daria **29/67 = 0,433** contra
0,388, +3 mundos.

**E o obstáculo.** O preço da lã é idêntico nos dois destinos até o dia 9 (206, 212, 215, 217,
190, 187) e só separa no dia 11. `bal_acc` 0,500 em todos os dias até lá. O `v006` compromete o
regime no dia 6, **cinco dias antes de a variável que decide o regime existir**.

### Próximo

Adiar o compromisso: manter a `YARN_STORE` como prior no dia 6 e reavaliar quando a demanda por
lã se revelar, medindo no mesmo cohort. Teto medido: +3 mundos (0,388 → 0,433), com a ressalva
de que os 17 mundos foram identificados dentro do próprio cohort.

## 2026-09-10 — compromisso adiado rejeitado: o custo de transição excede a informação

`experiments/delayed_commitment.py` construiu a ponte legal do dia 6 ao dia 11 — `state.plan`
atribuído duas vezes, prefixo no turno 144 e sufixo no 264, overlays intactos — e mediu o teto
com rótulo do futuro. Detalhes em `docs/DELAYED_COMMITMENT.md`.

    A   v006, coop sempre, sem ponte                 0,2963  (16/54)
    B1  prefixo coop,  oraculo -> ovelha no d11      0,2037
    B2  prefixo ovelha, oraculo -> coop no d11       0,1852

**Os dois tetos, com conhecimento perfeito do regime, ficam abaixo do `v006`.** Pela lógica de
promoção declarada, isso rejeita a direção e a regra observável não foi construída.

O custo de transição, medido com o mesmo regime final:

    termina em ovelha:  0,185 sem ponte -> 0,019 com ponte   custo -0,167
    termina em coop  :  0,296 sem ponte -> 0,167 com ponte   custo -0,130

Nos 17 mundos de lã sã, a ovelha desde o dia 6 ganha 0,471 e a mesma ovelha alcançada por ponte
no dia 11 ganha 0,000. A informação não é o gargalo; a execução é. Um tape que constrói 18
tiles de pasto até o dia 11 não pode ser iniciado no dia 11.

Duas referências delimitam a direção: um oráculo perfeito **no dia 6** (sem ponte) daria 0,3519,
ou **+3 mundos em 67**; e o melhor-de-todas por mundo, que não é uma política, daria 0,4074.

E esse teto de +3 também não é alcançável: prever "lã sã no d15" a partir de 52 observáveis do
dia 5/6 dá LOO de **0,639** contra um nulo de seleção de mediana 0,594 e **p95 0,647**. A melhor
regra fica abaixo do ruído do próprio procedimento.

### O que isso implica

O custo de transição é uma propriedade de **decisões de construção** — terra, pasto, coop,
rebanho acumulam estado físico e não podem ser retrofitados. Qualquer adaptação tardia sobre os
tapes do `v006` que mexa nelas paga 13 a 17 pontos. Isso fecha a adaptação de build, não a
adaptação em geral: decisões de mercado não acumulam estado físico e são comutáveis a qualquer
momento.

A aritmética aponta para lá: +100 moedas uniformes viram 8 mundos (0,388 -> 0,508) contra os +3
que o regime perfeito valeria.

### Próximo

Uma decisão de mercado comutável tarde, medida no mesmo cohort. Holdout estrito já preparado: a
faixa 2400–2600, 195 replays arquivados no total, 3.405 views restantes.
## 2026-09-10 — schedule compiler Stage 1: quotas agregadas ainda não são execução compilada

### Reality e seleção

MEASURED FACT. O campeão continua `versions/v006/main.py`, SHA-256
`495bfa4825c9e58638e804aeeb62a9537826f6d4d869222815e1bbeefd84d3dc`. No ladder ele
completou 204/204 episódios, sem assimetria de assento, atingiu pico 2644,4 e estava em
2513,3 na última leitura. Nos 20 replays da faixa 2600–2800, o motor reproduziu 20/20
exatamente e a mediana de `|margem|` foi 102 moedas.

Três perguntas foram comparadas antes de implementar: (1) schedule compiler por fases,
(2) reconstrução do evaluator com adversários reativos 2600–2800, e (3) correção micro de
near-ties. A primeira foi selecionada somente para Stage 1: seu teste é independente da
calibração competitiva, ataca o gargalo corporal já medido e decide se esse executor é
salvável. A segunda permanece obrigatória antes de interpretar Stage 2; a terceira é
dominada pelos oráculos negativos já registrados.

### Stage 0

MEASURED FACT. Sete perfis de equipes >=2950, 36 mundos no mesmo motor, constituem prova
construtiva de 3 quadrantes, 53–61 culturas em t288, 11–17 pastos e pico diário de 10–11
mãos. O baseline reativo refeito pelo mesmo instrumento alcançou no mínimo 8 culturas em
t288; isto resolve a contradição com `state.json`, que dizia zero, e não altera a ordem de
magnitude do déficit.

STATIC CEILING. Contra o trace reativo preservado de 9 culturas, chegar ao gate de 40 abre
31 tiles. Mesmo usando o valor base conservador de WHEAT, 25 moedas por tile-dia, nos 18 dias
restantes após t288, o teto bruto é `31 * 18 * 25 = 13.950` moedas, 137 vezes a margem viva
mediana de 102. O teto não é lift esperado: custo e contenção podem inverter o sinal. A
hipótese passou Stage 0 porque a magnitude possível é material e a elite demonstra
viabilidade física.

### Implementação mínima e Stage 1

O modo opt-in `compiled_schedule` fixou deadlines de compra de terra, meta cumulativa de
plantio, dois lotes de pasto e piso de equipe por fase, mantendo o comportamento histórico
quando desligado. `experiments/compiled_schedule_gate.py` mede observações executadas em
t96/t192/t288/t480 e picos de mãos, com baseline sheep-max congelado, 10 seeds dev e ambos
os assentos. Um bug no reshape de replay descartava o verdadeiro frame step 0 quando ainda
não havia ações; ele foi corrigido e ganhou regressão.

Resultado, 10 mundos x 2 assentos, contra `v006`, zero callback failures:

| gate | compiled | reactive control |
|---|---:|---:|
| 3 quadrantes em t480 | 20/20 | 18/20 |
| >=40 culturas em t288 | 10/20 | 0/20 |
| 12 pastos em t288 | 4/20 | 20/20 |
| pico de equipe >=10 nas três fases | 20/20 | 16/20 |
| todos os gates | 4/20 | 0/20 |

Os pares de assentos foram idênticos, portanto o resultado total representa 2/10 mundos
independentes passando, não quatro evidências independentes. Minima compiled: 3 quadrantes,
13 culturas, 6 pastos e pico 10. Minima do controle: 2, 8, 12 e 9. Terra e pico de equipe
foram materializados de forma robusta; a combinação culturas+pasto não foi.

MEASURED FACT. Em 16/20 execuções compiled apenas seis pastos existiam em t288. Pedidos de
BUILD_PASTURE continuaram aparecendo e o audit da temporada chegou a doze, mas o segundo lote
foi atribuído tarde demais. Em seeds 1003/1004 havia só 13 culturas em t288 após o ciclo de
colheita. A meta global foi compilada, mas a atribuição por unidade continuou reativa; ela
reprecificou reposição de culturas contra construção e não garantiu nenhum dos dois
compromissos executados.

### Falsificação independente

PASS para a conclusão estreita: não executar Stage 2. Uma reprodução nova da seed 1001 nos
dois assentos mediu 37 culturas e 6 pastos em t288, 3 quadrantes em t480, picos 10/10/10,
zero falhas de callback e 580/580 ordens de mercado executadas; o sumarizador concordou com
os tiles contados diretamente. O candidato refeito tinha hash `309d728b…` e `v006`
`495bfa48…`. Trinta e três testes focalizados passaram.

Ressalvas que limitam, mas não revertem, o resultado: o JSON commitado não inclui hashes,
fingerprint do motor, runtime nem telemetria diária; o gate de equipe mede apenas pico e não
capacidade sustentada (na fase 192:288 da seed 1001, dez mãos existiram em somente 23/96
snapshots); os assentos não são amostras independentes; e a comparação elite no relatório é
uma faixa hardcoded, não as linhas brutas. Nenhuma dessas falhas pode transformar 37 culturas
e 6 pastos executados nos gates de 40 e 12.

### Decisão e próximo teste

CONTINUE MECHANISM TEST. Não promover e não rodar paired. A nova medição corrige a formulação:
um compiler de metas globais servido pelo mesmo assignment reativo não é ainda um executor
compilado. O próximo teste é substituir somente a atribuição dos dois lanes conflitantes por
compromissos persistentes por unidade até sua conclusão e exigir, nos mesmos 10 mundos,
40 culturas e 12 pastos em t288 sem perder os gates robustos de terra e equipe. Se isso passar,
reconstruir o evaluator reativo 2600–2800 antes de dar significado competitivo à Stage 2.

## 2026-09-10 — compromisso persistente por unidade rejeitado no Stage 1

REALITY. Refresh às 21:44 BRT: `v006` estava em 2.441,2, rank 652; SpaTaro liderava com
3.130,4 e o corte top-10 era 2.961,0. O déficit competitivo, portanto, continua aumentando.

MEASURED FACT. O trace congelado mostrou que a formulação inicial estava incompleta: em
seed 1003, a reserva de 2.000 moedas para terra bloqueou HIRE de t216 até t280. A capacidade
foi só 72/762 worker-turns possíveis, e o mundo terminou t288 com 13 culturas e seis pastos.

CAUSAL RESULT. Liberar apenas HIRE da reserva levou seeds 1003/1004 a 39/12 e 38/12, mas
compras opcionais de animais ocuparam três dos dez slots de mercado e limitaram a equipe a
7/8. Adiar esses pedidos até t288 restaurou 2.760/2.760 worker-turns e produziu 39/12 e
40/12. Capacidade de equipe era a primeira causa, não ownership por unidade.

CAUSAL RESULT. Sobre a base com recursos corrigidos, persistência passou seed 1003 exatamente
em 40/12, mas falhou o boundary pré-definido 1007 em 38/12; o controle imediato atingiu
42/12. O mecanismo regrediu porque compromissos ativos saíam de `jobs`/`planned`: houve 166
estados sem sementes contra dois no controle e BUY_SEED caiu de 46 para 32. As 183 ações
PLANT/BUILD auditadas eram transições distintas do estado real, e falhas medidas de PLANT,
BUILD_PASTURE, HIRE e BUY_LAND foram zero.

DECISION. REJECT no Stage 1; nenhum run de 10 mundos e nenhum Stage 2. Persistência permanece
opt-in e desligada por padrão. Retêm-se instrumentos de capacidade diária, arbitragem de
recursos, execução por transição e custo de oportunidade. Detalhes e hashes:
`docs/COMPILED_SCHEDULE_PERSISTENCE.md`.

NEXT QUESTION. A remoção do throttle de quatro turnos em `v006.advance_sales`, que ganhou 23
mundos e regrediu um no cohort de desenvolvimento, preserva net positivo numa população
independente e contra adversários reativos depois de corrigir a identidade/hash do bundle?
