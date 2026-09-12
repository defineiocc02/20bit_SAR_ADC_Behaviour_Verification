# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [7.0.8] — 2026-09-12

Closes the three **"not implemented / not aligned with patents"** items from
the review ledger: standalone mechanism-level models for patents [12], [13]
and [14], each with a dedicated executable-specification test module. All
three are **independent technique models, not wired into the pipeline main
path** — integration moves published numbers and stays gated behind the
R1/R2 physical-pool work (release discipline).

### Added

- **`interleave_tracking.py` — patent [12] US 10,707,889 B1** (interleaving
  tracking phase). Mechanism-level charge accounting for the IDLE phase
  being a *tracking* update from **another sub-ADC's most recent conversion
  result** instead of a reset-to-midscale: per-cycle `v_hold[i] = x[i−1]`
  identity under deterministic rotation; kickback charge ratio vs reset
  < 0.2 for slow signals; filter-BW ∝ charge and driver-noise ∝ √charge
  scaling laws; weighted variant (disclosed example N=3, 10/30/60%);
  optional phase randomization. The model **reproduces the patent's own
  background arithmetic honestly**: near Nyquist, mean |Δ₁| can exceed
  mean |x| and reset wins — the known regime where tracking is *not*
  better is not masked.
- **`aux_input.py` — patent [13] US 10,541,702 B1** (auxiliary input for
  ADC input charge). First-order settling scaling law: with the auxiliary
  path supplying the back-gate parasitic charge, R_f may grow by
  (C_f+C_pg)/C_f, the *minimum required* filter bandwidth drops by the
  same factor, driver noise drops by its square root, per-sample driver
  charge sheds the C_pg·ΔV share; `gate_boost` (FIG.4/5) zeroes the r_on
  code modulation (the term `dyn_ron_code_coeff` describes) as a
  structural property. Modes: `off` / `dedicated_pin` (FIG.2) /
  `opamp_midpoint` (FIG.3) / `gate_boost` (FIG.4-5).
- **`ref_track.py` — patent [14] US 10,826,519 B1** (low-power reference,
  arrangement B: comparator + threshold adjust + end-of-cycle S1 top-up).
  Behavioural charge-threshold loop answering the **A06 gap**: end-of-cycle
  residual converges geometrically, settling in **6 conversion cycles**
  (disclosed magnitude "5 or 6"; integrator gain is ASSUMED, the
  order-of-magnitude agreement is a structural result, not a fit); external
  top-up charge decays by orders of magnitude after settling; the threshold
  residual converges to the charger tracking lag (≈1/750 of the first-cycle
  error — not claimed to be exactly zero). A06 structure: in steady state
  the coarse trials carry mV-level reference error (inside the Δ1/2
  self-healing window of [09] §1.3) while the final trials see sub-nV
  error (>20 b equivalent) — "RA starts at ~15 b reference, the last
  trials need 20 b".
- `tests/unit/test_patent12_interleave_tracking.py`,
  `tests/unit/test_patent13_aux_input.py`,
  `tests/unit/test_patent14_ref_track.py` — 43 mechanism-level tests
  (information flow, identities, scaling laws, validation refusals).
- `reproduction_results.md`: M11–M13 registered (mechanism-level);
  N2 re-scoped to "main-path integration not done".
- `docs/report/` — XeLaTeX reproduction report (16 pp., 8 disclosed-
  mechanism ↔ model-data comparison figures) with its figure generator
  `gen_figs.py` (all plotted quantities read from `tools/results/
  results.json` or computed at runtime; no hard-coded model results).

### Notes

- Patent texts [12]/[13]/[14] remain **not redistributed** (see NOTICE);
  mechanisms are paraphrased at mechanism level with figure/claim pointers.
- Numerical parameters without disclosed values are graded ASSUMED in each
  module's `*_GRADES` table: c_parasitic_ratio, r_aux, I_max, comparator
  bandwidth, threshold gain, t_conv, power-up initial condition, S1 top-up
  gain. c_signal / c_dac anchors derive from this repository's
  `c_active_nominal()`.
- Reference outputs unchanged: `tools/results/results.json` fingerprint
  `a1ccd92f…35ac70` untouched (no pipeline code path modified).

