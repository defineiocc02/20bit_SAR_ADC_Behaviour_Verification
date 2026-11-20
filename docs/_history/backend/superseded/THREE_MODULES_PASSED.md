# 三个模块完整通过 RTL-to-GDS + Formality

时间：2026-11-17　基线：`cb6aac6`

## 1. 结果

| # | 模块 | 功能 | RTL-to-GDS | Formality | 耗时 |
|:--:|:---|:---|:---|:---|--:|
| 1 | **`sched18`** | D1：18 片 8/8/2 调度器 | ✅ | **SUCCEEDED 234/0/0** | ~3 min |
| 2 | **`adc2_capture`** | D6：ADC2 捕获与返回 | ✅ | **SUCCEEDED 490/0/0** | ~3 min |
| 3 | **`acq_conv_ctx`** | D6：受理/转换上下文控制 | ✅ | **SUCCEEDED 1037/0/0** | ~4.5 min |

**同一套脚本、tech LEF、site row、UPF，只换 RTL —— 三个模块全部一次通过。**

## 2. 详细数据

| 指标 | `sched18` | `adc2_capture` | `acq_conv_ctx` |
|:---|--:|--:|--:|
| **Total cell area** | 778.32 µm² | 731.47 µm² | **1,320.94 µm²** |
| **Total Routed Wires** | 14,899 | 6,755 | **18,880** |
| **DRC** | 0 | 0 | **0** |
| **slack (MET)** | +14.93 ns | +22.54 ns | **+20.87 ns** |
| **CHK_UNPLACED** | 0 | 0 | **0** |
| **Formality Passing** | 234 | 490 | **1,037** |
| **Formality Failing** | 0 | 0 | **0** |
| 网表 | 190 KB | 101 KB | 264 KB |
| GDS | 2.0 MB | 0.94 MB | 2.5 MB |
| DC 单模块面积 | 1,265.38 | 625.73 | 1,255.87 |

**注意**：`acq_conv_ctx` 的 FC 面积（1,320.94 µm²）比 DC（1,255.87 µm²）略大 5%，
在合理范围内（工具不同、优化目标不同）。

## 3. 累计规模

| 项 | 三模块合计 |
|:---|--:|
| FC Total cell area | **2,830.73 µm²** |
| Routed Wires | **40,534** |
| DRC | **0** |
| Formality compare points | **1,761**（全部 passing）|

## 4. 流程可重复性已充分验证

| 验证项 | 结果 |
|:---|:---|
| 脚本通用性 | ✅ 三模块共用，只改 RTL 路径/模块名/工作目录 |
| tech LEF（推导生成） | ✅ 三个不同模块都适用 |
| site row 生成 | ✅ 通用（48 / 48 / 66 行）|
| UPF + 电压 | ✅ 通用 |
| 禁用时钟门控 | ✅ 通用 |
| Formality 脚本 | ✅ 通用 |

**连续三次一次通过（零调试），流程已确认稳定。**

## 5. 覆盖的 D1/D6 功能

| 模块 | 覆盖 |
|:---|:---|
| `sched18` | **D1 全部**：18 片池、8/8/2 分区、round-robin 选片、IDLE 补集 |
| `adc2_capture` | **D6 大部分**：owner 快照、hold 解耦、epoch 锁存、rsp 返回 |
| `acq_conv_ctx` | **D6 控制**：不可变描述符、两笔流水、受理门禁、stop 排空 |

**D1 和 D6 的核心逻辑都已通过物理实现与等价性验证。**

## 6. 待推进（剩余 10 个模块）

| 模块 | DC 面积 µm² | 预期难度 |
|:---|--:|:---|
| `sar_evt_v21` | 254.31 | 低（最小组）|
| `dem_mask_v3` | 310.07 | 低 |
| `dither_pre` | 46.65 | 低（纯组合，无 clk）|
| `dem_phase_rr` | 178.65 | 低 |
| `dem_phase_gen` | 472.56 | 低 |
| `follower_rt` | 145.24 | 低 |
| `recip_q60` | 9,246.99 | 中 |
| `recip_rnd_v21` | 16,619.82 | 中 |
| `recon_v21` | 26,128.47 | 高（alpha 除法器）|
| `wsum_v21` | 102,201.55 | **已确认不可行** |

**`dither_pre` 无时钟端口，需要调整约束（无 `create_clock`）。**

## 7. 三条限定（不变）

1. 技术 LEF 为推导生成（`AFP-1022` 已证实工具从库也推导出同样方向）
2. 无时钟门控
3. 无 LVS / 无 GDS 验证 / 单角 / 无 IR-EM
