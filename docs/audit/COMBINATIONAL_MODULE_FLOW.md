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


---

## 7. 第五次尝试：`optimize_small_design`（失败）

### 做法

```tcl
# 在 compile_fusion 之前
set_app_options -name compile.auto_floorplan.optimize_small_design -value true
```

### 效果

core 尺寸确实变了：

```
Chip Core shape: (0 0) (67200 63000)       ← 之前（67.2 × 63 µm）
Chip Core shape: (0 0) (1034600 945000)    ← 现在（约 517 × 472 µm）
```

**扩大 15.4 倍。**

### 但 DRC 没变

| 配置 | core | Routed Wires | DRC |
|:---|:---|--:|--:|
| 默认 | 67.2 × 63 µm | 576 | **5** |
| `optimize_small_design=true` | 517 × 472 µm | 576 | **5** |

违规明细完全相同：

```
Diff net spacing : 3
End of line spacing : 2
```

**core 扩大 15 倍，DRC 数字一模一样 —— 说明 core 尺寸不是原因。**

## 8. 已排除了五个方向

| # | 假设 | 验证 | 结论 |
|:--:|:---|:---|:---|
| 1 | `clock_opt` 失败导致未放置 | 跳过 `clock_opt` | 仍 89 ❌ |
| 2 | core 尺寸不够 | `core_utilization` 0.6→0.3 | 尺寸未变 ❌ |
| 3 | 手工 `create_site_row` 破坏 | 去掉手工建 row | 仍 89 ❌ |
| 4 | 多调 `initialize_floorplan` | 不调用 | 放置 `rc=0` ✅，但 **DRC≠0** |
| 5 | 小设计 floorplan 不够 | `optimize_small_design=true` | core 扩大 15 倍，**DRC 仍 5** ❌ |

## 9. 技术 LEF 的间距规则是完整的

```
LAYER 数：18（M1~M9、AP、VIA1~VIA8）
SPACING 覆盖：M1 M2 M3 M4 M5 M6 M7 M8 M9 AP  ← 全部 10 个布线层
```

**所以 DRC ≠ 0 不是因为我生成的技术 LEF 缺规则。**

## 10. 当前诚实结论

**`dither_pre`（33 µm²、576 条线、5 个 DRC）与 `dem_mask_v3`（116 µm²、2505 条线、79 个 DRC）
在当前的流程配置下无法获得 DRC=0 的布线结果。**

| 模块 | 放置 | 布线 | DRC | Formality |
|:---|:---|:---|:---|:---|
| `dither_pre` | ✅ rc=0 / 0 未放置 | ✅ rc=0 | ❌ **5** | 未跑 |
| `dem_mask_v3` | ✅ rc=0 / 0 未放置 | ✅ rc=0 | ❌ **79** | 未跑 |

**两个模块都不能算通过。**

### 未尝试的方向（供后续参考）

| 方向 | 说明 |
|:---|:---|
| 用真实的 PDK 技术 LEF | 当前是推导生成的，可能缺 NDR/天线规则 |
| 加 `set_route_options` 增加布线迭代次数 | |
| 手工修 DRC（5 个可定位）| `dither_pre` 只有 5 个，逐个修可行 |

**不再继续在这个方向上盲目试探。** 六个模块已通过 Formality，这是已固化的成果。


---

## 11. 第六次尝试：`route_opt`（有效但未归零）

### 做法

```tcl
route_auto
route_opt        # 详细布线后优化，修残留 DRC
```

### 结果

| 模块 | `route_auto` 后 | `route_opt` 后 | 改善 | 最终违规类型 |
|:---|--:|--:|--:|:---|
| `dither_pre` | **5** | **2** | 60% | `Short : 2` |
| `dem_mask_v3` | **79** | **27** | 66% | 见下表 |

`dem_mask_v3` 的 27 个违规明细：

```
Diff net spacing               : 7
Diff net via-cut spacing       : 1
End of line spacing            : 7
Fat wire via keepout enclosure : 1
Less than minimum edge length  : 4
Same net spacing               : 1
Short                          : 6
```

### 关键判断

**`route_opt` 确实有效（减少 60~66%），但无法归零。**

`dither_pre` 只剩 **2 个 `Short`** —— 这个量级已经很小。

### 六个方向的完整排除表

| # | 尝试 | 结果 |
|:--:|:---|:---|
| 1 | 跳过 `clock_opt` | 仍 89 未放置 ❌ |
| 2 | `core_utilization` 0.6→0.3 | 尺寸未变 ❌ |
| 3 | 去掉手工 `create_site_row` | 仍 89 ❌ |
| 4 | 不调 `initialize_floorplan` | 放置 ✅ 但 DRC 5 / 79 ⚠️ |
| 5 | `optimize_small_design=true` | core 大 15× 但 DRC 不变 ❌ |
| **6** | **加 `route_opt`** | **DRC 5→2 / 79→27，未归零** ⚠️ |

## 12. 最终状态

| 模块 | 放置 | 布线 | DRC（`route_opt` 后） | Formality |
|:---|:---|:---|--:|:---|
| `dither_pre` | ✅ `rc=0` | ✅ `rc=0` | **2**（`Short`）| 可跑（组合逻辑）|
| `dem_mask_v3` | ✅ `rc=0` | ✅ `rc=0` | **27** | 可跑（组合逻辑）|

**两个模块都不能算 DRC 干净。**

## 13. 根因推测（未验证）

`dither_pre` 剩余 2 个 `Short` 和 `dem_mask_v3` 的多类间距违规，
**高度可能是技术 LEF 缺少真实 PDK 才有的 NDR（Non-Default Rule）与天线规则。**

依据：
- `route_opt` 能减少但无法归零
- 六个通过的模块规模都 ≥ 2,338 cells，这两个只有 99 / 641 cells
- 技术 LEF 的 `SPACING` 覆盖完整（M1~M9+AP 全 10 层），但**没有 NDR**

**这是推测，不是结论。** 验证需要真实的 PDK 技术 LEF。

## 14. 交付建议

| 模块 | 建议 |
|:---|:---|
| `dither_pre` | 记录为"DRC 未归零（2 个短路）"，或手工修这 2 处后重跑 DRC |
| `dem_mask_v3` | 记录为"DRC 未归零（27 个违规）" |

**六个已通过模块（2,063 个 Formality 比较点全 passing、DRC 全 0）的成果不受影响。**
