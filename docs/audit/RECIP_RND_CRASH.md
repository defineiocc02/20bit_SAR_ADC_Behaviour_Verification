# `recip_rnd_v21`：`clock_opt` 中 XFORM fatal 崩溃

时间：2026-11-17　基线：`6b79f9c`

## 1. 结果：工具内部致命错误

**`recip_rnd_v21` 的 RTL-to-GDS 在 `clock_opt / final_opto` 阶段崩溃。**

日志最后三行（文件共 11,217 行，第 11,215 行是最后一条有效输出）：

```
XFORM[117217] ----------------------------------------------------------------------------------
XFORM[117217] Detected fatal while optimizing near HFSINV_3244_5159/ZN
XFORM[117217] ----------------------------------------------------------------------------------
```

**`XFORM` 是 Fusion Compiler 的转换/优化引擎。它在优化 `HFSINV_3244_5159/ZN` 这个网络时检测到 fatal，随后进程终止，没有继续执行。**

## 2. 已完成与未完成

| 阶段 | 状态 | 说明 |
|:---|:---|:---|
| `STEP_CREATE_LIB_OK` | ✅ | |
| `STEP_ELAB_OK` | ✅ | |
| `STEP_TLU_OK` | ✅ | |
| `STEP_CONSTRAINT_OK` | ✅ | |
| `STEP_SYNTH_OK` | ✅ | 综合通过（约 9 分钟）|
| `STEP_FLOORPLAN_OK` | ✅ | |
| `STEP_PLACE rc=1` | ⚠️ | 有未放置单元 |
| `STEP_CTS` | ❌ | **未出现：在 `clock_opt` 崩溃** |
| `STEP_ROUTE` | ❌ | |
| 导出 `.v` / `.def` / `.gds` | ❌ | **无产物** |
| Formality | ❌ | 无网表 |

## 3. 崩溃前的布线数据（综合内嵌阶段）

崩溃前曾出现：

```
Routed357/361 Partitions, Violations =53
Routed358/361 Partitions, Violations =24
Routed359/361 Partitions, Violations =1
Routed360/361 Partitions, Violations =1
Routed361/361 Partitions, Violations =1
Routed1/1 Partitions, Violations =0
Total Number of Routed Wires = 451
Total number of DRCs = 0
```

**这是 `compile_fusion` 内部嵌的试布线，361 个分区全部完成、违规归零。**
**但这不等于最终布线 —— 因为 `place_opt` 有未放置单元，且随后 `clock_opt` 崩溃。**

## 4. 未放置单元

```
Error: place_opt bailed out due to unplaced cells. (OPT-062)
Error: Cell u_recip/ctmi_28860 is not placed. (ZRT-064)
Error: Cell u_recip/ctmi_28861 is not placed. (ZRT-064)
```

**`u_recip/` 是层次化子模块实例**，`recip_rnd_v21` 内部的 `recip_q60` 实例。

## 5. 与其他模块的对比

| 模块 | DC 面积 | 失败点 | 性质 |
|:---|--:|:---|:---|
| `recip_q60` | 9,247 µm² | detail routing 14/225 | 密度超标，可观察 |
| **`recip_rnd_v21`** | **16,620 µm²** | **`clock_opt` XFORM fatal** | **工具崩溃** |
| `wsum_v21` | 102,202 µm² | `compile_fusion` 未完成 | 超时 |

**三种不同的失败模式，都不是配置问题。**

## 6. 网格规模

```
Number of partitions: 1 (1 x 1)
Size of partitions: 177 gCells x 177 gCells
```

**177×177，比 `recip_q60`（143×143）大 1.5 倍。**

## 7. 结论

**`recip_rnd_v21` 的物理实现在当前配置下不可行**，但失败方式与另外两个不同：
- **不是超时**（是工具崩溃）
- **不是密度**（试布线违规曾归零）
- **是 `XFORM` 引擎在优化高扇出网络（`HFSINV`）时 fatal**

**`HFSINV` = High Fanout Synthesis Inverter**，即高扇出缓冲插入的反相器。
`recip_rnd_v21` 的 60 位运算产生高扇出节点，`clock_opt` 期间的优化在这些节点上触发工具缺陷。

## 8. 状态

| 项 | 状态 |
|:---|:---|
| 综合 | ✅ 通过 |
| floorplan | ✅ 通过 |
| **CTS / 布线 / 导出** | ❌ **崩溃** |
| Formality | ❌ 无网表 |
| 产物 | **无** |
