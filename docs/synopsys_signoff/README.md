# Synopsys 商用数字链条实测与签核评估报告（TSMC 28 nm HPC+）

> **版本标识**：`v21-dualrate-40msps`（核心双域 40 MSPS 转换核）+ `ad4080-silicon-shell-r1`（外围流片边界外壳） · 对应软件基线 `9.0.1+adi.repro`（基准单时钟核 `v21` 与冻结外围包 `rtl/ad4080_periph/` 218 文件 SHA-256 均保持原样不动）
>
> **工艺基线声明（重要）**：本目录全部综合、形式等价、多角静态时序与门级网表仿真结果均运行在 **TSMC 28 nm HPC+ 工艺库**（`tcbn28hpcplusbwp7t40p140_180a`：SS 0.81 V -40°C / 125°C、FF 0.99 V -40°C / 125°C；门级仿真单元库 `tcbn28hpcplusbwp7t40p140_110a`）与 **Synopsys W-2024.09 全套商用工具链**（VCS W-2024.09-SP1、Design Compiler W-2024.09-SP3、Formality W-2024.09-SP3、PrimeTime W-2024.09-SP3）。**本报告不是 40 nm 实测数据**；若迁移至 ISSCC 2024 原文所用的 **40 nm CMOS** 流片，必须逐条执行本文第四节的 **28 nm → 40 nm 迁移注意事项**。

---

## 一、多版本分层与目录结构

本仓库严格隔离三层 RTL 版本，各有独立编译清单、测试台与签核脚本，互不污染：

