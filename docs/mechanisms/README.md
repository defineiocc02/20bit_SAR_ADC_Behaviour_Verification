# 原理、来源核对与模型实验

这里按机制检索原文页、行为实现及历史对照。v21 的工程取舍以 [当前合同](../CONTRACT_CURRENT.md) 为入口，已运行证据见 [状态页](../STATUS.md)。从 [机理与验证图册](../figures/20261117/README.md) 可以沿片组、DEM、扰动和重构读完整路径。

| 要查的问题 | 阅读入口 | 数据与适用范围 |
| --- | --- | --- |
| 三处分路扰动如何对应 | [Fig.19 分路](dither_fig19.md)、[Fig.17/19 位段判读](fig19_fig17_decode.md) | 一并看原图符号和工程选取的符号。行为实验的减法通路与 v2.1 的加法合同分别解释。 |
| DEM 的列、保留输入和控制门 | [Fig.11 机理](dem_fig11.md)、[Fig.19 信号门](fig19_shuffle_gate.md)、[Fig.20 跨片 MUX](fig20_cross_slice_mux.md) | `Fig.11` 页含更早的几何和性能对照。`362` 与 `MX1` 的纠正以 [判读勘误](source_reconciliation.md) 为准。 |
| 分离量化器及余差 DAC | [US10516408B2 核对记录](patent09_reconciliation.md) | 逐页来源与适用范围。v21 的跟随与归属示意见图册 M00–M01。 |
| 保留列、AUX、参考缓冲 | [判据模块交付](p1_gaps_20261113.md)、[Fig.5 参考缓冲](reference_fig5.md) | 模块判据和片段级研究。接入及物理验证沿各项限制追溯。 |
| 事件身份与连续性 | [事务 trace v2](transaction_trace_v2.md)、[单笔记录](data/one_sample_trace.json) | 区分交易的主键、阶段和数据生成条件。 |
| 模型假设的灵敏度 | [公开指标与失配假设对照](paper_metrics_calibration.md) | 历史行为级参数反解，适用范围限该轮模拟配置。 |

[原件核对总表](source_crosscheck_20261112.md)汇集 T01–T10 的依据。[后续勘误](source_reconciliation.md)修订了保留输入、信号门和扰动位数等读法。`figures/` 保存专题引用的来源图，行为结果与数值在各自图册或数据文件中说明。

新增专题沿用 [来源分级](../CONTRACT_CURRENT.md)，记录来源图号和页码、事件顺序、可定位实现、一笔数据、同条件对照及实现缺口。读图发现勘误时在专题与勘误索引互链，原实验继续按原条件解释。
