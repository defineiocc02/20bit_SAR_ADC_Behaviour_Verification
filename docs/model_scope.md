# Model scope — what this model can and cannot support

> **Read this before quoting a number from this repository.**
> Every claim below is either backed by a test in `tests/`, or it is marked as
> out of scope. If a statement about the target chip is not in the "can support"
> column, this model does not support it.

The audit's deepest finding was not any single wrong number: it was that the
previous release did not distinguish *demonstrating that a mechanism is
compatible with a published result* from *predicting that result*. This document
draws that line explicitly.

---

## 1. Evidence grades, restated as claims

| Claim type | Requires | Verdict |
|:---|:---|:---|
| "The paper says X" | `DISCLOSED` parameter | ✅ quotable |
| "X follows from the disclosed values" | `DERIVED`, derivation shown | ✅ quotable with the algebra |
| "X is consistent with the published figure" | `FITTED` parameter | ⚠️ consistency check only |
| "If X, then Y" (sensitivity) | `ASSUMED` parameter | ⚠️ conditional statement only |
| "The chip achieves X" (given our model) | — | ❌ never, unless X is `DISCLOSED` |
| "The yield is X%" | PDK data | ❌ not available |

## 2. ✅ What the model can support

1. **Structural / topological conclusions under a stated reading.** E.g. with the
   `paper_consistent` reading (b1 = 7, 2b dither range) the DAC must provide
   ≥ 2⁹ = 512 levels; the segmented 64×8 topology does, and the input-referred
   full-scale shrink is ≈ 1.7 %. Verified by
   `tests/integration/test_pipeline_equivalence.py::TestSADCThresholdConsistency`
   and the `validate()` checks.
2. **Error-budget attribution between mechanisms.** With every non-ideality off,
   the residual error equals the backend quantisation referred to the input
   (`test_error_floor_is_the_backend_quantisation`). Turning one mechanism on at
   a time attributes the delta to it.
3. **Bit-exact equivalence of two independent implementations.** `pipeline.py`
   and `sim_split.py` agree bit-for-bit with non-idealities off
   (`test_degenerate_equivalence_is_bitwise`) — the strongest available check
   that the shared interface is interpreted identically.
4. **Reproducibility.** Same seed ⇒ identical output, asserted for both chains and
   enforced by a source scan for global RNG use.
5. **Cross-cycle causality *of the scheduling policy*.** Under the default A/B
   ping-pong policy every converting slice holds a charge it actually acquired —
   8191/8191 transitions, mean coverage 8.000/8
   (`tests/audit/test_audit_findings.py::TestF2SliceCausality`), and the same
   holds for `PhysicalSlicePool.shuffle_causal`. **Qualification (2026-09-11):**
   this is a property of a *policy*, and two things it does not cover are open:

   - the policy actually used by the stage-19 shuffling study is
     `ShuffledScheduler`, which redraws an independent permutation per cycle and
     therefore satisfies `conv[n] = acq[n-1]` on **0/8191** transitions (mean
     coverage 3.547/8, against the 8·8/18 = 3.556 expectation for an independent
     draw). The shuffled-suppression numbers must not be read as a property of
     the architecture until this is fixed.
   - `dem_mode` reaches **one** of the three entry points. `run_pipeline`
     consumes `reserve_dual`, which `ShuffledScheduler` overrides, so `"permute"`
     really does change its output; `run_sim` and `run_sim_split` consume
     `reserve`, which `ShuffledScheduler` inherits unchanged, so there `"permute"`
     still yields ping-pong slice groups. Stated identically in
     `scheduler.make_scheduler`'s docstring. (**2026-09-11, fourth review:** the
     wiring was called closed after the second review; it is 1/3 closed.)
   - in **neither** path are the *physical* capacitors bound to the sample:
     `c_sig` is a constant vector and `SplitDAC.evaluate_physical(k_eq, sid)`
     receives no conversion group, so a sample cannot be attributed to the slices
     that converted it. `PhysicalSlicePool` is not referenced by any runner or
     experiment.

   Tracked by `tests/audit/test_review_contracts.py::TestR1SampleOwnership`,
   `::TestR2PhysicalPoolOnMainPath` and `::TestR10DemModeIsWired` (all
   `xfail(strict=True)`); see `docs/review_response_2026-09-11.md` §2.1,
   `docs/review_response_2026-09-11b.md` §2.5 and
   `docs/review_response_2026-09-11c.md` §4.
6. **Mechanism compatibility with the disclosed numbers.** The disclosed 2b dither
   range enhancement is reproduced as a *derived* quantity
   (`units_per_lsb1 = 4`), not fitted; the auto-zero cost and the ADC2 dynamic
   bandwidth are checked for the sign and order of the disclosed dB figures
   (`stage22`), not tuned to hit them.
