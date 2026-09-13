# ADR 0011: Noisy, identifiable and frozen split-unit calibration

Status: accepted for the static charge-gain model. Final fixed-point code
reconstruction is a separate contract; this calibration initially reconstructs
an analog-equivalent estimate from real raw backend codes.

## Estimand and available data

Estimate one effective weight per physical capacitor:

```
w_main = Cmain/Cf
w_sub  = beta_slice*Csub/Cf
```

Each physical slice has a fixed bridge/subnode ratio, so these effective weights
include unit, slice, bridge and feedback-capacitance effects without asking the
estimator to separately identify physical parameters that have the same effect.
A separate constant ADC2-domain offset is also fitted.

The training equation is:

```
fine_code_voltage[n] = sum_j w[j] * (
    signal_connection[n,j] * known_input[n]
    + known_sampling_or_injection_voltage[n,j]
    - commanded_bottom_voltage[n,j]
) + backend_offset
```

The digital interface explicitly whitelists nominal geometry, actual digital
slice allocations, DEM states, RDAC commands, known injection commands and raw
ADC2 integer codes. `CalibrationSpec` contains no fabricated capacitance, true
gain, physical noise values or simulated error. The reference input is supplied
separately to training. Driver/sampling noise is not smuggled into that reference
by reading the simulator's noisy `sample.x1`.

Raw ADC2 codes are recorded **before** any experimental observer correction.
The KTC extension and fixed-gain topology need different calibration contracts
and are explicitly outside this estimator's domain. Sampling dither must use
discrete physical masks. The old continuous interpolation baseline is not a
unit-switch calibration stimulus.

## Identifiability and fitting

`fit_unit_weights` scatters known active-slice mask terms into physical-unit
columns in bounded blocks. One SVD fits the weights and offset. It reports rank,
condition number, residual RMS and marginal standard errors. A deficient rank
raises `CalibrationUnidentifiableError` with an excitation explanation; there
is no nominal prior that silently supplies the unobservable directions.

The standard errors use a conditional linear-model residual approximation.
They do not cover unknown reference-source accuracy, drift, correlated noise
or unmodeled dynamics. Such effects require an expanded statistical model,
block validation or hardware reference measurements. An algebraically full
rank is necessary; successful independent validation is still required.

For 18 slices with 63+8 capacitors, the fit has 1,278 effective unit weights
plus one offset. Too few samples and clipped training conversions are rejected.
The matrix/SVD memory and computation are explicit training costs, not a model
of on-chip calibration hardware or energy.

## Frozen reconstruction and same-chip validation

`FrozenCalibration` is a frozen dataclass with immutable bytes-backed arrays.
Direct assignment and making the arrays writeable fail. Standard JSON exports
carry a version, units, training digest and diagnostics; loading checks shapes,
positivity and legal values. Independent reconstruction solves:

```
x_hat = (fine_voltage - offset
         + sum(w * (commanded_bottom_voltage - known_injection_voltage)))
        / sum(w * signal_connection)
```

The estimator never sees the validation input. The simulation harness reads
clean/reference input only afterward to score errors. Supplying the frozen fit
through `DigitalState.weight_calibration` applies it inside the production
split runner; `calibration_applied` records `unit_weights` and the original
`uncalibrated_out` remains available for a matched comparison. Neither the
physical ADC2 codes nor the fabricated capacitors change during that comparison.

Runtime masks/DEM controls are passed explicitly to the fixed physical pool.
Thus training with sampling dither and validating with no dither or quantizer
dither uses one chip with the correct electrical connections in each run.

## Workflow and scope

`run_with_split_calibration` provides a documented controlled static transfer
experiment. It uses permutation DEM and discrete sub-bank dither for training,
preserves requested sampling/RA/driver noise, freezes the fit, then runs the
requested validation waveform/dynamics on the same pool with a separate noise
stream. The full actual training configuration is returned in the report.

The static training setup isolates signal dynamics and aperture mismatch; it is
not a claim that physical device imperfections can be switched off in hardware.
Implementing this calibration in a circuit requires controlled static/slow
reference measurements and an uncertainty budget for those imperfections.
Validation retains the requested dynamic mechanisms, whose errors remain visible.

```python
from adi_model import Config, run_with_split_calibration, sine_input

cfg = Config.paper_literal(
    dem_enable=True, dem_mode="permute", mismatch_sigma0=1e-3,
)
result = run_with_split_calibration(
    cfg, sine_input(2.2, cfg.fs * 509 / 16384), 16384, n_cal=8192,
)
coefficients = result.state.weight_calibration.to_dict()
diagnostics = result.calibration_report
```

The old `run_with_calibration` remains an explicitly unary gain/beta workflow.
It now retains configured sampling noise and DEM rather than silently creating
a noise-free calibration record. Its quantizer-dither regressor uses the known
RDAC-port injection, not the distinct quantizer-port amplitude.

## Evidence

Tests cover actual noisy residuals, full rank, independent waveform/noise
validation, frozen coefficient reuse across dither modes, absence of physical
truth in the estimator record, strict JSON/immutability, rejection of missing
excitation, static-domain checks and actual workflow configuration reporting.

A full 18-slice / 8-active pilot with 8,192 training samples identified all
1,279 coefficients (condition approximately 458). An independent 16,384-sample
same-chip record improved from about 95.1 uV to 43.6 uV RMS, with sampling and
RA noise retained. These are conditional behavioral results, not a PDK yield
or measured-silicon claim. Final acceptance includes reproducible training-size
and independent-validation experiments, rather than treating this pilot as a
complete characterization.
