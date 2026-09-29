# Vivado 2018.3 内部 BUFG 的 OOC 实现检查

`run_vivado_buffered_impl.tcl` 从调用者已确认功能有效、已冻结来源的 post-synthesis DCP 开始，在内存中加入一个 BUFG 后实施布局布线。不重新综合，不修改 RTL、原始 DCP 或原来的 `run_vivado_impl.tcl`。这是单时钟 7-series FPGA 的独立实验入口；实际厂商运行尚需执行，本文的本地 mock 结果不能证明路由成功。

## 原始 API 依据

以下均为厂商 2018.3 文档，页码按 PDF 印刷页码：

- [UG835 Tcl Command Reference](https://docs.amd.com/api/khub/documents/wNJReNjblikQ29AHV1THwg/content)：`create_cell`（235–237）、`create_net`、`disconnect_net`（479–481）和 `connect_net`（198–201）允许编辑已综合或已实现的内存网表；写到新 checkpoint 才持久化。`get_nets -segments -top_net_of_hierarchical_group`（809–813）获取顶层网段；`get_pins -leaf -of_objects`（838–842）读取叶级连接；`all_registers -clock_pins`（93–95）涵盖寄存器、DSP 和 BRAM 的时钟端点。`get_nodes/get_pips -of_objects`（814、843）、`report_route_status`（1376–1379）用于实物路由查询。2018.3 版本没有较新手册中的 `report_route_status -return_nets`，本脚本不使用它。
- [UG905 Hierarchical Design](https://docs.amd.com/api/khub/documents/dUZkF9u9qQ4x4MtRgpH9aQ/content)：内部全局缓冲器需要 LOC（第 6 页）；OOC 内部 BUFG 的输出时钟使用真实全局路由，外部驱动的端口时钟则采用估计（第 8 页）；可从综合 checkpoint 继续实现而不重新综合（第 11 页）。该版明确适用于 7-series。
- [UG953 Libraries Guide](https://docs.amd.com/api/khub/documents/2_SY2Gv3tP4QoF_iUeElvQ/content)：BUFG（259–261）为单输入 I、单输出 O 的简单全局时钟缓冲器，没有新增状态、分频或门控逻辑。
- [UG912 Properties Reference](https://docs.amd.com/api/khub/documents/inpwp1PoWqJdtx8f_soQIw/content)：`ROUTE_STATUS`（339–340）是路由器提供的只读网属性；`ROUTED`、`PARTIAL`、`HIERPORT` 等状态含义不同。成功标记不能只依赖“设计有某些 routing”。

文档 PDF 及 SHA-256 保存在外部 `outputs/sar_adc_vivado_20260930/clock_bufg_candidate/primary_sources.json`，不把厂商全文纳入 Git。

## 最小 ECO 与约束

原结构为 `clk port -> 原 clk 网 -> 所有时钟负载`。ECO 仅将顶层端口从原网移除，增加 `clk port -> 新输入网 -> BUFG/I`，并把 `BUFG/O` 接回原网。原网的层级分段及全部负载连接不搬动；`disconnect_net` 不使用 `-prune`。所有连接都以对象传递，不拼接可执行命令字符串。

必须先确认只有一个来自 `clk` 端口的 `core_clk`，没有现有 BUFG/BUFR/BUFH/BUFIO/MMCM/PLL，且全部寄存器时钟端点恰好等于原网的叶级输入集合。缺失、多个驱动、其它外部端口、另一个时钟域、把 clk 当普通数据等非规范形态均停止。端口方向、真实 BUFGCTRL site 类型/名称/占用情况、库单元和 ECO 名称冲突均被检查。

新 BUFG 设置真实 `LOC`，只对该缓冲器加 `DONT_TOUCH` 以保留可审计身份。端口上若有旧 `HD.CLK_SRC` 则清除，避免把同一个内部缓冲器再建模成外部估计源。primary clock 仍定义在 `clk` 端口，沿 BUFG 传播；不在输出另建 primary/generated clock。保留 0.05 ns uncertainty，显式 `set_propagated_clock`，记录请求周期及工具实际周期（允许 1 ps 网格舍入）。不增加 false path、multicycle、时钟专用布线豁免或 case analysis。输入输出 0 ns delay 仍是实验假设。

## 证据与停止条件

1. 编辑前、编辑后保存排序去重的全部时钟端点列表，以 UTF-8/LF 编码，并记录数量和 SHA-256；集合与哈希必须相同。原 cell 集合只允许新增一个 BUFG，端口集合不能变化。
2. 保存 `post_clock_eco.dcp` 后执行 `opt_design / place_design / phys_opt_design / route_design`。后端优化可能复制或合并寄存器，因此另存 post-route 端点清单和哈希，并明确标记集合是否变化；不能用这个变化掩盖 ECO 当时的端点丢失。
3. 布线后确认唯一 BUFG、LOC、输出唯一驱动、没有外部端口直接连到输出、没有输入端口旁路。所有当前寄存器时钟端点仍必须受同一个 BUFG 输出和 `core_clk` 驱动。
4. BUFG 输出网必须同时满足 `ROUTE_STATUS=ROUTED`、实际 `get_nodes` 非空、实际 `get_pips` 非空；完整路由树、nodes、pips、clock utilization 与展开时钟路径报告均落盘。`HIERPORT` 或只看到非空路由资源均不足以通过。
5. 整体 route 完成、route error=0、DRC error=0；总体和内部 `core_clk -> core_clk` 的最差 setup/hold 路径必须存在且 slack 有限。四个 slack 均非负才记 `TIMING_MET=1`。时序失败但流程完成为退出码 3；流程/完整性失败为 1；两者不可合并为成功。
6. 原 DCP 的 SHA-256 在运行前后必须相同。Windows 使用系统 `certutil -hashfile ... SHA256`，Unix 使用 `shasum -a 256`；哈希工具失败、返回多个或缺少摘要均停止。

即使成功，状态仍分别记录 `INTERNAL_CLOCK_ROUTED=1`、`EXTERNAL_CLOCK_ROUTED=0` 和 `CONSTRAINT_COVERAGE=NOT_CERTIFIED`。外部端口到 BUFG 输入仍是 OOC 边界，板级 I/O、时钟源和未约束路径不能被宣称已签核。`check_timing`、unconstrained summary、exceptions 报告必须继续审查；原 DSP 无时钟起点问题不会被 BUFG 自动消除。功耗是 vectorless，不能冒充活动率实测。

## 执行入口

在厂商环境中，用当前已验证的 DCP、全新输出目录以及当前器件确认存在的 BUFGCTRL site：

```text
vivado -mode batch -source synth/run_vivado_buffered_impl.tcl -tclargs C:/validated/post_synth.dcp C:/new/buffered_impl 1.5625 BUFGCTRL_X0Y0
```

该 site 只是调用示例，脚本会查询器件并拒绝不存在或被占用的 site。调用者应冻结 DCP 和脚本哈希，并保存厂商完整 stdout/stderr；Windows batch 应保留 Vivado 退出码，不将 3 误记为 0。无需上传或重新综合 RTL。

本地脚本检查入口：

```text
python -m pytest -q tests/unit/test_vivado_buffered_impl.py
```

这些 mock 用小型可变连接表检验 ECO 顺序、全端点集合守恒、失败原因、哈希、边界状态和门禁，包括模拟 Windows 本地化/分隔摘要输出。它们没有调用远端、打开真实 DCP 或检验 Vivado 的物理算法；首次真实运行必须独立保留厂商报告。
