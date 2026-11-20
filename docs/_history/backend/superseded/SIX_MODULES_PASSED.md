# 六个模块完整通过 RTL-to-GDS + Formality

时间：2026-11-17　基线：`3a30245`

## 1. 结果

| # | 模块 | 功能 | Formality |
|:--:|:---|:---|:---|
| 1 | `sched18` | D1：18 片 8/8/2 调度器 | **SUCCEEDED 234/0/0** |
| 2 | `adc2_capture` | D6：ADC2 捕获与返回 | **SUCCEEDED 490/0/0** |
| 3 | `acq_conv_ctx` | D6：受理/转换上下文 | **SUCCEEDED 1037/0/0** |
| 4 | `sar_evt_v21` | D2：Flash/SAR 逐事件 | **SUCCEEDED 133/0/0** |
| 5 | **`dem_phase_rr`** | **D3：DEM 相位发生器** | **SUCCEEDED 97/0/0** |
| 6 | **`follower_rt`** | **D2：RDAC follower** | **SUCCEEDED 72/0/0** |

**六个模块全部 Formality `SUCCEEDED`，0 failing。**

## 2. 覆盖的模块

| 模块 | 覆盖的链 |
|:---|:---|
| `sched18` | **D1 全部** |
| `sar_evt_v21` | **D2 全部**：事件表、guard F=7、apply_fire |
| `follower_rt` | **D2**：RDAC 逐事件累加 |
| `dem_phase_rr` | **D3**：相位发生器 |
| `adc2_capture` | **D6 大部分** |
| `acq_conv_ctx` | **D6 控制** |

**D1、D2、D6 三条链完整覆盖；D3 覆盖相位发生器。**

## 3. 两个模块的 DRC 问题（未通过）

| 模块 | 放置 | 布线 | DRC | Formality |
|:---|:---|:---|:---|:---|
| `dither_pre` | ✅ `rc=0` | ✅ `rc=0` | ❌ **5** | 未跑 |
| `dem_mask_v3` | ✅ `rc=0` | ✅ `rc=0` | ❌ **79** | 未跑 |

**根因**：这两模块的 core 只有 67.2 × 63 µm（`sched18` 是 334.6 × 329），
`place_opt` 插入的单元放得下但布线密度过高。

- 调用 `initialize_floorplan` → 破坏 `compile_fusion` 建的 site row，放置失败
- 不调用 → 放置成功，但没有 die/core 边界，布线 DRC 不为 0

**两条路都有问题，需要第三种做法。**

## 4. 尝试失败的方向

| 尝试 | 结果 |
|:---|:---|
| 在 `compile_fusion` 前 `set_app_options compile.auto_floorplan.enable false` | 命令返回成功，但之后 `compile_fusion` 无输出（可能卡住）|

## 5. 累计交付

| 项 | 六模块合计 |
|:---|--:|
| Formality compare points | **2,063**（全 passing）|
| Failing | **0** |
| DRC（六个模块） | 全 **0** |

（`dem_phase_rr` / `follower_rt` 的面积数据需从各自日志补取）

## 6. 剩余 7 个模块

| 模块 | DC 面积 µm² | 状态 |
|:---|--:|:---|
| `dither_pre` | 46.65 | 放置通过，DRC 5 |
| `dem_mask_v3` | 310.07 | 放置通过，DRC 79 |
| `dem_phase_gen` | 472.56 | 未跑 |
| `recip_q60` | 9,246.99 | 未跑 |
| `recip_rnd_v21` | 16,619.82 | 未跑 |
| `recon_v21` | 26,128.47 | 未跑（alpha 除法器）|
| `wsum_v21` | 102,201.55 | **已确认不可行** |

## 7. 三条限定（不变）

技术 LEF 为推导生成 / 无时钟门控 / 无 LVS、无 GDS 验证、单角、无 IR-EM