7. **Sensitivity trends.** e.g. DEM's net benefit as a function of the mismatch
   variance split, and the fact that a unit-level-only split `(0,0,1)` is an
   optimistic bound, not a design point.
8. **Segmented-vs-unary trade-offs at equal total capacitance.** The Pelgrom
   area law is applied per unit, so the "larger units ⇒ smaller sigma" benefit is
   real in the model, and shrinking capacitance pays twice (kT/C and mismatch).
9. **Attribution between ADC-internal and whole-chain error.** `SimResult`
   reports `err` (the design target), `err_to_x1`/`err_to_x2` (the two fixed
   sampling instants) and `err_vs_clean` (the whole chain, against the
   pre-driver-noise input). With `driver_noise_rms = 0` the last two coincide
   bit for bit; with 1 mV of driver noise `err` reads 0.99 µV while
   `err_vs_clean` reads 992 µV. Mixing them is what made v6.1 under-report the
   chain error by three orders of magnitude
   (`tests/regression/test_error_reference.py`).

## 3. ⚠️ Statements that require an explicit conditional

| Subject | Condition |
|:---|:---|
| Absolute SNDR/DR values | rest on `mismatch_sigma0 = 100 ppm` (`FITTED`) |
| Noise floor | the RA noise term is reverse-solved from the target DR (`FITTED`, an anchor) — the model shows the budget is *satisfiable*, not that the circuit attains it |
| Anything at the assumed PDK sigma (1117 ppm) | `ASSUMED` area law; use for calibration-pressure arguments only |
| Dynamic-error magnitudes | every `dyn_*` parameter is `ASSUMED`; use the shapes and trends, not the values |
| Interleaving spur levels | skew / offset / bandwidth spreads are `ASSUMED`; the *positions* (f_S/2, f_S/2 ± f_IN) are structural and usable |
| KTC observer performance | `RESEARCH_EXTENSION` — our design, not the paper's; **and an upper bound**: the observation voltage `v_N` is subtracted in the digital domain as a float array with no modelled quantiser, coding or latency, so the gain quoted is what an *ideal digital observation read-out* would give (see §4) |
| Capacitor-shrinking study | scaling a behavioural capacitance, not a layout |

## 4. ❌ What the model cannot support

1. **A 20-bit output-code-domain claim.** `n_bits_target` only defines the LSB
   reference. `test_n_bits_target_does_not_change_the_analog_output` asserts that
   the simulator's output is *identical* for `n_bits_target = 20` and `18` — so
   no code-density, DNL, or ENOB-from-codes statement can come from this model.
2. **A code-density DNL/INL measurement.** `metrics.static_test` is an averaged
   mean-error curve over a DC ramp; it is a first-order INL proxy. Its docstring
   says so, and `TestF8EvidenceScope` asserts that the docstring does.
3. **A yield number.** Monte-Carlo runs are sensitivity studies over an assumed
   sigma. The per-chip samples are returned (`mc_pdk_sigma(..., )["sndr_per_chip"]`)
   and a test forbids drawing a histogram from a resampled `(mean, sigma)` pair —
   which is exactly what the previous release did.
4. **Any statement about the published chip's measured silicon.** The model is
   calibrated against *published figures*, so agreement is a compatibility check.
   Reproducing 94.6 dB DR is an identity of the anchor construction, not evidence.
5. **1/f behaviour in the main record.** At `fs = 40 MHz`, `N = 2¹⁵` the FFT bin is
   1220 Hz; a 40 Hz corner is below the record. The main record carries no
   resolvable flicker; the generator is validated separately at 4 kHz
   (`TestF9FlickerBandLimit`). Test `test_main_record_claim_is_not_silently_made`
   pins this arithmetic so a change to `fs`/`N` forces this document to be
   revisited.
6. **Area, power, or transistor sizing.** No device models, no layout
   parasitics, no current budget.
7. **Threshold-level comparator noise / metastability.** Not modelled.
8. **The dynamic reference buffer and shared RA as a circuit.** Their *effects*
   (auto-zero cost, dynamic sampling bandwidth) are modelled as behavioural
   factors with disclosed magnitudes; the circuit that produces them is not
   designed here.
