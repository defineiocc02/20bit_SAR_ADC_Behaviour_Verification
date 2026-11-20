# 四个模块完整通过 RTL-to-GDS + Formality

时间：2026-11-17　基线：`09f4f58`

## 1. 结果

| # | 模块 | 功能 | Formality | 耗时 |
|:--:|:---|:---|:---|--:|
| 1 | **`sched18`** | D1：18 片 8/8/2 调度器 | **SUCCEEDED 234/0/0** | ~3 min |
| 2 | **`adc2_capture`** | D6：ADC2 捕获与返回 | **SUCCEEDED 490/0/0** | ~3 min |
| 3 | **`acq_conv_ctx`** | D6：受理/转换上下文 | **SUCCEEDED 1037/0/0** | ~4.5 min |
| 4 | **`sar_evt_v21`** | D2：Flash/SAR 逐事件 follower | **SUCCEEDED 133/0/0** | ~2.5 min |

**四次连续一次通过，零调试。**

## 2. 详细数据

| 指标 | `sched18` | `adc2_capture` | `acq_conv_ctx` | `sar_evt_v21` |
|:---|--:|--:|--:|--:|
| **Total cell area** | 778.32 µm² | 731.47 µm² | 1,320.94 µm² | **240.00 µm²** |
| **Routed Wires** | 14,899 | 6,755 | 18,880 | **2,356** |
| **DRC** | 0 | 0 | 0 | **0** |
| **slack (MET)** | +14.93 | +22.54 | +20.87 | **+21.45 ns** |
| **CHK_UNPLACED** | 0 | 0 | 0 | **0** |
| **Formality Passing** | 234 | 490 | 1,037 | **133** |
| **Failing** | 0 | 0 | 0 | **0** |
| DC 单模块面积 | 1,265.38 | 625.73 | 1,255.87 | 254.31 |

**`sar_evt_v21` 的 FC 面积（240.00 µm²）比 DC（254.31 µm²）小 5.6%** —— FC 优化更强。

## 3. 累计规模

| 项 | 四模块合计 |
|:---|--:|
| FC Total cell area | **3,070.73 µm²** |
| Routed Wires | **42,890** |
| DRC | **0** |
| Formality compare points | **1,894**（全 passing）|
| Failing | **0** |

## 4. 覆盖的 D1/D2/D6

| 模块 | 覆盖 |
|:---|:---|
| `sched18` | **D1 全部** |
| `sar_evt_v21` | **D2 全部**：Flash3+SAR6 事件表、guard F=7、apply_fire 资格、原子应用 |
| `adc2_capture` | **D6 大部分** |
| `acq_conv_ctx` | **D6 控制** |

**D1、D2、D6 三条链都已通过物理实现与等价性验证。**

## 5. 剩余 9 个模块

| 模块 | DC 面积 µm² | 预期 |
|:---|--:|:---|
| `dither_pre` | 46.65 | 需改约束（无 clk）|
| `follower_rt` | 145.24 | 低 |
| `dem_phase_rr` | 178.65 | 低 |
| `dem_mask_v3` | 310.07 | 低 |
| `dem_phase_gen` | 472.56 | 低 |
| `recip_q60` | 9,246.99 | 中 |
| `recip_rnd_v21` | 16,619.82 | 中 |
| `recon_v21` | 26,128.47 | 高（alpha 除法器）|
| `wsum_v21` | 102,201.55 | **已确认不可行** |

## 6. 三条限定（不变）

1. 技术 LEF 为推导生成（`AFP-1022` 证实工具从库也推导出同样方向）
2. 无时钟门控
3. 无 LVS / 无 GDS 验证 / 单角 / 无 IR-EM
