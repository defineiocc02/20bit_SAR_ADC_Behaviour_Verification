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
| M2.1 | Separate 7b/9b decision hypotheses, DAC grid/range and dither amplitude enhancement | Implemented; test_architecture_candidates.py; ADR 0009 |
| M2.2 | Residue, ADC2 range, capacitor definitions and endpoint headroom are independently verified | Implemented; test_architecture_candidates.py; ADR 0009 |
| M3.1 | PhysicalSlicePool drives held charge, gain, noise and sample ownership in the main runner | Implemented; physical_pipeline + causal audit regressions; final sweep pending |
| M3.2 | Causal schedule, startup, latency and independently seeded physical mismatch | Implemented; physical_pipeline + causal audit regressions; final sweep pending |
| M3.3 | Realizable multidimensional DEM masks and correct non-pipeline scheduler behavior | Implemented; physical_pipeline + causal audit regressions; final sweep pending |
| M4.1 | Continuous-input tracking with shared source impedance and physical slice states | Implemented; input_network KCL/AC/charge/convergence tests; ADR 0010; full-goal sweep pending |
| M4.2 | Code-dependent signed reference charge and coarse/fine reference state | Implemented; direct node/rail charge and causal state tests; small-droop scope in ADR 0011 |
| M4.3 | Finite-bandwidth/slew RA, phase switching and actual ADC2 aperture in the joint chain | Implemented; convolution/ODE/slew/swing/aperture tests; noise and ideal AZ limits in ADR 0011 |
| M4.4 | Auxiliary and interleave tracking mechanisms use real state and available quantized decisions | Implemented; causal/charge/precharge/combined 9b tests; ADR 0012 |
| M5.1 | Noisy training, identifiable weights, frozen coefficients and independent validation on same chip | Implemented; static effective-unit SVD/frozen workflow; tests + full-18 pilot; ADR 0013; full 18-slice/two-chip/two-training-size acceptance passed |
| M5.2 | Fixed-point coarse/fine code reconstruction, clipping and transition/code-width verification | Implemented; checked Q30/Q32/96-bit integer core; exhaustive output oracle + all coarse carries + noisy holdout; ADR 0014 |
| M6.1 | PSD normalization, harmonic collisions, noise integration and separate paper/slide benchmarks | Complete; independent SciPy/Parseval/alias tests + separate source profiles; mandatory acceptance passed |
| M6.2 | Long-record low-frequency state/noise validation and explicit observer-extension limits | Complete; physical-time state + covariance/chunk/64s PSD and mode convergence; observer scope ADR 0015 |
| M6.3 | Full tests, lint, typing, experiment sweep, build, source/assumption documentation and results | Local complete: 394 tests, 52 gates twice, byte-identical source/wheel results, build and 47-module typing; exact-head CI pending |
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
noise. These scopes are recorded in ADR 0011. M4.4 (causal quantized pretracking
and auxiliary input integration), M5, M6 and GitHub submission remain pending.

### M4.4 checkpoint (2026-09-13)

`pretracking.QuantizedPretracker` queues actual integer SADC decisions with
availability timestamps. Latest/own/weighted predictions use nominal code
weights and known dither/attenuation only. Finite local precharge drivers update
the real slice and SADC capacitor states before the main acquisition. The shared
input filter remains isolated from these drivers. A clocked parasitic input
branch can be connected to the common bus or an independent auxiliary source;
its acquisition and reset charges are recorded separately. Physical IDs retain
its history. Model details and limitations are in ADR 0012.

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

### M4 sweep and M5 prerequisites (2026-09-13)

The full M4 sweep at 5d78aff completed in **371.9 s**. Structural charge closure
now passes. Five gates remain failing: il_offset.PASS, pipeline.PASS,
rdac_bitwise.PASS and the two s13 Ron hypothesis/boundary records. The old
bitwise experiment compares inactive proxy controls and reports a NaN ratio;
M6 must replace that experiment with actual signed-event/ref/RA observations
and enforce standard finite JSON output. No gate was exempted or relaxed.

M5 prerequisites: physical split pool methods now accept explicit runtime cfg
for masks/DEM while retaining their construction config and fixed capacitances.
This prevents calibration masks from being accidentally frozen into validation.
The reused-chip regression passes (9 physical tests). Raw ADC2 integer codes
are exposed before observer correction in every runner; invalid analog samples
and malformed digital buffers are rejected. Raw-code/observer tests passed
20 cases (two already-tested dense INL cases excluded from this focused run).
Ruff check/format and mypy pass at this checkpoint. Real noisy weight training,
frozen validation and fixed-point final reconstruction remain M5 work.

### M5.1 checkpoint (2026-09-13)

`weight_calibration` defines a digital-only geometry/observation interface,
fits effective physical unit C/Cf weights (beta*C/Cf for sub units) plus ADC2
offset with one rank-revealing SVD, reports uncertainty diagnostics and rejects
rank deficiencies. Frozen coefficients have immutable bytes-backed arrays and
versioned strict JSON. A separately supplied known training reference is the
only training input; validation reconstruction receives no ideal input/physical
truth. DigitalState injection applies coefficients in the production split path.
`run_with_split_calibration` records its controlled static training setup and
retains requested noise, then validates on the same pool. The old unary gain
wrapper now retains sampling noise/DEM and uses the correct RDAC-port injection.

