# 20-bit SAR ADC behavioral verification

[中文与完整使用指南](README.md) · [Model scope](docs/model_scope.md) · [Implementation evidence](docs/IMPLEMENTATION_CHECKPOINT.md)

Python behavioral verification inspired by ISSCC 2024 Session 9.8. The current
split path couples actual slice charge, causal acquisition, a shared input
network, signed reference events, finite RA/ADC2 response, noisy identifiable
calibration and checked integer reconstruction.

`Config.paper_literal()` selects nine unknown-input decision bits and a separate
fourfold known-dither port candidate. `Config()` retains the historical seven-bit
hypothesis for baseline comparisons. Known dither does not create extra decision
information. The 63+8 topology, detailed DEM exchange, conversion timing and
backend ranges remain explicitly assumed implementations.

8.1.0 adds the digital-side fixed-point RTL (P0-P3): a synthesizable calibration core, dual SAR with a shared 3-bit Flash, 18-slice scheduling, Verilator simulation and mutation-testing gates. Behavioral `results.json` stays byte-identical to v8.0.0.

8.2.0 fixes two statistical-integrity defects and hardens the entry guards. (i) The MC loops shared one integer seed between the mismatch draw and the noise realisation, so the two streams drew the same values (probe-verified: the stream coincided, the per-chip spread did not collapse); mismatch and noise now use independent `SeedSequence.spawn` streams. (ii) The unit-crosstalk activity is now A(k)/2 consistently with `_dem_fluctuation` (fractional units, prefix-sum interpolation). The reference output was regenerated and reconciled leaf by leaf: **878 of 926 leaves are byte-identical, and all 48 changed leaves sit in RNG-dependent sections** (`mc`, `mc_cal_*`, `mc_pdk_*`, `budget`, `s13`) - which makes "only F1/F2/F9 are active fixes" a measured result rather than a claim.

> **The numerical deltas cannot be attributed to the fix.** A bootstrap test on the per-chip SNDR (20,000 resamples, chart 7) puts **all 10 statistics - delta-worst-chip and delta-sigma across 5 sections - inside the 95% null band** (|z| <= 1.44). At n=16 / n=60 the MC and yield metrics are sampling-noise dominated, so the v8.1.0 and v8.2.0 MC conclusions **do not contradict each other**. The case for the fix rests on the **code-level defect** (one integer seed seeding both streams, reproducible by probe), not on these outputs. **Do not** read the MC extremes of this model as yield claims.

8.2.1 closes an entire **class** of defects exposed by an independent adversarial re-review of v8.2.0: domain-validation predicates checked only the sign or the range and forgot finiteness, so `nan` / `+/-inf` slipped through and **propagated silently** (`nan <= 0` and `nan < 0` are both `False`). 32 predicate lines across 13 source/tool files were hardened onto the repository's existing correct idiom `not math.isfinite(x) or x <= 0` (`config.py:1036`, `chip.py:186`). Two adjacent defects were fixed as well: `AuxInputStage` could be built directly, **bypassing** `build_stage` (a `__post_init__` now forces validation), and `rtl_localparams` carried a **provably unreachable** dead guard whose intended input actually escaped as a bare `OverflowError` (now a contract-consistent `ValueError`).

8.2.2 is a forward-only patch that **fixes v8.2.1's red CI** (the published tag is already released; per the append-only rule it is not rewritten). The root cause is not a version but the **BLAS backend**: in `fit_unit_weights` the arithmetic between `svd()` and the weight guard is unguarded, so a non-finite factor makes a zero entry of `design` multiply a non-finite entry of `theta` - i.e. `0 * inf`, an invalid floating-point operation; x86-64 OpenBLAS therefore raises `RuntimeWarning` (a hard failure under this repo's `filterwarnings`), Apple Accelerate does not, hence green locally and red on CI. Two gates were added: an **SVD-factor gate** (before any arithmetic and before the rank comparison) and an **entry gate on the spec scales** (`v_fs` / `adc2_v_min` / `adc2_v_max` / `dither_units_range`), which closes both the `design` and the `fine_v` ingress.

> **This release also changes no reference output.** The three fingerprints (v8.2.0 / v8.2.1 / v8.2.2) are **character-identical** and all 926 leaves are byte-identical. The evidence is one probe run against 7 ingress scenarios on both trees (before/after) plus three mutation tests that each disable one gate and show only the corresponding assertion failing - see [docs/release_v8.2.2](docs/release_v8.2.2/).

> **This release changes no reference output.** `results.json` is **byte-identical** to v8.2.0 (all 926 leaves identical) - every change is a guard that only fires on invalid input. This is cross-checked by **two independent adversarial verification rounds**; the second also ran mutation tests: reverting 15 source guards made the corresponding test fail in 14 cases (the single toothless one was fixed in this release).

> **What this release does NOT claim.** `charge_ref.py` has no domain guards at all - that is a "missing guard" problem of a different class and was deliberately **not** addressed here (it needs its own review); the `ktc.beta_n_of` / `beta_x_of` guards guarantee a positive finite *input* but not a finite *output* (`g_r` as small as 1e-308 still overflows to inf - a numerical reality, not a guard hole); the v8.2.2 gates likewise guarantee a finite *input* but not finite *intermediate* quantities (with `v_fs = 1e308` and an injection of `1e308`, `design` already trips `overflow encountered in subtract` during assembly - no gate can catch that, and adding a magnitude ceiling on positive values would wrongly reject legal inputs). See the [CHANGELOG](CHANGELOG.md).

