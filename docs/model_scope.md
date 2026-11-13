# Current model scope — physical behavioral closure

The current maintenance review is [20261105-independent-maintenance.md](reviews/20261105-independent-maintenance.md).
Earlier implementation milestones remain in [IMPLEMENTATION_CHECKPOINT.md](IMPLEMENTATION_CHECKPOINT.md).
The former v7 scope statement is retained in [history/v7-model_scope.md](EXECUTABLE_ARCHIVES.md)
for version history; its open-item statements are not current capabilities.

> Current structural-DUT event, sample and calibration contracts supersede older checkpoint statements: see [ADR 0021](adr/0021-structural-contract-calibration.md). Other configurations retain their own explicitly scoped assumptions.

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
  raw backend words and checked fixed-point final output. The structural
  profile can additionally fit real RTL observation packets against an
  independent known input and reload weights for a static-plant holdout.
* Correct one-sided PSD, band integration, alias/rank diagnostics, distinct
  published benchmark records and stationary finite-band low-frequency state.
* Optional sample-domain [chopping-assisted AZ](caaz/README.md), adapted from
  a separately reviewed VLSI 2025 ADC: full-input polarity, held AZ error
  before RA clip/ADC2, complete-word reconstruction and nonoverlapping pairing.
  It is disabled by default; the project chooses 40 MS/s raw and 20 MS/s pairs.

## Assumptions and limits

| Domain | Limit that remains explicit |
|---|---|
| Architecture | Nine decisions are independent of known dither. The **17/5** array (main 15×2C + B4 + S4, sub-array 4 columns) and ideal 4x port are the current realization; they are engineering candidates, not a disclosed full netlist. (63+8 was the earlier substitute topology and is rejected.) Historical seven-bit default is retained as a hypothesis. |
| Electrical state | Each top-level record resets electrical state and retains fabrication. General chunk-continuous pipeline simulation is not provided. The separate slow-noise state supports arbitrary continuous-time queries. |
| Reference | Loads are linearized at nominal rail voltages; peak droop reports the small-error domain. Not a full nonlinear reference-buffer circuit solve. |
| Timing | The structural `rtl16` profile matches seven resolved RTL load events at phases 3–9; other profiles use scaled project timing. The trial/reject helper is historical. No transistor-level timing closure or metastability probability prediction. |
| Noise | Aperture noise budgets remain explicit; finite signal bandwidth is not an independent switched noise-transfer calculation. AZ reset/folding factors are reduced assumptions. |
| Calibration | Static controlled training uses a simulated plant and external reference. Reference-source error, drift, on-chip training timing, 748-bit observation transport and hardware power are not inferred. |
| Codes | Real 20-bit integer reconstruction is available; exhaustive digital and local carry tests do not establish full-chip noisy INL/DNL. Mean-error curves are conditional statistics. |
| Low frequency | Stationary 1/f needs an assumed low cutoff; finite log-frequency quadrature is checked for convergence. A 64 s slow-component probe is not a full 2.56 billion-sample conversion simulation. |
| KTC extension | Research observer, not attributed to the ISSCC device. Its unquantized correction is rejected by the integer output API. |
| CAAZ source extension | Sample-domain mechanism and digital-core protocol only. Independent assumed AZ amplitudes; rejects unsupported continuous-chopper/old AZ-budget combinations. Physical CBIAS hold, FGA/CP-FIA, target timing and DSP-wrapper enablement remain open. See the separate source and implementation evidence in the CAAZ note. |
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
