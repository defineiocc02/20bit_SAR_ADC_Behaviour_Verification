# 20-bit / 40 MS/s Pipelined SAR ADC: Models and Digital RTL

[中文](README.md) · [Repository layout](docs/PROJECT_LAYOUT.md) · [Model scope](docs/model_scope.md) · [DSP interface](ad4080_dsp/docs/SAR20_INTERFACE.md)

This research repository studies slice scheduling, capacitor mismatch, digital calibration and post-processing for a two-stage residue SAR ADC, guided by [ISSCC 2024 Session 9.8](https://doi.org/10.1109/ISSCC49657.2024.10454329) and related patents. It contains a Python behavioral model, SAR digital RTL and a separate AD4080-inspired DSP subsystem. The default sampling rate is **40 MS/s**.

> **Evidence boundary:** This is a research prototype. A 20-bit output word does not imply 20-bit ENOB. The plotted performance figures are conditional behavioral simulations, not silicon measurements, PDK signoff or timing closure.

## Results at a glance

| Question | Project approach | What was observed under the stated conditions |
|---|---|---|
| **Capacitor mismatch** | Fit effective weights externally and load them into the digital RTL | For two static mismatch fixtures, independent holdout error standard deviation is **55.75 → 2.08 µV** and **59.38 → 2.14 µV**; see [Figure 4](#capacitor-mismatch-calibration-and-effect) |
| **Interleaving timing skew** | Randomize slice selection to spread the spur | Under the stated 10 ps assumption, the `fs/2−fin` spur changes **−96.44 → −123.05 dBFS/bin**, while total error RMS increases; see [Figure 6](#interleaving-mismatch-spurs-and-total-error) |
| **Model/RTL agreement** | Align switch masks, reference events and sample ownership | Under the project contract, **262144** code/DEM-state masks and **10752** reference events across three dither modes match; see [ADR 0021](docs/adr/0021-structural-contract-calibration.md) |

These results use static behavioral fixtures or stated behavioral-simulation conditions. Random scheduling is not a per-slice timing calibration, and these are not silicon measurements.

## Architecture and configurations

![Two-stage SAR architecture and project configurations](docs/readme/fig/unified_1_architecture.png)

*Figure 1. An original illustration based on public descriptions and repository code, not a reproduction of the paper figure.*

The paper discloses a 20 b / 40 MS/s converter, an 18-slice structure and a shared residue amplifier. The model connects acquisition, coarse quantization, residue charge, RA, ADC2 and digital reconstruction. Structural RTL implements phase control, comparator interfaces and reconstruction; a project interface connects the DSP to the reconstructed result.

| Profile | Sampling rate | Coarse quantization and DAC | Boundary |
|---|---:|---|---|
| `Config()` | 40 MS/s | 7-bit, unary | Retained behavioral default; Python SADC uses threshold search rather than bit-by-bit SAR timing |
| `Config.paper_literal()` | 40 MS/s | 9-bit, 63+8 split | Candidate matching the paper's coarse-bit reading; physical 63+8 allocation is a project assumption |
| `Config.rtl_structural()` | 40 MS/s | 9-bit, 63+8 split | Matches structural priming, slice ordering, dither and 16-phase reference events under the project contract |
| Structural `sar20_digital_core` | 40 MS/s target | 9-bit, 63+8 split | 16 digital phases/sample imply a 640 MHz clock budget if one clock is used per phase; target-library STA is unavailable |

The Python profiles are not identical circuit models. A nominal sample-rate setting is not evidence of attainable RTL timing. See [`config.py`](src/adi_model/config.py), [`sadc.py`](src/adi_model/sadc.py) and the [RTL arithmetic contract](docs/rtl/RTL_ARITHMETIC_CONTRACT.md).

## Digital work: purpose, current results and open problems

The digital work has four packages. “Available” means a research prototype verified under stated conditions.

**1. Conversion control and sample ownership.** Coordinate sampling, coarse decisions, residue formation and ADC2. The structural RTL has 16-phase control, comparator interfaces, slice scheduling, sample IDs and fault flags; Python and RTL agree on slice order and seven resolved RDAC events. **Still needed:** real comparator completion, analog switch/reference phase contracts and system-wide fault/reset handling.

**2. DEM, dither and interleaving mismatch.** Rearrange slices and capacitor units to spread error and inject known perturbations. The project has fixed physical pools, interleaving experiments, RTL slice allocation, DEM addressing, a bridge candidate and a dither generator; physical masks and three dither modes have been compared. **Still needed:** identifiable per-slice offset, gain, timing and bandwidth estimation/correction. The complete slide bridge circuit and dual-port dither enhancement remain unreproduced. DEM redistributes error; it does not measure mismatch.

**3. Capacitor calibration and digital reconstruction.** Estimate effective weights from a known reference and digital observations, then reconstruct using frozen coefficients and known dither. Structural RTL observation, external fitting, quantized loading and independent holdouts are connected; see [ADR 0021](docs/adr/0021-structural-contract-calibration.md). **Still needed:** real reference and observation transport, dynamic settling/drift and ADC2 nonlinearity verification; the 748-bit observation port has no FIFO or backpressure.

**4. System correction, DSP and data output.** Correct overall signal-chain gain/offset and provide filtering, buffering and external interfaces. SAR and DSP tops are connected; correction, SINC, FIFO, SPI and fault-stop blocks are prototypes. **Still needed:** correct the two-point coefficient direction, complete filter coefficients, FIFO-to-serial routing, output metadata and a lossless DDR/differential interface.

**Shared priorities.** First establish real reference, observation transport and analog-phase interfaces; then study per-slice correction and dynamic error; finally complete DSP and output. Sample IDs, faults, reset and verification span all four packages. Formal equivalence, CDC/RDC, target-library STA and analog post-layout verification remain open. The 640 MHz clock implied by 16 phases at 40 MS/s is an ideal budget.

ADC-core weight correction and system gain/offset correction in the [AD4080 data sheet](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) act at different levels. The [historical task assessment](docs/digital_task_assessment.md) listed integration verification separately; here it is shared work.

## Mapping public sources to this project

The table maps published mechanisms to the implemented paths and their limits.

| Mechanism | Implemented here | Boundary |
|---|---|---|
| Separate quantizer and residue DAC | Python quantization/charge models and structural RTL signal path | Capacitor allocation and switch timing include assumptions; no complete paper netlist |
| Capacitor mismatch and DEM | Fixed physical units, weight fitting, element permutations and RTL reconstruction | DEM redistributes error rather than measuring it; Python and RTL masks now agree under the project candidate; the original chip bridge circuit remains undisclosed |
| Interleaving mismatch | Slice offset, timing skew, bandwidth spread and scheduling sensitivity models | No complete hardware estimation/correction loop for slice offset, skew and bandwidth |
| Dither | Behavioral injection/digital correction and RTL generator | Structural off, sampling and quantizer modes are compared; dual-port range enhancement and large-input overflow remain open |
| RA, reference and ADC2 | RA/reference dynamics and ideal ADC2 quantization | Seven resolved reference events agree in the structural profile; ADC2 nonlinearity learning, analog implementation and PDK verification remain open |

See [`mechanism_inventory.json`](mechanism_inventory.json), [`sources_manifest.json`](sources_manifest.json), the [source comparison](docs/engineering_vs_patents_papers.md) and the [known limitations](docs/rtl/KNOWN_LIMITS_20261104.md). Selected public references cover [quantizer/RDAC separation](https://patents.google.com/patent/US10516408B2/en), [dither](https://patents.google.com/patent/US10505561B2/en), [DEM](https://patents.google.com/patent/US10511316B2/en) and [interleaved tracking](https://patents.google.com/patent/US10707889B1/en). The candidate implementation is not a complete reconstruction of a disclosed product. The [paper](https://doi.org/10.1109/ISSCC49657.2024.10454329) prints reference [12] as 10,797,889, while its title and authors match [US 10,707,889 B1](https://patents.google.com/patent/US10707889B1/en); this project uses the latter based on that comparison, not an author-issued erratum.

## Capacitor mismatch: calibration and effect

![External fitting to RTL reconstruction](docs/readme/fig/unified_3_calibration.png)

*Figure 2. Structural digital RTL observation, external fitting and independent holdout. This original illustration uses retained results from a static behavioral mismatch fixture, not silicon.*

Digital observations and an independent reference input feed a Python batch estimator. Frozen effective unit weights are quantized into Q30 weights and Q32 scalars, loaded into RTL `weight_store` and `calib_regs`, and applied by `recon_core`. [ADR 0019](docs/adr/0019-fitted-weight-rtl-closure.md) used Python training observations; the current flow also captures real structural-DUT observations, as described in [ADR 0021](docs/adr/0021-structural-contract-calibration.md).

![1.05 MHz before and after capacitor calibration](docs/paper_comparisons/figures/02_ac_calibration.png)

*Figure 3. A behavioral comparison at the input frequency reported on slide 38: 40 MS/s, 1.05 MHz, 2.85 V peak, N=16000, seed 42. Training and holdout records are separate, share the simulated physical pool, and use an exact external training reference. The slides do not disclose amplitude, FFT settings, or complete noise conditions.*

With noise enabled, output SNDR improves **88.88 to 92.46 dB** and input-referred error standard deviation improves **72.53 to 48.04 µV**. The 94.6 dB noise budget is fitted, so this is not an independent prediction of paper SNR. The [experiment comparison](docs/paper_comparisons/README.md) includes static and noiseless results.

![Before and after errors on real structural RTL holdouts](docs/readme/fig/structural_calibration_holdout.png)

*Figure 4. Real structural digital DUT results for 512 holdout samples: seed 42, an ideal 640 MHz phase clock corresponding to 40 MS/s, a static fixed-mismatch plant, 4096 training samples and no added noise. The two panels use different vertical scales. The plot is independently drawn from the retained DUT inputs and output data.*

The off-chip estimator receives DUT physical-switch observations and independently known inputs, not the plant's capacitor truth. Error standard deviations for two mismatch seeds are **55.75 → 2.08 µV** and **59.38 → 2.14 µV**. A separate test with an assumed 48 µV RMS input-referred noise yields **71.25 → 54.40 µV**. Frozen weights are reloaded into the DUT and its outputs compared word by word with an independent fixed-point reconstruction. This is a digital RTL plus static behavioral-plant loop, not on-chip learning or a silicon measurement.

### What is the binary-to-unary bridge?

![Illustrative boundary error and project bridge candidate](docs/readme/fig/bridge_state_example.png)

*Figure 5. Top: an illustrative mismatch changes the 7→8 charge step from 1.00 to 1.20 units. Bottom: nominal zero-sum exchange generated by the project Python switching decoder. This original illustration does not reconstruct the complete paper circuit.*

The slides place unary units labeled 8 beside binary/dither units labeled 4, 2 and 1. Error in their actual capacitance ratio affects the charge step at segment boundaries. This project assumes 63 main units and eight subunits: `176=22×8+0`. For `sid[8]=0`, all eight active slices use 176; for `sid[8]=1`, four use 177 and four use 175, retaining a total nominal code of 1408. The 175 code is `21×8+7`, across the segment boundary. This is a bridge-related engineering proxy that changes physical-unit usage and mismatch error distribution; it **does not reproduce the slide's complete exchange circuit or automatically remove mismatch**. The actual structural RTL masks match an independent project reference for all 512 codes × 512 DEM states. This 50% state gating is an engineering assumption, not a verified implementation of the slide's `P=50%` truth table.

The similarly named `C_bridge` is a **physical capacitor** between the main and sub arrays and changes the effective subarray weight. See the [bridge explanation](docs/binary_unary_bridge.md) for examples, dither interaction and undisclosed circuit details.

## Interleaving mismatch: spurs and total error

![10 ps timing skew and scheduler output spectra](docs/paper_comparisons/figures/01_timing_skew.png)

*Figure 6. Inspired by slides 21–22: 40 MS/s and a nominal 10 ps skew. The project assumes independent static Gaussian slice skew, an approximately 4.0015 MHz / 2.85 V peak input, N=16384, and no random noise. The slides do not disclose the exact input, 10 ps statistical definition, or FFT setup.*

The `fs/2 − fin` bin falls from **−96.44 to −123.05 dBFS/bin** when fixed rotation is replaced by random 8-of-18 scheduling. Reference error RMS rises from **127.97 to 182.15 µV** in this example. The scheduler redistributes error; it does not estimate or correct timing skew. Other seeds, a second 10 ps interpretation and the remaining slide comparisons are in the [experiment comparison](docs/paper_comparisons/README.md).

Slice offset, gain, and bandwidth differences also cause interleaving errors; the [offset/gain comparison](docs/readme/fig/unified_7_interleave_effect.png) shows that spur and total RMS changes can differ. Dedicated hardware learners and compensation for per-slice timing and bandwidth are not closed.

## DSP connection and fault handling

![SAR-to-DSP code formats and faults](docs/readme/fig/unified_4_dsp.png)

*Figure 7. An original illustration of the current RTL integration boundary.*

[`sar20_system_top.sv`](ad4080_dsp/rtl/sar20_system_top.sv) instantiates the SAR digital core and DSP. Alternatively, connect [`sar20_ad4080_top.sv`](ad4080_dsp/rtl/sar20_ad4080_top.sv) separately to the SAR result, per-sample flags and independent sticky `analog_ovf`. The SAR now has a per-result `dout_analog_bad` aligned with the sample ID, while the DSP still stops on the sticky analog flag. It converts offset binary to the DSP's two's-complement input and converts the parallel output back; serial payloads remain two's complement. Any result flag or sticky analog overflow halts the stream and latches a fault. `dsp_restart` or `sar_epoch_clear` resets the DSP, **including FIFO, filter state and register configuration**; the SAR epoch clear is needed to clear its sticky analog fault before reconfiguration.

Acceptance/rejection events and IDs exist at the input boundary; filtered and FIFO outputs **do not yet carry per-output IDs or source flags**. Both sides require the same clock and synchronous reset release; no new CDC is provided. The single-lane serial prototype cannot losslessly send 20-bit frames when inputs arrive every 16 clocks, and FIFO read data is not connected to the serializer. DDR differential PHY is absent and the default SINC5 compensation coefficients are placeholders. See the [DSP README](ad4080_dsp/README.md) for the remaining boundaries.

The separate `cal_two_point` block currently fits `y=m·x+c` at fixed x coordinates. If x is a known input and y is the measured raw code, its result describes the **forward transfer**, not the inverse correction to apply to raw codes; it is not connected to an automatic top-level learning flow.

## Provenance and verification

![Parameter provenance and evidence limits](docs/readme/fig/unified_5_evidence.png)

*Figure 8. Original illustration generated from the runtime parameter provenance ledger. Fitted and assumed results are not disclosed facts or chip measurements.*

Behavioral models, RTL simulation, historical FPGA evidence and target-PDK synthesis/STA are separate evidence levels. Figures 3 and 6 are 40 MS/s behavioral simulations on baseline `6863fc3`; Figure 4 combines the current structural digital DUT with a static behavioral plant. None is transistor or silicon data. Historical detailed documentation is retained in [`docs/history/`](docs/history/); historical tests are not automatically new results for the present checkout.

### Code and data entry points

| Looking for | Entry point |
|---|---|
| Behavioral model, configuration and switching | [`src/adi_model/`](src/adi_model/) |
| Structural RTL and observation/reconstruction interface | [`sar20_digital_core.sv`](rtl/top/sar20_digital_core.sv) · [RTL contract](docs/adr/0021-structural-contract-calibration.md) |
| Calibration training, loading and holdouts | [`run_structural_calibration.py`](tools/run_structural_calibration.py) · [result data](docs/readme/data/) |
| Interleaving, reference and paper comparisons | [Experiment comparisons](docs/paper_comparisons/README.md) |
| DSP and external interfaces | [DSP README](ad4080_dsp/README.md) · [SAR interface](ad4080_dsp/docs/SAR20_INTERFACE.md) |

### Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -c 'from adi_model import Config; print(Config().fs, Config().b1, Config().dac_arch)'
pytest -m 'not slow' -q
```

RTL regression requires Verilator 5.x and a C++ compiler; the independent DSP script requires Icarus Verilog:

```bash
python tools/run_open_rtl.py --tops calibration_fit_tb sar20_dsp_integration_tb sar20_system_top_tb
BUILD_DIR="${TMPDIR:-/tmp}/ad4080-dsp-build" bash ad4080_dsp/sim/run_all.sh
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_unified_figures.py
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_contract_figures.py \
  --out "${TMPDIR:-/tmp}/sar-adc-contract-figures"
PYTHONPATH=src python docs/readme/reproduce_interleave_40m.py \
  --out "${TMPDIR:-/tmp}/sar-adc-interleave-new" --compare-frozen
```

The plotting scripts redraw retained small data sets; they do not retrain the model. Structural observation and calibration reproduction commands are in [ADR 0021](docs/adr/0021-structural-contract-calibration.md). The interleaving reproduction script requires a new, non-existent `--out` directory. Store full new simulation outputs outside the repository, for example `adi-run-all --results-dir "${TMPDIR:-/tmp}/sar-adc-results"`. See the [file conventions](docs/PROJECT_LAYOUT.md) for directory responsibilities and historical evidence exceptions.

## License and citation

Code is distributed under [BSD-3-Clause](LICENSE); see [NOTICE](NOTICE) and [CITATION.cff](CITATION.cff) for third-party and patent statements. Illustrations and result plots are independently drawn and do not copy paper, slide or patent figures; original source documents are not redistributed. This independent project has no affiliation with or endorsement from Analog Devices and grants no patent license.
