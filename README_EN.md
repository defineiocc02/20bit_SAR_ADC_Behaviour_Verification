# 20-bit / 40 MS/s Pipelined SAR ADC: Behavioral Models and Digital Implementation

[中文](README.md) · [Current status](docs/STATUS.md) · [Papers and presentations](references/README.md) · [Environment and contributing](CONTRIBUTING.md)

This project studies how capacitor mismatch, sampling/interleaving errors and digital reconstruction jointly affect a multi-slice, two-stage SAR ADC. It contains a Python behavioral model with a fixed physical capacitor pool, SAR conversion-control and weight-reconstruction RTL, and a separate output-DSP prototype. The default sample-rate parameter is **40 MS/s**.

Its clearest current result is an off-chip calibration flow: fit effective weights from observations emitted by the structural RTL DUT and independently known inputs, freeze and reload them into the DUT, then reduce output error on independent holdout samples from the same static mismatch plant. **20 bits is the final word width; behavioral simulation, digital RTL, joint validation with a static plant, and silicon performance are different evidence levels.** Target-process STA, transistor-level and post-layout signoff have not been established. A model sample-rate parameter does not demonstrate hardware throughput.

## System architecture and configurations

![Signal chain, randomization locations and training/application boundary](docs/readme/fig/current_architecture.png)

*Figure 1. An independently drawn engineering structure based on current execution paths and interface contracts, not the original chip's complete circuit. The 18 slices form an RDAC sampling/capacitor resource pool: eight convert, eight acquire and two remain available in the same cycle, sharing the RA and ADC2. They are not 18 complete independent ADCs.*

| Entry point | First stage / DAC | Dither and scheduling | Intended use |
|---|---|---|---|
| `Config()` | Retained 7-bit / unary reference | Original baseline settings; each runner has its own restrictions | Budgets and early mechanisms. Do not treat it as the same circuit as the 9-bit RTL |
| `Config.paper_literal()` | 9-bit / 63+8 split candidate | Generic scheduling; an ideal dual-port candidate when quantizer dither is enabled | Physical-pool and mechanism sensitivity studies, not a complete chip netlist |
| `Config.rtl_structural()` | 9-bit / 63+8 split, 12-bit ADC2 | Discrete range model; 16 phases; the DEM enable bit also controls slice shuffle | Sample, mask and event comparisons against the project's structural RTL |

Dither is disabled by default in all three factories; the table describes the contracts when the corresponding mode is enabled. If each of the structural configuration's 16 ideal phases occupies one clock, the clock budget is 640 MHz. The 63+8 allocation, bridge gating and some analog parameters are engineering choices. The [configuration and model scope](docs/model_scope.md) explains where the entry points cannot be interchanged.

## Four work areas: implementation and verification scope

| Work area | Problem addressed | Current implementation | Verification scope | Gaps and next step |
|---|---|---|---|---|
| **Conversion and physical signal chain** | Preserve charge, timing and sample ownership | Python sampling pool and dynamic chain are connected; RTL supplies phases, codes and IDs | Charge/causality checks, structural events and static-plant comparisons; dynamic models have their own approximations | Establish real comparator/macro completion edges, local hold and reference-settling contracts |
| **DEM, dither and slice scheduling** | Reduce input-correlated mismatch effects and observe interleaving errors | Physical masks, a zero-sum bridge candidate, perturbation injection, code adjustment and per-raw reconstruction are connected | Project mask/event agreement; fixed-code usage counts and behavioral experiments with specified mismatch | Bound usage bias and near-full-scale headroom; per-slice learning and the original chip's complete switching circuit are separate work |
| **Training, freezing and fixed-point reconstruction** | Correct static effective weights and related gain | Fit C/Cf using an off-chip known reference; freeze, load Q30/Q32 coefficients and reconstruct integer words | Structural RTL DUT plus fixed static plant; separate training/holdout and integer comparisons | Validate the real reference, observation readout/bandwidth, drift and dynamic errors |
| **Output and system extensions** | Correction, buffering, transport and noise research | DSP/FIFO/SPI prototypes; disabled-by-default sample-domain CAAZ and digital pairing | Directed interface/fault checks; CAAZ experiments under specific noise and combination conditions | Two-point correction direction, filter coefficients, FIFO/serial connectivity and metadata remain open; CAAZ analog macros are unverified |

