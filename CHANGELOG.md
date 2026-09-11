# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [7.0.2] — 2026-09-11

Response to a **third external review** (2026-09-11), fixed at the v7.0.1 commit
`54ba47f`. The review confirmed that v7.0.1's main-path fixes are real (fixed RA
gain, the hidden fit, the exit code) and accepted the qualification that the
**default** ping-pong scheduler is causally correct — then found three further
gaps. All three are confirmed on the code, and checking them turned up a live
failing requirement that no gate had ever seen. Per-finding adjudication, with
the reproduction command for each: `docs/review_response_2026-09-11b.md`.

Nothing in this section changes `tools/results/results.json`: the file is
byte-identical to v7.0.1 (`65c047a9…4ecdec`), verified by a full re-run.

### Fixed

- **The gate skipped the configuration self-checks entirely.** v7.0.1's rule
  enumerated only `results[name]["PASS"]` and `results[name + "_summary"]`.
  `run_all.py` also writes `results["validate"][check]["PASS"]` and
  `results["validate_ktc"][check]["PASS"]`, and **neither was collected** — the
  reviewer's counterexample,
  `hard_failures({"validate": {"invalid_config": {"PASS": False}}})`, returned
  `[]`. So "a bad configuration always stops the flow" was not true. Both tables
  are now collected explicitly (`acceptance.ACCEPTANCE_CONTAINERS`), with a
  **coverage floor** (`MIN_RECORDS = 50`) so a whole record class going missing
  is itself an error, and the rule that a config `False` or a deliberately
  failing control experiment still does not count is preserved and pinned by
  test.

  This immediately surfaced a **live `PASS: False`** that had never been
  reported: `validate_ktc` → *KTC 校正项带宽上限 f_max (满幅)*, measured
  2.5465 MHz against a 5 MHz requirement. It is now registered in an explicit
  **known-limits ledger** (`acceptance.KNOWN_LIMITS`) with its reason and its two
  exit conditions. The ledger has `xfail(strict=True)` semantics: an entry that
  is missing from the results, or that starts passing, is an **error** — a
  hidden exemption cannot survive. Every exemption is printed on every run.

- **An overridden value could still pass a `DISCLOSED` check.** v7.0.1 added
  `audit_provenance(...)["overridden"]`, which only a human reading the report
  can act on. `Graded` still carried the grade of the field *name*, so
  `annotate_config(Config(fs=80e6))["fs"].require(SourceGrade.DISCLOSED)`
  returned `8e7` — a downstream caller could obtain a hand-edited number under
  the paper's authority. `Graded` now carries `overridden`, and `require()`
  checks `effective_grade`, which demotes an overridden `DISCLOSED`/`DERIVED`
  value to `ASSUMED`. `map()` propagates the flag, so a derived quantity cannot
  launder an overridden ancestor.

- **An illegal configuration was reported, not refused.** `validate()` returning
  a `False` record is *reporting*; the reviewer's contract is that a config must
  take effect where it is declared **or be refused**. `gain_vector()` still fell
  through to the capacitor-ratio path for any non-`"fixed"` string, so
  `ra_gain_model="Fixd"` produced numbers. New `Config.LEGAL_VALUES` +
  `Config.check_legal()` raise `ConfigError` **at the entry point** of
  `run_sim`, `run_sim_split` and `run_pipeline`. Scope is deliberately narrow
  and separate from `validate()`: unrecognised enum values, non-positive sizes,
  an undefined bridge formula (`dac_n_sub < 2`), and a stage-1 reading the DAC
  cannot express (`2**b1 > dac_levels`).

- **The "physical pool is wired in" test was a source-string scan.**
  `assert "PhysicalSlicePool" in inspect.getsource(...)` — which an unused
  import, or even a comment, would satisfy. Replaced with a behavioural contract
  (a converted sample must be traceable to the slices that produced it, so that
  internal charge, DAC weight and residue gain can be tied to one capacitor
  set), plus a **positive control** proving the contract is not vacuous: the
  scheduler already returns per-sample acquisition/conversion index arrays, so
  what is missing is the binding, not the data. Still `xfail(strict=True)`.

