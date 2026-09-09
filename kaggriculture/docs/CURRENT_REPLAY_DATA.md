# Current ladder replay data

The project can now fetch the official daily Kaggriculture replay dump without a
Kaggle login:

```bash
.venv/bin/python scripts/sync_kaggle_replays.py \
  --destination data/kaggle/official \
  --library data/kaggle/official/tapes-latest.json
```

The command reads `kaggle/kaggriculture-episodes-index`, chooses the newest release,
downloads it atomically, checks the declared episode count, records the archive SHA-256
and optionally builds a deduplicated tape library. Downloads live under ignored `data/`;
credentials and multi-gigabyte archives never enter Git.

The benchmark hard-fails when any episode's engine differs from the arena fingerprint.
Older corpus shards and the historical `matchups_top.parquet` cannot silently become
evidence for engine 1.32.7; the complete corpus admission work remains tracked in #52.

## Extractor correction

Kaggle's state at `steps[0]` is the initial position and contains a placeholder action.
The 719 actions that actually ran are stored in `steps[1:]`. The old extractor used
`steps[:-1]`, which inserted the placeholder and discarded the last real action.

Episode `106611436` is the known-answer case. The old slice produced
`43,771 x 91,408`; the corrected slice reproduces the published
`81,819 x 76,416` exactly with both local engine backends. Results in
`LADDER_META.md` that used old daily-dump tapes are explicitly marked invalid.

## What the 2026-09-08 data changed

The official archive contains 663 episodes on engine 1.32.7. Correct extraction found
1,289 distinct action streams from 47 teams. Screening the 32 richest winning streams
on fresh seeds found several strong plans; copying the eight best through the V004
chassis did not improve V004 on the fixed public panel.

One unwrapped stream was different. Tape `b4d064f411cd`, recorded from team `mtmr_s1`,
became V005 after three independent checks:

| check | V004 | V005 | reading |
|---|---:|---:|---|
| current daily behaviours, 1,326 games | 64.44% | **69.42%** | paired delta +4.98 pp, 95% CI +3.23 to +6.73 pp |
| fixed public panel, 200 games each | **83%** | 81% | delta -2 pp, CI includes zero; worst family improves 62% to 66% |
| V005 directly against V004, 200 games | 20.5% | **79.5%** | both seats agree; 95% CI 71.6% to 87.4% |

All 3,252 games in those three comparisons completed without callback failures. V005 passed the official
submission preflight on engine 1.32.7. V004 remains useful as the complementary finalist:
it keeps the small aggregate advantage on the older fixed panel, while V005 wins the
current-meta and direct measurements.

The daily benchmark is deliberately labelled `current_meta_open_loop_benchmark`.
Recorded opponents replay what they submitted in the observed game and cannot choose a
different action after our candidate changes the world. Its score is evidence for
behavioural coverage, not a direct estimate of live ladder win rate. The compact,
machine-readable evidence is in `docs/current-replay-evidence.json`; full rows remain
under ignored `data/`.

The V005 source episode is also a known-answer proof: replaying both recorded streams at
seed `665066317` reproduces `142,233 x 138,960` exactly on both the fast and official
backends.

Reproduce the daily measurement with:

```bash
.venv/bin/python -m experiments.current_meta_benchmark \
  --candidate versions/v005/main.py \
  --source data/kaggle/official/kaggriculture-episodes-2026-09-08.zip \
  --agent-dir data/kaggle/official/current-meta-agents \
  --output data/kaggle/official/v005-current-meta-full.json
```

Sources:

- <https://www.kaggle.com/datasets/kaggle/kaggriculture-episodes-index>
- <https://www.kaggle.com/datasets/kaggle/kaggriculture-episodes-2026-09-08>
- <https://www.kaggle.com/datasets/georgymamarin/kaggriculture-episodes>
- <https://www.kaggle.com/datasets/destbreso/kaggriculture-benchmark-matchups>