Byte accounting and the significance test are in the [CHANGELOG](CHANGELOG.md); the 7 comparison charts and `significance.json` are in [docs/release_v8.2.0](docs/release_v8.2.0/); the v8.2.1 hardening census and byte account are in [docs/release_v8.2.1](docs/release_v8.2.1/); the v8.2.2 ingress-closure comparison (one probe run on both trees) and byte account are in [docs/release_v8.2.2](docs/release_v8.2.2/).

![v8.1.0 to v8.2.0 headline metric comparison](docs/release_v8.2.0/fig/headline_compare.png)

![Bootstrap test: all 10 deltas inside the 95% resampling null band](docs/release_v8.2.0/fig/significance_null.png)

![v8.2.2 non-finite ingress closure: one probe run on v8.2.1 and on the fixed tree](docs/release_v8.2.2/fig/ingress_closure_v822.png)

![v8.2.2 reference-output byte account: three fingerprints character-identical](docs/release_v8.2.2/fig/byte_account_v822.png)

![v8.2.1 domain-guard hardening census](docs/release_v8.2.1/fig/guard_hardening_map.png)

![v8.2.1 reference-output byte account: the hardening is behaviour-preserving](docs/release_v8.2.1/fig/byte_account_v821.png)


## Results at a glance (v8.2.2)

v8.0.0 integrates the physical slice pool, interleave pretracking, auxiliary
input, coupled reference/RA/ADC2 dynamics and the fixed-point digital core into
the main path. A leaf-by-leaf diff of `results.json` against the v7.0.10
aggregate baseline shows **516 of 626 shared metrics exactly identical**,
40 at float-noise level (<1e-6 relative) and **70 genuinely changed** — all in
subsystems newly covered by the physical main path (see the byte accounting in
the [CHANGELOG](CHANGELOG.md)):

![v7→v8 headline metric comparison](tools/results/fig/v7_v8_compare.png)

Headline numbers (`paper_literal` main configuration, all traceable in
`tools/results/results.json`): ENOB 20.58 bit, SNDR/SFDR 125.6/155.5 dB (ideal
no-mismatch path), DEM-on/off SNDR 93.4/93.5 dB, worst-case MC SNDR/SFDR 82.0/85.1 dB,
post-calibration noise floor ratio 1.006.

Verification figures (generated by `tools/run_all.py` and
`tools/make_readme_compare.py`, refreshed together with `results.json`):

| | |
|:---|:---|
| ![Static errors](tools/results/fig/static_curves.png) | ![DEM spectrum](tools/results/fig/dem_spectrum.png) |
| *Segmented-DAC static errors, topology comparison, INL contributions* | *DEM on/off spectrum: unary permutation pushes mismatch spurs to the floor* |
| ![Physical calibration](tools/results/fig/physical_calibration.png) | ![KTC trade-off](tools/results/fig/ktc.png) |
| *Physical-pool calibration: noisy training, frozen weights, held-out validation* | *KTC observed-noise reduction: scaled-capacitor trade-off* |
| ![Mismatch stress](tools/results/fig/mismatch_stress.png) | ![Error budget](tools/results/fig/budget.png) |
| *Mismatch stress sweep* | *Noise/mismatch error budget* |
| ![Sweep](tools/results/fig/sweep.png) | ![Signal chain](tools/results/fig/v5_structure.png) |
| *Parameter sweeps and feasibility region* | *Segmented sub-DAC signal chain* |

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy
pytest -q --cov
adi-run-all --results-dir ./output/verification
adi-make-report --results-dir ./output/verification
```

```python
from adi_model import Config, sine_input
from adi_model.pipeline import run_pipeline
from adi_model.metrics import sine_fit_metrics

cfg = Config.paper_literal(dither_mode="off")
n = 16384
fin = cfg.fs * 307 / n
result = run_pipeline(cfg, sine_input(2.1, fin), n)
words = result.to_codes()
metrics = sine_fit_metrics(words.voltage, cfg.fs, fin)
```

`result.out` remains a floating diagnostic. `to_codes()` uses raw backend codes,
digital switching and nominal/frozen weights with Q30/Q32 coefficients, checked
96-bit accumulation and 20-bit offset-binary words. Analog overflow and final
range clipping remain separate. The unquantized research KTC observer is outside
this integer interface.

`run_with_split_calibration` retains requested noise during controlled static
training, rejects deficient rank, freezes coefficients and validates with an
independent waveform/noise sequence on the same physical chip. The full sweep
checks two chips and two training sizes using final integer words.

Paper (94.2 dB / 9.3 nV/√Hz) and slides (94.6 dB / 8.8 nV/√Hz) remain separate
benchmark records. RA noise inferred from either target is a fitted budget, not
a prediction. PSD uses V²/Hz with explicit ENBW and endpoint normalization.
A stationary slow process can be queried over 64 seconds at actual 40 MHz ADC
timestamps; sparse low-frequency observation does not validate the entire fast
chain or a transistor-level auto-zero noise transfer.

Results are atomic UTF-8 standard JSON with explicit null/status paths for
undefined numerical quantities. Reports are generated from actual evidence.
CI tests Python 3.10–3.13, full acceptance and byte-identical repeat runs within
one environment. No PDK, transistor, layout, silicon power or yield claims are
made. See the Chinese README for citations, references, BSD-3-Clause license
and the independent-implementation/patent-scope declaration.
