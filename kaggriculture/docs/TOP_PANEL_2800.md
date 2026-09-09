# Raising the bar to 2800: what a top-ten opponent is, and what it is not

`docs/TOP10_STANDING.md` states the blocking problem plainly: the bands that decide a paid
finish were empty, because the best artifact anyone publishes as a notebook sits far below
the agents holding the top of the ladder. This document closes that gap with the host's own
daily dump, and reports the two findings that came out of doing it - one of them a refutation
of the obvious way to use that data.

## Admission

`experiments/top_panel.py` reads the 2026-09-08 dump (663 episodes, engine 1.32.7, the
newest release the index offers) and admits a recorded stream only when all three hold:

1. **Both teams in the episode are rated at or above 2800** on the dated leaderboard
   snapshot `kaggriculture-publicleaderboard-2026-09-09T13:22:21.csv`. Kaggle pairs agents
   of similar rating, so a pairing where both sides clear the bar says something about the
   game that was played; one strong team beating an unrated one says nothing.
2. **The replayed seat won that game.** A losing stream from a strong team is a recording
   of the day that team lost.
3. **The episode reproduces exactly.** Both streams are replayed at the recorded seed on
   the fast and official backends and must return the published final money to the coin.

Of 1,289 distinct streams: 966 refused for a team below the bar, 156 for a lost game, 10
for a team absent from the snapshot, leaving **157 admissible**. Capped at four per team
and one per distinct opening, **20 opponents** are pinned under `opponents/recorded/`, all
20 reproduced exactly on both backends. They come from teams at ranks 1, 2, 3, 4, 6, 7, 10
and 32 - the actual top of the ladder, for the first time in this repository.

## First finding: a top tape on a fresh seed is not a top opponent

The obvious use of those pins is to add them to the fixed panel and play them on fresh
seeds. Measured over 50 dev seeds and both seats, 2,000 games each, zero failures:

| candidate | score against the 20 recorded top opponents, fresh seeds |
|---|---:|
| `v006` | 0.960 |
| `yhay_router_0909` | 0.961 |

Both of them beat the recorded play of the world's top four teams 96% of the time, which is
obviously not a statement about those teams. A tape is a bet on the town it was recorded
in: shops are drawn with replacement, so on a fresh seed the recorded agent builds for
demand that does not exist while we route on the shops we can see. **Transferring a tape to
a new world measures the tape's transfer rate, not the agent's strength**, and a panel built
that way would have raised the number while lowering the bar. The pins stay, with that
caveat in their manifests, because they are cheap and provenance-complete; they are not
evidence of top-ten strength.

## Second finding: at the recorded seed, our stack is exactly its parent

The measurement that does mean something is the one that puts the candidate in the recorded
world: `experiments/current_meta_benchmark.py` replaces each seat of each episode in turn at
the episode's own seed, while the other seat replays what it actually submitted. Restricted
to the 157 both-teams-above-2800 episodes, 314 seat assignments, zero failures:

| candidate | score | 95% CI by episode | worst team |
|---|---:|---|---|
| `v006` | **0.7930** | [0.744, 0.842] | binghua (rank 6) 0.571 |
| `yhay_router_0909` | **0.7930** | [0.744, 0.842] | binghua (rank 6) 0.571 |
| `v005` | 0.7452 | [0.691, 0.800] | mtmr_s1 0.400 |

| opponent team | rank | games | `v006` | `0909` | `v005` |
|---|---:|---:|---:|---:|---:|
| SpaTaro | 1 | 81 | 0.889 | 0.901 | 0.889 |
| Otter Vibe | 2 | 44 | 0.727 | 0.727 | 0.727 |
| Matthew Huang | 3 | 57 | 0.860 | 0.860 | 0.807 |
| Mengfei Li | 4 | 44 | 0.795 | 0.773 | 0.773 |
| binghua | 6 | 49 | **0.571** | 0.571 | 0.510 |
| Tarang222 | 10 | 21 | 0.762 | 0.762 | 0.667 |
| mtmr_s1 | 32 | 10 | 0.900 | 0.900 | 0.400 |

`v006` and `0909` differ on **2 of 314 assignments**. That is the finding that matters, and
it reframes the v006 release measured in `docs/SHOP_ROUTER_STACK.md`: the +0.373 gate delta
is real and it is against `v004`, and the 0.825 head-to-head against the parent is real and
it is a *tie-breaker*. The room guard, the terminal rescue and the refitted routes each win
by a few hundred coins, which flips a mirror game and almost never flips a game against an
opponent whose money differs by tens of thousands.

On the live ladder that is still worth having - `shop-router-0909` has 89 votes and at least
two public forks, so a large share of our opponents near 2800 are its clones, and the host
counts a tie as half a win, which our stack converts into a whole one. It is not a path to
the top ten. The top ten are not beaten by out-tie-breaking them.

## What this changes about the next piece of work

The bar now exists, so the objective can be stated in it: `v006` scores 0.793 against
top-2800 behaviours in their own worlds, and the hole is concentrated - binghua at 0.571 and
Otter Vibe at 0.727 against 0.889 for the world number one. Beating them needs a bigger
economy, not another overlay, and the same 157 episodes are the donor material for building
one. The concrete next step is unchanged from `docs/SHOP_ROUTER_STACK.md` and now has a
fitness function: generate a plan for the 49 non-yarn shop pairs, where every 0909 descendant
including ours plays the identical fallback tape, and score it here rather than in mirrors.

## Reproducing

```bash
.venv/bin/python scripts/sync_kaggle_replays.py --destination data/kaggle/official \
    --library data/kaggle/official/tapes-latest.json

.venv/bin/python -m experiments.top_panel \
    --library data/kaggle/official/tapes-latest.json \
    --archive data/kaggle/official/kaggriculture-episodes-2026-09-08.zip \
    --leaderboard data/kaggle/leaderboards/kaggriculture-publicleaderboard-2026-09-09T13:22:21.csv \
    --observed-at 2026-09-09 --min-rating 2800 --per-team 4 \
    --agents opponents/recorded --output docs/top-panel-2800.json

.venv/bin/python -m experiments.current_meta_benchmark \
    --candidate versions/v006/main.py \
    --source data/kaggle/official/top2800-2026-09-08.zip \
    --output data/kaggle/official/top2800-v006.json --agent-dir /tmp/top2800-agents
```

The dump, the extracted subset and the per-game rows live under ignored `data/`; the
admission report is `docs/top-panel-2800.json` and the scores are
`docs/top-panel-2800-scores.json`.
