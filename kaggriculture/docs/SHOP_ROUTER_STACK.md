# The public top is one parent with three children, and they compose

Measured on 2026-09-09. Our team stood at 2611.7, rank 257 of 8362, with `v004` in the
scoring slot. The artifact at rank 27 was `yhay81/shop-router-0909`, and two other public
notebooks were already built on top of it. This document records what each of them is
worth, the one route change we contributed, and why the stack of all three plus that
change became `v006`.

## What the field actually looks like now

`shop-router-0909` supersedes the `0908` router that `docs/YHAY_ROUTER_FINDING.md`
measured. It carries thirteen complete 719-turn tapes instead of four, routes on the first
two unlocked shops at step 144 rather than on a two-stage tree, and adds three
observation-based repairs: a `DIG` inserted when weeds block planting, which delays only
the blocked worker and only inside the current day; sales brought forward one turn; and a
terminal liquidation of the projected shed. Against our own artifacts it is decisive - 20
dev seeds, both seats, 240 games, zero failures:

| opponent | `yhay_router_0909` score |
|---|---:|
| `versions/v005` | 0.900 |
| `versions/v004` | 1.000 |
| `yhay_router_0908` | 1.000 |
| `thomas_t95` | 0.900 |

Two public notebooks then modify it, and both are pinned here byte for byte:

* `aurax7/kaggriculture-shop-router-reactive-v2` adds `room_guard`: on the last turn of a
  day it sells everything above 99 items, highest price first. Inventories drop into a
  100-slot shed at dawn and the excess is destroyed, so this is a leak, not a refinement.
* `dmitriigluzdov/kaggriculture-seven-turn-rescue-best-lb-2800` replaces callbacks 712-718
  with a bounded planner that searches short collect-and-return routes for each worker
  against an extracted copy of the engine's unit semantics, keeping only routes whose goods
  are actually delivered and sold.

## Our own contribution: the routing table is refittable

Every key in the parent's table contains `YARN_STORE`. Shops are drawn with replacement
from eight kinds, so roughly three quarters of games match no key and fall back to plan 0.
That looked like the obvious hole, and it is not one.

`experiments/shop_route_search.py` forces each of the thirteen plans in turn and plays all
of them against the unmodified parent over 800 dev seeds and both seats: 20 800 games, zero
failures, every one of the 64 ordered shop pairs observed. The prefix up to step 144 is
plan 0 for every route, and both shops unlock inside that prefix, so all thirteen legs meet
the *same* world and each cell is a paired comparison rather than two samples.

Two things came out of it. In the 49 pairs with no yarn store, **plan 0 is dominant**: the
best alternative scores between 0.20 and 0.38 against it, and nothing survives the test.
In the yarn pairs, where the parent does route, a different tape beats the parent's own
choice in nine of fifteen cells, several of them 32-0 and 38-0 in paired games. The
incumbent score in every cell is exactly 0.500 because same-tape play is an exact draw;
`experiments/fit_shop_table.py` therefore replaces a cell only on a one-sided sign test at
p < 0.05 over at least 20 paired games with at least a 0.10 gain, and the nine replacements
clear that by orders of magnitude.

The refit is real but small: against the parent it converts 17% of games from mirror draw
to win, 0.565 overall, and head to head against the same stack without it, 0.535 with a
95% interval of [0.515, 0.557] over 400 games.

## The layers do not compose the way their solo numbers suggest

Each layer beats the parent alone, on 200 games each over held-out dev seeds:

| candidate | vs `0909` | vs `aurax_reactive_v2` | vs `gluzdov_e182` | all |
|---|---:|---:|---:|---:|
| terminal rescue alone | **0.975** | 0.235 | 0.500 | 0.570 |
| room guard alone | 0.795 | 0.500 | 0.765 | 0.687 |
| room guard + rescue | 0.815 | 0.975 | 0.795 | 0.862 |
| room guard + rescue + our table (`v006`) | 0.825 | 0.945 | 0.815 | **0.862** |

The terminal rescue wins 97.5% against the bare parent and still loses 3 games in 4 to the
room guard, because a seven-turn cleanup cannot pay for a season of shed overflow. Reading
either solo column as a ranking would have shipped the weaker agent. Only the combination
wins every column.

## The release gate

`arena.paired`, 100 reserved validation seed blocks (`500000:500100`), both seats, eight
pinned opponents, full evidence profile, 3 200 games, zero callback failures.

```
v006 absolute        0.924   95% CI [0.898, 0.948]
v004 absolute        0.551
delta over incumbent +0.373  95% CI [0.346, 0.400]
verdict              SUBMIT
```

| opponent | rating (2026-09-09) | `v006` | `v004` |
|---|---:|---:|---:|
| `yhay_router_0909` | 2838.5 | 0.840 | 0.005 |
| `gluzdov_e182` | 2790.4 | 0.830 | 0.005 |
| `aurax_reactive_v2` | 2755.7 | 0.960 | 0.005 |
| `yamakawanin_king_v4e` | 2648.4 | 0.990 | 0.890 |
| `aberatozer_d5e3` | 2618.5 | 0.880 | 0.780 |
| `lynnsakurai_v4` | 2572.7 | 0.980 | 0.955 |
| `reyhanksatria_v1` | 2427.3 | 0.990 | 0.980 |
| `thomas_t95` | 2359.8 | 0.920 | 0.785 |

The improvement survives removing any single opponent and any single family; the smallest
leave-one-out delta is +0.290 with the room guard removed. The three tape-router opponents
are declared mirrors, since they share our tapes.

`v004` losing 199 of 200 games to each of the three current public routers is the finding
behind the ladder drop from rank 44 to rank 257: the artifact did not get worse, the field
moved past it in one day.

## What this does not establish

Absolute win rate against a panel is not a rating, and three of the eight opponents are
mirrors that share our tapes. The panel still has no opponent from the live top 10, so
`docs/TOP10_STANDING.md` remains unanswered: 0.840 against the rank-27 artifact bounds our
distance to the top from one side only. The routing table was fitted against a single
opponent - the parent itself - and the non-yarn cells were left alone precisely because
nothing in the tape library beat plan 0 there. Beating a clone in a mirror is worth a full
win under the host's stated tie rule, but a genuinely different plan for the 49 non-yarn
worlds still has to be generated rather than selected, and that is the next piece of work.
