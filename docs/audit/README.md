# 审计与后端报告（权威索引）

时间：2026-11-17　基线：`6df4ad2`

## 〇、当前状态（重要）

**本工程曾以「7 通过 / 2 DRC 未归零 / 4 物理不可行」收尾，该结论已被独立复核更正。**

更正要点（详见 `ALL_13_MODULES_FINAL.md` §0）：

| 原说法 | 更正 |
|:---|:---|
| `DRC = 0` | `router DRC = 0`；**无 PG 布线几何、无 foundry DRC、无 LVS** |
| 4 个「物理不可行」 | **这些冻结输入与给定预算下未完成**（2 预算到期 / 1 人工终止 / 1 未合法放置后继续 CTS）|
| D5 占数字面积 95.8% | **删除**（`recon`→`recip_rnd`→`recip_q60` 三层嵌套，面积不可相加）|
| D2/D3「完整可交付」| **模块级证据，未在顶层实例化** |

**待办清单见 [`todo/README.md`](todo/README.md)：6 项，全部未开始，逐项需授权。**

**已知源码缺陷（未修）**：`recon_v21.sv:373` 的 `err3_q` 溢出判定有两处问题，见 `todo/README.md` §2。

## 一、目标要求与文档对应

| 目标要求 | 对应文档 | 结论 |
|:---|:---|:---|
| 审计数字算法是否满足论文专利披露 | **`AUDIT_ALGO_VS_SOURCE.md`** | 12 项判定：符合 6、`[E]` 假设 4、待裁定 2 |
| 端口等关键设计是否满足论文片上要求 | **`PORT_AND_ONCHIP_AUDIT.md`** | 端口全部一致；ADC2 位宽待裁定 |
| 拆分小模块一个一个做综合 | **`SYNTH_PER_MODULE.md`** | **13/13 完成**，合计 160,951 µm²，全满足 25 ns |
| 结合 rtl-to-gds 完成后端 | **`ALL_13_MODULES_FINAL.md`** | **7 通过 / 2 DRC 未归零 / 4 物理不可行** |

## 二、现行文档（11 份）

### 算法与端口审计

| 文件 | 内容 |
|:---|:---|
| `AUDIT_ALGO_VS_SOURCE.md` | 13 项算法判定、ADC2 位数裁定、`[E]` 假设清单 |
| `PORT_AND_ONCHIP_AUDIT.md` | 87 个顶层端口对照论文要求、13 模块端口实测 |

### 综合

| 文件 | 内容 |
|:---|:---|
| `SYNTH_PER_MODULE.md` | 13 模块 DC 单模块综合（含耗时/面积/cells/slack）|
| `RECON_ALPHA_DIVIDER.md` | `recon` 的 alpha 除法器瓶颈（`DW_div` 13→6、10min→99s）|

### 后端：通过的模块

| 文件 | 内容 |
|:---|:---|
| `FORMAL_SUCCEEDED.md` | `sched18` Formality `SUCCEEDED` 234/0/0 的完整记录 |

### 后端：失败的模块

| 文件 | 内容 |
|:---|:---|
| `COMBINATIONAL_MODULE_FLOW.md` | `dither_pre`/`dem_mask_v3` 六次归因（DRC 2 / 27）|
| `WSUM_FINAL_VERDICT.md` | `wsum_v21` 38 分钟未完成综合 |
| `RECIP_Q60_ROUTING_ANALYSIS.md` | `recip_q60` 布线 14/225、违规 2,286→8,551 |
| `RECIP_RND_CRASH.md` | `recip_rnd_v21` `XFORM` fatal |

### 总表

| 文件 | 内容 |
|:---|:---|
| `ALL_13_MODULES_FINAL.md` | **13 模块完整结论 + 面积分界 + 失败模式对照** |

### 产物

`artifacts/`：

| 文件 | 内容 |
|:---|:---|
| `sched18_final.v` / `sched18.svf` | 布线后网表 + 综合等价变换记录（190 KB / 25 KB）|
| `DC_MODULE_SUMMARY.txt` | 13 模块 DC 综合汇总 |
| `README.md` | 产物说明与验证方式 |

## 三、一句话结论

**13 个模块全部完成 DC 综合（均满足 25 ns 单段约束）；7 个完成 RTL→GDS 全流程，router DRC=0 且 Formality `SUCCEEDED`（合计 2,189 点、0 failing）；2 个 router DRC 未归零（2 / 27）；4 个在冻结输入与给定预算下未完成。**

**「router DRC=0」不等于 foundry DRC clean：7 个通过模块的 DEF 均无 PG 布线几何，无 LVS、无多角、无 IR/EM。**

**4 个未完成模块的原因逐条列明（预算到期 / 人工终止 / 未合法放置后继续 CTS），不作「算法不可行」推断。**
**13 个模块面积不可横向相加（`recon`→`recip_rnd`→`recip_q60` 三层嵌套）。**

## 四、三条硬性限定

1. **技术 LEF 为推导生成**（`tsmc28_tech_derived.lef`）。
   `PITCH`/`WIDTH`/`SPACING` 来自 PDK `.tf` 真实值；`DIRECTION` 按 M1 水平/M2 垂直交替补；
   `AP` 层按典型值补。**`AFP-1022` 证实工具从库也推导出同样方向。**

2. **无时钟门控**（`set_clock_gating_options -minimum_bitwidth 1000000`）。
   带门控版本的 Formality 为 FAILED（20 点，根因 `clock_gate_acq_q_reg/*lat.00*`）。

3. **无 LVS、无 GDS 验证、单角、无 IR/EM。**

**不是 tapeout signoff。**

## 五、已被取代的文档（`_superseded/`）

**不得引用。** 记录了探索过程的中间判断：

| 文件 | 当时的判断 | 后来被什么取代 |
|:---|:---|:---|
| `TWO_MODULES_PASSED.md` | 2 个通过 | `ALL_13_MODULES_FINAL.md` |
| `THREE_MODULES_PASSED.md` | 3 个通过 | 同上 |
| `FOUR_MODULES_PASSED.md` | 4 个通过 | 同上 |
| `SIX_MODULES_PASSED.md` | 6 个通过 | 同上 |
| `BACKEND_FINAL.md` | 7 个通过时的快照 | 同上 |
| `WSUM_PHYSICAL_COST.md` | `wsum` 三工具交叉验证 | `WSUM_FINAL_VERDICT.md` |
| `WSUM_PLACEMENT_OVERLAP.md` | `wsum` 寄存器重叠（措辞曾过头，已修正）| 同上 |

更早的 12 份（FC 流程探索期）见本目录更早的提交历史。
