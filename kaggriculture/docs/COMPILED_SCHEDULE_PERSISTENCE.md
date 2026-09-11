# Compiled schedule Stage 1: persistence is rejected

Issue: [#70](https://github.com/baskpascal/kaggle-projects/issues/70). This is a
mechanism test, not competitive evidence. All milestones below are read from the next
engine observation; requested orders do not satisfy a gate.

## Static ceiling

Three quadrants expose 75 tiles. The target of 40 crops plus 12 pastures leaves 23 tiles
of slack. In the worst committed-v0 world, the missing 27 crops and six pastures require
at most 297 worker-turns under the deliberately loose bound of nine turns per completion;
the scheduled crew could provide 762 worker-turns over t216:t287. Seed capital for 27
WHEAT tiles is only 270 coins. The mechanism therefore had enough physical and monetary
ceiling to matter.

## Sequential causal ablations

The first clean trace invalidated the initial problem formulation. At t216, the pending
2,000-coin land reserve blocked every scheduled HIRE until t280. Seed 1003 consequently
had only 72 of 762 possible worker-turns, fell from 37 crops to 12, and reached t288 with
13 crops and six pastures.

Each subsequent arm changes one named opt-in switch and preserves aggregate v0 as a
bit-exact control:

| world | aggregate v0 crops/pasture | HIRE bypass | + structural market priority | + persistent assignments |
|---|---:|---:|---:|---:|
| seed 1003, seat 0 | 13 / 6 | 39 / 12 | 39 / 12 | **40 / 12** |
| seed 1004, seat 0 | 13 / 6 | 38 / 12 | **40 / 12** | not needed |
| seed 1007, seat 0 | 41 / 12 | 42 / 12 | **42 / 12** | **38 / 12** |

The HIRE bypass removed the false choice between future land and today's crew, but
optional animal orders then occupied three of the ten market slots and limited days 10–11
to seven/eight hands. Deferring those optional orders through t287 restored 2,760/2,760
expected worker-turns in seeds 1003/1004. It still missed seed 1003 by one crop, so the
original per-unit hypothesis survived long enough to receive its cheapest boundary test.

## Persistent assignment verdict

Persistent assignment passed seed 1003 exactly, then failed the predefined boundary seed
1007: 38 crops versus 42 for its immediate resource-only control, with 12 pastures, three
quadrants, and 2,755/2,760 worker-turns. All 183 audited PLANT/BUILD_PASTURE commands were
distinct real tile transitions. Audited PLANT, BUILD_PASTURE, HIRE, and BUY_LAND failures
were zero. This scope is intentionally called *measured structural failures*; it is not a
claim that every possible engine failure was audited.

The causal trace explains the regression. Active persistent PLANT targets disappear from
the ordinary `jobs`/`planned` demand counter used by seed procurement. In seed 1007 this
produced 166 zero-seed states versus two in the control and reduced BUY_SEED calls from 46
to 32. A worker remembered where to plant while the purchasing layer forgot that the crop
was planned.

## Decision

**REJECT at Stage 1.** The predefined two-world boundary gate failed, so the 10-world Ray
run and Stage 2 were not executed. No competitive lift is claimed. Persistent assignment
remains opt-in and off by default; the reusable output is the resource-arbitration
instrument, actual-state execution audit, sustained-crew telemetry, and the regression
showing that commitments must participate in upstream dependency accounting.

Implementation hashes used by the persistent smokes:

- planner: `a7c0763398084709c76d9fd96b8b8bade1ac88e4fa2ed577734f16d60fb3fc8b`
- parameters: `72b4ec13b5ca328276bc4a4d0818c3c37db9a91afb2ee8ab648a3f1f4b385049`
- gate before evidence-only field rename: `93488daf4a282720294fe57056a1e276094ea9100c16870fa3347ce187197516`
- frozen opponent `v006`: `495bfa4825c9e58638e804aeeb62a9537826f6d4d869222815e1bbeefd84d3dc`

Focused verification after the evidence-only rename: 33 tests passed.
