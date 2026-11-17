# 早期 SMIC180 等价验证记录

这里保留的是 `digital_top` 的 SMIC180 试验。[synth_with_netlist.tcl](synth_with_netlist.tcl) 编译早期 RTL 并导出 `netlist.v`、`sdc.sdc`，原有库路径和源码搜索目录来自当时的 EDA 工作区。早期顶层已收在 [rtl/_legacy/tops/](../../../rtl/_legacy/tops/)，重做时须把当轮库、综合网表、RTL 及约束配成同一组。原记录中的 `fm4.tcl` 为当时的临时脚本，工作树未保留该文件。

2026-11-13 的日志记载读入 RTL、库和网表成功，匹配阶段的 38 个输入均匹配，`verify` 进入计算。此处未记录最终验证结论。旧日志见 `git show 391c55a:flow/synth/fm/README.md`。

后续排查可复用两条经验。读取 SystemVerilog 时选择 `read_sverilog`；若父进程 CPU 空闲，先看 `fm_shell_exec` 子进程与日志再判定是否仍在计算。

v21 的入口在 [v21/fm_equiv.tcl](../v21/fm_equiv.tcl) 与 [check_formal.sh](../../../tools/check_formal.sh)，试验结果见 [v21 整核 Formality 状态](../../../docs/v21/FORMAL_STATUS.md) 和 [模块级证据](../../../docs/audit/README.md)。
