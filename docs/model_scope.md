# Current model scope — physical behavioral closure

The active implementation and evidence ledger is [IMPLEMENTATION_CHECKPOINT.md](IMPLEMENTATION_CHECKPOINT.md).
The former v7 scope statement is retained in [history/v7-model_scope.md](history/v7-model_scope.md)
for version history; its open-item statements are not current capabilities.

## Implemented physical path

* Fixed per-slice capacitances, bridge/subnode parasitics, actual DEM masks and
  sample ownership; shared implementation for both split entry points.
* Continuous shared-source tracking, optional filter state, per-branch nonlinear
  Ron and physical source charge; no division of the shared Rs by slice count.
* Available quantized decisions for local pretracking; actual auxiliary input
  acquisition/reset charge at its appropriate source.
* Signed nominal-rail capacitor charge events and persistent coarse/fine
  reference recovery driving finite RA gain, bandwidth, slew/swing and ADC2
  tracking phases; final released-slice reference backaction.
* Noisy identifiable effective-unit calibration, frozen same-chip holdout,
  raw backend words and checked fixed-point final output.
* Correct one-sided PSD, band integration, alias/rank diagnostics, distinct
  published benchmark records and stationary finite-band low-frequency state.

## Assumptions and limits

| Domain | Limit that remains explicit |
|---|---|
| Architecture | Nine decisions are independent of known dither. The 63+8 complete-range realization and ideal 4x port are candidates, not a disclosed full netlist. Historical seven-bit default is retained as a hypothesis. |
| Electrical state | Each top-level record resets electrical state and retains fabrication. General chunk-continuous pipeline simulation is not provided. The separate slow-noise state supports arbitrary continuous-time queries. |
| Reference | Loads are linearized at nominal rail voltages; peak droop reports the small-error domain. Not a full nonlinear reference-buffer circuit solve. |
| Timing | Quantizer/RA/aperture phases and binary trial-loading sequence are assumed; no transistor-level timing closure or metastability probability prediction. |
| Noise | Aperture noise budgets remain explicit; finite signal bandwidth is not an independent switched noise-transfer calculation. AZ reset/folding factors are reduced assumptions. |
| Calibration | Static controlled training is a behavioral characterization protocol. Reference-source error, drift, on-chip training timing and hardware power are not inferred. |
| Codes | Real 20-bit integer reconstruction is available; exhaustive digital and local carry tests do not establish full-chip noisy INL/DNL. Mean-error curves are conditional statistics. |
| Low frequency | Stationary 1/f needs an assumed low cutoff; finite log-frequency quadrature is checked for convergence. A 64 s slow-component probe is not a full 2.56 billion-sample conversion simulation. |
| KTC extension | Research observer, not attributed to the ISSCC device. Its unquantized correction is rejected by the integer output API. |
| Devices | No PDK/layout/transistor measurements; mismatch, area, power and yield remain sensitivity assumptions. |

## Evidence and units

Use final words decoded to input volts for code-domain dynamic metrics. Inspect
analog overflow and final clipping separately. Use V²/Hz for noise density and
sum(PSD*df) for band power. The paper's 2.2 ppmFS corresponds to approximately
2.307 LSB20; an experiment choosing 2.2 LSB uses an engineering threshold.

[behavioral-closure.md](behavioral-closure.md) and the ADR directory provide
formulas, numeric settings, API examples and independent validation methods.
Historical review documents preserve the original findings; they must not be
read as a current release performance declaration.
