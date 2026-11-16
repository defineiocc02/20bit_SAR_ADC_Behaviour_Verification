# `wsum_v21` 布局卡住的直接原因：权重寄存器重叠

时间：2026-11-17　基线：`a069bd2`

## 1. 决定性证据

`initial_place` 阶段的告警统计（`MSG-3100` 汇总）：

```
4 unique error and warning message tags while observing compile_fusion / initial_place
34 error&warning MSGs observed
```

其中出现最多的告警：

```
Warning: Cell wt_main_reg[3][4][9] is placed overlapping with other cells
         at {{8.181 0.921} {11.261 1.621}}. (ZRT-763)
Warning: Cell wt_main_reg[4][3][11] is placed overlapping with other cells
         at {{82.637 1.091} {85.577 1.791}}. (ZRT-763)
Warning: Cell wt_main_reg[1][2][16] is placed overlapping with other cells
         at {{216.331 1.367} {219.411 2.067}}. (ZRT-763)
```

| 统计 | 值 |
|:---|:---|
| `ZRT-763` 出现次数 | **50** |
| 涉及的不同 `wt_main_reg[*]` 单元 | **34** |
| 其他高频告警 | `LGL-397` × 49、`ZRT-625` × 10 |

## 2. 几何分析

从告警里的坐标算单元宽度：

```
{8.181 0.921} -> {11.261 1.621}
宽 = 11.261 - 8.181 = 3.080 µm
高 = 1.621 - 0.921  = 0.700 µm
```

**单个 `wt_main_reg[*]` 单元宽 3.08 µm、高 0.70 µm。**

对比 `core7T` site：

| 项 | 值 |
|:---|:---|
| site 宽度 | **0.140 µm** |
| site 高度 | 0.700 µm |
| **单元宽 / site 宽** | **22 倍** |

3.08 / 0.14 = **22 个 site**。

**一个 40 位字的触发器阵列被当成单个"单元"处理，宽度是标准 site 的 22 倍，布局器无法在标准行里无重叠放置。**

## 3. 这与 RTL 结构完全对应

```systemverilog
// rtl/v21/wsum_v21.sv
logic [39:0] wt_main [0:17][0:15];   // 18×16 = 288 个 40 位字
logic [39:0] wt_sub  [0:17][0:3];    // 18×4  =  72 个 40 位字
```

360 个 40 位字 = **14,400 个触发器**。

DC/FC 把每个 40 位字维持成一个"寄存器组"形态，
导致布局时每个字占 3.08 µm 连续宽度。

## 4. 完整因果链（已可闭合）

```
RTL: 18×16 + 18×4 二维寄存器堆（360 个 40 位字）
  ↓
DC 综合：2,304 路 MUX + 14,400 FF → 153,735 cells / 102,202 µm² / 515 s
  ↓
FC compile_fusion：34 轮优化 / 1,150 s
  ↓
FC initial_place：34 个 wt_main_reg 单元宽度 3.08 µm（22× site）
  → 50 次 ZRT-763 重叠告警
  ↓
布局无法收敛 → 25 分钟以上未完成
```

## 5. 这不是工具配置问题

| 已排除 | 依据 |
|:---|:---|
| 技术 LEF | `sched18` 同一套配置 3 分钟跑通 |
| site row | `CHK_SITE_ROWS made=51`，与 `sched18` 同机制 |
| 时钟门控 | 已禁用 |
| VA / UPF | `sched18` 同配置通过 |

**唯一变量：`wsum` 的寄存器堆结构。**

## 6. 结论

**`wsum_v21` 用"二维寄存器堆 + 逐笔查全表"的结构，
其门级规模与单元几何形态都不适合标准单元物理实现。**

具体两条独立证据：
1. **规模**：153,735 cells（`sched18` 的 66 倍），综合 515 s
2. **几何**：单个寄存器组宽 3.08 µm = 22 个 site，布局器无法无重叠放置

## 7. 可行的改法（需人决策）

| 方案 | 做法 | 能解决 |
|:---|:---|:---|
| **A** | 权重表改用**单端口 SRAM** | ✅ 规模与几何都能解决，但需 memory compiler |
| **B** | 用 `set_register_merging` / `-flatten` 让工具自由拆分寄存器组 | ⚠️ 可能解决几何问题，规模仍大 |
| **C** | 改算法：增量更新而非每笔查全表 | ✅ 根治，但需重构 D5 + 重跑全部回归 |
| **D** | 接受：`wsum` 只做 DC 综合与功能验证 | ✅ 诚实交付 |

## 8. 后续修正：`initial_place` 实际能完成

**上文"放不下"的措辞需要修正。** 后续观察：

```
Ending compile_fusion / initial_place / Initial Placement   ✅ 完成
Ending compile_fusion / initial_drc                          ✅ 完成
Starting compile_fusion / initial_opto                       ⏳ 进行中
```

**`initial_place` 与 `initial_drc` 都完成了**，`ZRT-763` 的 52 次重叠告警
没有阻止流程推进。

所以准确表述是：

| 说法 | 准确性 |
|:---|:---|
| "物理上放不下" | ❌ 过头了 —— 布局器完成了粗放置 |
| **"能放，但产生 52 次无重叠告警"** | ✅ 准确 |
| "耗时不可接受" | ✅ 准确（31 分钟仍在 compile_fusion）|

## 9. 一句话（修正版）

**`wsum` 的寄存器组几何（3.08 µm = 22× site）导致布局产生 52 次重叠告警，
虽然流程能推进，但 `compile_fusion` 单独就超过 30 分钟，
物理实现代价不可接受。根因是 14,400 个触发器 + 2,304 路 MUX 的结构选择。**