### Changed

- **The gate now always prints a verdict line when the command may exit zero.**
  With one registered known limit, the log previously contained only a `KNOWN`
  line and no "this passed" statement, so neither a reader nor a CI log search
  could tell a clean run from a truncated one.
- **`pytest` now collects the library's docstring examples.** `testpaths` was
  `["tests"]` and `--doctest-modules` was absent, so every `>>>` block in
  `src/adi_model` was decorative. `src` is now in `testpaths` with doctests
  enabled: **7 examples** run as part of the gate, which is why the suite goes
  from 160 to 167 passing tests. Nothing else about the gates changed.

### Documented

- **`model_scope.md` §2.1 item 5 (causality) is qualified.** The claim was
  stated unconditionally with a single coverage number. It is true of the
  default ping-pong *policy* (8191/8191, coverage 8.000/8) and of
  `PhysicalSlicePool.shuffle_causal`, but the stage-19 shuffling study uses
  `ShuffledScheduler`, which satisfies `conv[n] = acq[n-1]` on **0/8191**
  transitions. And in neither path are the *physical* capacitors bound to the
  sample: `c_sig` is a constant vector and `evaluate_physical(k_eq, sid)` takes
  no conversion group.
- **`model_scope.md` §5 no longer claims a unique reading.** "Only one satisfies
  both disclosures" is now "the default, self-consistent *given* the
  codeword-range = decision-bits assumption" — the very identification ADR 0003
  §1 rejects and §2 then uses. The same overstated word was removed from
  `config.py`'s field comment and module docstring. `b1` remains graded
  `ASSUMED`; the API rename is still deferred as a version-level decision.
- **`model_scope.md` §6 records the KTC bandwidth limit**, so the check that is
  now failing has a written boundary rather than only a ledger entry.

### Found while verifying (not raised by the review)

- **`dem_mode` is declared, documented, and read by nobody.** No module compares
  against it: which scheduling behaviour you get is decided by *which
  `Scheduler` subclass the caller instantiates*. This is the same
  name-promises-an-unwired-mechanism defect as `ra_gain_model` in v7.0.0. Pinned
  by `TestR10DemModeIsInert`, which is written to fail when it is wired.
- **The `require()` enforcement hook has no production call site.** It appears in
  a doctest and in tests only. The grading machinery is now enforced where it
  can actually bite (`require` honours `overridden`), but "every number is
  checked at its point of use" is still an aspiration, not a fact.
- **A comment cited a test file that does not exist.** `LEGAL_VALUES` said
  "`tests/unit/test_config.py` will check this table against `validate()`" —
  there is no such file (`tests/unit/` holds `test_cli`, `test_provenance`,
  `test_slice_pool`). The same paragraph claimed that every value in the table
  "has a comparison branch in the code": measured, `self.stage1_reading` has
  **zero** comparison points anywhere (the factory methods only ever *write* it)
  and `dem_mode` likewise. The comment now states which seven fields are
  compared, which two are not, and why those two are still listed — the
  reviewer's own charge, turned on this round's own comments. Fixed before
  release rather than after.