DEM rearranges physical units, dither injects a known perturbation, and slice shuffle changes the physical group used for a conversion. None automatically measures per-slice timing skew or capacitor weights. The [DEM and dither implementation figures](docs/randomization_caaz/README.md) show actual masks, reference rails and digital processing order separately. In the current structural interface, one bit controls both unit DEM and slice shuffle; they cannot be assumed independently switchable.

Detailed status, conditions and dependencies are maintained on the [current status page](docs/STATUS.md). The referenced CICC continuous-time backend / LSB tracking averaging has not been implemented; existing output SINC or CAAZ is not evidence of its reproduction.

## Three representative results

### Static capacitor mismatch: does training and frozen reconstruction help?

![Calibration errors on independent holdout data from the same static plant](docs/readme/fig/current_calibration.png)

*Figure 2. Structural RTL DUT plus a static mismatch behavioral plant; seed 42, DEM plus sampling D=2, 4096 training samples, 512 independent holdout samples and no added random noise. The main panel uses the same vertical scale before and after calibration; the right panel explicitly enlarges the calibrated residual. This is not full-code INL/DNL or a silicon measurement.*

Only the frozen reconstruction coefficients change; nominal and fitted reconstructions use the same holdout raw observations. The estimator reads digital masks, codes and independently known inputs, not the plant's true capacitors. Input-referred error standard deviations for two chips are **55.75→2.08 µV** and **59.38→2.14 µV**, supporting static effective-weight correction. This does not validate dynamic settling, reference errors, drift or ADC2 nonlinearity. Error mean, RMS and conditions are in the [data summary](docs/readme/data/structural_calibration_summary.json).

