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
5. **Causality of the interleaving.** Every converting slice held a charge it
   actually acquired; 0 violations over 8192 cycles, mean coverage 8/8
   (`tests/audit/test_audit_findings.py::TestF2SliceCausality`). This is a *hard
   physical constraint*, not a modelling choice.
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
| KTC observer performance | `RESEARCH_EXTENSION` — our design, not the paper's |
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
   RA would not.** This is audit finding A06 and it is **explicitly not
   implemented**. There is no coarse/fine reference MUX event, no RA tracking
   equation on a time-varying reference, and no finite-bandwidth / slew /
   cross-sample memory in the reference path: `ra.py` remains an instantaneous
   gain + noise + clip stage. Consequently this model cannot explain, quantify or
   support:
   - why the published part reaches its accuracy despite a reference that is only
     ~15b accurate when the RA starts and ~20b only at ~65 % of the RA phase;
   - why an integrating RA is unsuitable under those conditions;
   - the 50 / 69 mW figures, or any signal-chain FoM improvement.

   This is the single largest gap between this model and the published
   architecture. See `docs/audit_response.md` §A06.
10. **Cross-frequency mismatch sensitivity / full-code static sweeps.** Listed in
    the audit's P1 recommendations; not implemented.

## 5. Reading/configuration matrix

Three readings of the ambiguous "9b in the first stage" are available; only one
satisfies both disclosures. Use `paper_consistent()` for new work.

| Constructor | `b1` | dither range | code word | ADC2 | Consistent with [00]? |
|:---|:--:|:--:|:--:|:--:|:---|
| `Config.paper_consistent()` | 7 | 2b | 9b | 14b, [−0.15, 1.65] V | ✅ with both disclosures |
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
| Flicker in the main record | pessimistic (absent) | the corner is below the record band; not an oversight |

## 7. Change control for this document

If you change any of the following, revisit the sections named:

| Change | Section to revisit |
|:---|:---|
| `fs` or the default record length `N` | §4.5 (flicker resolvability) |
| any `Config.paper_consistent()` derivation | §5 table, §2.1 |
| a new `ASSUMED`/`FITTED` parameter | §3 table, §2.6 |
| the Monte-Carlo API | §4.3 |
| the static-test definition | §4.2 |
