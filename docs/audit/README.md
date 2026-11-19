# 审计与后端证据

这里保存独立审查、模块级后端结果和配套产物。当前工程进度见[复现状态](../STATUS.md)，实现参数见[工程合同](../CONTRACT_CURRENT.md)，源码路线见[RTL 导航](../../rtl/README.md)。

## 先定位证据

| 要查的问题 | 文档 |
| --- | --- |
| 负数是否污染下一笔，沿前片选会否重叠 | [Issue 10 逐项复现与修复](ISSUE_10.md) |
| 后端各模块做到哪一步，哪些结论曾更正 | [模块结果与边界](ALL_13_MODULES_FINAL.md) |
| 单模块综合用了哪些配置，怎样解读面积、时序和耗时 | [逐模块 DC 综合](SYNTH_PER_MODULE.md) · [DC 汇总原始文本](artifacts/DC_MODULE_SUMMARY.txt) |
| 数字 IP 的端口、算法与原文对应到哪一步 | [顶层与原文核对](TREE_AUDIT_REPORT.md#dither-word-audit) · [端口实测](PORT_AND_ONCHIP_AUDIT.md) · [历史算法审查](AUDIT_ALGO_VS_SOURCE.md) |
| `sched18` 模块的等价结果及网表来源 | [Formality 记录](FORMAL_SUCCEEDED.md) · [产物说明](artifacts/README.md) |
| 复查整核综合停滞与 Formality 尝试 | [整核综合记录](../v21/SYNTH_STALL.md) · [Formality 状态](../v21/FORMAL_STATUS.md) · [区分算术证明、RTL 检查与形式工具结果](../v21/PROOF_LEDGER.md) |
| 工具运行路径、远端落盘检查与清理 | [EDA 工作目录规范](REMOTE_WORKDIR_POLICY.md) |
| 工作树结构审查的输入、发现和复现 | [审查任务](../_history/restructure/TREE_AUDIT_PROMPT.md) · [审查报告](TREE_AUDIT_REPORT.md) |
| 找待办、已有源级反例和验收条件 | [独立复核待办](todo/README.md) |

生产 ADC2 为 13 位，窗口跨度 0.75 V。表内历史综合和 Formality 结果绑定各自输入。同版整核的待验事项见[项目状态](../STATUS.md)。组件的接口和证据在 [components/](components/CURRENT_INTERFACE_AND_ARITHMETIC.md)，专题评审在 [reviews/](reviews/)。

## 专项实验

| 对象 | 记录 |
| --- | --- |
| `dither_pre` / `dem_mask_v3` | [组合模块后端尝试](COMBINATIONAL_MODULE_FLOW.md) |
| `wsum_v21` | [物理实现与超时记录](WSUM_FINAL_VERDICT.md) |
| `recon_v21` | [alpha 旁路诊断](RECON_ALPHA_DIVIDER.md) |
| `recip_q60` | [布线阶段记录](RECIP_Q60_ROUTING_ANALYSIS.md) |
| `recip_rnd_v21` | [工具报错与前序放置状态](RECIP_RND_CRASH.md) |

专项实验说明实际尝试及停止位置。结论以[复核后的模块结果](ALL_13_MODULES_FINAL.md)划定的证据范围为准。单模块 Formality 记录绑定当时的 RTL、网表与配置。整核等价需要同版整核证据。物理结果中的 `router DRC` 指工具内部路由检查，电源几何、foundry DRC、LVS、多角与 IR/EM 分别追踪。

## 产物与历史

- [`artifacts/sched18_final.v`](artifacts/sched18_final.v) 和 [`artifacts/sched18.svf`](artifacts/sched18.svf) 是 `sched18` 对应后端试验的网表与综合等价指引。加载整核 RTL 时使用 [v21 源清单](../../flow/build/filelists/files_adc_v21.f)。
- [`artifacts/DC_MODULE_SUMMARY.txt`](artifacts/DC_MODULE_SUMMARY.txt) 留存当时的单模块汇总，包括 `recon_v21` 的诊断配置。层级重叠的面积不用于估算整核面积。
- [`_superseded/`](_superseded/) 保留按阶段写下的探索记录和已更正判断。复查更正原因时从[模块结果与边界](ALL_13_MODULES_FINAL.md)的更正表进入。