Weight/frozen/workflow/raw-code/physical tests passed 30 cases; mypy passes 40
modules. Full 18-slice/8-active, 63+8 pilot: rank 1279, condition 458.403,
training residual RMS 1.1809 mV at ADC2, independent validation RMS
95.103 -> 43.608 uV with noise retained (implementation_m5_full_pool_probe.log).
Full current suite completed: **350 passed / one known flicker xfail in
209.25 s**, logged in implementation_m5_full_pytest.log. Ruff check/format
and mypy (40 modules) pass.
No final full-sweep/CI acceptance is claimed. M5.2 fixed-point reconstruction,
M6 noise/metrics/benchmarks/acceptance/docs and GitHub submission remain pending.


### M5.2 checkpoint (2026-09-13)

`SimResult.to_codes()` now reconstructs final 20-bit words from raw ADC2 codes,
digital switching and nominal/frozen coefficients using checked fixed-point
arithmetic. Explicit register widths, signed ties-to-even, half-open input bins,
independent analog/output clipping and versioned immutable JSON are documented
in ADR 0014. Fractional RDAC masks and unquantized observer paths are rejected.

28 fixed-point/calibration tests passed in 5.22 s, including exhaustive 2^20
code enumeration, all 511 coarse carries, poisoned float/truth buffers and
independent noisy calibrated holdout. Mypy passes 41 modules. Final acceptance
experiments must measure final code streams, not only floating diagnostics.
M6 metrics, low-frequency noise, full sweep reconciliation, documentation,
release build and exact-head GitHub CI/submission remain pending.


### M6 metrics/noise checkpoint (2026-09-13)

PSD V²/Hz and tone amplitude are now separate, including odd/even endpoint
normalization and explicit band integration. Harmonic fitting deduplicates aliases
and handles Nyquist as one real column; rank/condition/reliability are reported.
Independent SciPy/Parseval and known-signal checks pass. Published paper and
slides benchmark records remain separate fitted noise anchors.

BandLimitedFlicker supplies a frozen stationary log-frequency state at actual
40 MS/s sample indices. The short-record wrapper's unreachable drift branch is
replaced with physical slow modes, not a record-scaled ramp. Chunk/64-second
probe and independent covariance integrals pass; the original strict xfail is
removed. Combined current noise/metric/audit checks: **110 passed in 4.60 s**.
ADR 0015 documents formulas, source references, low cutoff and observer limits.
Full long-time PSD/mode convergence must still join mandatory acceptance gates.
M6 final sweep reconciliation/build/docs/results and GitHub submission remain
unfinished; do not mark this checkpoint as full-goal acceptance.


### Local release verification checkpoint (2026-09-13)

All implemented requirements are locally verified. Full suite: **394 passed,
zero xfails**, coverage **65.37%** (implementation_release_pytest.log). Ruff
check/format, mypy (47 modules), diff whitespace and source/wheel build pass.
The installed wheel was imported outside the checkout and its bundled CLI ran
the entire acceptance sweep: **52/52 gates**, matching the source sweep byte for
byte. Both results.json SHA-256:
`f3e1a7967f22b30e037d881668b40124cea4ed3f47600a2addb69a502f67c0eb`.
Logs: implementation_m6_sweep2.log, implementation_wheel_sweep.log,
implementation_release_build.log. No process needs restarting.

The previous M6 sweep found a statistically fragile single-realization flicker
ratio and an integer-key serialization incompatibility. Both were corrected:
32 independent noise realizations with tighter uncertainty, paired white noise
for mechanism comparisons, and collision-checked numeric JSON key normalization.
The successful sweeps include those changes. New mandatory gates cover noisy
frozen final words, long-record PSD, physical reference loading, independent
dither range, and explicit conditional Ron scan status. No exemption was added.

Full 18-slice / 8-active, 63+8 candidate: chip seeds 42 and 99; Ncal 8192/16384,
Nholdout 16384. Final-word RMS at Ncal=16384 is 43.423/43.273 uV versus
uncalibrated 81.618/90.125 uV. Coefficient SE RMS falls from about 5.18e-5 to
3.44/3.48e-5. All fits have rank 1279 and no overflow. These are conditional
noisy behavior results with a DR-fitted RA source, not silicon predictions.

64 s physical-time noise probe: 40 MHz ADC clock, 256 Hz explicit antialias
observation, 0.25 Hz PSD spacing, 32 states. Slope -9.9207 dB/dec; integrated
2–20 Hz power 7.3593e-15 V² versus 7.3494e-15 predicted. Covariance error falls
from 0.02509 at 32 modes to 0.000428 at 512 modes. The fast pipeline record is
not claimed to span those 64 seconds.

Current HTML report and eight figures are generated from the serialized result;
old hard-coded performance text is removed. Source labels and ppmFS-to-LSB
conversion are corrected. ADR IDs are normalized: historical 0001–0008 retained,
physical closure 0009–0015. The package now includes both CLI scripts in wheels.

GitHub main remains ffcc011 (live API verified). Git HTTPS preflight again timed
out at 25 s; authenticated Git-data API transport will be used if needed. Local
commit, GitHub branch/PR submission and exact-head CI inspection remain to do.
Do not mark the goal complete before that evidence exists.
