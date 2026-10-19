# 无时钟起点的只读核查（Vivado 2018.3）

`inspect_clockless_paths.tcl` 是诊断脚本，不能代替 STA，也不会加入 false path、multicycle、case analysis 或修改时钟。它只检查当前已打开的、由调用者确认功能及来源有效的 DCP。成功执行的状态名是 `QUERY_COMPLETE`，覆盖程度始终明确为 `PARTIAL_BOUNDED_PATH_SAMPLING`，不是 `PASS`。

## 本次待解释现象

最初无效全顶层综合网表的 `timing_summary.rpt` 同时列出：

- `check_timing` 的 no_clock、constant_clock、unconstrained_internal_endpoints 等项目为 0；
- `(none) -> core_clk` 存在 max/min 各 64 个端点；
- 代表性起点为 `op3_w__7/ACOUT[29]` 与 `op3_w__11/ACOUT[29]`，slack 为 `inf`。

这几项并不等价：寄存器时钟检查与内部计时图起点检查的对象不同。旧 EDIF 已发现部分 DSP A 输入路径的 `A_INPUT=DIRECT, AREG=0, ACASCREG=0`，且 `A[29]` 连接 GND，提示可能是常量级联输出被计时图保留为起点。但旧顶层归约树本身存在常量化缺陷，因此不能把两个已知实例的结果推广到新有效网表，亦不能凭这两个实例宣布所有 64 个端点都无问题。

旧 `baseline_followup_query.tcl` 的主要覆盖不足是硬编码两个 DSP 名称、没有枚举来源集合和核对端点总计数。不能仅按 UG835 的位置参数写法认定其 `all_fanin -to` / `all_fanout -from` 非法；同版本工具的小单元实际查询已经使用过 `-from`。新脚本采用文档中的末尾位置参数写法，只是写法选择。

## 一次运行方式

在完成实现、当前有效 DCP 仍打开时：

```tcl
source synth/inspect_clockless_paths.tcl
clockless_audit::run C:/path/to/new_clockless_query
```

也可在单独的批处理驱动中打开已冻结的 DCP 后 source：

```tcl
open_checkpoint C:/path/to/validated_post_route.dcp
source C:/path/to/inspect_clockless_paths.tcl
clockless_audit::run C:/path/to/new_clockless_query
close_design
exit 0
```

结果目录必须不存在。调用者负责记录 DCP SHA、源码 SHA、Vivado 版本及本脚本 SHA。不要在后台已有 Vivado 任务时另开实例。脚本不会读取或重写原 DCP 文件；`open_checkpoint` 是调用者行为，查询运行期间不更改当前设计。

## 查询边界与资源上限

1. 保存原始 `timing_summary.rpt`、`check_timing.rpt`、`exceptions.rpt` 和 `clocks.rpt`，保留工具报告的全部类别及总计数。
2. 对全部寄存器数据端点和全部输出端口分别作一次 bulk `all_fanin -flat -startpoints_only -trace_arcs timing`。仅把起点名字合并写入 `bulk_startpoints.txt`；不逐个查询约六万寄存器，不建立逐路径全图。
3. max/min 各进行一次 `get_timing_paths -sort_by group -max_paths 256 -nworst 1`。保存每条样本的源、终点、源/终点时钟、slack、数据延迟。缺少任何一端时钟的样本另保存完整路径报告。
4. 候选来源是：抽样路径发现的无时钟源，以及 bulk 起点集合中的 DSP48E1 ACOUT 引脚。最多追踪 256 个候选；超过限额的名字仍逐项列出 `NOT_INSPECTED`。
5. 只追踪候选的实际选中输入和唯一物理驱动链，深度上限 64。不会分析整个 MAC 的所有组合锥，不对六万个寄存器反复调用时序分析。

`-sort_by group` 使 `max_paths` 作用于各组；`-nworst 1` 仍只能给出每个端点的一个候选路径，可能漏掉通向相同端点的其他起点。`group_counts.tsv` 明确记录每组采样数及是否达到上限。bulk 集合只覆盖所选寄存器数据和输出端点，不承诺覆盖异步控制等所有时序检查类型。即使某组不足 256 个样本，也不能仅凭这一点宣称起点全覆盖。UG835 2018.3 第 913–916 页记录这些选项；不存在需要使用的 `-unconstrained` 正向开关，脚本保留默认包含未约束路径的行为。[官方 UG835](https://docs.amd.com/api/khub/documents/wNJReNjblikQ29AHV1THwg/content)

## 常量分类的严格条件

脚本仅给出两种已识别常量：`CONSTANT_ZERO` 和 `CONSTANT_ONE`。证明链必须结束于真实的 GND 或 VCC primitive，连接必须有且仅有一个叶输出驱动，且不能有外部输入端口驱动。网名包含 GND、fan-in 为空或引脚名是 ACOUT，都不构成常量证明。

对 DSP48E1 `ACOUT[n]`，同时要求 `AREG=0` 与 `ACASCREG=0`；根据实际 `A_INPUT` 属性选择 `A[n]`（DIRECT）或 `ACIN[n]`（CASCADE）继续追踪。属性缺失、寄存路径、多驱动、外部驱动、未知 primitive、断网、循环和深度超限均保留为 `UNRESOLVED`。`constant_connections.txt` 保存每步引脚、网络、驱动及被访问 DSP 的完整属性，并记录 CLK 网络与工具识别的时钟。

这些条件对应 DSP48E1 已公开的 A 输入选择与 ACOUT 寄存配置。它们只能证明指定的 ACOUT 位；不能据此断言整个 DSP 的 P、M、B 等路径不需要时钟，也不能替代对所有动态输入的正常时序检查。[UG479 DSP48E1 属性表](https://docs.amd.com/api/khub/documents/gu4oRPFEh_Pm2uaAlfY6Kg/content)、[AMD DSP48E1 primitive 说明](https://docs.amd.com/r/2023.2-English/ug953-vivado-7series-libraries/DSP48E1)

命令或属性查询真正报错会生成 `STATUS=FAILED`、保存错误堆栈并向调用者重新抛出；不会把失败转为“没有起点”或“常量”。已知属性不存在只返回 `UNAVAILABLE`，分类保持未解决。

## 实跑后允许形成的结论

需要同时核对：

- 原始 summary 的每个未约束类别、max/min 总端点计数；
- `sampled_paths.tsv` 中去重后的每模式端点和源列表，是否覆盖该类别的报告计数；
- `bulk_startpoints.txt` 是否还有相关但未解释的内部起点；
- `source_classification.tsv` 中每个实际无时钟起点的来源、常量证明和未解决项；
- 当前 checkpoint 的 no_clock、constant_clock、输入/输出延迟、异常约束等诊断。

若新网表中的确只有所列常量 ACOUT 位触发该类别，且源和端点计数能逐项对应，可以形成“这些已列出的源是常量级联路径，不是该 A 路径缺失寄存时钟”的局部结论。其余类别、遗漏起点、达到限额、未识别源或计数无法对齐必须单列为未解决，不能泛化成“全部未约束路径已关闭”。本脚本不自动作此签核判断，也不为常量源创建时序例外。

## 当前验证状态

`tests/unit/test_clockless_inspection.py` 的 9 项 Tcl mock 检查了只读命令边界、两次 bulk/两次时序采样、GND/VCC 与选中级联、寄存/多驱动/动态/断网拒绝归常量、失败重新抛出及限额/空采样保持 PARTIAL。它们只验证脚本控制逻辑；截至此文件建立时，尚未对新有效 Vivado DCP 执行本查询。
