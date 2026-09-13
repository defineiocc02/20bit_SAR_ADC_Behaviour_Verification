# Behavioral closure: model contracts and validation

This document describes the implementation following the September 2026 review.
The live requirements/evidence ledger is `IMPLEMENTATION_CHECKPOINT.md`.

Continuous input tracking, state/units and independent verification are specified
in [ADR 0008](adr/0008-continuous-input-network.md). Use `Config.from_dict` to
reload exported JSON configurations with typed nested parameter groups.
The joint reference/RA/ADC2 model, event observations, numerical verification and
small-droop/noise limits are specified in [ADR 0009](adr/0009-joint-reference-ra-adc2.md).
Available-code pretracking and auxiliary input source/charge contracts are in
[ADR 0010](adr/0010-causal-input-assistance.md).
Noisy effective-unit calibration, strict digital observations and frozen
same-chip validation are specified in [ADR 0011](adr/0011-noisy-identifiable-unit-calibration.md).

## Shared parameter and charge contracts

All SADC constructors accept **nominal** thresholds. Offset, gain mismatch and
fixed threshold mismatch are applied once in `sadc.py`, for every topology and
runner. Split gain mismatch is centered on the nominal DAC range midpoint.
Non-finite or non-increasing thresholds raise `ConfigError` rather than entering
`searchsorted`. A parameter perturbation is verified at the decision and residue
nodes; backend redundancy can legitimately hide a small decision error at the
final output.

`sampling_charge.py` owns the split sampling-charge conversion. A sampling mask
controls both signal charge and dither charge. Its three code/voltage quantities
are distinct:

| Quantity | Unit | Use |
|---|---|---|
| `sample.dither_bank_code` | bank capacitor increment | physical switch mask |
| `sample.dither_code` | nominal fine RDAC step | RDAC command and nominal correction |
| `sample.dither` | V, physical input referred | analog stored charge only |

For a bank increment with nominal effective capacitance `w_bank`,
`delta_bank = 2*Vfs*w_bank/Csig_nom`. Thus
`d_rdac = d_bank*delta_bank/delta_rdac`. Digital correction uses the nominal
voltage, never the fabricated capacitor values. For the default 64+8 split,
main-bank increments are eight fine RDAC steps; sub-bank increments are one.

The physical signal coefficient is
`alpha_true = 1 - Cmask_weighted_true/Csig_true`. The digital division remains
nominal (or estimated by a separate calibration), preserving mismatch as an
observable error. Continuous dither is interpolation on this same bank scale;
only discrete integer masks represent actual switch configurations. A repeated
charge-conversion call is idempotent. Unsupported masks fail configuration
validation instead of being silently truncated.

`Config.c_feedback0` is the unary capacitance parameter.
`Config.split_feedback_cap_f` explicitly sets the split feedback capacitance in
farads at the current area; its default `None` derives `Csig_nom/g0`. This permits
independent feedback-capacitance sensitivity studies without conflating the
unary and split signal-capacitance definitions.

Every `SimResult.effective_config` exposes the runner, requested configuration,
actual feedback capacitance and gain range, requested/applied calibration, and
inactive topology-specific overrides. This diagnostic metadata is not an input
to digital reconstruction or estimation. Bare runners use an injected digital
state; a calibration label alone is not evidence that training occurred.

## Independent regressions

`tests/regression/test_charge_parameter_closure.py` checks:

- Offset/gain/threshold mismatch move SADC decisions consistently in both split
  runners, while preserving deterministic chip mismatch.
- Main/sub, continuous/discrete dither configurations retain the backend
  quantization floor in their admissible input range.
- A single fabricated mask capacitor perturbation agrees with the independently
  eliminated subnode charge equation.
- Invalid thresholds/masks are rejected; bank normalization is idempotent.
- Explicit feedback capacitance changes physical gain and appears in serializable
  run metadata.

