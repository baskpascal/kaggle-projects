# State-conditioned Option selector: rejected at the oracle gate

The focused hypothesis is rejected for the current owned planner and this minimal Option
set. Fifty high-information midgame states produced no win under `FOLLOW_CURRENT`,
`COW_CAPACITY`, or `SHEEP_CAPACITY`. The per-state oracle therefore scored 0%, exactly the
same as every fixed continuation. There is no reward signal for a learned selector to
recover, so Phase 3 was not run and no checkpoint layer was integrated.

This supersedes the narrower `option_selector.py` v0 result. That script did not test a
real cow policy: `animal_type` is ignored while `economic_planner=True`, it used no sheep
Option, and it did not retain auditable raw evidence. The new test commits the existing
planner to a static cow or sheep capacity target for eight days. The unchanged planner,
scheduler and executor then perform the necessary pasture build, animal purchase and
placement, feeding, production, harvesting and sales.

## Sample and result

The driver screened 60 worlds and selected 50 baseline losses by observable feature
diversity. Forty-six came from actual v006 losses in the existing top-2800 replay panel:
20 binghua, 11 Otter Vibe, 8 Mengfei Li and 7 Matthew Huang states. Four came from paired,
reactive v006 games. Checkpoints were chosen immediately before sustained cash-gap
acceleration: turns 168, 192, 216 and 240.

| continuation | WR | mean margin | median terminal money | median transition cost |
|---|---:|---:|---:|---:|
| `FOLLOW_CURRENT` | 0.000 | -56,722.72 | 58,244.0 | 13,177.5 |
| `COW_CAPACITY` | 0.000 | -68,546.80 | 35,513.0 | 17,009.0 |
| `SHEEP_CAPACITY` | 0.000 | -70,244.30 | 43,308.5 | 18,933.5 |
| oracle per state | **0.000** | — | — | — |

The oracle gap over the best overall fixed continuation is **0.000** and an alternative
Option strictly beats current in **0/50** states. All outcomes tie as losses; money margin
is reported as diagnosis and is never used to select a policy.

## Mechanism and counterfactual validity

The null result is not an executor or detector artifact. Every state had a real targeted
buy and positive target-herd growth. Across the 50 cow branches, 1,145 cows were bought,
the realized cow herd grew by 460 and pasture count grew by 741. Across sheep branches,
1,167 sheep were bought, the realized sheep herd grew by 377 and pasture count grew by
748. Both alternatives spent more transition capital and worsened mean margin.

Every branch replays an identical deterministic prefix and the driver refuses a mismatched
checkpoint digest. This is required because v006 is stateful; restoring only the public
observation would reset its private Python memory. Full arena telemetry supplies executed
market commits and realized herd/structure deltas. Agent or opponent callback failures
invalidate the run rather than being scored.

The 150 Option branches were distributed across both Ray hosts: 98 matches on
`DESKTOP-V3A6VJ6` and 52 on `DESKTOP-DM63QP1`. The elite tapes are open-loop and therefore
serve as screening counterfactuals, not claims about live-agent win rate. That limitation
cannot rescue the direction: the reactive v006 controls also contain no Option win, and
the success gate already fails before any learned model is justified.

Machine-readable evidence and source hashes are in
`docs/state-option-counterfactual-20260910.json`. The full per-state report remains under
ignored `experiments/results/` and is identified there by SHA-256.

## Decision

Kill this hierarchical-RL direction for now. The tested macro expansions are real but
uniformly inferior to the current continuation, and state conditioning cannot select a win
that no Option produces. The next experiment must first discover at least one macro
continuation that wins in some strong, reactive worlds; only then should an Option selector
or fitted value model be reconsidered.
