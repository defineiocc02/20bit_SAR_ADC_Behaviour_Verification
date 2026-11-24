# 文档索引

查设计规则读[工程合同](CONTRACT_CURRENT.md)，查已经验证到哪一步读[项目状态](STATUS.md)。新读者可以从[数字顶层框图](algorithms/figures/digital-transaction-flow.svg)沿一笔采样找到对应的模块。

| 要回答的问题 | 入口 |
| --- | --- |
| 从哪个版本开始复现、引用哪个提交？ | [本次结构与唯一性实测](audit/TREE_AUDIT_REPORT.md#current-tree-review) · [分支、软件包与发布记录](meta/CHANGELOG.md) · [软件引用](meta/CITATION.cff) |
| 调度、扰动与 DEM 怎样运行？ | [核心原理与代码精讲（7篇）](guide/deep_dive/README.md) · [数字 IP 图册](algorithms/README.md) · [逐模块算法和状态](algorithms/digital.md) · [顶层与实例清单](../rtl/README.md) |
| 模拟宏怎样同数字 IP 交接？ | [接口合同](CONTRACT_CURRENT.md) · [物理接口与重构方程](v21/PHYSICAL_INTERFACE.md) · [开关编码、保护码和受理沿](v21/SWITCHING_RULES.md) · [权重装载](v21/CONFIG_CONTRACT.md) |
| 有哪些数字实测、哪些属于模型估算？ | [逐项状态](STATUS.md) · [扰动注入与扣回实测](audit/TREE_AUDIT_REPORT.md#dither-word-audit) · [全码 DNL/INL 模型对照](audit/TREE_AUDIT_REPORT.md#全码静态线性度) |
| 想用自己的输入复核 | [数字仿真入口](../flow/sim/README.md) · [Python 快速上手](guide/QUICKSTART.md) · [行为模型图和试验输入](figures/behavioral/README.md) · [故障排查](guide/DEBUGGING.md) |
| 找论文、专利与工程假设 | [论文与讲稿原件](../references/README.md) · [生产算法与原文差异](audit/AUDIT_ALGO_VS_SOURCE.md) · [逐图机制对照](mechanisms/README.md) · [设计决策及适用范围](guide/decisions/README.md) |
| 查正式验证与后端记录 | [Synopsys 28HPC+ 全链条签核报告（含 40 nm 迁移清单）](synopsys_signoff/README.md) · [审计索引](audit/README.md) · [证明台账](v21/PROOF_LEDGER.md) · [模块级综合、布线与等价结果](audit/ALL_13_MODULES_FINAL.md) |
| 查独立研究或历史数据 | [CAAZ 实验](caaz/README.md) · [DSP 子系统](../rtl/ad4080_dsp/README.md) · [历史验收](_history/acceptance/) · [历史模型结果](_history/model_results/README.md) |

`algorithms/` 存放数字 IP 的可编辑图和证据清单；`figures/` 存放行为模型图与静态线性度结果。`v21/` 收接口详表及整核专题，`audit/` 收评审和工具证据，`_history/` 按各记录原有的提交和条件追溯。[完整目录与文件归属](meta/REPOSITORY_LAYOUT.md)
