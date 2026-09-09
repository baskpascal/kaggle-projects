# The field is a ladder, not a rock-paper-scissors: what that changes

> **Correction (2026-09-09).** The daily-dump extractor used `steps[:-1]`, but Kaggle
> stores the initial placeholder at `steps[0]` and the actions actually executed at
> `steps[1:]`. Every daily replay tape measured below was shifted by one turn. Replaying
> a current episode exposed the error: the shifted tapes produced 43,771 × 91,408, while
> `steps[1:]` reproduced the published 81,819 × 76,416 exactly on both local backends.
> Findings based on notebook-embedded tapes and our local games remain valid; claims below
> about the 1,313 daily-dump tapes and their transfer rate must be regenerated before use.

This supersedes the direction proposed in `docs/TAPE_ROUTING_FINDING.md`. That
document read a single-tape replayer scoring 0,506 against a top-heavy opponent
set and concluded that tapes are *matchup-specific*, so the way forward was a
library of tapes specialised per opponent plus finer routing on rival state. The
first half of that reading does not survive measurement. The second half does,
for a different reason.

## What the competition's own discussion says

Two things were being assumed locally that the forum settles.

**Episode replays are available.** A previous session concluded that scraping was
shut because the `episodes.get` endpoint is denied. It is, but the hosts publish
the day's highest-rated episodes as a dataset every day
(`kaggle/kaggriculture-episodes-index` lists them). The 2026-09-07 dump is 663
episodes, 20 GB, recorded on engine `1.32.7` — the version this repository pins.
Host rulings in topics #737788 and #738837 state that building a submission from
public notebooks and public replays is allowed and encouraged.

**Someone has already run the experiment we were about to run.** In topic #739273,
sobameshi reports a round-robin of 14 public implementations spanning a month of
the competition, on 96 fresh seeds and both seats: no intransitive triple, the
newer implementation beat the older one in 86 of 91 chronological pairs, and the
ordering tracks average final money. Their conclusion is that the interaction
surface between two agents is much smaller than it looks.

