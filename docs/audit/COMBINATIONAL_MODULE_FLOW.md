# 纯组合模块的后端流程差异

时间：2026-11-17　基线：`c8da14a`

## 1. 现象

`dither_pre`（D4 分裂 dither）**没有 `clk` 端口**（纯组合）。
用与时序模块相同的流程跑，结果：

```
STEP_SYNTH_OK        ✅
STEP_FLOORPLAN_OK    ✅
STEP_PLACE rc=1      ❌  89 个单元未放置
STEP_CTS rc=1        ❌  clock_opt 失败
STEP_ROUTE rc=0      ⚠️  命令执行但意义有限
CHK_UNPLACED 89      ❌
```

错误信息：

```
Error: place_opt bailed out due to unplaced cells. (OPT-062)
Error: clock_opt failed during trial clock synthesis prior to useful skew schedule computation.
Error: Cell ctmi_664 is not placed. (ZRT-064)
```

## 2. 根因

**纯组合模块没有时钟域，`clock_opt` 无法执行。**

`ctmi_*` = Clock Tree Macro Instance，是 CTS 插入的时钟树单元。
没有时钟，这些单元无从放置，`place_opt` 的 `OPT-062` 与 `clock_opt` 的失败同源。

**这不是设计缺陷，是流程需要按模块类型调整。**

## 3. 正确的处理

| 模块类型 | 约束 | `clock_opt` |
|:---|:---|:---|
| 时序模块（有 clk） | `create_clock` + IO delay | ✅ 执行 |
| **纯组合（无 clk）** | **不建时钟、不设 IO delay** | **跳过** |

脚本里改为：

```tcl
# 纯组合模块没有时钟，clock_opt 必然失败，跳过它
puts "STEP_CTS SKIPPED (组合模块无时钟)"
```

**明确打印 SKIPPED，不伪装成成功。**

## 4. 哪些模块是纯组合

已核实（`grep -c "input\s+logic\s+clk"`）：

| 模块 | 有 clk | 处理 |
|:---|:---:|:---|
| **`dither_pre`** | **0** | 跳过 CTS |
| **`dem_mask_v3`** | **0** | 跳过 CTS |
| `dem_phase_rr` | 1 | 正常 |
| `follower_rt` | 1 | 正常 |
| `sched18` / `adc2_capture` / `acq_conv_ctx` / `sar_evt_v21` | 1 | 正常（已通过）|

## 5. 对 Formality 的影响

**纯组合模块的 Formality 会更简单**（无 DFF 比较点），但仍能验证组合逻辑等价性。

`CHK_UNPLACED` 仍需为 0 才算通过。

## 6. 待验证

改脚本后重跑 `dither_pre` 与 `dem_mask_v3`，确认：

| 检查项 | 期望 |
|:---|:---|
| `STEP_SYNTH_OK` | ✅ |
| `STEP_FLOORPLAN_OK` | ✅ |
| `STEP_PLACE rc` | 应为 0（不再有 ctmi 单元）|
| `STEP_CTS` | `SKIPPED`（明确打印）|
| `STEP_ROUTE rc=0` | ✅ |
| **`CHK_UNPLACED`** | **0** |
| Formality | 预期 `SUCCEEDED` |