| 版本代号 | 顶层 / 模块范围 | 编译清单 | 角色定位 |
| --- | --- | --- | --- |
| **`v21`（单时钟参考基线）** | `adc_digital_ip_v21`、`ad4080_chip_top` | [files_adc_v21.f](../../rtl/filelists/files_adc_v21.f) · [ad4080_chip.f](../../rtl/filelists/ad4080_chip.f) | 与 Python `adi_model` 逐位对拍、算法图册与 218 文件冻结外围包的唯一参考基准（单时钟需 13 拍/样本）。 |
| **`v21-dualrate-40msps`（核心重点）** | `adc_digital_ip_v21_40msps`（`ALPHA_UNIT_BYPASS=1`） | [files_adc_v21_40msps.f](../../rtl/filelists/files_adc_v21_40msps.f) | 真实双时钟域 40 MSPS 转换核：>95% 重逻辑跑 **40 MHz（25 ns）`clk_fs`**，仅 ~1k 门事件跟随器走自定时 `ev_clk_i` 突发，消除单时钟 640 MHz / 1.6 GHz 假时钟。 |
| **`ad4080-silicon-shell-r1`（外围边界）** | `ad4080_crc24_service`、`ad4080_otp_macro_adapter`、`ad4080_result_store_foundry`、`ad4080_lvds_phy_wrapper` | [files_ad4080_silicon_shell.f](../../rtl/filelists/files_ad4080_silicon_shell.f) | 零改动冻结外围包前提下，补齐芯片边界可综合硬件服务与工艺厂硬宏替换接缝（`` `ifdef USE_FOUNDRY_*_MACRO ``）。 |

本目录文件组织：
- [`v21_dualrate_40msps/`](v21_dualrate_40msps/)：核心转换核全套 VCS RTL、TSMC 28HPC+ DC QoR/层级面积/功耗/关键路径、PrimeTime 四角点 STA、VCS 179k 单元门级网表仿真与 SHA-256 绑定收据。
- [`silicon_shell_r1/`](silicon_shell_r1/)：四项外围边界模块的 VCS RTL、TSMC 28HPC+ DC、Formality 100% 等价、PrimeTime 四角点 STA 与 VCS 28HPC+ 门级仿真报告。

---

## 二、核心数字功能模块（`adc_digital_ip_v21_40msps`）实测结果

### 1. Synopsys 全链条汇总（TSMC 28 nm HPC+）

| 阶段 | 工具与配置 | 实测结果 | 原始证据 |
| --- | --- | --- | --- |
| **四态 RTL 仿真** | VCS W-2024.09-SP1 (`+lint=TFIPC-L`) | **PASS**（0 编译警告；192 混合三路径 + 192 单单元校准归零 + 25.000 ns 节拍 + RA 安静窗 0 翻转 + 双负对照） | [vcs_rtl_sim.txt](v21_dualrate_40msps/vcs_rtl_sim.txt) |
| **逻辑综合** | DC W-2024.09-SP3 (`ssg0p81vm40c_ccs.db`) | **179,656 叶子单元**（17,532 触发器，**0 未时钟寄存器**），总面积 **`135,479.3 µm²`**（`0.1355 mm²`），**0 `max_trans` / 0 `max_cap` 违规** | [dc_qor.rpt](v21_dualrate_40msps/dc_qor.rpt) · [dc_area_hier_top.rpt](v21_dualrate_40msps/dc_area_hier_top.rpt) |
| **主帧域 `clk_fs` (25 ns / 40 MHz)** | PrimeTime W-2024.09-SP3 四角点 | **全四角点 0 Setup 违规**：SS -40°C **`+2.7194 ns (MET)`**（到达 `22.0043 ns`）、SS 125°C `+5.3521 ns`、FF -40°C `+14.3962 ns`、FF 125°C `+13.9864 ns` | [pt_4corner_summary.txt](v21_dualrate_40msps/pt_4corner_summary.txt) · [dc_sta_clk_fs_top.rpt](v21_dualrate_40msps/dc_sta_clk_fs_top.rpt) |
| **跟随域 `ev_clk` (1.00 ns 约束)** | PrimeTime W-2024.09-SP3 四角点 | SS -40°C 到达 `1.0186 ns` / slack `-0.1089 ns`（实际单步 `1.11 ns` = **902 MHz**）；SS 125°C `-0.0422 ns`；FF -40°C **`+0.2794 ns (MET)`**；**四角 Hold 全部 MET**（`+56.6 / +45.5 / +2.2 / +4.3 ps`） | [pt_4corner_summary.txt](v21_dualrate_40msps/pt_4corner_summary.txt) · [dc_sta_ev_clk_top.rpt](v21_dualrate_40msps/dc_sta_ev_clk_top.rpt) |
| **门级网表仿真** | VCS + `tcbn28hpcplusbwp7t40p140.v` | **PASS**（179,656 颗真实 28HPC+ 单元跑满 40.000 MSPS 闭环，`DUALRATE_40MSPS_PASS`，1.06s CPU） | [vcs_gls_sim.txt](v21_dualrate_40msps/vcs_gls_sim.txt) |
| **形式等价** | Formality W-2024.09-SP3 + SVF | 18,079 比较点 **100% 按名匹配（0 未匹配）**；实测推进至 **17,852 通过 / 0 失败 / 0 中止（98%）**，剩余 227 个深算术锥（`wsum_v21`/`recip_q60`）因单核耗时过长暂止，后续加 8 核或层次化边界再收口 | [fm_equiv_partial.txt](v21_dualrate_40msps/fm_equiv_partial.txt) · [v21-inputs.sha256](v21_dualrate_40msps/v21-inputs.sha256) |

> **关键修复记录**：初版整核 DC 综合暴露了 `recon_v21` 内部 64-bit `DW_div` 未被跨层常量传播剪除的问题（64 条 `-57.75 ns` 死路径、面积 `147,287 µm²`）；为 `recon_v21` 增加编译期参数 `ALPHA_FIXED_UNIT`（默认 `0` 保持基线 `v21` 零变化，双域核传 `1`）并将 `conv_ids_q` 预译码为扁平 8:1 MUX 后，`clk_fs` 违例从 64 条清零为 **0 条（`+2.72 ns` MET）**，`ev_clk` 延迟再压 65 ps、TNS 砍半，总面积立省 **`11,808 µm²`**（13,740 单元）。

---

## 三、外围流片边界外壳（`ad4080-silicon-shell-r1`）实测结果

| 模块 | 目标频率 | 28HPC+ 面积 / 单元 | SS -40°C Setup 裕量 | Formality 等价 | VCS 28HPC+ 门级仿真 | 说明 |
| --- | --- | --- | ---: | --- | --- | --- |
| **`ad4080_crc24_service`** | 400 MHz (`2.50 ns`) | `361.4 µm²` / 467 cells | **`+0.7423 ns (MET)`**（到达 `1.26 ns`） | **107/107 SUCCEEDED** | **PASS**（5 组 `crc24.py` 黄金向量 + 2 负对照） | 40 nm 折算仍可稳跑 400 MHz |
| **`ad4080_otp_macro_adapter`** | 100 MHz (`10.0 ns`，10× AON) | `1,040.4 µm²` / 1,146 cells | **`+6.6214 ns (MET)`**（到达 `1.76 ns`） | **355/355 SUCCEEDED** | **PASS**（联调真实 `otp_shadow_loader` + `ROM_CRC_ERR`） | 实际 Fmax >500 MHz，对 10 MHz AON 裕量极充裕 |
| **`ad4080_result_store_foundry`** | 400 MHz (`2.50 ns`) | `80.5 µm²` / 196 cells（4 银行控制壳） | **`+1.23 ns (MET)`**（到达 `0.78 ns`） | 黑盒 SRAM 桩（RTL 行为已过 VCS） | **PASS**（4 银行跨界 + 轻睡保持） | 定义 `USE_FOUNDRY_SRAM_MACRO` 接工艺厂 4096×20 Compiler 硬宏 |
| **`ad4080_lvds_phy_wrapper`** | 400 MHz DDR (`2.50 ns`) | `30.6 µm²` / 28 cells（数字接口壳） | **`+0.0005 ns (MET)`** | **20/20 SUCCEEDED** | **PASS**（ODDR + 0°/90°/180° DCO + OE） | **非模拟 LVDS 电路**，仅数字边界封装壳；流片须用 `USE_FOUNDRY_LVDS_PHY` 换工艺厂模拟 LVDS TX 宏 |

原始证据见 [`silicon_shell_r1/`](silicon_shell_r1/)（[vcs_rtl_sim.txt](silicon_shell_r1/vcs_rtl_sim.txt)、[dc_qor_viol_summary.txt](silicon_shell_r1/dc_qor_viol_summary.txt)、[fm_equiv_summary.txt](silicon_shell_r1/fm_equiv_summary.txt)、[pt_4corner_summary.txt](silicon_shell_r1/pt_4corner_summary.txt)、[vcs_gls_sim.txt](silicon_shell_r1/vcs_gls_sim.txt)）。

---

## 四、诚实评估：什么已达签核质量，什么还不算流片签核？

1. **已达数字前端 / 综合级签核质量**：
   - `adc_digital_ip_v21_40msps` 的 `clk_fs`（40 MHz）全四角 Setup 零违规（`+2.72 ns`~`+14.40 ns`）、`ev_clk` 全四角 Hold 零违规、0 未时钟寄存器、0 `max_trans`/`max_cap`、179k 单元 28HPC+ 门级网表 40.000 MSPS 仿真零误差；
   - `ad4080_crc24_service`、`ad4080_otp_macro_adapter`、`ad4080_result_store_foundry` 控制壳在 28HPC+ 下均达成 Setup 充裕 MET、Formality 100% 等价与 28HPC+ 门级仿真通过。
2. **尚未达最终 Tape-out 物理签核（必须由后续步骤闭环）**：
   - **`ev_clk` 28 nm SS -40°C 下对 `1.00 ns` 硬约束差 `-109 ps`**（实际 `1.11 ns`/步 = 902 MHz，8 步总计 8.9 ns 仍留 >16 ns RA 窗；把 `ev_clk` 约束放宽至 `1.15 ns` 或对该 ~1k 门跟随器换 28HPC+ LVT 单元即可转正）；
   - **Pre-CTS Hold 违例**（全核 `clk_fs` FF 角 `-111 ps`、CRC24 `-6.4 ps`、OTP `-11.7 ps`）：属综合后零时钟树、零线载状态，需进入 P&R（Fusion Compiler / Innovus）完成 CTS 与 Hold buffer 插入后复验；
   - **物理版图与模拟硬宏**：尚未包含寄生参数提取（SPEF）、OCV/AOCV、Foundry DRC/LVS、IR/EM、片上模拟核心（前端/SADC/RDAC/共享 RA/LDO/POR）及模拟 LVDS TX Pad（100 Ω 端接 + ESD）。

---

## 五、从 TSMC 28 nm HPC+ 迁移至 40 nm CMOS 的关键注意事项（流片必读）

ISSCC 2024 AD4080 商业硅片采用 **40 nm CMOS**（1.1 V 数字核心 + 3.3 V 模拟开关 + 1.8 V I/O）。基于本次 **TSMC 28 nm HPC+** 的实测数据，迁移到 40 nm（40G / 40LP）时有 **6 项必须提前处理的硬约束**：

| 序号 | 迁移关注点 | 28HPC+ 实测基线 | 40 nm 折算预估与风险 | 40 nm 必须采取的工程措施 |
| --- | --- | --- | --- | --- |
| **1** | **主帧域 `clk_fs` 关键路径（`recip_q60` S2 两次串联 Newton）** | 到达 **`22.00 ns`** / 25.0 ns（裕量 `+2.72 ns`） | 40 nm 门延迟约 **×1.35–1.55** → 预计升至 **`29.7–34.1 ns`（必违 25 ns！）** | **首项必改**：将 `recip_q60.sv` 的 S2 两次串联 Newton 迭代拆为两级流水（S2a/S2b 各 1 次乘法），并在 `recon_v21` 的 `S2` 路径补 1 拍对齐，单级延迟压回 `~14–16 ns`，在 40 nm 稳过 25 ns。 |
| **2** | **快速跟随域 `ev_clk`（~1k 门）+ 电平转换延迟** | SVT SS 逻辑 `1.02 ns`（含输入延迟 `1.11 ns`/步） | 40 nm 逻辑约 **`1.50–1.72 ns`** + 1.1 V→3.3 V Level Shifter **`0.25–0.45 ns`** ≈ **`1.8–2.1 ns`/步**，8 步达 `14.4–16.8 ns`，严重压缩 RA 建立窗 | 对该 ~1k 门跟随器专用 **40 nm LVT/ULVT 单元**、物理布局紧贴 RDAC 模拟阵列、把 `dem_mask_v3` 29 级组合逻辑精简至 ≤15 级，并将真实 Level Shifter 延迟与上升/下降偏斜写入 `sdc_dualrate_40msps.sdc` 的 `set_output_delay`。 |
| **3** | **400 MHz DDR IO（`ad4080_crc24_service` / LVDS）** | CRC24 到达 `1.26 ns` / 2.50 ns；SRAM 壳 `0.78 ns` | 40 nm 折算约 `1.95 ns` / `1.21 ns`（仍 < 2.50 ns，但 1.25 ns UI 眼宽更紧） | CRC24 末拍组合路径预留 LVT 选项；40 nm 模拟 LVDS TX 需重评估封装寄生与 90° DLL 跨 PVT 偏斜，确保发射端 skew ≤ `±0.15 ns`。 |
| **4** | **标准单元面积与 `wsum_v21` 权重表实现** | 全核 `135,479 µm²`（`0.1355 mm²`，含 14,400-bit 触发器权重表） | 40 nm 面积约 **×1.7–2.1** → 预计 **`0.23–0.28 mm²`**（注：ISSCC 2024 `CTRL` `0.0264 mm²` 不含权重表与重构器） | 若 40 nm 芯片面积预算紧张，将 `wsum_v21` 内 360×40-bit 触发器堆替换为 40 nm 编译双口/多行 Register File 硬宏，可削减 >40% 数字面积与漏电。 |
| **5** | **1.1 V 衬底耦合与多电源域 UPF 隔离** | 28HPC+ 标称 `0.9 V`（无 UPF 隔离单元） | 40 nm 核心 `1.1 V` 数字翻转注入衬底的 $CV^2$ 电荷约为 28 nm `0.9 V` 的 **$(1.1/0.9)^2 \approx 1.49\times$** | 必须为 `ev_clk` 跟随器与 `clk_fs` 数字核加 **深 N 阱（DNW）+ 双保护环**，严守 `[7..25 ns]` RA 安静窗零翻转；在 40 nm UPF 中为 `AON / CORE / IO` 补齐真实 `ISO`、`ELS` 与 `Level Shifter` 单元。 |
| **6** | **40 nm 工艺厂硬宏（SRAM / OTP / LVDS TX）** | 28HPC+ 无 40 nm 硬宏 `.lib/.lef` | 40 nm 4×4096×20 SRAM 读访问时间 `tAA`、eFuse 电荷泵建立拍数、LVDS TX 100 Ω 端接均随 40 nm PDK 变化 | 分别启用 `USE_FOUNDRY_SRAM_MACRO`（并用 `sram_macro_gate.py` 校验 40 nm Corner）、`USE_FOUNDRY_OTP_MACRO`（按 40 nm 读周期重配 `OTP_TIMEOUT`）与 `USE_FOUNDRY_LVDS_PHY`（绑定模拟 LVDS TX 网表）。 |

---

## 六、复现入口

- 核心双域综合 / 等价 / 四角 STA 脚本：[dc_synth_dualrate_40msps.tcl](../../flow/synth/v21/dc_synth_dualrate_40msps.tcl) · [fm_equiv_dualrate_40msps.tcl](../../flow/synth/v21/fm_equiv_dualrate_40msps.tcl) · [pt_sta_dualrate_40msps.tcl](../../flow/synth/v21/pt_sta_dualrate_40msps.tcl) · [sdc_dualrate_40msps.sdc](../../flow/synth/v21/sdc_dualrate_40msps.sdc)
- 外围边界外壳综合与 IO 约束脚本：[dc_synth_silicon_shell.tcl](../../flow/synth/dc_synth_silicon_shell.tcl) · [ad4080_chip_io_timing.sdc](../../flow/synth/ad4080_chip_io_timing.sdc)
- 仿真台架：[tb_40msps_dualrate.sv](../../tb/v21/tb_40msps_dualrate.sv) · [tb_ad4080_silicon_periph_closure.sv](../../tb/v21/tb_ad4080_silicon_periph_closure.sv)
