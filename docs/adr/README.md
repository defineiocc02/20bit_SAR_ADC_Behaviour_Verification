# 架构决策记录（ADR）

ADR 记录决策发生时的背景、取舍和适用范围，编号沿用原有记录。阅读 v21 交付先查 [当前工程合同](../CONTRACT_CURRENT.md) 和 [状态](../STATUS.md)。独立 `sar20_digital_core` 路径的 source binding、双采集端口及 calobs v2 使用 [ABI2 接口与整数算术合同](../rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)。ADR 0018–0019、0021 保存结构接口历史，旧 748 位报文和寄存器地址按各自版本解释。

## 基线与模型约定

- [0001 ADR 约定](0001-adr-conventions.md)
- [0002 冻结 v6.1 基线与仓库重构](0002-frozen-baseline-and-repo-split.md)
- [0003 第一级分辨率](0003-stage-1-resolution.md)
- [0004 第一级量化器栅格](0004-single-source-of-truth-quantiser-grid.md)
- [0005 交织片池与物理状态](0005-physical-slice-pool.md)
- [0006 数字域观测校正](0006-observer-correction-is-digital.md)
- [0007 运行时参数来源](0007-provenance-as-a-runtime-value.md)
- [0008 精确斜率与时序偏差](0008-exact-slope-for-skew.md)

## 采样、残差与校准

- [0009 判决分辨率与扰动范围](0009-independent-decisions-and-dither.md)
- [0010 共用输入网络的连续跟踪](0010-continuous-input-network.md)
- [0011 参考恢复、RA 与 ADC2 的时间轴](0011-joint-reference-ra-adc2.md)
- [0012 因果预跟踪与 AUX](0012-causal-input-assistance.md)
- [0013 可辨识单元校准与冻结](0013-noisy-identifiable-unit-calibration.md)
- [0014 整数输出合同](0014-fixed-point-output-contract.md)
- [0015 频谱密度与低频状态](0015-spectral-density-and-low-frequency-state.md)

## 独立结构核接口沿革

- [0016 RTL 配置 epoch 与采样扰动](0016-rtl-configuration-and-dither.md)
- [0017 固定相位采集和显式 RTL 结构](0017-rtl-fixed-phase-capture-and-structure.md)
- [0018 物理校准和结构控制](0018-physical-calibration-and-structural-controls.md)，后续决策见 0021
- [0019 拟合权重的 RTL 闭环](0019-fitted-weight-rtl-closure.md)，后续决策见 0021
- [0020 共享扰动列归约](0020-shared-dither-column-reduction.md)
- [0021 结构样本合同与 RTL 观测校准](0021-structural-contract-calibration.md)
