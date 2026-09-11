# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Python 3.10 support (CI-found, C5).** `tests/unit/test_cli.py` imported
  `tomllib`, which is stdlib only from 3.11 — on the declared minimum Python
  (3.10) the import raised `ModuleNotFoundError` and **aborted collection, so
  zero tests ran on 3.10**. Replaced with a `sys.version_info` shim that falls
  back to `tomli`, and added `tomli>=2.0; python_version < '3.11'` to the dev
  extra. Local verification is impossible here (only 3.13 is installed); the
  guard is the 3.10 job in the CI matrix.

### Changed

- **Chinese README is now the default landing page.** `README_CN.md` →
  `README.md`; the English version moved to `README_EN.md`, with the badge set
  mirrored onto the Chinese page and all cross-references updated
  (`MANIFEST.in`, `CITATION.cff`, `docs/STATUS.md`).

## [7.0.0] — 2026-09-10

Major release. Triggered by an external audit of `adi_model_release_v6.1`
(see `docs/audit_response.md`). Every item below is a **fix to a defect the
audit demonstrated numerically**, or a **governance change** that makes such
defects detectable by CI instead of by peer review.

> **Scope note.** This release does **not** claim to reproduce the target chip.
> Audit finding A06 (shared residue amplifier on a time-varying reference) is
> explicitly **not implemented** — see `docs/model_scope.md` §4. Anything that
> would need it (why the published part reaches its accuracy, why an
> integrating RA is unsuitable, the 50/69 mW figures) remains out of scope.

### Added

- **OSS governance**: `pyproject.toml` (PEP 621, hatchling), `LICENSE`
  (BSD-3-Clause + explicit third-party IP notice), `NOTICE`, `.gitignore`,
  `CONTRIBUTING.md`, `.pre-commit-config.yaml`, GitHub Actions CI
  (`ruff` + `mypy` + `pytest` on Python 3.10–3.13).
- **Console entry points** `adi-run-all` / `adi-make-report`
  (`src/adi_model/cli.py`, `src/adi_model/__main__.py`). They locate the
  un-installed `tools/` directory themselves, so `pip install -e .` and
  `PYTHONPATH=src python tools/run_all.py` both work.
- **Machine-checkable provenance** (`src/adi_model/provenance.py`):
  `SourceGrade` (`DISCLOSED` / `DERIVED` / `FITTED` / `ASSUMED` /
  `RESEARCH_EXTENSION`), a `Graded[T]` container with a runtime
  `require()` assertion, an 88-entry `PARAM_GRADES` table, and
  `audit_provenance()` which reports ungraded fields, fitted-in-use and
  assumed-in-use. See `docs/adr/0007-provenance-as-a-runtime-value.md`.
- **Physical 18-slice pool** (`src/adi_model/slice_pool.py`):
  `PhysicalSlicePool` owns the unit capacitors, the held charge and the
  acquisition history; `check_causality()` verifies that a converting slice
  actually holds the sample. `dac_error(conv, k, sid)` binds the mismatch to the
  *selected* physical units.
- **A07.6 — driver noise was invisible in every reported error.** `capture()`
  adds the driver noise *to* `x1`/`x2`, so `err_to_x1 = out − x1` cancels it:
  a 1 mV `driver_noise_rms` read as a **0.99 µV** error. `SampleBatch.x1_clean`
  / `x2_clean` now retain the pre-noise input and `SimResult.err_vs_clean`
  exposes the whole-chain error. Measured: 1 mV driver noise → `err` 0.99 µV,
  **`err_vs_clean` 992 µV**; with `driver_noise_rms = 0` the two fields are
  bit-identical, so the default path is unchanged. Implemented in all three main
  loops, walled by `tests/regression/test_error_reference.py`.
- **`SimResult.err_to_x1` / `err_to_x2`** are documented for what they are —
  references at the two **fixed sampling instants** `t1` and `t2` — not as
  "internal vs whole-chain" errors.
- **Test suite** (`tests/`, 134 tests): `unit/` 41 (provenance, slice pool, CLI
  incl. CI-contract guards), `integration/` 12 (chain equivalence, determinism,
  charge closure), `audit/` 40 (adversarial regressions transcribed from the
  audit findings — all failed on v6.1), `regression/` 41 (defects found by
  *running* the model: observer headroom, skew derivative, error reference).
