# 负注入数字版：SKY130HD 工件

这些 GDS 来自冻结源码 `66b2e2e1cbec666a43b12d0d7a93e199cb8a41fd`，
后续 CI／文档修复未改变 RTL、SDC 或物理流程字节。两个控制顶层的独立上游规则 LVS 已通过；原公开规则的 FAIL 原样保留。
仍不能把这些单角／公开规则结果当作完整签核。

| 顶层 | 建立裕量 | 保持裕量 | 当前适用范围 |
| :--- | :---: | :---: | :--- |
| `dither8_top` | +0.165540 ns | +0.102391 ns | 6.25 ns、TT 25°C／1.80 V、提取后 SPEF |
| `sar20_digital_top` | +0.267818 ns | +0.046777 ns | 6.25 ns、TT 25°C／1.80 V、提取后 SPEF |

每个 ZIP 内含 GDS、综合网表、SDC、SPEF、STA、输入快照和运行清单。
下载后的全部 64 个工件均已逐项核对大小与摘要；原始文件不重签、不改写。

- [八片控制包](dither8_top.zip) · [原始清单](dither8_top_manifest.json)
- [外接重构顶层包](sar20_digital_top.zip) · [原始清单](sar20_digital_top_manifest.json)
- [八片上游LVS](dither8_top_lvs.json) · [外接重构上游LVS](sar20_digital_top_lvs.json)
- [本版成功数字CI完整工件](digital_ci_2e42bc2.zip) · [数字CI范围](digital_ci_summary.json)
- 含片上 LMS 的 `sar20_digital_lms_top`：正在独立长时作业中，未在此冒充已完成。

数字实现平台是公开 SKY130HD，不能替代未来 180 nm BCD。
当前 `PARTIAL_SIGNOFF` 的未闭合项包括整体等价、多角时序、活动功耗、PG 完整验收和 EM／IR。
技能 MoSCoW 清单结构检查通过：15 个 MUST 中 5 PASS、7 PARTIAL、3 NOT_RUN；
这是清单完整性检查，不能提升各项技术状态。
