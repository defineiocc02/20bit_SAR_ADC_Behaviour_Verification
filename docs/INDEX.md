# docs 总索引（权威）

更新：2026-11-17　基线：`ffc10ef`

**先看这一页。** 本页如实反映当前结构，不夸大收敛程度。

---

## 一、三条验证链，各有自己的顶层与脚本

**这是本工程最容易搞错的地方：`rtl/` 下有四个顶层，是不同交付范围，不是版本迭代。**

| 链 | 顶层 | 验证脚本 | 覆盖 |
|:---|:---|:---|:---|
| **v21 全链** | `rtl/v21/adc_digital_ip_v21.sv`（8 实例）| `tools/verify_v21.sh` | D1/D2/D5/D6 |
| **D3/D4 IP** | `rtl/_legacy/tops/d3d4_ip.sv`（6 实例）| `tools/verify_d3d4_ip.sh` | D3/D4 |
| **D6 权重路径** | `rtl/_legacy/tops/digital_top_w.sv`（7 实例）| 多个 `tb/tb_*.sv` | D6（不含 D3/D4）|
| 早期骨架 | `rtl/_legacy/tops/digital_top.sv`（4 实例）| — | — |

**四个顶层的详细说明见 [`../rtl/README.md`](../rtl/README.md)。**

---

## 二、读这五份就够（核心功能）

| 顺序 | 文件 | 作用 |
|:--:|:---|:---|
| 1 | [`../README.md`](../README.md) | 交付什么、怎么跑 |
| 1b | [`STATUS.md`](STATUS.md) | **唯一权威状态表**（能/不能复现、作废数字清单）|
| 2 | [`D3D4_IP_INTERFACE.md`](D3D4_IP_INTERFACE.md) | **IP 对外合同**：端口/时序/两种模式/位序/合法性标志 |
| 2b | [`STATUS.md`](STATUS.md) | **D1–D6 复现状态** + 原文逐条定位 + 不能声称什么 |
| 4 | [`ACCEPTANCE_STATUS.md`](ACCEPTANCE_STATUS.md) | 哪个模块在什么证据下通过 |
| 5 | [`CONTRACT_CURRENT.md`](CONTRACT_CURRENT.md) | 适用哪份合同（v2 + v2.1，冲突以 v2.1 为准）|

---

## 三、目录索引

### 现行文档（127 份，不含 `_history/`）

| 目录 | 份数 | 内容 | 入口 |
|:---|--:|:---|:---|
| **`audit/`** | 32 | **审计与后端报告**（11 现行 + 21 历史）| [`audit/README.md`](audit/README.md) |
| `adr/` | 22 | 架构决策记录 | 见目录 |
| **`v21/`** | **9** | v21 专项（配置合同、形式验证、合成阻塞、证明台账等）| [`STATUS.md`](STATUS.md)（本目录为专项证据）|
| **`rtl/`** | **10**（+7 支撑）| RTL 复审（含 `dr_rtl_20261007/`、`reaudit_20261002/`）| [`rtl/RTL_ENGINEERING_GUIDE.md`](rtl/RTL_ENGINEERING_GUIDE.md) |
| `mechanisms/` | 13 | 机制原理与源交叉核对 | 见目录 |
| `guide/` | 4 | 使用指南 | 见目录 |
| `meta/` | 3 | 元信息 | 见目录 |
| `verification/` `spec/` `caaz/` `acceptance/` | 各 2 | 专项 | 见目录 |
| `review/` `decisions/` `figures/` `randomization_caaz/` | 各 1 | 专项 | 见目录 |
| `_history/` | **105** | **历史，不得引用** | — |

### `docs/` 根级 14 份

**14 份**。其中 `STATUS.md` 是唯一权威状态表（含原文逐条定位）。

`STATUS` / `ACCEPTANCE_STATUS` / `BRIDGE_VS_FIG31` / `CONTRACT_CURRENT` /
`D3D4_IP_INTERFACE` / `DIGITAL_ALGORITHM` /
`HANDOFF_D3D4` / `INDEX` / `ISSCC2024_ONCHIP_CONTRACT_v2` /
`ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM` / `LIB_STATUS` /
`MECHANISM_UNDERSTANDING` / `PHASE_AB` / `SYNTH_D3D4`

**合同优先级**：`v2_1_ADDENDUM` > `v2` > 其余。

---

## 四、当前状态（重要）

### 已完成的

| 项 | 证据 |
|:---|:---|
| **13 个模块全部完成 DC 综合** | 均满足 25 ns 单段约束，见 `audit/SYNTH_PER_MODULE.md` |
| **7 个模块完成 RTL→GDS 全流程** | router DRC=0 + Formality `SUCCEEDED`，合计 2,189 点 0 failing |
| **算法 vs 论文/专利审计** | 12 项判定，带证据等级与页码，见 `audit/AUDIT_ALGO_VS_SOURCE.md` |
| **端口 vs 片上要求审计** | 87 端口逐项对照，见 `audit/PORT_AND_ONCHIP_AUDIT.md` |
| **12 张效果图** | 本轮实际生成，见 `figures/20261117/` |

### 未完成的

| 项 | 说明 |
|:---|:---|
| **顶层从未综合成功** | `v21/adc_digital_ip_v21` 未综合过 |
| **3 个模块未在 v21 顶层实例化** | `dem_phase_rr`、`dem_phase_gen`、`follower_rt` |
| **4 个模块预算内未跑完** | `wsum`、`recon`、`recip_q60`、`recip_rnd` |
| **2 个模块 router DRC 未归零** | `dither_pre`（2）、`dem_mask_v3`（27）|
| **无 foundry DRC / LVS / PG / 多角 / IR-EM** | 7 个通过模块的 DEF 均无 PG 布线几何 |
| **`recon_v21.sv:373` 有未修缺陷** | 见 `audit/todo/README.md` §2 |

**待办清单：[`audit/todo/README.md`](audit/todo/README.md)（6 项，全部未开始，逐项需授权）。**

---

## 五、三条硬性限定

1. **技术 LEF 为推导生成**（`flow/synth/v21/tsmc28_tech_derived.lef`），非 PDK 官方交付。
   `PITCH`/`WIDTH`/`SPACING` 来自 `.tf` 真实值；`DIRECTION` 按 M1 水平/M2 垂直交替补
   （`AFP-1022` 证实工具从库也推导出同样方向）。

2. **无时钟门控**（`set_clock_gating_options -minimum_bitwidth 1000000`）。
   带门控版 Formality 为 FAILED（20 点，根因 `clock_gate_acq_q_reg/*lat.00*`）。

3. **router DRC=0 ≠ foundry DRC clean。** 无 LVS、无多 PVT 角、无 IR/EM。

**不是 tapeout signoff。**

---

## 六、历史文档（`_history/`，105 份，**不得引用**）

| 子目录 | 内容 |
|:---|:---|
| `_history/`（根） | 38 份早期文档 |
| `_history/v21_checkpoints/` | 9 份过程检查点 |
| `_history/archive/` | 34 份更早期归档 |
| `_history/v21_legacy/` | 11 份 v21 旧版 |
| `_history/rtl_reviews/` | 11 份 2026-09 复审 |
| `_history/v21_status/` | 1 份旧 STATUS（`d5a1576`）|
| `_history/root_docs/` | 2 份已合并的根级索引（PROJECT_MAP / ROOT_FILES）|
| （另含 D1_D6_STATUS_20261115 已并入 STATUS.md §1B）| |

**原 `INDEX.md` 声称"顶层只保留 14 份"，与实际不符，本版已改为如实反映。**
