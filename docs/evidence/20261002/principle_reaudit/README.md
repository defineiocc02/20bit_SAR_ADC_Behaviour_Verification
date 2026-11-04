# 原理/RTL/综合结构再复核的自有证据

源码基线 `71935ab54238c7ed638f5fa66c5ffb58e10287bb`，24 SV +2 头文件。见 [集成分析](../../../rtl/PRINCIPLE_AND_SYNTHESIS_REAUDIT_20261002.md) 及 [46 页独立 LaTeX/PDF 报告](../../../reports/rtl_principle_reaudit_20261002/README.md)。没有修改生产 RTL，没有新 EDA、模拟或 ASIC 签核。

## 目录及计数

- `input_manifest.json`：原样冻结的26源SHA和16原始PDF身份，含当次本机路径；`source_identity_public.json`提供同样的源SHA与PDF文件名/页数/SHA，去除本机路径。清单不包含或分发原文/页图/OCR。
- `calibration_evidence_manifest.json`：原样封存校准报告、两 bench/两镜像/数学计算及源副本。MAC 6 个实例，ADC2 9 个实例（6 个相对/整体 RTE 差异）。独立计算 24,576 ADC 码、5,000 floor、186,744 小位宽算法项，后者不是 RTL 仿真。
- `control_audit_seal.json`、`control_minimal_witness/`：两个有限控制见证。1926 沿、120 帧、57 接受/63 drop、1920 既有协议检查；16 个模拟 bad 归属检查。影子 stageA，无真实 weight/recon 数据通路及全 cfg-clear/re-enable 覆盖。
- `synthesis_structure_counts.json`、计数脚本及验证记录：源码公式、26 SHA、24 模块名、历史 71e7/P5 Vivado 数据。不是 HDL elaborator、门数或新 STA。
- `figures/` 与 `generate_audit_figures.py`：3 张原创 PNG/矢量 PDF，分别为历史 P5 FF 分解、不同端点两条关键路径、57 接受事件的真实稳定间隔。源数据和图 SHA 一起保存。
- `report_compile.log`、`report_xelatex.log`、`report_validation.json`：最终导出编译/排版/身份记录；native editor 的成功另记，非本机 XeLaTeX 日志冒充。

独立报告在本目录保留原始封存字节。`docs/rtl/reaudit_20261002/` 为阅读版，综合报告的朴素逐项 SRAM 读取限制经交叉审阅补充限定；集成文档还修正源码符号、原文页码、MCP 必须核最短稳定区间等表述。原始 SHA 清单不被这些编辑覆盖。

## 可移机复算

在含历史 `71e7d5a` 对象的仓库中执行，输出目录必须不存在。该工具检查全部冻结源 SHA，重定位捕获脚本的运行路径，不改它们的字节：

```sh
python tools/reproduce_principle_reaudit.py --repo . --out /tmp/sar_reaudit_calculation
```

实际已复算三项数学输出字节相等；结构 JSON 只容许文档提交 HEAD 不同，所有源码/公式/历史来源仍须完全相等。该命令不编译 RTL、不运行 EDA。

两组合 RTL bench 可用同版 Verilator 5.020 在仓库根目录分别重建（下面将输出留在临时目录，保留捕获证据）：

```sh
verilator --binary --timing --timescale 1ns/1ps --top-module calibration_domain_tb -Irtl/params rtl/core/cal_residue_mac.sv docs/evidence/20261002/principle_reaudit/calibration_domain_tb.sv --Mdir /tmp/sar_domain_obj
/tmp/sar_domain_obj/Vcalibration_domain_tb
verilator --binary --timing --timescale 1ns/1ps --top-module adc2_rounding_semantics_tb -Irtl/params rtl/core/adc2_dec.sv docs/evidence/20261002/principle_reaudit/adc2_rounding_semantics_tb.sv --Mdir /tmp/sar_adc2_obj
/tmp/sar_adc2_obj/Vadc2_rounding_semantics_tb
```

控制两见证的实际 argv、cwd、版本与允许的 bench WIDTH 警告见 `execution_metadata.json` 及两个 `*_compile_command.json`；移机需替换原 argv 的绝对源码/输出路径与本机外部 libc++ 适配 wrapper，不能直接执行捕获二进制。它们分别直接例化 context/status 和包裹既有 structural bench，不能当作全核 regression。

图生成器使用 `--audit-dir` 和 `--repo`，应在证据的独立副本或新计算输出中生成，避免覆盖已封存图。各清单中绝对路径是当次运行身份，不作为移机路径要求。

原样捕获 `.py` 源码从格式化 hook 排除以保持 hash；维护中的两项 `tools/` CLI 正常受 Ruff/格式和提交 hook 检查。原日志保持字节，README/阅读版文档与工具仍按仓库规范检查。
