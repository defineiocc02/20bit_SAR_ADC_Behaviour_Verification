# 单模块后端证据文件

这组文件随 `d024928` 轮次留存。`sched18` 的等价范围是该轮冻结 RTL 与无时钟门控的布线网表。Formality 报告列出 234 个比较点通过、0 个失败。查看 [原运行记录](../FORMAL_SUCCEEDED.md) 和 [审计索引](../README.md)。

| 文件 | 用途 |
| --- | --- |
| [sched18_final.v](sched18_final.v) | `sched18` 布线后网表，189,986 字节 |
| [sched18.svf](sched18.svf) | 综合映射信息，供当时的 Formality 比较使用，24,576 字节 |
| [DC_MODULE_SUMMARY.txt](DC_MODULE_SUMMARY.txt) | 独立 DC 综合汇总。各行设计与约束见 [逐模块综合记录](../SYNTH_PER_MODULE.md)。 |

当次 `sched18` 使用 TSMC 28HPC+ `ssg0p81vm40c` 单角及 25 ns 约束。报告的标准单元面积为 778.32 µm²，布线 14,899 条，router DRC 为 0，slack 为 +14.93 ns。技术 LEF 的层方向和 AP 层参数包含工程推导。带时钟门控的先前网表曾报 20 个 failing compare point。

当次 DEF、GDS、远端 Formality 启动脚本和原始日志未随本目录入库。复核完整运行需取得同轮工具、库、输入及脚本。现存比较覆盖单模块功能等价，物理范围以当次工具报告为限。电源布线、foundry DRC、LVS、多角和 IR/EM 状态见 [后端汇总](../ALL_13_MODULES_FINAL.md)。
