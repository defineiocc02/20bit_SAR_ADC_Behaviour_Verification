# 数字 IP 后端最终状态

时间：2026-11-17　基线：`70ea0bd` 之后

## 1. 总览：13 个模块

| # | 模块 | RTL-to-GDS | DRC | Formality |
|:--:|:---|:---|--:|:---|
| 1 | `sched18` | ✅ | 0 | **SUCCEEDED 234/0/0** |
| 2 | `adc2_capture` | ✅ | 0 | **SUCCEEDED 490/0/0** |
| 3 | `acq_conv_ctx` | ✅ | 0 | **SUCCEEDED 1037/0/0** |
| 4 | `sar_evt_v21` | ✅ | 0 | **SUCCEEDED 133/0/0** |
| 5 | `dem_phase_rr` | ✅ | 0 | **SUCCEEDED 97/0/0** |
| 6 | `follower_rt` | ✅ | 0 | **SUCCEEDED 72/0/0** |
| 7 | **`dem_phase_gen`** | ✅ | **0** | **SUCCEEDED 126/0/0** |
| 8 | `dither_pre` | ✅ | **2** | 未跑 |
| 9 | `dem_mask_v3` | ✅ | **27** | 未跑 |
| 10 | `wsum_v21` | ❌ 超时 | — | — |
| 11 | `recip_q60` | 未跑 | — | — |
| 12 | `recip_rnd_v21` | 未跑 | — | — |
| 13 | `recon_v21` | 未跑 | — | — |

**7 个模块完整通过（DRC=0 且 Formality SUCCEEDED）。**

## 2. 七个通过模块的数据

| 模块 | FC 面积 µm² | Routed Wires | DRC | slack (MET) | Formality Passing |
|:---|--:|--:|--:|--:|--:|
| `sched18` | 778.32 | 14,899 | 0 | +14.93 ns | 234 |
| `adc2_capture` | 731.47 | 6,755 | 0 | +22.54 ns | 490 |
| `acq_conv_ctx` | 1,320.94 | 18,880 | 0 | +20.87 ns | 1,037 |
| `sar_evt_v21` | 240.00 | 2,356 | 0 | +21.45 ns | 133 |
| `dem_phase_rr` | 需补 | 需补 | 0 | 需补 | 97 |
| `follower_rt` | 需补 | 需补 | 0 | 需补 | 72 |
| `dem_phase_gen` | **268.81** | **5,308** | **0** | **+12.10 ns** | **126** |

**Formality 比较点合计：** 234+490+1037+133+97+72+126 = **2,189，全部 passing，0 failing。**

## 3. 覆盖的论文/专利功能链

| 链 | 模块 | 状态 |
|:---|:---|:---|
| **D1** 数字权重调度器 | `sched18` | ✅ 完整 |
| **D2** Flash/SAR 事件引擎 | `sar_evt_v21`、`follower_rt` | ✅ 完整 |
| **D3** DEM | `dem_phase_gen`、`dem_phase_rr`、`dem_mask_v3` | ✅ 相位链通过；mask 掩码 DRC≠0 |
| **D4** 分裂 dither | `dither_pre` | ⚠️ DRC 2 |
| **D5** 重建 | `recon_v21`、`wsum_v21`、`recip_q60`、`recip_rnd_v21` | ❌ `wsum` 不可行；`recon`/`recip` 未跑 |
| **D6** 上下文与捕获 | `acq_conv_ctx`、`adc2_capture` | ✅ 完整 |

## 4. 三种非通过状态及原因

### ① `dither_pre` / `dem_mask_v3`：DRC 未归零

| 模块 | `route_auto` | `route_opt` 后 |
|:---|--:|--:|
| `dither_pre` | 5 | **2**（`Short`）|
| `dem_mask_v3` | 79 | **27** |

**六次归因尝试都已记录**（见 `COMBINATIONAL_MODULE_FLOW.md`）。

### ② `wsum_v21`：物理实现不可行

- DC：153,735 cells / 102,202 µm² / 515 s
- FC：`compile_fusion` 超过 38 分钟未完成
- 52 次 `ZRT-763` 重叠（寄存器组宽 3.08 µm = 22× site）

### ③ `recip_q60` / `recip_rnd_v21` / `recon_v21`：未跑

DC 数据有（9,247 / 16,620 / 26,128 µm²），但未做物理实现。

## 5. 三条硬性限定

1. **技术 LEF 为推导生成**（`tsmc28_tech_derived.lef`）。
   `PITCH`/`WIDTH`/`SPACING` 来自 PDK `.tf` 真实值；
   `DIRECTION` 按 M1 水平/M2 垂直交替补；`AP` 层按典型值补。
   **`AFP-1022` 证实工具从库也推导出同样方向。**

2. **无时钟门控**（`set_clock_gating_options -minimum_bitwidth 1000000`）。
   带门控版本的 Formality 为 FAILED（20 点，根因 `clock_gate_acq_q_reg/*lat.00*`）。

3. **无 LVS、无 GDS 验证、单角、无 IR/EM。**

**结论：7 个模块达到 `IMPLEMENTATION_ONLY`（DRC=0 + Formality SUCCEEDED），
不是 tapeout signoff。**
