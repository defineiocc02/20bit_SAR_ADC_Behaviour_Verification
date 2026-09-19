# Architecture decision records

Identifiers are unique. ADR 0001–0008 retain their historical numbering.
The physical-closure records use 0009–0015; references in current documentation
follow these identifiers. Historical decisions can be superseded by later records.

- [架构决策记录（ADR）](0001-adr-conventions.md)
- [ADR 0002 — 冻结 v6.1 基线，另建仓库重构](0002-frozen-baseline-and-repo-split.md)
- [ADR 0003 — 第一级分辨率的架构读数](0003-stage-1-resolution.md)
- [ADR 0004 — 第一级量化器栅格的唯一真相源](0004-single-source-of-truth-quantiser-grid.md)
- [ADR 0005 — 交织 slice 必须是有记忆的物理对象](0005-physical-slice-pool.md)
- [ADR 0006 — 观测器校正必须在数字域扣除](0006-observer-correction-is-digital.md)
- [ADR 0007 — 参数来源分级升级为运行时机制](0007-provenance-as-a-runtime-value.md)
- [ADR 0008 — 斜率用精确/谱导数，不用 `np.gradient`](0008-exact-slope-for-skew.md)
- [ADR 0009 — Decision resolution and dither range are independent](0009-independent-decisions-and-dither.md)
- [ADR 0010: Continuous tracking through a shared input network](0010-continuous-input-network.md)
- [ADR 0011: One time axis for reference recovery, RA and ADC2](0011-joint-reference-ra-adc2.md)
- [ADR 0012: Causal pretracking and auxiliary input in the physical loop](0012-causal-input-assistance.md)
- [ADR 0013: Noisy, identifiable and frozen split-unit calibration](0013-noisy-identifiable-unit-calibration.md)
- [ADR 0014: integer split-ADC output contract](0014-fixed-point-output-contract.md)
- [ADR 0015: spectral power, harmonic rank and low-frequency state](0015-spectral-density-and-low-frequency-state.md)
- [ADR 0016: Complete RTL configuration epochs and sampling dither](0016-rtl-configuration-and-dither.md)
