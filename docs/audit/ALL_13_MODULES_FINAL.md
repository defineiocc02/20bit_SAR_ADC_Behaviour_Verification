# 13 个模块后端状态（已按独立复核更正）

时间：2026-11-17　基线：`d8b5f37` 之后
更正依据：Codex 独立复核意见（本文件为更正后版本）

## 0. 本版相对上一版的四处更正

| # | 上一版说法 | 更正后 | 依据 |
|:--:|:---|:---|:---|
| 1 | `DRC` 列 | 改名 **`router DRC`**，并声明**无 PG 布线几何、无 foundry DRC、无 LVS** | `sched18_final.def` 的 `SPECIALNETS` 中 VDD/VSS 仅 `( PIN VDD_PORT )` + `( * VDD )`，**无 `+ ROUTED`**；`NETS` 段 VDD/VSS 计数为 0 |
| 2 | 「D5 四模块物理不可行」 | **「这些冻结输入与预算下未完成」**，逐模块注明真实原因 | 见 §3 |
| 3 | 「D5 占数字面积 95.8%」 | **删除** | `recon_v21` 实例化 `recip_rnd_v21`，`recip_rnd_v21` 实例化 `recip_q60`；13 个独立层级面积**不可相加** |
| 4 | 「D1/D2/D3/D6 完整可交付」 | D2、D3 相位为**模块级**证据，**未在顶层实例化** | `grep adc_digital_ip_v21.sv` 零命中 |

## 1. 总表

| # | 模块 | DC 面积 µm² | 后端 | router DRC | Formality |
|:--:|:---|--:|:---|--:|:---|
| 1 | `sched18` | 1,265.38 | ✅ 完整 | 0 | **SUCCEEDED 234/0/0** |
| 2 | `sar_evt_v21` | 254.31 | ✅ 完整 | 0 | **SUCCEEDED 133/0/0** |
| 3 | `dem_phase_rr` | 178.65 | ✅ 完整 | 0 | **SUCCEEDED 97/0/0** |
| 4 | `follower_rt` | 145.24 | ✅ 完整 | 0 | **SUCCEEDED 72/0/0** |
| 5 | `dem_phase_gen` | 472.56 | ✅ 完整 | 0 | **SUCCEEDED 126/0/0** |
| 6 | `adc2_capture` | 625.73 | ✅ 完整 | 0 | **SUCCEEDED 490/0/0** |
| 7 | `acq_conv_ctx` | 1,255.87 | ✅ 完整 | 0 | **SUCCEEDED 1037/0/0** |
| 8 | `dither_pre` | 46.65 | ✅ 完整 | **2** | 未跑 |
| 9 | `dem_mask_v3` | 310.07 | ✅ 完整 | **27** | 未跑 |
| 10 | `recip_q60` | 9,246.99 | ⏸ 未完成 | — | — |
| 11 | `recip_rnd_v21` | 16,619.82 | ⏸ 未完成 | — | — |
| 12 | `recon_v21` | 26,128.47（**alpha 旁路诊断值**）| ⏸ 未完成 | — | — |
| 13 | `wsum_v21` | 102,201.55 | ⏸ 未完成 | — | — |

**「router DRC」是工具报告值，不等于 foundry DRC。所有 7 个通过模块的 DEF 均无 PG 布线几何。**

## 2. 七个通过模块的实测数据

| 模块 | FC 面积 µm² | router DRC | Formality Passing | Failing |
|:---|--:|--:|--:|--:|
| `sched18` | 778.32 | 0 | 234 | 0 |
| `adc2_capture` | 731.47 | 0 | 490 | 0 |
| `acq_conv_ctx` | 1,320.94 | 0 | 1,037 | 0 |
| `sar_evt_v21` | 240.00 | 0 | 133 | 0 |
| `dem_phase_rr` | 85.26 | 0 | 97 | 0 |
| `follower_rt` | 135.63 | 0 | 72 | 0 |
| `dem_phase_gen` | 268.81 | 0 | 126 | 0 |
| **合计** | **3,560.43** | **全 0** | **2,189** | **0** |

**这七个模块的证据范围**：对应源码 + 该技术环境下的**独立模块**实现与等价证据。

**不包含**：电源布线、foundry DRC、LVS、多 PVT 角、IR/EM、全芯片集成。

## 3. 四个未完成模块的真实原因（不作「不可行」推断）

| 模块 | 停在哪里 | **真实原因** | 不能据此断言什么 |
|:---|:---|:---|:---|
| `wsum_v21` | `compile_fusion` 内 `Optimization (5)` 后 | **预算到期**（38 分钟）| 不能断言综合不可完成 |
| `recon_v21` | `compile_fusion` 内 `Auto-Floorplan (2)` | **预算到期**（约 21 分钟）| 不能断言综合不可完成 |
| `recip_q60` | `route_auto` 后 `Routed14/225` | **我方人工终止**（约 25 分钟）| 不能断言密度超标或不会收敛 |
| `recip_rnd_v21` | `clock_opt` 中 `XFORM` fatal | **未合法放置后继续 CTS** | 不能断言宽位数或工具缺陷 |