9. **Why the published reference-settling scheme works — or why an integrating
   RA would not.** Audit finding A06. Since **v7.0.8** the *mechanism level*
   is answered by `ref_track.py` (M13): a behavioural charge-threshold loop
   (limited top-up current + first-order servo + S1 bleed) that settles in
   **6 conversion cycles** (disclosed "5 or 6"), whose external top-up charge
   decays by orders of magnitude, and whose coarse trials tolerate mV-level
   reference error inside the Δ1/2 self-healing window while the final
   trials see sub-nV error (>20 b equivalent). **Still not supported** is the
   *main-path* level: no coarse/fine reference MUX event inside
   `pipeline.py`, no RA tracking equation on a time-varying reference, and no
   finite-bandwidth / slew / cross-sample memory in the reference path —
   `ra.py` remains an instantaneous gain + noise + clip stage. Consequently
   the main-path model still cannot explain, quantify or support:
   - why the published part reaches its accuracy despite a reference that is only
     ~15b accurate when the RA starts and ~20b only at ~65 % of the RA phase;
   - why an integrating RA is unsuitable under those conditions;
   - the 50 / 69 mW figures, or any signal-chain FoM improvement.

   Mechanism-level evidence: `reproduction_results.md` M13 (43 dedicated
   tests repo-wide for [12]–[14], doctests included). Main-path integration
   is gated behind R1/R2 (physical pool on the main path). See
   `docs/audit_response.md` §A06.
10. **Cross-frequency mismatch sensitivity / full-code static sweeps.** Listed in
    the audit's P1 recommendations; not implemented.
11. **The paper's three-dimensional DEM mechanism.** The digital core counts
    512 states (`N_DEM_STATES = 8·8·8`), but the DAC's two rotations are both
    driven by that single `sid`, so the joint physical permutation space is
    **64** (period lcm(64,8), each main rotation paired with exactly one sub
    rotation — pinned by `TestR16DemPermutationSpace`). What the 8/18 slice
    selection, the lateral/vertical shuffling and the binary-to-unary
    bridging of [11] do is a different mechanism; no DEM sweep in this repo
    may be quoted as that mechanism's quantitative benefit. Matches neither
    the slice-selection space nor the shuffler structure of the published
    part.
12. **[12] tracking and [13] auxiliary input — main-path level.** Since
    **v7.0.8** both mechanisms exist as *standalone mechanism-level models*
    (`interleave_tracking.py`, M11: tracking-phase charge accounting with the
    `v_hold[i] = x[i−1]` identity; `aux_input.py`, M12: auxiliary-path
    settling scaling — R_f may grow by (C_f+C_pg)/C_f, minimum required
    filter bandwidth and driver noise drop accordingly). **But neither is
    wired into the pipeline main path**: there is no pre-conversion tracking
    state update driven by another ADC's result inside `pipeline.py`, and no
    second (auxiliary) input port with its own charge accounting in the
    sampling path — so main-path input-drive or interleaving sizing
    conclusions cannot be derived from this model (integration is gated
    behind R1/R2; `reproduction_results.md` N2 records the re-scoped gap).
13. **KTC cancellation as a designable circuit.** The `f_max` check
    (v7.0.3-corrected, 134.5 MHz at the observation node) is a **swing**
    criterion only. "Does not exceed swing" and "settles within the
    Δt = Ts/256 ≈ 97.66 ps extraction window" are different requirements —
    a one-pole settle-to-0.1 % would demand f_BW ≥ ln(1000)/(2π·Δt) ≈
    11.26 GHz (conditional design-pressure estimate, fifth review §11).
    Additionally `noise_phase.kappa_optimal(sigma_eN>0)` now minimises the
    *total* residual (sampling + observer noise); deepest cancellation of the
    sampling noise alone is not the same objective. The circuit realisation
    (extraction bandwidth, observer quantisation, timing) remains open.

## 5. Reading/configuration matrix

Three readings of the ambiguous "9b in the first stage" are available.
`paper_consistent()` is the **default**, not a proven unique answer: it is the
only reading that is self-consistent *under the additional assumption* that the
disclosed 2b dither-range enhancement is the same thing as the extra decisions
taken against an unknown input (equivalently, that `b1 + b_enh = 9`). ADR 0003
§1 explicitly rejects conflating codeword range with decision information — and
§2 of the same ADR then performs exactly that identification to solve for
`b1 = 7`. `provenance.PARAM_GRADES["b1"]` grades it `ASSUMED`
("ARCHITECTURAL READING"); the prose here previously called it the "唯一" (only)
consistent reading, which claimed more than the evidence supports (external
review 2026-09-11, R3). Use `paper_consistent()` for new work, and quote it as a
stated reading.

| Constructor | `b1` | dither range | code word | ADC2 | Satisfies the disclosures? |
|:---|:--:|:--:|:--:|:--:|:---|
| `Config.paper_consistent()` (default) | 7 | 2b | 9b | 14b, [−0.15, 1.65] V | ✅ *given* the codeword-range = decision-bits assumption |
| `Config(b1=9, dither_enhancement_bits=0, stage1_reading="paper_literal", adc2_n_bits=14, adc2_v_min=-0.0375, adc2_v_max=0.4125, ra_v_clip=0.45)` | 9 | 0b | 9b | 14b, [−0.0375, 0.4125] V | ❌ contradicts the 2b disclosure |
| `Config.legacy_v61()` | 6 | 3b | 9b | 15b, [−0.3, 3.3] V | ❌ 3b ≠ disclosed 2b; kept for reproducing old results |

