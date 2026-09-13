# Behavioral closure implementation checkpoint

Requested outcome: implement the 2026-09-12 review recommendations, perform full
verification, and submit the resulting changes to GitHub. Baseline: ffcc011
(v7.0.10). Working branch: `codex/physical-behavioral-closure`.

This is a Python behavioral-model delivery. Disclosed architecture constraints
and conditional circuit assumptions must remain distinguishable. Semiconductor
PDK, transistor-level power, and undisclosed silicon details are not available;
passing behavioral checks must not be presented as measured silicon validation.

## Requirements and evidence ledger

Every row needs implementation, relevant independent verification, and user-facing
documentation. Existing green gates alone do not close a row.

| ID | Required outcome | Status / authoritative evidence |
|---|---|---|
| M1.1 | Common SADC offset/gain/mismatch construction and legal thresholds | Implemented; regression/test_charge_parameter_closure.py + full suite |
| M1.2 | Main/sub bank dither units agree in analog charge, RDAC commands and digital correction | Implemented; regression/test_charge_parameter_closure.py + full suite |
| M1.3 | Real sampling masks drive signal charge as well as dither charge | Implemented; regression/test_charge_parameter_closure.py + full suite |
| M1.4 | Effective configuration, supported controls, feedback capacitance and applied calibration are observable | Implemented; regression/test_charge_parameter_closure.py + full suite |
| M2.1 | Separate 7b/9b decision hypotheses, DAC grid/range and dither amplitude enhancement | Implemented; test_architecture_candidates.py; ADR 0007 |
| M2.2 | Residue, ADC2 range, capacitor definitions and endpoint headroom are independently verified | Implemented; test_architecture_candidates.py; ADR 0007 |
| M3.1 | PhysicalSlicePool drives held charge, gain, noise and sample ownership in the main runner | Implemented; physical_pipeline + causal audit regressions; final sweep pending |
| M3.2 | Causal schedule, startup, latency and independently seeded physical mismatch | Implemented; physical_pipeline + causal audit regressions; final sweep pending |
| M3.3 | Realizable multidimensional DEM masks and correct non-pipeline scheduler behavior | Implemented; physical_pipeline + causal audit regressions; final sweep pending |
| M4.1 | Continuous-input tracking with shared source impedance and physical slice states | Pending |
| M4.2 | Code-dependent signed reference charge and coarse/fine reference state | Pending |
| M4.3 | Finite-bandwidth/slew RA, phase switching and actual ADC2 aperture in the joint chain | Pending |
| M4.4 | Auxiliary and interleave tracking mechanisms use real state and available quantized decisions | Pending |
| M5.1 | Noisy training, identifiable weights, frozen coefficients and independent validation on same chip | Pending |
| M5.2 | Fixed-point coarse/fine code reconstruction, clipping and transition/code-width verification | Pending |
| M6.1 | PSD normalization, harmonic collisions, noise integration and separate paper/slide benchmarks | Pending |
| M6.2 | Long-record low-frequency state/noise validation and explicit observer-extension limits | Pending |
| M6.3 | Full tests, lint, typing, experiment sweep, build, source/assumption documentation and results | Pending |
| GIT | Commit and push reviewable branch/PR; inspect GitHub CI for that exact head | Pending |

## Current milestone

M1 complete: 269 passed, 4 known xfailed; repository-wide ruff check/format
and mypy (32 modules) pass. Added common SADC construction, sampling_charge
module, dither mask/units, physical alpha and observable feedback/run contracts.
M2 implemented: independent 9b/4x dual-port candidate, complete-count 63+8
full-range topology, real bridge area allocation and command-overflow telemetry.
Full suite after implementation: 279 passed / 4 known xfailed. After provenance
and ADR corrections: 72 targeted tests passed; mypy passes. Next: M3 real
physical pool integration and causal sample event flow. The four
remaining xfails belong to M3/M6 and are not counted as completed capabilities.
GitHub API confirms remote main remains ffcc011; HTTPS git fetch times out,
but API authentication and repository ADMIN permission are verified. Submission
will retry Git transport or use the authenticated GitHub Git-data API.

## Resume instructions

Inspect this ledger, `git status`, latest commits, test logs and active process
handles before resuming. Record completed evidence and failures here at milestone
boundaries. Never restart a completed experiment solely because a model stream
ended. Do not mark the objective complete until every row and GitHub submission
has direct current evidence.

### M3 checkpoint (2026-09-13)

PhysicalSlicePool now drives both split entries through pipeline_engine;
run_sim_split_reference retains the aggregate algebraic comparison. Shared causal
schedule, independent fabrication/clock/noise streams, physical mask charge,
per-sample gain/noise and actual unary slice selection are implemented. DEM has
independent physical row/column/subarray maps and optional zero-sum code exchange.
Three original xfails were removed after actual XPASS and causal perturbations.
An intermediate full run passed 282 tests / one flicker xfail (129.31 s); later
physical/DEM additions passed 18 dedicated tests and focused causal tests.
Final M3/full-goal verification must use the current head, not that intermediate
run. M4-M6 and GitHub submission remain unfinished. Continuous acquisition and
joint RA/reference dynamics are explicitly still pending.
