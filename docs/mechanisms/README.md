# 原理、来源核对与模型实验

这里按机制检索原文页、行为实现及历史对照。沿一笔生产采样查调度、DEM、扰动和出码，请读[数字 IP 算法和状态机](../algorithms/README.md)。查开关和符号读[工程合同](../CONTRACT_CURRENT.md)，查已验证的范围读[状态页](../STATUS.md)。

| 要查的问题 | 阅读入口 | 数据与适用范围 |
| --- | --- | --- |
| 三处分路扰动如何对应 | [Fig.19 分路](dither_fig19.md)、[Fig.17/19 位段判读](fig19_fig17_decode.md) | US10505561B2 图 19 写出七片实施例。生产 IP 采用八槽，按本笔实际采样开关扣回扰动。 |
| DEM 的列、保留输入和控制门 | [Fig.11 机理](dem_fig11.md)、[Fig.19 信号门](fig19_shuffle_gate.md)、[Fig.20 跨片 MUX](fig20_cross_slice_mux.md) | `Fig.11` 页含更早的几何和性能对照。`362` 与 `MX1` 的纠正以 [判读勘误](source_reconciliation.md) 为准。 |
| 分离量化器及余差 DAC | [US10516408B2 核对记录](patent09_reconciliation.md) | 逐页来源与适用范围。生产顶层的事件与片号归属见[逐笔流程](../algorithms/digital.md#transaction)。 |
| 保留列、AUX、参考缓冲 | [判据模块交付](p1_gaps_20261113.md)、[Fig.5 参考缓冲](reference_fig5.md) | 独立模块的判据，以及旧量程实验对论文中 dither 的误引。 |
| 事件身份与连续性 | [历史实验的事务记录格式](transaction_trace_v2.md)、[单笔记录](data/one_sample_trace.json) | 区分交易的主键、阶段和数据生成条件。 |
| 模型假设的灵敏度 | [公开指标与失配假设对照](paper_metrics_calibration.md) | 历史行为级参数反解，适用范围限该轮模拟配置。 |

[原件核对总表](source_crosscheck_20261112.md)汇集 T01–T10 的依据。[后续勘误](source_reconciliation.md)修订了保留输入、信号门和扰动位数等读法。`figures/` 保存专题引用的来源图，行为结果与数值在各自图册或数据文件中说明。

新增专题沿用 [来源分级](../CONTRACT_CURRENT.md)，记录来源图号和页码、事件顺序、可定位实现、一笔数据、同条件对照及实现缺口。读图发现勘误时在专题与勘误索引互链，原实验继续按原条件解释。
