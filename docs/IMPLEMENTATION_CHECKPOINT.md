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
| M4.1 | Continuous-input tracking with shared source impedance and physical slice states | Implemented; input_network KCL/AC/charge/convergence tests; ADR 0008; full-goal sweep pending |
| M4.2 | Code-dependent signed reference charge and coarse/fine reference state | Implemented; direct node/rail charge and causal state tests; small-droop scope in ADR 0009 |
| M4.3 | Finite-bandwidth/slew RA, phase switching and actual ADC2 aperture in the joint chain | Implemented; convolution/ODE/slew/swing/aperture tests; noise and ideal AZ limits in ADR 0009 |
| M4.4 | Auxiliary and interleave tracking mechanisms use real state and available quantized decisions | Implemented; causal/charge/precharge/combined 9b tests; ADR 0010 |
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

### M3 full-sweep result and M4.1 checkpoint (2026-09-13)

The M3 full sweep completed in 141.2 s and failed five gates: il_offset.PASS,
pipeline.PASS, s12 charge closure, and two s13 Ron assumptions. No exemptions
were added. The first-stage gain mismatch now actually acts in the ideal s12
fixture; that fixture explicitly disables it. Interleave ensemble experiments
previously changed only the sample RNG, which no longer redraws physical clock
or offset mismatch; their statistical oracle needs a real chip-seed ensemble
or conditional per-chip prediction. Historical Ron-derived limits must be
recomputed with the continuous network. These failures remain final-acceptance
work, not passing claims.

M4.1 implements shared Rs, individual Ron/C, SADC loading, optional persistent
filter-bus state, exact harmonic/linear-interval integration, and explicit
nonlinear-Ron substeps. The physical pool supplies actual initial states and
loads. New voltage/source-charge traces and JSON configuration round trips are
available. Independent KCL/RK4, charge integration, AC and step-convergence
checks passed. A focused input/skew/physical/provenance run passed 63 tests;
after analog-source and JSON additions, 38 input/charge regressions passed and
mypy passed 36 modules. No full-goal acceptance claim is made yet. Next: M4.2–4
joint reference/RA/ADC2 dynamics and causal pretracking/auxiliary input.

### M4.2–M4.3 checkpoint (2026-09-13)

`reference_charge` computes signed rail loads from physical capacitor terminal
charge, sampled subnode charge, dither connections and actual DEM masks.
`conversion.ConversionEngine` advances coarse/fine reservoir recovery, finite
RA gain/bandwidth/slew/swing and ADC2 wide/narrow tracking phases on one time
axis. It runs inside the per-sample acquisition loop, so the final reference
perturbation updates released slice state before reuse. No reference proxy is
added a second time. Phase/event traces and separate ADC2 input are exposed.
SciPy >=1.10 is declared for the linear matrix exponential and nonlinear ODE.

Direct rail/node tests: 5 passed. Joint analytic convolution, repeated poles,
independent ODE, slew, swing, actual ADC2 aperture and slice backaction tests:
10 passed. Full suite before the final trace-only additions passed **317 /
one known flicker xfail in 217.71 s**. After phase trace additions, 27 focused
input/reference/joint tests passed. No current full experiment-sweep pass is
claimed; M3 failed legacy gates and new M4 acceptance experiments still require
reconciliation in M6. Final-head tests must cover the later changes.

Reference loading is explicitly linearized at nominal rail voltages; peak
droop is reported. Auto-zero uses an ideal reset phase and existing aperture
noise budgets; the signal-pole solver does not pretend to validate cyclostationary
noise. These scopes are recorded in ADR 0009. M4.4 (causal quantized pretracking
and auxiliary input integration), M5, M6 and GitHub submission remain pending.

### M4.4 checkpoint (2026-09-13)

`pretracking.QuantizedPretracker` queues actual integer SADC decisions with
availability timestamps. Latest/own/weighted predictions use nominal code
weights and known dither/attenuation only. Finite local precharge drivers update
the real slice and SADC capacitor states before the main acquisition. The shared
input filter remains isolated from these drivers. A clocked parasitic input
branch can be connected to the common bus or an independent auxiliary source;
its acquisition and reset charges are recorded separately. Physical IDs retain
its history. Model details and limitations are in ADR 0010.

Independent availability, actual precharge influence, source/reset charge and
auxiliary bus-improvement tests passed. Combined nine-bit sampling/quantizer
dither + reference + RA + auxiliary + pretrack tests verify digital provenance
and zero backend/RDAC overflow. Integration exposed and fixed SADC dynamic
attenuation: tracking error must pass through the nominal sampling attenuation,
and a digital pretracking estimate must undo that same nominal factor.
After that correction, 59 focused assistance/joint/input/charge tests passed;
the final weighted-policy/configuration run adds provenance coverage. Full
M4.2/M4.3 suite evidence remains 317/one known xfail at the earlier snapshot;
M5/M6 and final-current-head acceptance remain unfinished.
