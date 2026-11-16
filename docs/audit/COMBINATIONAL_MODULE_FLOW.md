# 纯组合模块的后端流程：四次归因与最终结果

时间：2026-11-17　基线：`ddce5bf`

## 1. 最终结论（先说结果）

**`dither_pre` / `dem_mask_v3` 的失败根因是：脚本里多调用了一次 `initialize_floorplan`。**

| 配置 | `STEP_PLACE` | `CHK_UNPLACED` |
|:---|:---|--:|
| 有 `initialize_floorplan`（原脚本） | `rc=1` | **89 / 258** |
| **不调用 `initialize_floorplan`** | **`rc=0`** | **0** |

**`compile_fusion` 内部已经建好 site row（实测 `CHK_BEFORE_FP_ROWS 9`），
再调用一次 `initialize_floorplan` 会把它破坏掉。**

## 2. 四次归因，前三次都错

| 次数 | 归因 | 验证方式 | 结果 |
|:--:|:---|:---|:---|
| 1 | `clock_opt` 失败导致未放置 | 跳过 `clock_opt` | **仍 89** ❌ |
| 2 | core 尺寸不够 | `core_utilization` 0.6→0.3 | **core 尺寸没变，仍 89** ❌ |
| 3 | 手工 `create_site_row` 破坏 | 去掉手工建 row | **仍 89** ❌ |
| **4** | **多调一次 `initialize_floorplan`** | **不调用** | **`rc=0`、0 未放置** ✅ |

**前三次都是"看起来合理"的猜测，每次都只改一个变量但数字没变 —— 数字没变就说明归因错。**

第四次成功，是因为对照实验脚本 `test_auto.tcl` 里**恰好没有** `initialize_floorplan`，
逐项 diff 后才看出来。

## 3. 当前的遗留问题：布线 DRC

去掉 floorplan 后放置成功，但**布线产生 DRC**：

| 模块 | Routed Wires | DRC | 面积 |
|:---|--:|--:|--:|
| `dither_pre` | 576 | **5** | 33.22 µm² |
| `dem_mask_v3` | 2,505 | **79** | 116.13 µm² |

`dither_pre` 的违规明细：

```
Diff net spacing : 2
End of line spacing : 3
TOTAL VIOLATIONS = 5
```

**这是因为没有 floorplan 就没有 die/core 边界，布线密度不受控。**

## 4. 所以正确的做法应该是

| 做法 | 结果 |
|:---|:---|
| ❌ 完全不调用 `initialize_floorplan` | 放置成功但 DRC 不为 0 |
| ❌ 调用 `initialize_floorplan`（在 `compile_fusion` 之后） | 破坏 site row，放置失败 |

**需要的是：让 `compile_fusion` 建的 site row 保留，同时补上 die/core 边界。**

可能的方向：
- 在 `compile_fusion` **之前**调 `initialize_floorplan`（先建边界，综合时保留）
- 或用 `create_floorplan` / `set_boundary` 之类只设边界、不动 site row 的命令

## 5. 对比：为什么时序模块没这个问题

已通过的 4 个时序模块（`sched18` / `adc2_capture` / `acq_conv_ctx` / `sar_evt_v21`）
**都成功调用了 `initialize_floorplan`**，且 `CHK_UNPLACED = 0`、`DRC = 0`。

**差异原因还需查明**，但可能与模块的实例数量、core 尺寸需求有关。

## 6. 状态

| 模块 | 放置 | 布线 | DRC | Formality |
|:---|:---|:---|:---|:---|
| `dither_pre` | ✅ rc=0 | ✅ rc=0 | ❌ **5** | 未跑 |
| `dem_mask_v3` | ✅ rc=0 | ✅ rc=0 | ❌ **79** | 未跑 |
| `dem_phase_rr` | ✅ | ✅ | ✅ 0 | 未跑 |
| `follower_rt` | ✅ | ✅ | ✅ 0 | 未跑 |

**四个模块都还没有跑 Formality，不能算通过。**
