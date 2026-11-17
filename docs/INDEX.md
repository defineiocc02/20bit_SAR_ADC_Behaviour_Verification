# 文档索引

当前集成 ADC 为 **v21，13 位 ADC2、0.75 V 窗口**。[项目概览](../README.md)给出运行入口，[工程合同](CONTRACT_CURRENT.md)定义接口和行为，[项目状态](STATUS.md)记录结果、证据范围与缺口。

| 用途 | 从这里读 | 深入资料 |
| --- | --- | --- |
| 理解系统与模块 | [两级转换机理和行为图](figures/20261117/README.md) | [机制专题](mechanisms/README.md) · [论文与讲稿](../references/README.md) |
| 查看 v21 实现 | [RTL 导航](../rtl/README.md) | [权重配置合同](v21/CONFIG_CONTRACT.md) · [源清单说明](../flow/build/filelists/README.md) |
| 跑 Python | [快速上手](guide/QUICKSTART.md) | [模型架构](guide/ARCHITECTURE.md) · [定位问题](guide/DEBUGGING.md) · [定点约定](../src/adi_model/contracts/README.md) |
| 跑 v21 或查接口 | [v21 仿真入口](../flow/sim/README.md) · [工程合同](CONTRACT_CURRENT.md) | [运行环境](v21/I5_ENVIRONMENT.md) · [组件验证记录](../flow/verification/README.md) |
| 查综合与等价 | [审计与证据索引](audit/README.md) | [综合流程](../flow/synth/README.md) · [顶层综合记录](v21/SYNTH_STALL.md) · [旧整核 Formality 状态](v21/FORMAL_STATUS.md) |
| 查算术与历史验收 | [证明台账](v21/PROOF_LEDGER.md) | [历史 D3/D4 验收记录](ACCEPTANCE_STATUS.md) · [独立记录目录](acceptance/) · [历史源控制事件证据](verification/vcs_source_control_trace/README.md) |
| 查设计依据和扩展 | [架构决策](adr/README.md) | [CAAZ 研究](caaz/README.md) · [随机化与 CAAZ](randomization_caaz/README.md) · [DSP 子系统](../subsys/ad4080_dsp/README.md) |
| 查仓库结构与历史 | [工作树与文件归属](meta/REPOSITORY_LAYOUT.md) | [版本记录](meta/CHANGELOG.md) · [历史文档](_history/) · [已取代的审计记录](audit/_superseded/) · [历史数值与图](../tools/results/README.md) |

旧工程记录按当时的源、输入与工具环境解释。需要核对当前接口、命令或证据时，从本页上方的合同或状态入口进入。
