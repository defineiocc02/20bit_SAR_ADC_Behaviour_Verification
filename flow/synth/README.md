# v21 综合、STA 与门级验证入口

| 路线 | 入口 | 结果范围 |
| --- | --- | --- |
| v21 整核 DC 与试验性 FC | [DC 综合](v21/dc_synth_v21.tcl) · [FC 整核脚本](v21/fc_rtl_to_gds.tcl) · [STA 预算](v21/sta_budget.tcl) | 目标为 `adc_digital_ip_v21`。阻塞点、当前同版网表与集成状态见 [SYNTH_STALL.md](../../docs/v21/SYNTH_STALL.md) 和 [STATUS.md](../../docs/STATUS.md)。 |
| v21 相关模块 | [单模块综合](v21/syn_module.tcl) · [sched18 流程](v21/fc_sched18.tcl) · [派生 LEF 的 sched18 试验](v21/fc_rtl_to_gds_derived.tcl) | 模块级数据按各自顶层、工艺输入与工具记录解读。包含关系、旁路与历史约束见 [审计与证据索引](../../docs/audit/README.md)。 |
| v21 Formality | [Formality Tcl](v21/fm_equiv.tcl) · [check_formal.sh](../../tools/check_formal.sh) | 需要同版 RTL、DC 网表与 SVF。整核记录见 [FORMAL_STATUS.md](../../docs/v21/FORMAL_STATUS.md)。 |

其他工艺、旧顶层和 FPGA 的结果按原有源包和约束解读。[审计与证据索引](../../docs/audit/README.md) · [历史 FPGA 记录](FPGA_SYNTHESIS_AND_STA.md)

v21 专用 Tcl 依赖 EDA 主机上的 PDK、库、许可证及暂存目录。请先在[环境说明](../../docs/v21/I5_ENVIRONMENT.md)和脚本中核对所需路径。比如 DC 脚本读取 `/tmp/ip/rtl`，Formality 还读取 DC 输出和 SVF。引用面积或裕量时同时注明源文件身份、工具、角、负载和约束。单模块综合或实验性布线的数据对应那一模块和运行设置。

## 通用本机封装

[run_synth.py](run_synth.py) 与 [run_synth.sh](run_synth.sh) 接收调用者声明的工具、顶层、时钟、库、SDC 和编译方式。它们使用 JSON 文件清单，与 [RTL 的 .f 清单](../build/filelists/README.md) 用途有别。JSON 必须提供 `sources`、`include_dirs`、`headers`、`defines` 四个列表。相对路径从 JSON 文件所在目录解析。`--out` 指向一个尚不存在的仓库外目录。

```bash
python3 flow/synth/run_synth.py --help
```

`--prepare-only` 生成源和约束的输入快照、Tcl 与 `manifest.json`，其中执行与 STA 均记为 `NOT_RUN`。实际运行还校验工具返回、完成标记、报告路径和输入摘要。收敛与各项设计规则需要另查报告和工艺条件。 [综合封装实现](run_synth.py) · [DC Tcl 驱动](run_dc.tcl)

早期远端 TSMC 28 nm DC 试验的时钟扫描、参数红绿对照和负载复核保存在 `git show 391c55a:flow/synth/README.md`。它们的旧远端参数可用于解释历史 PPA，当前通用封装的参数以 `--help` 为准。 [旧综合复核](../../docs/_history/rtl_reviews/RTL_SYNTHESIS_REVIEW_20260920.md)
