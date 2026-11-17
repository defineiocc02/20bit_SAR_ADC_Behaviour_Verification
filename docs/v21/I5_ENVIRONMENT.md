# I5 环境与工具核查（TSMC 28HPC+）

时间：2026-11-15　执行主机：`vm-yian`
依据：方案 v1.4 §10、§11.2 I5；目标要求"开工时并行核实……工具与许可、
实际目标 PDK、标准单元 Liberty/LEF、PVT/RC 角、提取参数和 DRC/LVS 规则"。

## 运行当前数字回归

在仓库根运行 `bash tools/verify_v21.sh`。VCS 在执行主机的专用目录启动。需要 SSH 执行时，通过 `V21_RUNNER` 提供已配置的 SSH 主机。默认创建 `/tmp/v21_verify.XXXXXX`。如设置 `V21_WORKDIR`，它必须是 `/tmp` 或 `$HOME/eda_runs` 下尚未存在的绝对路径，路径不含空格，父目录需已存在。使用 `--check-sources` 可只查看编译输入。

本页其余部分记录 2026-11-15 的后端环境。每次实际执行遵守 [EDA 工作目录规范](../audit/REMOTE_WORKDIR_POLICY.md)。

## 1. 标准单元库（已核实，齐全）

路径：`/home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/`

| PVT 角 | db 文件 | 用途 |
|:---|:---|:---|
| **ss 0.81V −40℃** | `tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db` | **本工程 setup 主用角** |
| ss 0.81V 125℃ | `...ssg0p81v125c_ccs.db` | 高温对照 |
| tt 0.9V 25℃ | `...tt0p9v25c_ccs.db` | 典型 |
| ff 0.99V −40℃ | `...ffg0p99vm40c_ccs.db` | hold 主用角 |
| ff 0.99V 125℃ | `...ffg0p99v125c_ccs.db` | 高温对照 |

CCS 模型（含 `.lib` 与 `.db` 双份）。

## 2. LEF / 物理（已核实）

| 项 | 路径 |
|:---|:---|
| 标准单元 LEF | `.../Back_End/lef/tcbn28hpcplusbwp7t40p140_110a/lef/tcbn28hpcplusbwp7t40p140.lef` |
| 高 VT LEF | `.../tcbn28hpcplusbwp7t40p140hvt_110a/lef/...hvt.lef` |
| 技术 LEF | `.../installed/tech/tsmc28_7t_site_via.lef` |

## 3. 工具可用性（实测）

| 工具 | 状态 | 用途 |
|:---|:---|:---|
| `dc_shell` (W-2024.09-SP3) | **OK** | 综合、STA |
| `pt_shell` (PrimeTime) | **OK** | 签核级 STA |
| `calibre` | **OK** | DRC / LVS |
| `starrc` | 安装存在 | 寄生提取 |
| `spyglass` | 安装存在 | Lint / CDC |
| `vc_static` | 安装存在 | 形式等价 |
| **`fc_shell`（Fusion Compiler）** | **OK** | **综合 + 布局布线 + 签核（一体会话）** |
| `icc2_lm_shell` / `lm_shell` | 存在 | 布局规划辅助 |
| `lib2tdb` / `starrc_wrapper` | 存在 | 库转换 / 寄生提取 |
| Innovus / Encounter | 缺失 | 布局布线（备选，不需要） |

**更正**：早前一版本文写"没有任何布局布线器"是**错的**。
错误原因是只用了 `command -v icc2_shell` 检查 PATH，而 Fusion Compiler
装在 `/opt/synopsys/fusioncompiler/W-2024.09-SP3/bin/` 下、**不在默认 PATH 里**。

`/opt/synopsys/` 实际内容：`1patch coretools fm fusioncompiler hspice lc
pprtl prime rtla scl spyglass starrc syn testmax vc_static vcs verdi
verdi_supp wv`。

**Fusion Compiler 实测**：

```
$ /opt/synopsys/fusioncompiler/W-2024.09-SP3/bin/fc_shell -version
fc_shell version W-2024.09-SP3
build date: Jan 16, 2025
```

许可（`SNPSLMD_LICENSE_FILE=27080@localhost`）可正常获取，
能进入 `Fusion Compiler (TM)` 会话。

**教训**：环境核查不能只看 PATH。必须 `ls` 安装目录本身
（`/opt/synopsys/`），并对候选目录逐个 `bin/` 检查可执行文件。

## 4. 因此 I5 的实际边界

**可以完成**（本轮已做/在做）：
- 28HPC+ 综合（`compile_ultra`）
- 综合后 STA（setup/hold，多 PVT 角）
- 形式等价（`vc_static`，若需要）
- 门级仿真（VCS + 综合网表 + SDF）
- Lint / CDC（SpyGlass）
- 约束覆盖检查

**可以完成**（Fusion Compiler 一体流程，本轮执行）：
- floorplan / 电源网 / 布局 / CTS / 详细布线
- 布线后寄生提取（StarRC）
- 后布线 STA（PT 或 FC 内建）
- DRC / LVS（Calibre，需要 GDS）
- **GDS / 抽象 LEF / SPEF / SDF 交付**

**仍需外部输入**（非工具问题）：
- 模拟硬宏（ADC2/RA/SADC）的 LEF/Liberty/GDS/CDL —— 当前用行为宏替代，
  所以整芯片级 LVS/DRC 只能做"数字硬宏"范围。
- 目标 40 nm 库 —— 不存在，按要求先用 28HPC+ 签核数字硬宏。

## 5. 许可

`SNPSLMD_LICENSE_FILE=27080@localhost`（root 与 yian 均可用）。
DC / PT / Calibre / VCS 实测均能取得许可。

## 6. 40 nm 迁移预留

方案目标是 40 nm 原型工艺，当前无该工艺库。为将来重映射保留：

| 项 | 当前状态 | 40 nm 时需要 |
|:---|:---|:---|
| RTL | 与工艺无关（纯行为 + 标准单元） | 直接复用 |
| 端口时序表 | `docs/v21/I5_PORTS.md`（79 端口） | 同一份 |
| 金标向量 | `tb/v21/vec_*.txt` | 同一套 |
| 约束 | `flow/synth/` 下 SDC（25 ns 周期） | 按目标库重算 uncertainty |
| 关键路径 | 见综合报告 | 按目标库重跑 STA |
| 面积/时序数值 | 28HPC+ 实测值 | **不可外推**，必须重跑 |

**结论**：40 nm 库到位后，RTL、端口表、金标可原样复用；面积/时序/签核必须重做。