Two further facts shape what is worth optimising. The rating counts wins and
losses only, matches you against opponents near your rating, and the final
leaderboard is a Bradley-Terry fit over the two weeks of games *after* the
deadline (#736219) — so win rate against strong opponents is the objective, and
mean coin totals are not. And the environment changed twice in August (#733431,
#735311): the town centre now buys once a day, shops are sampled **with
replacement**, and tomato/carrot/egg prices spike when demand exists without
production. Games therefore differ from one another far more than they used to.

## Our own round-robin reproduces it

Ten agents, every ordered pair, six dev seeds, both seats, 1 080 games, no
failures. Score is win rate with draws at 0,5.

|            | t95 | t93 | tape | kaito48 | boat21 | cok10 | boat16 | seyam | lone | v002 | **all** | **worst** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| thomas95   | –    | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | **1,00** | 1,00 |
| thomas93   | 0,00 | –    | 0,83 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | 1,00 | **0,87** | 0,00 |
| tape_best  | 0,00 | 0,17 | –    | 0,83 | 1,00 | 0,83 | 0,83 | 1,00 | 1,00 | 1,00 | **0,74** | 0,00 |
| kaito48    | 0,00 | 0,00 | 0,17 | –    | 0,83 | 0,83 | 0,58 | 0,92 | 1,00 | 1,00 | **0,59** | 0,00 |
| boatlee21  | 0,00 | 0,00 | 0,00 | 0,17 | –    | 0,42 | 0,92 | 1,00 | 1,00 | 1,00 | **0,50** | 0,00 |
| cok10      | 0,00 | 0,00 | 0,17 | 0,17 | 0,58 | –    | 0,50 | 1,00 | 1,00 | 1,00 | **0,49** | 0,00 |
| boatlee16  | 0,00 | 0,00 | 0,17 | 0,42 | 0,08 | 0,50 | –    | 1,00 | 1,00 | 1,00 | **0,46** | 0,00 |
| seyamalam  | 0,00 | 0,00 | 0,00 | 0,08 | 0,00 | 0,00 | 0,00 | –    | 1,00 | 1,00 | **0,23** | 0,00 |
| lonespear  | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | –    | 0,67 | **0,07** | 0,00 |
| v002       | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,33 | –    | **0,04** | 0,00 |

**Zero intransitive triples out of 120.** The field is totally ordered. A router
that picks a tape according to *who the opponent is* therefore has almost nothing
to win: there is no opponent against whom a different tape is the right answer.

The row that matters most is `tape_best`, the single-tape replayer from the
previous session. It is not a weak agent — it ranks third of ten and beats every
public family except the two it was harvested from. The earlier figure of 0,506
came from measuring it against a set consisting almost entirely of the top of the
ladder; against the field it scores 0,74. The tape is not matchup-specific. It is
simply one rung below the agent that produced it.

## So where does routing actually earn its keep?

Not on the opponent — on the world. The gap between `thomas93` (0,87) and the
best single tape harvested out of `thomas93` (0,74) is the value of its routing,
and the mechanism is visible in the observation itself:

- at turn 0 the town has **no shops** and every market price is identical in
  every game, so there is nothing to condition on and no room for a better
  opening;
- the first shop unlocks on day 3 (turn 72) and one more every three days;
- since the August balance change, shops are sampled **with replacement**, so
  which products have demand varies enormously between games.

A tape is a bet on a particular demand profile. Routing at 72-turn block
boundaries is not "reading the opponent", it is *waiting until the game has told
you which world you are in*. That is why every strong public artifact branches on
a 72- or 144-turn schedule.

> **Correção (2026-09-08).** Este documento afirmava que as cinco fitas do
> `thomas95` são byte-idênticas nos primeiros 144 turnos. **Elas não são**: as
> quatro divergem da rota 0 já no turno 1, em 15 a 29 dos primeiros 144 turnos.
> A normalização da abertura é uma *decisão de projeto* que o notebook v23 do
> `ahmedberatozer` toma explicitamente (`tape[:144] = base[:144]`), não uma
> propriedade dos dados. O argumento de que não há nada a decidir antes do
> primeiro shop continua válido pelo lado do jogo — a cidade não revelou nada —
> mas não pode ser apoiado nessa evidência.

The corrected direction is therefore a portfolio of strong tapes routed on **town
and market state** at block boundaries, not a library specialised per opponent.

## What the ladder dump contains

`experiments/episode_tapes.py` turns a daily dump into a labelled library. From
2026-09-07: 663 episodes, 0 unreadable, **1 313 distinct action streams**, best
final money 171 497 (team *Suliman Tadros*). Two things stand out.

Almost nothing repeats: only nine streams appear more than once in the whole
dump. The top of the ladder is not replaying tapes verbatim; these are live
agents producing a fresh stream per game.

The openings say what our own agent is missing, and they agree with
`docs/NEXT_OPENING_REVENUE.md`. The three richest seats all make their **first
sale at turn 1 or 2**, hire to 4–5 hands within a day and 11–12 by the end, and
monetise WHEAT, MILK, STRAWBERRY and FERTILIZER. Our `v002` makes its first sale
at step 252 and finishes around 40 000; these finish above 165 000.

## A ladder tape is worth almost nothing on a fresh seed

All 1 313 tapes were replayed against `thomas95` on two dev seeds and both seats,
5 252 games, no failures. The result is close to binary:

| tapes | score against `thomas95` |
|---:|---|
| 1 311 | 0,00 |
| 2 | 1,00 |

Both survivors belong to the same team (*by*). Widening them to ten seeds and five
families says the rest:

| tape | team | thomas95 | thomas93 | kaito48 | cok10 | boatlee21 | worst |
|---|---|---:|---:|---:|---:|---:|---:|
| `4d78dad61a98` | by | 0,80 | 0,70 | 0,00 | 0,00 | 0,00 | **0,00** |
| `473a74950112` | by | 0,70 | 0,70 | 0,00 | 0,00 | 0,00 | **0,00** |
| best of the eight richest | — | 0,00 | 0,00 | 0,00 | ≤0,10 | 0,00 | **0,00** |

So the strategy the forum describes as normal — take the strongest public replay
and submit it — does not survive being measured. The richest recorded game in the
dump (171 497 coins, *Suliman Tadros*) scores 0,00 against every family we hold.

The explanation is consistent with everything above. A recorded stream is only a
strong agent if the agent that produced it was not adapting; those seats belong to
live agents, and what made them win was reacting to their own town. Replayed into
a town with a different shop mix the plan is simply wrong. The tapes that *do*
transfer are the ones taken from agents that were already replaying tapes —
which is exactly what `tape_best` (0,74 against the field) is.

The two *by* tapes are worth keeping for a different reason: they beat the top two
agents and lose to everyone below them. That is the field's only intransitivity we
have found, and it is a branch a portfolio can use, not a submission on its own.

## Routing is worth more than any tape in the portfolio

The two strongest artifacts carry nine distinct tapes between them, and they are
extractable directly from the published notebooks, so the value of routing can be
isolated exactly: same tapes, different chooser.

The game is deterministic given a seed and both action streams, so a spliced tape
can be *evaluated* rather than estimated. `experiments/` searches, for each
situation, which tape to commit to at each 72-turn boundary — a suffix move,
because a routing decision changes every block after it, and a switch that only
pays off two blocks later is invisible to a search that changes one block alone.
Over eleven tapes, two opponents, eight seeds and both seats:

| contexts | best single tape played whole | oracle route committed at turn 72 |
|---|---:|---:|
| 8 seeds (1000–1007) | 0,500 | **1,000** |
| 20 seeds (1030–1049) | 0,550 | **0,775** |

On the eight-seed set the oracle wins every game, including all sixteen against
`thomas95`; on twenty seeds it reaches 0,775, so 1,000 was partly the luck of a
small sample and 0,775 is the honest reading. Either way a tape that beats the
strongest public artifact exists in this pool for most situations, and the whole
problem is knowing which one at turn 72. Note also that *which* tape is best on
its own changes with the seed set — index 3 on one, index 6 on the other — which
is the same variance seen from the other side.

The oracle's choice tracks the **seed**, not the opponent: both seats and both
opponents agree on the right tape for a given world, and the answer varies from
world to world. That is the shop mix, and it is the mechanism behind the whole
finding.

Fitting one threshold on one public feature and measuring the result on twenty
seeds that were never used for fitting, four families, both seats, 160 games per
candidate:

| candidate | thomas95 | kaito48 | cok10 | boatlee21 | all | worst |
|---|---:|---:|---:|---:|---:|---:|
| opening tape alone (index 3) | 0,100 | 0,600 | 1,000 | 1,000 | 0,675 | 0,100 |
| **routed at turn 72 → v003** | 0,250 | 0,775 | 1,000 | 1,000 | **0,756** | **0,250** |
| the same rule applied at turn 144 | 0,250 | 0,775 | 0,625 | 1,000 | 0,662 | 0,250 |
| opening tape alone (index 6) | 0,000 | 0,775 | 1,000 | 1,000 | 0,694 | 0,000 |
| twenty-seed fit, routed at turn 72 | 0,050 | 1,000 | 1,000 | 1,000 | 0,762 | 0,050 |
| twenty-seed fit, routed at turn 144 | 0,000 | 0,775 | 1,000 | 1,000 | 0,694 | 0,000 |
| `thomas95` itself | 0,500\* | 1,000 | 1,000 | 1,000 | 0,875 | 0,500 |
| `v002` (previous champion) | 0,000 | 0,000 | 0,000 | 0,000 | 0,000 | 0,000 |

\* mirror match. `v002`'s row is from the round-robin above.

Three things are stable across both fits. Routing beats the tape it opens with,
every time. Routing at turn 144 instead of turn 72 is worse than routing at 72 —
the decision belongs at the first moment the town says anything. And every router
we fitted lands between 0,69 and 0,76, well short of the oracle, so the gap is the
router and not the library.

`v003` is the routed portfolio with the best worst family: two tapes, one rule.
At turn 72, if the town has opened a yarn store take the second tape, otherwise
stay on the first. It scores 0,756 against the field where the same opening tape
alone scores 0,675 and our previous champion `v002` scores 0,00. It does not beat
`thomas95` (0,875), and the manifest says so.

## Method note

The arena needed one fix to measure these tapes at all. A tape recorded in a game
with twelve hands, replayed by an agent that has not hired them, submits more hand
actions than it has hands. The interpreter resolves the missing hand to no
position and no-ops silently; our audit indexed the hand list directly and crashed,
refusing a game the official engine plays without complaint. Fixed in
`arena/match.py` with a regression test in `tests/test_engine.py`.