These supplement, rather than replace, the existing full acceptance sweep.
Stress values used to expose software defects are not claimed as PDK statistics.

## Architecture candidates and range

See [ADR 0007](adr/0007-independent-decisions-and-dither.md). New architecture
studies should start from `Config.paper_literal()` and compare the historical
`Config.paper_consistent()` baseline. Nine decisions and fourfold dither-port
amplitude are separate parameters. The complete-count 63+8 topology, ideal
dual-port injection and backend redundancy remain explicit assumptions.

`SimResult.rdac_over` records requested commands outside the physical range.
The physical DAC saturates; nominal digital command evaluation remains visible
so the saturation error is not silently removed. Full-scale, half-open input
boundaries and both discrete dither modes have independent regression coverage.

Bridge capacitance now uses its actual nominal size in the area allocation.
Consequently old fitted noise/area sweeps may shift slightly; this is a physical
accounting correction and must be reflected in regenerated results.

## Physical samples and scheduling

`run_pipeline` and `run_sim_split` use `pipeline_engine.execute_split` and the
same `PhysicalSlicePool`. Sharing production execution prevents divergent unit
contracts; `run_sim_split_reference` remains an independent aggregate algebraic
reference for the ideal electrical limit. It is not the physical 18-slice runner.

The physical split candidate gives each slice a scaled main/sub network and its
own floating subnode, bridge and parasitic. Signal charge and noise covariance
are summed after solving each subnode; connecting all subnodes into one global
beta would change the circuit. Counts and segmentation remain model assumptions.
The template `result.chip` holds nominal design/shared feedback information;
`result.pool.unit_caps`, bridge caps and selected IDs hold the actual arrays.
Supplying an aggregate `chip` explicitly distributes that realization equally
across slices; default fabrication independently draws the full physical pool.

`timing.build_slice_plan` is the single scheduling policy. Conversion groups
must equal the preceding acquisition groups, and conversion/acquisition may
not overlap. Records explicitly prime their first sample over a negative-time
acquisition interval; returned output IDs start at zero. Each independent call
resets electrical state while preserving an injected pool's fabricated values.
This record API does not imply seamless chunk streaming.

An injected pool retains its fabricated arrays and construction configuration.
Production calls pass the current runtime configuration explicitly to its split
charge methods, allowing different calibration/validation masks and DEM controls
on the same chip. Direct pool callers can use the same optional `cfg` keyword.

Results expose `sample_id`, `conv_slice_ids`, `acq_slice_ids`, `held_sample`,
`stored_charge`, `acquisition_start`, and `acquisition_error`. Noise, gain and
DAC voltage all derive from the selected capacitors. Continuous acquisition is
described below; the aggregate reference retains its historical dynamics only
for comparison.

`dem.split_switch_command` produces nominal row/column/subarray permutations
and optional zero-sum cross-slice code exchanges. Integer commands produce
real binary switch masks and invariant nominal charge, including carry
boundaries. These are explicit realizable candidate mappings, not a claim to
know the paper's exact undisclosed switch network. The old aggregate rotation
test remains a historical reference; the physical path has its own coverage
and charge-conservation checks.

The unary runner also evaluates DAC charge using its actual per-sample slice
IDs, including mask charge, instead of reusing fixed bank weights after a
shuffle. `tests/integration/test_physical_pipeline.py` verifies locality of a
single-capacitor perturbation, independent direct charge sums, startup and
sample ownership, nominal DEM charge, and random-stream independence.

## Integer output

`codes = result.to_codes()` executes a checked fixed-point reconstruction from
raw codes and digital switch commands. Use `codes.voltage` for final-code
spectral metrics; inspect `clipped_low`, `clipped_high`, `analog_overflow` and
`peak_accumulator_bits`. The floating `result.out` is retained for diagnosis.
The default is Q30 weights, Q32 normalized voltage, 96-bit accumulation and
20-bit offset binary. Register serialization and scope are in ADR 0012.
