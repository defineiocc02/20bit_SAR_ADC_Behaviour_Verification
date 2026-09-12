# ADR 0007 — Decision resolution and dither range are independent

Status: accepted, 2026-09-12. Supersedes ADR 0003's claim that seven decision
bits plus two dither bits are the uniquely consistent reading.

## Evidence and decision

The ISSCC 2024 digest separately states first-stage nine-bit quantization and
two-bit dither-range enhancement on transfer to the RDAC. A dither word contains
known information; it cannot supply missing decision bits about the unknown
input. Therefore no universal identity `b1 + dither_bits = 9` is imposed.

`Config.paper_consistent()` retains the historical 7b grid-ratio model for
comparison. The name is compatibility terminology, not a proof of the paper's
architecture. `Config.paper_literal()` supplies a 9b candidate whose dither-port
amplitude and fine DAC grid are independent. The default `Config()` remains
historical so existing experiments retain an explicit baseline.

| Candidate | Decisions | Fine steps/decision | Dither semantics | ADC2 assumption |
|---|---:|---:|---|---|
| `paper_consistent()` | 128 | 4 | legacy grid ratio | 14b, [-0.15, 1.65] V |
| `paper_literal()` | 512 | 1 | independent dual-port ratio 4 | 12b, [-0.0375, 0.4125] V |
| `legacy_v61()` | 64 | 8 | historical granularity | 15b, [-0.30, 3.30] V |

Backend windows assume floor residue and 10% margin on each side. These values
are not the undisclosed ADC2 implementation. Explicit overrides remain visible.

## Complete-count split candidate

The historical 64+8 model addresses main counts 0..63 despite having 64 main
capacitors; its upper input range is reduced. The new complete-count candidate
uses 63 main capacitors, allowing counts 0..63, and eight sub capacitors with
beta=1/8. Its code voltage follows directly from charge conservation:

`VD = Vfs * [(2m-63) + (2s-8)/8] / 64`, `m=0..63`, `s=0..7`.

Hence `VD(k)=-Vfs+k*(2Vfs/512)`. All 512 codes are realizable and the SADC's
upper boundary reaches +Vfs. The 63+8 segmentation is an explicit model choice;
neither the paper nor DEM bit counts establish that this is the fabricated
topology. Both candidates remain available for comparison.

Area accounting includes the actual nominal bridge size, not a fictitious one
unit bridge. Sampling load, signal coefficient, noise capacitance and feedback
capacitance are independently reported/derived.

## Explicit dual-port dither contract

This is a conditional, ideal charge-injection model, not a transistor circuit
claim. Let known discrete `dQ` enter the quantizer and `dR=4*dQ` enter the stored
RDAC charge. The command offset is `(dR-dQ)/delta_RDAC` and digital correction is
`dR`. All three operations must be present:

```text
coarse = Q(x + dQ)
command = coarse * steps_per_decision + (dR-dQ)/delta_RDAC
residue = x + dR - DAC(command)
output = DAC_nominal(command) + ADC2(G*residue)/Ghat - dR
```

Discrete injections lie on the nominal RDAC grid. The ratio does not change
decision thresholds. Finite injection capacitance/bandwidth belongs to physical
extensions; the ideal port cannot be used as evidence for their power or noise.
The sampling-mask dither model remains a separate physically explicit option.

RDAC command overflow is recorded before physical saturation. Increasing dither
amplitude does not manufacture extra headroom. The full-scale DC boundary sweep,
independent bottom-plate charge sum, and all three runner port checks are in
`tests/integration/test_architecture_candidates.py`.
