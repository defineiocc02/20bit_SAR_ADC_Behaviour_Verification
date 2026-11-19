# v21 综合、STA 与门级验证入口

| 路线 | 入口 | 结果范围 |
| --- | --- | --- |
| v21 整核 DC 与试验性 FC | [DC 综合](v21/dc_synth_v21.tcl) · [FC 整核脚本](v21/fc_rtl_to_gds.tcl) · [STA 预算](v21/sta_budget.tcl) | 目标为 `adc_digital_ip_v21`。阻塞点、当前同版网表与集成状态见 [SYNTH_STALL.md](../../docs/v21/SYNTH_STALL.md) 和 [STATUS.md](../../docs/STATUS.md)。 |
| v21 相关模块 | [单模块综合](v21/syn_module.tcl) · [sched18 流程](v21/fc_sched18.tcl) · [派生 LEF 的 sched18 试验](v21/fc_sched18_derived_lef.tcl) | 模块级数据按各自顶层、工艺输入与工具记录解读。包含关系、旁路与历史约束见 [审计与证据索引](../../docs/audit/README.md)。 |
| v21 Formality | [Formality Tcl](v21/fm_equiv.tcl) · [check_formal.sh](../../tools/check_formal.sh) | 需要同版 RTL、DC 网表与 SVF。整核记录见 [FORMAL_STATUS.md](../../docs/v21/FORMAL_STATUS.md)。 |

其他工艺、旧顶层和 FPGA 的结果按原有源包和约束解读。[审计与证据索引](../../docs/audit/README.md) · [历史 FPGA 记录](FPGA_SYNTHESIS_AND_STA.md)

## 整核只读取本次检出的生产清单

DC、FC、Formality 的参考端通过同一个 [Tcl 读取器](v21/design_inputs.tcl) 解析[生产清单](../build/filelists/files_adc_v21.f)，路径相对读取器所在的检出。仿真侧用 `-f` 引入同一份清单后再加测试台。独立 `sched18` 试验只读该模块，派生 LEF 的 `sched18` 试验读取仓内的 `tsmc28_tech_derived.lef` 和 `min_single_domain.upf`。其记录只覆盖该叶子及当次工艺输入。

从仓库根运行下面的静态命令即可列出实际源文件及顺序。它不加载 PDK 或 EDA。

```bash
tclsh flow/synth/v21/design_inputs.tcl --list
```

DC/FC、单模块试验及 STA 按执行主机的 `V21_PDK_ROOT` 找 28HPC+ 库。缺省从当前用户的 `~/PDK/tsmc28/digital_technology` 解析。先切换到本次专用运行目录，再以检出中脚本的绝对路径启动相应工具。DC 默认写该目录的 `dc/`，FC 默认写 `fc/`；`V21_DC_DIR`、`V21_FC_DIR` 可以指定本次产物的位置。

DC 在打开 SVF 前登记 filelist 和每个 RTL 的 SHA-256，写完网表、关闭 SVF 后才生成 `v21-inputs.sha256`。中断的目录保留 `.v21-inputs.pending`，可使用新目录重试。Formality 核对当前 RTL、网表和 SVF 的实物字节再读设计，`V21_DC_DIR` 指向三者所属的 DC 输出目录。下面的检查只读已有产物。

```bash
tclsh flow/synth/v21/design_inputs.tcl --check-dc "$V21_DC_DIR"
```

[Formality 包装](../../tools/check_formal.sh) 会将发起检出的精确源文件与清单暂存到新目录。设置 `V21_RUNNER` 可指定已配置的 SSH 别名，`V21_WORKDIR` 可指定尚未创建的任务目录。比对已有历史网表的 STA 仍可显式传入 `STA_NETLIST`，结果应标明原网表身份。旧工具结果和工艺限制见[历史环境](../../docs/v21/I5_ENVIRONMENT.md)与[状态页](../../docs/STATUS.md)。

## 通用本机封装

[run_synth.py](run_synth.py) 与 [run_synth.sh](run_synth.sh) 接收调用者声明的工具、顶层、时钟、库、SDC 和编译方式。它们使用 JSON 文件清单，与 [RTL 的 .f 清单](../build/filelists/README.md) 用途有别。JSON 必须提供 `sources`、`include_dirs`、`headers`、`defines` 四个列表。相对路径从 JSON 文件所在目录解析。`--out` 指向一个尚不存在的仓库外目录。

```bash
python3 flow/synth/run_synth.py --help
```

`--prepare-only` 生成源和约束的输入快照、Tcl 与 `manifest.json`，其中执行与 STA 均记为 `NOT_RUN`。实际运行还校验工具返回、完成标记、报告路径和输入摘要。收敛与各项设计规则需要另查报告和工艺条件。 [综合封装实现](run_synth.py) · [DC Tcl 驱动](run_dc.tcl)

早期远端 TSMC 28 nm DC 试验的时钟扫描、参数红绿对照和负载复核保存在 `git show 391c55a:flow/synth/README.md`。它们的旧远端参数可用于解释历史 PPA，当前通用封装的参数以 `--help` 为准。 [旧综合复核](../../docs/_history/rtl_reviews/RTL_SYNTHESIS_REVIEW_20260920.md)
