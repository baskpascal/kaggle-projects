# Evidence profiles

Every match declares the amount of evidence it produces. The profile is part of both the
job identity and the `RunSpec`, so resume and shared transports cannot substitute a compact
result for a diagnostic one.

| Profile | Use | Payload |
|---|---|---|
| `score` | leagues, paired gates, searches and screening | outcome, money/margin diagnostics, failures, hashes, configuration, engine and wall time |
| `audit` | runtime and action-counter diagnosis | `score` plus aggregate audit/sales counters and callback runtime percentiles |
| `full` | economic analysis and replay capture | `audit` plus daily economics and per-turn runtime samples; replay is opt-in |

`arena.league`, `arena.paired`, and the benchmark drivers default to `score`. A caller that
needs daily economics must request `full` explicitly. Replay capture refuses a new compact
profile request because a replay is intentionally full diagnostic evidence.

The deprecated `telemetry_enabled` argument remains accepted for old callers: `True` maps
to `full` and `False` maps to `audit`. It must not be combined with a conflicting explicit
profile.
