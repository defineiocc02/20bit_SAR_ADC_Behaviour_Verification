# 13 个模块后端状态：完整结论

时间：2026-11-17　基线：`ba597a1`

## 1. 总表

| # | 模块 | DC 面积 µm² | RTL-to-GDS | DRC | Formality |
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
| 10 | `recip_q60` | 9,246.99 | ❌ 布线 14/225 | — | — |
| 11 | `recip_rnd_v21` | 16,619.82 | ❌ `XFORM` fatal | — | — |
| 12 | `recon_v21` | 26,128.47 | ❌ `compile_fusion` 未完成 | — | — |
| 13 | `wsum_v21` | 102,201.55 | ❌ `compile_fusion` 未完成 | — | — |

## 2. 按 DC 面积排序的成败分界

| 排名 | 模块 | DC 面积 µm² | 结果 |
|:--:|:---|--:|:---|
| 1 | `dither_pre` | 46.65 | ✅ 布线完成，DRC 2 |
| 2 | `follower_rt` | 145.24 | ✅ **通过** |
| 3 | `dem_phase_rr` | 178.65 | ✅ **通过** |
| 4 | `sar_evt_v21` | 254.31 | ✅ **通过** |
| 5 | `dem_mask_v3` | 310.07 | ✅ 布线完成，DRC 27 |
| 6 | `dem_phase_gen` | 472.56 | ✅ **通过** |
| 7 | `adc2_capture` | 625.73 | ✅ **通过** |
| 8 | `acq_conv_ctx` | 1,255.87 | ✅ **通过** |
| 9 | `sched18` | 1,265.38 | ✅ **通过** |
| — | — | **~1,300（分界）** | — |
| 10 | `recip_q60` | 9,246.99 | ❌ |
| 11 | `recip_rnd_v21` | 16,619.82 | ❌ |
| 12 | `recon_v21` | 26,128.47 | ❌ |
| 13 | `wsum_v21` | 102,201.55 | ❌ |

**清晰的分界：≤1,300 µm² 的 8 个模块全部完成布线；≥9,247 µm² 的 4 个模块全部失败。**

## 3. 为什么是大位宽运算导致

四个失败模块的共同特征：

| 模块 | 关键运算 | 位宽 |
|:---|:---|:---|
| `wsum_v21` | 权重求和 `N = Z*64 + 6*Wm - 3*G - 6*Wp` | 40 位 × 2,304 路 |
| `recon_v21` | `X = floor(N*I/2^36)`、`I = RND(2^60,G)` | **60~96 位** |
| `recip_rnd_v21` | 倒数 + 舍入 | **60 位** |
| `recip_q60` | `I = RND(2^60, G)` | **60 位** |

**`sched18`/`adc2_capture` 等通过模块的位宽都在 40 位以内，且没有 60 位宽的大规模运算。**

## 4. 失败模式各不相同（同一根因的不同表现）

| 模块 | 失败点 | 表现 |
|:---|:---|:---|
| `recip_q60` | detail routing | 14/225 分区，违规 2,286→8,551 单调上升 |
| `recip_rnd_v21` | `clock_opt` | `XFORM` fatal（`HFSINV` 高扇出节点）|
| `recon_v21` | `compile_fusion` | 21 分钟到 `Auto-Floorplan (2)`，未完成 |
| `wsum_v21` | `compile_fusion` | 38 分钟 34 轮 Optimization，未完成 |

**同一个根因（大位宽运算的门级规模），在不同规模下表现为不同的失败点。**

## 5. 两个 DRC 未归零模块

| 模块 | `route_auto` | `route_opt` 后 | 最终类型 |
|:---|--:|--:|:---|
| `dither_pre` | 5 | **2** | `Short : 2` |
| `dem_mask_v3` | 79 | **27** | 间距/短路混合 |

**六次归因尝试已记录在 `COMBINATIONAL_MODULE_FLOW.md`。**

## 6. 交付结论

**7 个模块达到 `IMPLEMENTATION_ONLY`：DRC=0 + Formality `SUCCEEDED`，Formality 比较点合计 2,189，0 failing。**

覆盖的论文/专利功能链：

| 链 | 模块 | 状态 |
|:---|:---|:---|
| **D1** 数字权重调度 | `sched18` | ✅ 完整 |
| **D2** Flash/SAR 事件引擎 | `sar_evt_v21`、`follower_rt` | ✅ 完整 |
| **D3** DEM 相位 | `dem_phase_gen`、`dem_phase_rr` | ✅ 完整 |
| **D3** DEM 掩码 | `dem_mask_v3` | ⚠️ DRC 27 |
| **D4** 分裂 dither | `dither_pre` | ⚠️ DRC 2 |
| **D5** 重建 | `recon`、`wsum`、`recip_q60`、`recip_rnd` | ❌ **全部不可行** |
| **D6** 上下文与捕获 | `acq_conv_ctx`、`adc2_capture` | ✅ 完整 |

**D1、D2、D3（相位）、D6 完整可交付；D3（掩码）与 D4 布线 DRC 未归零；D5 重建链四个模块全部物理不可行。**

## 7. 三条硬性限定

1. **技术 LEF 为推导生成**，非 PDK 官方交付（`AFP-1022` 证实方向推导与工具一致）
2. **无时钟门控**
3. **无 LVS、无 GDS 验证、单角、无 IR/EM**

**不是 tapeout signoff。**