- **Industrial-grade docstrings across all 336 API nodes**: every public
  function/method/property now carries a one-line summary with its
  *reference frame and unit*, an `Args:` block (meaning + **unit** + source
  grade), a `Returns:` block (structure + unit), and — where true —
  `Raises:` / `Side effects:` / `Notes:` (formula, approximation level,
  literature tag). Previously 41 nodes had no docstring at all and 184
  lacked `Args`/`Returns`. Every stage function in `experiments.py` also
  states *what it tests*, *how*, *the pass/fail criterion*, and the meaning
  of each key in its heterogeneous result dict.
  **Verified numerically inert**: `tools/results/results.json` is
  byte-identical before and after (SHA256 `fb77298d…42bfd3c`, 93 751 B).
- **`docs/STATUS.md`** — release-time snapshot: scale, the four gates,
  audit closure table, source-grading rules, docstring coverage, and an
  honest known-debt list (D1–D5).
- **OSS scaffolding**: `CITATION.cff` (with the ISSCC 2024 DOI plus an
  explicit note that it is *not* a DOI for this repository), `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, `MANIFEST.in`, issue templates (bug report and
  model/provenance question, both requiring a **source-grade** checkbox)
  and a PR template requiring a **numeric-impact** answer.
- **Packaging**: `python -m build` produces a self-contained sdist
  (source + tests + tools + docs + reference output, 75 entries) and wheel;
  released with SHA256 sums.

### Fixed

- **A01 — first-stage resolution.** v6.1 resolved only 64 first-stage levels
  (`b1=6`, Δ1 = 93.75 mV) while describing itself as "9b in the first stage".
  The reading is now an explicit, self-describing field `stage1_reading`, and
  `Config.validate()` enforces five architectural consistency checks
  (`2**b1 <= DAC levels`, grid divisibility, `dither_enhancement_bits ==
  log2(DAC levels / 2**b1)`, `Δ2/G0 <= LSB20`, residue peak < ADC2 upper rail).
  Default is `paper_consistent`: **7b SADC + 2b dither range enhancement = 9b**,
  the unique split consistent with *both* published statements.
  See `docs/adr/0003-stage-1-resolution.md`.
- **A02 — cross-cycle sample binding.** v6.1 drew an independent 8/18
  permutation every cycle: only **1** transition in 8191 had the conversion
  group equal to the previous acquisition group (mean **3.5572 of 8** slices),
  and 8180 conversions re-selected a slice immediately. Randomisation now
  happens *within* the causality constraint (`shuffle_causal`) or not at all
  (`pingpong`): **0 violations, mean coverage 8.0 / 8**. See
  `docs/adr/0005-physical-slice-pool.md`.
- **A02b — mismatch was not bound to the 8/18 choice.** v6.1's fixed-schedule
  and shuffled-schedule runs were bit-identical (max difference **0.0 µV**)
  because the physical DAC evaluation never saw the selection. Now
  `max|Δe_dac|` between two different 8/18 groups is **23.64 µV = 4.13 LSB20**.
- **A03 — dither code unit error.** `d_code = -round(d/gran)` was computed in
  coarse-step units but added to `coarse * units_per_lsb1`, which is in
  **RDAC unit steps** — missing the factor `gran/step0`. Reproducing the
  audit's stage-21 rerun, the single-unit fix moves the coarse-transfer branch
  from **32.6721 % → 15.9668 %** ADC2 overflow and **16308.07 → 5698.59 µV**
  RMS error, and flips that check from PASS to FAIL — i.e. the old PASS
  depended on the broken baseline. The conversion now lives in one place
  (`mapper.dither_transfer_code`) that both main loops must call, and the
  stage-21 criterion was rewritten.
- **A04 — the DR cannot be both an anchor and a prediction.** RA noise is
  reverse-solved from `target_dr_db`, so DR tracks the target to within
  0.02 dB *by construction*. The dependency is now a graded fact:
  `ra_out_noise_rms`, `mismatch_sigma0` and `sadc_rdac_gain_mismatch` are
  `FITTED` and listed by `audit_provenance()["fitted_in_use"]`. Note that
  `target_dr_db = 94.6 dB` itself is `DISCLOSED` — what is fitted is the noise
  solved *from* it.
- **A05 / A06 — the observer correction was consuming ADC2 range.** The KTC
  correction used to be subtracted *ahead* of ADC2's quantiser
  (`quantize(vra - κ*v_N)`). The correction grows with input slope, so the
  ADC2 overflow rate was **0 % at fs/64, 12.24 % at fs/8 and 49.63 % near
  Nyquist** — while the checks that "passed" only looked at low frequencies.
  Now `ADC2.quantize_with_correction(vra, κ*v_N)` quantises first and subtracts
  in the digital domain: **0 % overflow at every frequency**, with the
  semantics of `over` restricted to what ADC2 actually sees. See
  `docs/adr/0006-observer-correction-is-digital.md`.
- **A07.1 — 20-bit code domain.** `n_bits_target` only defines the LSB
  reference; the output is an analog-voltage equivalent, not a 20-bit encoder
  word. A test asserts that changing `n_bits_target` from 18 to 20 changes
  **not one output sample**, so this boundary cannot be lost again.
- **A07.2 — flicker was identically zero in the main record.** At
  `N = 32768`, `fs = 40 MHz` the first FFT bin is 1.22 kHz, so a 40 Hz corner
  is unresolvable and the sequence was all-zero. The generator is now
  **band-limited** (zero above the corner) rather than dropping the term
  silently, and stage-23's ±3 dB tolerance is derived from the χ² degrees of
  freedom of the (2-bin) low band instead of being hard-coded. The main record
  carries **no** flicker, and says so.
- **A07.3 — timing-skew slope bias.** `np.gradient` has amplitude response
  `sin(2πf/fs)/(2πf/fs)`: 0.6366 at 10 MHz, **0.0524 at 19 MHz** (a 25.6 dB
  underestimate), 0 at Nyquist. `sampler.input_derivative()` now uses the
  analytic derivative when the input supplies one (`sine_input` / `dc_input`
  do) and a spectrally exact FFT derivative otherwise. Re-running each record
  with `np.gradient` monkey-patched back in reproduces the audited bias
  point-by-point (5/10/15/18/19 MHz → measured ratios 1.1 / 1.6 / 3.3 / 9.2 /
  18.3 against theoretical 1/|H| = 1.11 / 1.57 / 3.33 / 9.15 / 19.1). See
  `docs/adr/0008-exact-slope-for-skew.md`.
- **A07.4 — Monte-Carlo histogram provenance.** `tools/run_all.py` drew
  **400** Gaussian samples from the MC mean/std and captioned the plot "60
  chips"; such a figure cannot support tail or yield statements.
  `monte_carlo*` now returns `sndr_per_chip` / `sfdr_per_chip` /
  `err_rms_per_chip_uV`, the figure plots those 60 real points, and a static
  guard scans **both** `src/` and `tools/` for a resampling pattern. The guard
  has its own test (`test_the_guard_would_catch_the_historical_line`) because
  the first version only scanned `adi_model.experiments` and therefore passed
  while the defect shipped.
- **A07.5 — `sine_fit_metrics` raised `ValueError` for low `f_in`.**
  At `fs = 40 MHz`, `N = 16384`, calling with 1 kHz or 5 kHz hit an empty
  harmonic-search window. Bin indices are now clamped and the result carries
  `harmonics_reliable` / `harmonics_dropped`, so an unreliable frequency is
  *declared* rather than raising or silently reporting garbage.

- **C1 — CI invoked a flag the CLI does not define.** The reproducibility job
  ran `adi-run-all --out`; the CLI only defines `--results-dir`, so it exited
  with `unrecognized arguments` before a single experiment ran. Fixed, and
  guarded statically by `test_cli.py::TestCIMatchesTheCLI`, which parses
  `.github/workflows/ci.yml` and checks every flag against the real parser.
- **C2 — CI's "full sweep" collected nothing.** `pytest -m "slow"` matches no
  test in the suite, so the job passed without executing an experiment. CI now
  calls `adi-run-all` directly; the same test class asserts it still does.
- **C3 — the lint gate could never pass.** `ruff check .` reported **6797**
  errors and `ruff format --check` wanted 37 of 38 files reformatted. **6251
  (92 %) were `RUF001/002/003` firing on the Chinese full-width punctuation
  this codebase deliberately uses**; `D400`/`D415` are worse — ruff does not
  recognise `。` as a sentence terminator and its autofix would append an ASCII
  period, producing `。.`. Those rules are now ignored *with the reason
  recorded*; the remaining 546 genuine issues were fixed (17 `F401`, 17
  `F841`, 12 `E702`, 16 `N816`, …). Gate: **6797 → 0**, format: 0 → 0.
- **C4 — the type gate could never pass.** `mypy` reported **147** errors.
  Four `Config` fields were declared `object = None` (using `None` as an
  "auto-derive" sentinel), which disabled checking at every downstream
  `float(...)`; they are now `float | None` and the existing `is None` guards
  let mypy narrow on its own. `build_first_stage_quantizer(cfg, dac: object)`
  hid its real contract behind `object` and now takes a structural
  `SplitDacLike` protocol (structural to avoid an import cycle with
  `dac_arch`). Gate: **147 → 0**.
- **Dead code removed** after per-site inspection (no latent bugs found):
  `dynamics.gamma` (superseded by `g_st`/`g_dy`), `experiments._b_img`,
  duplicated `ktc_kappa` computations, and a branch guarded by
  `"f_target" in _coherent_fin.__code__.co_varnames` — a runtime check that is
  **always False**, left over from a removed parameter.

### Changed

- **Sub-weight-induced INL is now derived, not hard-coded.** With the
  7b + 2b reading, `units_per_lsb1 (4) < n_sub (8)`, so the sub-array *is*
  code-modulated and its lumped node parasitics produce a deterministic
  (period-2) INL that DEM cannot average out. stage-13's previously
  hard-coded `< 1.5 LSB20` bound is replaced by the analytic prediction
  `0.5·v_FS·|g_true − g_nom| / LSB20` (predicted 3.17, measured 3.29,
  ratio 1.04), with the equality enforced to ±25 %. This effect is what the
  paper's PPT p.31 "Binary-to-unary bridging" calibrates; **this model does
  not implement that calibration**, so the base-row INL being above the
  published 2.2 LSB is *expected*, not a regression. It is booked as such in
  `docs/model_scope.md`.
- **Calibration observability rank is now a law, not a constant.** The old
  check asserted `rank(U) == 64 and top group size == 8` — derived from the
  v6.1 (b1=6) reading. Under the 7b reading the code sweep is `2**b1` and the
  rank law is `rank(U) = n_units_sig / n_active`. The stage-11 check now
  computes the sweep from `2**b1` and the expected rank from the configuration,
  so a reading change cannot produce a stale expectation.
- **`tools/run_all.py` and `tools/make_report.py` moved out of `src/`** and are
  reachable both as console scripts and directly.

### Not done (recorded deliberately)

- **A06 — reference / RA event-level analysis.** No coarse/fine reference MUX
  switching, no RA tracking equation on a time-varying reference, no
  finite-bandwidth / slew / cross-sample memory in the reference path. The
  `ra.py` model remains an instantaneous gain + noise + clip stage. This is
  the single largest gap between this model and the published architecture.
- **A07.3 in `sim_split`.** Timing skew is only implemented in `pipeline.py`;
  `sim_split.py` has no skew path, so the two chains are not equivalent in the
  presence of skew. The degenerate (skew off) equivalence holds bit for bit.
- **P2 — observer circuit study.** No noise covariance from a phase-node
  equation, no bandwidth / swing / load / energy budget. KTC conclusions
  remain "conditional on the given observation quality".
- **A05.4 — full-code static / cross-frequency mismatch sweeps.** Referenced
  by the audit's P1 list; not implemented.
- **Output-schema drift is not gated.** The C-series cleanup renamed a
  `results.json` key by accident; only the byte-comparison against a stored
  baseline caught it, because the CI reproducibility job compares two *fresh*
  runs and would have passed. A golden-schema test is the obvious follow-up.

## [6.1.0] — 2026-09-10

- Previous release (`adi_model_release_v6.1`). Kept, unmodified, outside this
  repository as the audited baseline. `docs/MANIFEST.txt` in that tree lists 37
  files with SHA-256; all 37 verify.