[Data and reproduction](docs/REPRODUCING.md#静态权重训练与结构dut留出) · [Contract and observation fields](docs/adr/0021-structural-contract-calibration.md)

### Interleaving skew: can smaller spurs accompany larger total error?

![Final integer-code spectra and metrics for three Python scheduling contracts with identical skew](docs/readme/fig/current_interleave.png)

*Figure 3. 40 MS/s, approximately 4.001465 MHz / 2.85 V peak, N=16384 and a coherent rectangular window; the same fixed realization of nominal 10 ps Gaussian slice skew, with other random noise disabled. This is Python behavioral simulation. The third strategy follows the RTL allocation contract; its spectrum is not captured directly from an SV DUT. Spectra use final 20-bit codes and one-sided dBFS/bin, not a matched-condition reproduction of undisclosed paper settings.*

For fixed A/B, generic random scheduling and the Python strategy following the RTL allocation contract, **SNDR is 95.98 / 84.22 / 84.93 dB and SFDR is 96.00 / 108.75 / 110.93 dBc**. In this example, randomization spreads spurs while increasing residual power outside DC and the fundamental. Fitting is used only for metrics; no gain or phase correction is applied to the output. Total error RMS against the ideal input is **127.97 / 182.15 / 176.44 µV**. That RMS retains fundamental gain/phase error and differs from the fitted-fundamental residual used for SNDR. SFDR alone cannot establish an overall benefit.

See [conditions, data and reproduction](docs/REPRODUCING.md#交织调度的当前严格基线). Fixed rotation, generic random selection and RTL allocation follow different rules; one “random” label cannot stand for all three.

### Dither: why can the backend overflow while the RDAC code remains legal?

![Node headroom and fault counts for near-full-scale dither](docs/readme/fig/current_dither_headroom.png)

*Figure 4. Structural range configuration with ideal devices; 40 MS/s, N=2048, fin=251Fs/2048 and coherent inputs. All specified amplitudes are swept, with failed points retained.*

At 2.99 V input peak, quantizer dither keeps the RDAC command within 0…511 and produces no final digital clipping, yet the SADC input exceeds its nominal range 18 times, the RA saturates four times and ADC2 overflows 15 times. Off and sampling modes have none of these analog excursions under the same conditions. This range implementation therefore needs a separate near-full-scale headroom check; “no final code clipping” is insufficient. The structural dither sequence is determined by D=2. Its effective-configuration report now explicitly marks the requested floating amplitude as inactive.

See [data, boundary definitions and reproduction](docs/REPRODUCING.md#dither的近满幅余量). Unit-usage bias and conditional bridge statistics are in the [mechanism note](docs/randomization_caaz/README.md).

## The most important next steps

1. **Analog timing and observation transport.** Real hold/reset behavior, reference timing and ADC2 completion edges remain open, limiting the move from functional fixtures to circuits. Establish macro and observation-transport contracts first. Completion requires physical evidence of sample hold, non-overlap and settling by the closing edge, plus actual readable observations and an independent reference completing training, freezing and holdout without combining samples across epochs.
2. **Confirmed gaps in the output prototypes.** Complete inverse two-point correction, usable filter coefficients, FIFO-to-serial routing and per-result metadata separately. Acceptance must check continuous output codes, IDs and fault consistency, not merely module instantiation.
3. **Separate range studies from extensions.** Keep strict diagnostics for DEM usage bias, dither headroom and scheduling tradeoffs. Per-slice estimation, the original chip's complete bridge/dual-port circuit, CAAZ analog macros and the CICC backend are independent research items. Each needs its own observations, targets and closure criteria; they should not all be classified as project defects.

Combining CAAZ with randomization also requires actual CBIAS retention across two cycles, correct dither injection locations and matched normalized noise transfer for both samples. Adjacent slice groups cannot simply be locked to the same group. See the [combination conditions](docs/randomization_caaz/README.md).

## Where to reproduce results

Install Python dependencies using the [environment instructions](CONTRIBUTING.md). Structural-DUT experiments additionally require Verilator 5.x and a C++ compiler. All new simulation/training commands use a **new output directory that does not yet exist**.

**A. Quick functional check:** checks structural reference events, sample ownership and rejection of illegal geometry. It needs no Verilator and does not establish performance.

```bash
PYTHONPATH=src pytest -q tests/unit/test_structural_contract.py
```

**B. Retrain and simulate:** structural profile, fixed mismatch plant and independent training/holdout; outputs raw observations, frozen coefficients and summary.json. Success also requires holdout improvement, independent integer agreement and zero faults in the summary.

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_structural_calibration.py \
  --output "${TMPDIR:-/tmp}/sar-calibration-new" --train 4096 --holdout 512 --chip-seeds 42 91
```

**C. Redraw retained data only:** reads the repository's frozen data without simulation or retraining and produces the original PNG/SVG figures above. A successful redraw establishes only agreement between the data and their presentation.

```bash
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_overview_figures.py \
  --out "${TMPDIR:-/tmp}/sar-overview-figures"
```

See the [complete reproduction guide](docs/REPRODUCING.md) for interleaving/dither simulation, success criteria, node/spectrum definitions and historical-data handling.

## Find code and documents by question

| What to understand | Entry point |
|---|---|
| Full physical behavioral chain and configurations | [pipeline_engine.py](src/adi_model/pipeline_engine.py) · [config.py](src/adi_model/config.py) · [Scope](docs/model_scope.md) |
| DEM, bridge and actual physical groups | [dem.py](src/adi_model/dem.py) · [slice_pool.py](src/adi_model/slice_pool.py) · [Scheduling](src/adi_model/timing.py) · [Interleaving reproduction](docs/readme/reproduce_interleave_40m.py) · [Metrics](src/adi_model/metrics.py) · [Implementation figures](docs/randomization_caaz/README.md) |
| Where dither is injected and removed | [sampler.py](src/adi_model/sampler.py) · [sampling_charge.py](src/adi_model/sampling_charge.py) · [Digital observations and charge terms](src/adi_model/weight_calibration.py) |
| How weights are learned, frozen and loaded | [weight_calibration.py](src/adi_model/weight_calibration.py) · [fixed_point.py](src/adi_model/fixed_point.py) · [ADR0021](docs/adr/0021-structural-contract-calibration.md) |
| RTL interfaces, samples and faults | [Digital core](rtl/top/sar20_digital_core.sv) · [RTL arithmetic contract](docs/rtl/RTL_ARITHMETIC_CONTRACT.md) · [DSP interface](ad4080_dsp/docs/SAR20_INTERFACE.md) |
| Current limits and this review | [Current status](docs/STATUS.md) · [Model scope](docs/model_scope.md) · [Review record](docs/reviews/20261105-readability-maintenance.md) |
| Originals and historical evidence | [Three paper/presentation pairs](references/README.md) · [Source manifest](sources_manifest.json) · [Historical documents](docs/history/) |

Code is distributed under [BSD-3-Clause](LICENSE). Selected originals retain their original copyright and embedded notices; see [NOTICE](NOTICE). This project is independent of the original device authors/manufacturers and grants no patent license. Version history and detailed validation records remain in linked documents rather than the main README.