- **The contract-test docstring indexed its findings against the wrong
  document.** Round 2's table pointed at `docs/review_response_2026-09-11.md`
  (round 1's adjudication) and mislabelled §3.1. The two rounds are now listed
  separately with the correct section of each adjudication document, plus an
  explicit note that "§3.3 of the third review" inside an `xfail` reason means
  the *reviewer's* §3 item 3 (adjudication §2.5), not this document's §3.3.

The pattern is worth naming: all three defects above are in content written
**during this round**, and all three are the same shape as the charges in the
review — a claim stated in prose that no execution checks. They are listed here
rather than quietly fixed because that is the only way the count stays honest.

## [7.0.1] — 2026-09-11

Response to a **second external review** (2026-09-11) of the published v7.0.0
tree. Per-finding adjudication: `docs/review_response_2026-09-11.md`.

The review's central charge was not "the code is wrong" but *"the fix landed in
a helper and a test, not in the path that produces the result — so the tests
pass while the thing that needed proving remains unproven."* That charge holds,
and verifying it turned up a further defect **of the same kind inside our own
documentation**, listed first below.

### Fixed

- **ADR 0005 claimed a fix that was never wired in.** The ADR was marked
  "已采纳" and stated that `pipeline` and `sim_split` both obtain their physical
  quantities "from the same pool". They do not: `PhysicalSlicePool` is
  referenced by `__init__` and by two test modules, and by **no runner and no
  experiment**. §3.5's "third sub-charge closed" claim is likewise false —
  `SplitDAC.evaluate_physical(k_eq, sid)` still receives no conversion group.
  Corrected with an erratum block, struck-through claims, and an honest
  open-items list. Third-party evidence for the two numbers the ADR quotes now
  comes from tests that exercise the pool directly, which is stated as such.

- **`audit_provenance` hid the model's most load-bearing fit.** `fitted_in_use`
  filtered on `value is not None`, but `ra_out_noise_rms = None` *means* the fit
  is active (it selects `resolve_ra_noise`). The default configuration's RA
  noise fit was therefore absent from the one report a reviewer is told to read
  first. Sentinel fields now carry their runtime-resolved value
  (`RESOLVED_SENTINELS`, reported under `resolved_sentinels`), and "in use"
  means "currently moves a number".

- **A disclosed value could be changed and keep its original source.** Grading
  is attached to the field *name*, so `Config(fs=80e6)` still reported
  `DISCLOSED` with source "[00] abstract: 40 MS/s". `audit_provenance` now
  returns an `overridden` list — `DISCLOSED`/`DERIVED` fields whose value no
  longer equals the stock default. The source string is deliberately left
  unedited: it describes what the paper says, while `overridden` describes
  whether the number is still that one.

- **`ra_gain_model="fixed"` never took effect in the split path.** `sim.py` read
  the flag; `sim_split.py` and `pipeline.py` hard-coded the capacitor ratio and
  passed it explicitly, bypassing `ResidueAmplifier.gain_vector` — which was
  consequently dead code with no caller anywhere. Both runners now go through
  `gain_vector`; the default `charge` mode is bit-identical (`gain_vector`
  returns exactly `c_sig/C_F`). Measured with `gain_error=0.01`: charge
  `g=31.999950` vs fixed `g=32.320000`, `max|Δout| = 4.63e-4 V` in both chains.
  `validate()` additionally rejects an unknown gain model instead of silently
  degrading to `charge` — a config must take effect where it is declared, or be
  refused.

- **Numeric FAIL did not fail CI.** `tools/run_all.py` printed PASS/FAIL and
  wrote them to `results.json` but contained no `sys.exit` and no `raise`, so
  the sweep step's exit status was unrelated to its findings: a report saying
  FAIL and a green CI could coexist. New `adi_model.acceptance.hard_failures()`
  decides over an **explicitly enumerated** set — `R[name]` dicts carrying a
  `"PASS"` key (10 records) plus `*_summary` dicts (5 groups), 15 in total,
  matching what the script already prints. Deliberately not a recursive
  boolean scan: `False` in a config and a counter-example row designed to fail
  are not acceptance failures. Verified against the current all-passing
  `results.json`: **0 false positives**.

### Added

- **`docs/review_response_2026-09-11.md`** — 8 findings adjudicated one by one,
  each reproduced or refuted on the real code with the repository's own
  measurements (no number is taken on the reviewer's authority).

- **`tests/audit/test_review_contracts.py`** — 13 contract tests that go through
  the real entry points (`run_sim_split`, `run_pipeline`, `audit_provenance`,
  the acceptance rule) rather than through a helper. 10 assert behaviour that
  holds; **3 are `xfail(strict=True)` and mark the defects still open**, so they
  appear in the test report and the marker is *forced* off on the day someone
  fixes them.

- **`.gitignore` guard against reference material** (hard requirement, not
  hygiene). `NOTICE` cites [00]/[00_1]/patents [09]-[14] and states that none is
  redistributed; committing the PDFs/slides of those third-party documents to a
  public repository would contradict that statement and constitute
  redistribution. `refs/`, `references/`, `literature/`, `*.pdf`, `*.ppt(x)`,
  `*.doc(x)`, `*.key` are now ignored, so `git add -A` cannot pick them up by
  accident. Own documents can still be tracked with an explicit `git add -f`.
  Verified: the repository tree and the pushed remote contain **no** such file.

### Changed

- **`docs/model_scope.md`** — the KTC entry now states the boundary the review
  identified: `v_N` is subtracted digitally as a float array with no modelled
  quantiser, coding or latency, so the quoted gain is what an *ideal digital
  observation read-out* would give. The flicker entry's claim that the absent
  low-frequency power is "not an oversight" was **wrong** and is corrected
  (see below).

### Documented as open (each changes `tools/results/results.json`; not applied)

- **Sample ownership is not enforced on the shuffled path.** Measured
  independently at N=8192, seed 20260911: the default `Scheduler` is causally
  correct (8191/8191 conversions use the previously acquired group, coverage
  8.000/8), whereas `ShuffledScheduler` — used by the stage-19 shuffling study
  — is not (**0/8191**, coverage **3.547/8**, against the 8·8/18 = 3.556
  expectation for an independent draw). The review's verdict on the *evidence*
  stands: "18-slice physical scheduling with cross-cycle causality is verified"
  is not supported. It is not true, however, that the default main path
  converts charge it never sampled — that applies to the shuffled path only.
- **`flicker_series` back-fill is unreachable.** The guard
  `if f_corner <= f_min or f_min <= f_low: return x` returns precisely when the
  corner sits below the record's resolution limit — the only case the drift
  back-fill was written for (its comment says "and", the code says "or").
  Measured 0/32768 non-zero samples at fs=40 MHz, n=32768, fc=40 Hz, t_obs=10 s.
  Fixing it moves the stage-23 40 Hz row, hence held back.
- **`7 + 2 = 9` is a hypothesis, not a convergence result.** ADR 0003 §1 rejects
  conflating codeword range with decision information, then §2 makes exactly
  that identification to solve for `b1 = 7`. `provenance` already grades `b1` as
  `ASSUMED`; the *prose* ("唯一分配", the name `paper_consistent`) overstates it.
  Wording/renaming deferred as a version-level API decision.
- **KTC observation channel is not realisable yet** — no quantiser, coding or
  latency for `v_N`; the reported gain is an upper bound.

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

- **C5 — Python 3.10 support (found by CI, not reproducible locally).**
  `tests/unit/test_cli.py` imported `tomllib`, which is stdlib only from 3.11.
  On the declared minimum Python (3.10) this raised `ModuleNotFoundError`, and
  because pytest aborts on a collection error, **zero of the 134 tests ran on
  3.10** — the job failed with no signal about the rest of the suite. Replaced
  with a `sys.version_info` shim falling back to `tomli`, and added
  `tomli>=2.0; python_version < '3.11'` to the dev extra. Verified by the 3.10
  CI job (now `success`); only CPython 3.13 exists on the development machine.
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

- **Chinese README is now the default landing page.** `README_CN.md` →
  `README.md`; the English version moved to `README_EN.md`. The badge set
  (Python / ruff / mypy / tests) was mirrored onto the Chinese page so the
  default entry point is not thinner than the English one, and every
  cross-reference was updated (`MANIFEST.in`, `CITATION.cff`, `docs/STATUS.md`).
  Internal anchors verified in both files (16 links each, 0 dangling).
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