## [7.0.7] — 2026-09-11

Response to a **sixth external review** (fixed at `c0787dd`): adjudication of
the release quality and the comparison document's wording.
`docs/review_response_2026-09-11e.md` — all 10 findings adjudicated
(E1–E6 confirmed and fixed, E7–E9 confirmed as-is, E10 accepted as the
next milestone's sole goal).

### Fixed

- **[12] patent number was wrong.** `US 10,797,889 B1` →
  **`US 10,707,889 B1`** (verified three ways: the uploaded PDF's filename,
  the review's quotation of its front page, and an independent patent-db
  entry — ADI International, Bodnar/Hurrell/Ahmad, 2020-07-07). Fixed in
  `NOTICE` and in both places of `docs/engineering_vs_patents_papers.md`.
- **The comparison document's "✅ aligned" column mixed locally-true
  principles with main-path implementation status** (sixth review §2):
  the 18-slice row is now split into three (scheduler invariants ✅ /
  PhysicalSlicePool verified but not wired ✅ / main-path 8-of-18
  charge–weight–gain linkage ⚠️); the 31 dB / 42 dB stage-19 numbers are
  annotated as **non-causal-shuffle implementation results, not physical
  architecture benefit**; the shared-RA row now states that the main entry
  passes a constant `c_sig` to `gain_vector` (pipeline.py:348–354) —
  interface capability ≠ execution; the AZ row is reclassified as a
  disclosed-effect **budget model**, not circuit-structure alignment; the
  4.6875 mV SADC margin is labelled a configuration-conditional derived
  value, not a patent-disclosed tolerance.

### Added

- **`docs/reproduction_results.md`** — preliminary simulation evidence
  mapping disclosures to model outputs, every row carrying the
  `results.json` record key and the parameter grade: 11 quantitative rows
  (kT/C 20.10 µV derived from the disclosed 20.5 pF; 2b dither enhancement
  reproduced as derived = 2.0 bit with 92.6 % absorption; SADC self-healing
  window predicted 4.6875 mV vs measured 4.4531 mV; α closure to 1.1e-16;
  charge closure to 1.06e-25 C; AZ budget model internally consistent to
  0.006 dB; same-seed reproducibility exactly 0.0), 10 mechanism rows
  (interleave spur positions; shuffle 31.4 dB *with the non-causal
  qualification*; dynamic-error triad explaining the 2.2 LSB INL gap
  conditionally at ρ=0.035 → 2.06 LSB; DEM nominal conservation; DEM
  ineffective on the C_C sawtooth ≤5 %; rank(U)=64 observability), the
  research-extension evidence (KTC MC-vs-analytic 0.06 %), and the honest
  negatives (dither static linearization shows no benefit in the tested
  configuration; NSD agreement is an anchor identity, not a prediction).
  Reference: `results.json` SHA256 `a1ccd92f…35ac70`.

Numeric impact: none — documentation, citation entries and version
metadata only. Reference outputs unchanged.

## [7.0.6] — 2026-09-11

### Added

- **`docs/key_technologies.md`** — the ten key technologies of this model,
  each with the concrete principle (with formulas), the code location, the
  counterexample that shows why it matters, and the pinning test: charge-
  consistent loop (T1), exact two-floating-node segmented-DAC solution and
  the input-referred unification (T2, the 0.9972 calibre gap), nominal/
  physical evaluation split (T3), sampling-state dither with three paired
  elements (T4), the DEM valid/invalid domain incl. the 64 joint
  permutation space (T5), rank-aware calibration observability (T6),
  per-phase noise transfer and KTC cancellation (T7), code-dependent
  dynamic-error triad (T8), physical slice-pool causality (T9), and the
  methodology layer — dual-implementation bitwise equivalence, independent
  charge truth, runtime provenance grading, acceptance gates,
  `xfail(strict=True)` discipline (T10). Documentation only; no code or
  reference-output change.

## [7.0.5] — 2026-09-11

Response to a **fifth external review** (fixed at the v7.0.4 commit
`aab84c97`). This round changed character: not "is each criterion bound to
the right node" but **what can this model actually guide, and what can it
not**. Every checkable assertion was reproduced independently on the code;
adjudication: `docs/review_response_2026-09-11d.md`. All 17 findings
adjudicated: 13 confirmed, 2 confirmed with qualification, 1 accepted as
roadmap, 1 self-found inconsistency (below). No reference output change.

### Added

- **`docs/engineering_vs_patents_papers.md`** — module-by-module engineering
  ↔ literature comparison: maps every code module back to its source in [00]
  and patents [09]–[14] with an explicit alignment status (aligned /
  mechanism-aligned-different-calibre / not implemented), the
  engineering-only methodology layer (provenance grading, charge_ref,
  digital/analog boundary, acceptance gates), the [12]/[13]/[14] gaps, and
  the citation-compliance statement. Citation entries only; no third-party
  text redistributed.

### Fixed

- **`noise_phase.kappa_optimal` minimised the wrong objective.** It returned
  `aᵀΣb / bᵀΣb` — the minimiser of the *sampling-noise residual only* — while
  `sigma_res_analytic` in the same module reports a total that also charges
  `κ²·σ_eN²` for the observer path. Minimising one objective and reporting
  another agree only when `σ_eN = 0`. The fifth review supplied the correct
  joint optimum (its §11); the code-level inconsistency between the two
  sibling functions is this release's own finding. `kappa_optimal` now takes
  an optional `sigma_eN` (default 0 = historical behaviour, the single
  production call site at `experiments.py:2797` is unaffected) and returns
  `aᵀΣb / (bᵀΣb + σ_eN²)` when observer noise is present. Pinned by
  `TestR18KappaOptimalWithObserverNoise` (joint optimality on a 2001-point
  grid for four `σ_eN` values; monotone shrinkage toward 0; default preserved).

### Qualified (comments whose "conservative" claim was not unconditional)

- **Input-settling τ.** "`τ_i = R·C_slice` is ~8× smaller, so the aggregate
  single-node RC is a conservative upper bound" is true only for the branch
  switch term. With a common source impedance the star-network common mode is
  `τ = R_s·C_total + R_on·C_slice` — the `R_s·C_load` term does not shrink
  (measured: N=8, 20.5 pF, 30 Ω, 20 Ω → aggregate 1.025 ns vs common mode
  0.666 ns, a factor of 1.54, not 8). `pipeline.py`'s header and
  `dynamics.py`'s applicability note state this; pinned by
  `TestR17CommonModeTau`.

### Documented (scope statements the review re-verified, now pinned)

- **DEM permutation space is 64, not 512.** `N_DEM_STATES = 8·8·8 = 512`
  counts digital labels; both DAC rotations are driven by that single `sid`,
  so the joint physical space is 64 (period lcm(64,8), each main rotation
  paired with exactly one sub rotation) — measured by enumeration and pinned
  as a *passing* test, `TestR16DemPermutationSpace`, so no DEM sweep in this
  repo can be quoted as the paper's three-dimensional mechanism
  (`docs/model_scope.md` §4.11).
- **[12] tracking and [13] auxiliary input are absent from the source**
  (`grep aux|tracking src/` → 0 hits); the input-drive benefits of those
  patents cannot be derived from this model (`model_scope.md` §4.12).
- **The KTC `f_max` criterion is a swing criterion only.** "Does not exceed
  swing" (134.5 MHz, the v7.0.3-corrected node) and "settles within the
  Δt = Ts/256 ≈ 97.66 ps extraction window" are different requirements — a
  one-pole settle-to-0.1 % would demand f_BW ≥ ln(1000)/(2π·Δt) ≈ 11.26 GHz
  (conditional design-pressure estimate, not a circuit requirement).
  Stated at the `validate_ktc` criterion and `model_scope.md` §4.13.
- **The RA noise anchor baseline is explicit**: `resolve_ra_noise` derives the
  anchor with AZ off and dynamic-bandwidth off; the two factors are then
  applied in fixed order (AZ first, then dynamic bandwidth), with no
  re-derivation — answering the review's "is the anchor before or after"
  question from the code (`ra.py` comment).

**Reference output unchanged**: full `adi-run-all` re-run, exit code 0, 50
records, `results.json` SHA256 `a1ccd92f…35ac70` — byte-identical to 7.0.4,
figures identical.

Gates: ruff / format / mypy clean; pytest **189 passed, 4 xfailed** (7 new
pins).

## [7.0.4] — 2026-09-11

Hotfix to 7.0.3: the K4 strictness introduced in 7.0.3 refused a *legitimate*
verdict type and broke the acceptance sweep that the gate exists to judge.

- **`_verdict` rejected `numpy.bool_`.** The in-memory sweep builds records
  straight from numpy comparisons — `s10_summary.C_F->增益(电荷一致)` was born
  as `np.True_` (a `ratio` of `np.float64` values, `experiments.py` stage 10) —
  so `main_run_all` died with
  `ValueError: … has PASS=np.True_ (bool), not a bool` before it could write
  the gate verdict. The JSON round-trip had always masked this
  (`json.dump` cannot write `np.bool_`, so `results.json` holds a real
  `bool`), which is why two local full re-runs passed on 7.0.3 while CI's
  in-memory path failed.
  Fix, two layers: `acceptance._verdict` accepts `np.bool_` and normalises it
  to `bool` — it is a genuine verdict, not one of the impostors K4 exists to
  catch (`"False"`, `0`, `1.0`, `None`, all still refused, pinned in
  `TestR15`); and the stage-10 source casts its comparison with `bool()` so
  records are born with the type they will be read as.
  **Reference output unchanged**: `results.json` SHA256
  `a1ccd92f…35ac70`, byte-identical to 7.0.3 (verified by a full re-run with
  exit code 0, 50 records, "硬性验收: 全部通过").
- README BibTeX `version` field was still `{7.0.2}` (missed in the 7.0.2 →
  7.0.3 bumps); corrected to `{7.0.4}` in both language versions.

Gates: ruff / format / mypy clean; pytest **182 passed, 4 xfailed** (one new
pin: `TestR15.test_a_numpy_boolean_is_a_verdict_not_an_impostor`, which also
pins that `np.bool_(False)` stays `False` and `np.float64(1.0)` is refused).

## [7.0.3] — 2026-09-11

Response to a **fourth external review** (2026-09-11), fixed at the v7.0.2 commit
`71a5668`. This round stopped asking whether a fix had reached the main path and
asked instead **which physical node each acceptance criterion is bound to**. That
is the more fundamental question: a criterion read at the wrong node cannot be
rescued by tightening the gate around it — it makes the wrong answer *more*
convincing. Per-finding adjudication, with the reproduction command for each:
`docs/review_response_2026-09-11c.md`.

**The reference output changes in exactly one record.** `tools/results/results.json`
is byte-identical to v7.0.2 except for the `validate_ktc` KTC bandwidth entry,
whose key, measured value and verdict all move together (see *Fixed*, first item).
Baseline `65c047a9…4ecdec` → `a1ccd92f…35ac70`, verified by a full re-run.

### Fixed

- **`f_max` was read at the wrong node — a 52.8× error, registered as a limit.**
  v7.0.2 evaluated the KTC bandwidth bound against the **second stage's input
  range**: `min(adc2_v_max − G·Δ1, −adc2_v_min) / (2π·v_fs·G0·Δt)` = **2.5465 MHz**
  against a 5 MHz requirement, and put that number in the known-limits ledger.
  The premise contradicts ADR 0006: the correction term is removed in the
  **digital** domain (`quantize(v_ra) − κ·v_N`), so `κ·v_N` never occupies
  second-stage range no matter how large it is. The quantity that is actually
  band-limited is the **observation** path, whose usable swing is `ra_v_clip` and
  whose observed step is scaled by `ktc_gain_n`:
  `(ra_v_clip / ktc_gain_n) / (2π·v_fs·Δt)` = **134.4541 MHz**, which **passes**.

  So the "known limitation" was a false failure manufactured by reading the wrong
  node. The ledger entry is **deleted** — not because the requirement was relaxed,
  but because it never applied. The record key was renamed as well
  (`KTC 校正项带宽上限 f_max (满幅)` → `KTC 观测通路摆幅上限 f_max (满幅)`), so a
  regression to the old criterion cannot hide behind the old name, and the new key
  is listed in `REQUIRED_RECORDS`. The same guard in
  `experiments.stage7_headline` was rebound identically; it does not fire on the
  default configuration (134 MHz ≫ `fs/2` = 20 MHz) and is kept only so that
  parameter degradation is still caught. `tools/make_report.py` follows, and the
  historical "374 mV / 3.1% overflow / 70 dB" sentence it carried has **no
  reproduction entry** in this repository — it is now marked as such in place.
  The alternative exit (raise the limit by changing `adc2_v_min` / `Δt` / `G0`)
  would move published numbers and is an architecture decision; it was not taken.

- **The physical-pool contract was still about the shape of an object.** v7.0.2
  replaced a source-string scan with `hasattr(res, "sample_id")` and friends. That
  is progress, but it is still a statement about an object rather than a cause: an
  attribute can be added and filled with a constant, and the assertion passes. The
  contract is now a **causal relation**, and its two halves are both required —
  (i) perturbing the capacitors that converted sample *i* must move the internal
  quantity reported for *that* sample, and (ii) samples whose conversion group does
  not contain those slices must **not** move. The second half is what makes it
  causal rather than merely sensitive: a global sensitivity would satisfy (i)
  alone. Two positive controls (both passing today) establish that the relation is
  well defined and sharp *on the pool*, so the `xfail(strict=True)` cannot succeed
  for the wrong reason — wiring the pool will make it meaningful, not merely green.

- **The gate accepted a verdict it had to guess at.** `bool("False")` is `True`, and
  v7.0.2 normalised every verdict with `bool(raw)`. A single serialisation change
  therefore flipped a FAIL into a PASS, and the gate could not notice because the
  type had already been discarded. `acceptance._verdict` now returns the value
  unchanged and raises `ValueError` on anything that is not a `bool` — refusing to
  read is the only behaviour that cannot be silently wrong. Alongside it,
  `REQUIRED_RECORDS` names the records that are the *sole* evidence for a
  conclusion: `MIN_RECORDS` only counts, so "delete one, add one" satisfied it; an
  ID set does not. `GateResult.missing_required` reports the difference, and
  `Config.LEGAL_VALUES` gained the `stage1_reading` ↔ `b1` consistency check so a
  label that describes one reading and parameters set to another is refused at the
  entry rather than simulated.

- **An empty exemption ledger is not a reason to delete the machinery.** The one
  `KNOWN_LIMITS` entry was the KTC false failure above, so the ledger is now empty
  — and v7.0.2's test asserted the ledger must be non-empty, which had turned into
  a demand to keep a false limitation alive in order to keep a test green. That
  assertion is rewritten, not satisfied: the ledger's behaviour is now pinned
  directly against synthetic input (a registered exemption moves a failure out of
  `failures` and is printed; an exemption that stops failing is a `stale` error;
  a blank reason raises), so every guarantee survives without needing a fake limit
  to demonstrate it. Deleting the mechanism instead would have brought the next
  real limitation back as a hard-coded whitelist.

### Known, not fixed — deliberately

- **`dem_mode` is bound at one entry out of three.** v7.0.2 routed all three
  runners through `scheduler.make_scheduler`, which selects `Scheduler` for
  `"rotate"` and `ShuffledScheduler` for `"permute"` and refuses a caller-supplied
  scheduler that contradicts the config. The binding is real but it binds the
  **name**: `run_pipeline` consumes the per-cycle `(conv, acq)` groups from
  `reserve_dual`, which `ShuffledScheduler` *does* override — measured, the output
  differs between modes. `run_sim` and `run_sim_split` call only `reserve`, which
  `ShuffledScheduler` inherits from the base class, so `dem_mode="permute"` still
  yields ping-pong slice groups there (measured: outputs bit-identical). Closing it
  means choosing *which* shuffle, and the causal one is the R1 fix that moves
  published stage-19 numbers — a physical main-path change that deserves its own
  decision and its own before/after table, not a free ride on a switch that was
  supposed to be cosmetic. Tracked by `xfail(strict=True)` in
  `TestR10DemModeIsWired`, and stated in `make_scheduler`'s own docstring.

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