`b1` is the only knob: `units_per_lsb1 = n_units_sig // 2**b1` and the range
enhancement are *derived*. `validate()` **fails** any configuration whose
stage-1 reading the DAC cannot express (`2**b1 ≤ DAC levels`) — instead of
silently pushing the residue out of the ADC2 window — and fails any
configuration whose declared `dither_enhancement_bits` disagrees with the grid
(`≠ log2(DAC levels / 2**b1)`). v6.1 had neither check: `b1 = 10` on a
512-level DAC simulated into volt-level garbage with no complaint.

## 6. Where the model is deliberately pessimistic / optimistic

| Aspect | Direction | Why |
|:---|:---|:---|
| Mismatch split `(0.15, 0.35, 0.50)` | neutral by default | `(0,0,1)`, the previous assumption, is an optimistic bound |
| DEM efficacy | optimistic if the split is truly unit-only | spatial correlation beyond the 8-unit group model is not represented |
| `slice_bw_spread` / skew defaults | 0 (ideal) | no published per-slice numbers; enable them explicitly for a study |
| KTC observer self-noise | 0 by default | an ideal observer is unrealistic; `ktc_noise_n > 0` is the honest setting |
| KTC correction bandwidth `f_max` | **covers the disclosed band** — and the previous "does not cover it" line was a **wrong-node reading, not a limitation** | **Correction (2026-09-11, fourth review).** This row used to report `f_max = 2.5465 MHz` against the disclosed DC–5 MHz band, computed from the second stage's input range (`min(adc2_v_max − G0·Δ1, −adc2_v_min) = 0.15 V`). That premise contradicts ADR 0006: the correction is subtracted in the **digital** domain (`quantize(v_ra) − κ·v_N`), so `κ·v_N` never occupies second-stage range. Read at the node it actually constrains — the observation path, swing `ra_v_clip`, observed step scaled by `ktc_gain_n` — the same configuration gives `f_max = (ra_v_clip / ktc_gain_n) / (2π·v_fs·Δt) = 134.4541 MHz`, a factor of **52.8** higher and comfortably above 5 MHz. `Config.validate()` reports it as `PASS: True` under the key `KTC 观测通路摆幅上限 f_max (满幅)`. The `KNOWN_LIMITS` entry that recorded the 2.5465 MHz figure is deleted (it never applied), and the key is now in `REQUIRED_RECORDS` so the criterion cannot silently return to the second-stage node. Anchored by `tests/audit/test_review_contracts.py::TestR12KtcMaxUsesTheObservedNode`, which pins both numbers so the two nodes stay distinguishable. What remains qualified is unchanged: KTC results here answer "how much could correlated-noise cancellation buy under an idealised read-out", not "what is the net system benefit with a real observation channel" — `v_N` is a float array with no modelled quantiser, coding or latency (§4) |
| Flicker in the main record | pessimistic (absent) | the corner is below the record band. **Correction (2026-09-11):** this line used to add "not an oversight" — that was wrong. `flicker_series` *does* try to back-fill the unresolvable sub-`f_min` power as drift, but its guard `if f_corner <= f_min or f_min <= f_low: return x` returns early in exactly that case, so the back-fill is unreachable (measured: 0/32768 non-zero samples at fs=40 MHz, n=32768, fc=40 Hz, t_obs=10 s). Tracked by `tests/audit/test_review_contracts.py::TestR6FlickerDriftBackfill` (forced xfail); see `docs/review_response_2026-09-11.md` §2.8 |
| Input-settling τ (aggregate single-node RC) | "conservative" **only when the branch switch dominates** — **Qualification (2026-09-11, fifth review §5.2).** The old claim "per-slice τ is ~8× smaller, so the aggregate bound is conservative" is true for the `R_on·C_slice` term only. In a star network with common source impedance, the common-mode τ is `R_s·C_total + R_on·C_slice` — the `R_s·C_load` term does not shrink with the partition (example: N=8, 20.5 pF, 30 Ω, 20 Ω → aggregate 1.025 ns vs common-mode 0.666 ns, only 1.54×). When `R_s` dominates the two readings converge and the "conservative" label fails. `pipeline.py`'s header and `dynamics.py`'s applicability note now state this; pinned by `TestR17CommonModeTau` |

## 7. Change control for this document

If you change any of the following, revisit the sections named:

| Change | Section to revisit |
|:---|:---|
| `fs` or the default record length `N` | §4.5 (flicker resolvability) |
| any `Config.paper_consistent()` derivation | §5 table, §2.1 |
| a new `ASSUMED`/`FITTED` parameter | §3 table, §2.6 |
| the Monte-Carlo API | §4.3 |
| the static-test definition | §4.2 |