### 3.1 `recip_q60` 的中间阶段与最终阶段不可混用

同一轮日志里有两段布线，**必须分开看**：

```
compile_fusion 内嵌试布线（早期）：
  Routed223/225 Partitions, Violations =2
  Routed224/225 Partitions, Violations =2
  Routed225/225 Partitions, Violations =2   ← 走完 225/225，收敛到 2

CTS 之后 route_auto（第 14513 行起）：
  Routed10/225 Partitions, Violations =8528
  Routed14/225 Partitions, Violations =8551  ← 到 14/225 被人工终止
```

**早期能收敛到 2，说明「密度不可收敛」的推断不成立。** 上一版把 14/225 的累计违规线性外推到「约 6.7 小时、密度超标」，**该外推无效，已删除**。

### 3.2 `recip_rnd_v21` 的事件顺序

```
第 4826 行: Error: place_opt bailed out due to unplaced cells. (OPT-062)
            未放置单元报错 608 条（u_recip/ctmi_*）
第 4851 行: set rc2 [catch {clock_opt} e2]     ← 未过门禁仍继续
第 4852 行: Starting 'clock_opt'
            随后 XFORM fatal: HFSINV_3244_5159/ZN
```

**先有 `OPT-062` 与未放置，再进 `clock_opt` 才 fatal。**
上一版把 fatal 归因于「高扇出/宽位宽触发工具缺陷」，**未先排除前置的非法放置状态，该归因已撤回**。

## 4. 模块层级嵌套（面积不可横向相加）

源码确认的三层嵌套：

```
recon_v21  → recip_rnd_v21 u_recip_rnd    (recon_v21.sv:314)
recip_rnd_v21 → recip_q60 u_recip          (recip_rnd_v21.sv:59)
```

**因此 13 个模块的 DC 面积直接相加会重复计入 `recip_q60` 与 `recip_rnd_v21`。**
上一版的「13 模块合计 160,951 µm²」「D5 占 95.8%」**均已撤回**。

## 5. 顶层实际实例化（8 个）

`rtl/v21/adc_digital_ip_v21.sv` 中确认的实例：

| 行 | 实例 | 模块 |
|--:|:---|:---|
| 192 | `u_pd` | `dither_pre` |
| 204 | `u_sched` | `sched18` |
| 219 | `u_ctx` | `acq_conv_ctx` |
| 293 | `u_evt` | `sar_evt_v21` |
| 320 | `u_dem` | `dem_mask_v3` |
| 478 | `u_adc2` | `adc2_capture` |
| 572 | `u_wsum` | `wsum_v21` |
| 611 | `u_recon` | `recon_v21` |

**`dem_phase_rr`、`dem_phase_gen`、`follower_rt` 未被顶层实例化。**

所以表 2 中这三个模块的 `SUCCEEDED` 是**模块级**证据，
**不能表述为「D2 / D3 片上链已接通」**。

## 6. 时序口径

七个通过模块的 `slack (MET)` 均为正（+12.10 ~ +22.54 ns，25 ns 周期）。

**其含义限于**：该模块在 25 ns 单段约束下时序收敛、端口齐备。

**不能推出**：整笔 40 MS/s 吞吐成立、或与论文披露的单时钟接口一致。

## 7. ADC2 位宽

**仍为 `[U]` 待裁定（13 vs 14）。** 本轮工具结果不改动该判定。

现有工程合同与源码取 **13 位**（`adc2_raw_i[12:0]`），与 §9.3 常数反推一致；
但二级量化误差 0.60 LSB₂₀ > 0.5 的问题仍未解决。

## 8. 交付边界

**可以说**：
- 13 个模块全部完成 DC 综合，均满足 25 ns 单段约束。
- 7 个模块完成了从 RTL 到 GDS 的完整流程，`router DRC = 0`，且各自 Formality `SUCCEEDED`（合计 2,189 点，0 failing）。
- 4 个模块在冻结输入与给定预算下未完成，原因已逐条列明。

**不可以说**：
- foundry DRC clean、LVS clean、tapeout signoff。
- 电源/全芯片签核。
- D5「算法不可实现」或「同一个大位宽根因」。
- D2 / D3 片上链已接通。
- D5 占整核 95.8%。

## 9. 三条硬性限定（不变）

1. 技术 LEF 为推导生成，非 PDK 官方交付（`AFP-1022` 证方向推导与工具一致）。
2. 无时钟门控（带门控版 Formality 为 FAILED，20 点，根因 `clock_gate_acq_q_reg/*lat.00*`）。
3. 无 PG 布线几何、无 foundry DRC、无 LVS、无多 PVT 角、无 IR/EM。
