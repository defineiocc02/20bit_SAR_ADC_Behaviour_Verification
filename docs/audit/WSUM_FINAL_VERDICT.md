# `wsum_v21` 物理实现：最终结论

时间：2026-11-17　基线：`220fc22`

## 1. 结果

**`wsum_v21` 的 RTL-to-GDS 在 38 分钟预算内未完成。**

停点：

```
Ending   compile_fusion / initial_opto    ← 到此为止，未出现 STEP_SYNTH_OK
```

即：**连 `compile_fusion` 都没跑完**（综合阶段内部的 `initial_opto` 阶段结束后超时）。
后续的 `initialize_floorplan` / `place_opt` / `clock_opt` / `route_auto` / 导出全部未执行。

**无网表、无 DEF、无 GDS、无 Formality。**

## 2. 完整失败路径（按时间）

| 时间 | 阶段 | 结果 |
|:---|:---|:---|
| 0~10 min | `initial_map / High-Level Optimization` | ✅ 最慢，约 10 分钟 |
| 10:36 | `Clock Gate Re-structuring` | ✅ |
| 11:12 | `Logic Simplification / Register Opt` | ✅ |
| 11:23 | `Timing Opt / DW Reselection` | ✅ |
| 12:03 | `Register Retiming` | ✅ |
| 12:25~18:30 | `Optimization (1)~(5)`（34 次子阶段）| ✅ |
| 15:58 | `Auto-Floorplan (1)` | ✅ |
| 16:00 | `Early Placement` | ✅ |
| 30:41 | `initial_place / Initial Placement` | ✅（含 52 次 `ZRT-763` 重叠）|
| 30:41 | `initial_drc` | ✅ |
| 31:11 | **`initial_opto`（含 GR phase 0/1）** | ⏳ **超时终止** |

## 3. 七项独立指标（全部指向同一结论）

| 指标 | `sched18` | `wsum_v21` | 倍数 |
|:---|--:|--:|--:|
| DC cells | 2,338 | **153,735** | 66× |
| DC 面积 | 1,265 µm² | **102,202 µm²** | 81× |
| DC 综合耗时 | 7 s | **515 s** | 74× |
| **FC 分区网格** | 26×26 | **160×160** | **38×** |
| **`ZRT-763` 重叠** | 0 | **52** | — |
| Coarse placer 线长 | 小 | **3.49e+06** | — |
| **总耗时** | **3 分钟** | **> 38 分钟（未完成）** | >12× |

## 4. 一个附带的重要验证：`AFP-1022`

日志中出现：

```
Information: Derive horizontal routing direction for Layer M1 from library (AFP-1022)
Information: Derive vertical routing direction for Layer M2 from library (AFP-1022)
```

**工具自己从库里推导出 M1 水平、M2 垂直，与我手工补进 tech LEF 的规则完全一致。**

这独立验证了 `tsmc28_tech_derived.lef` 里 `DIRECTION` 推导的正确性 ——
不是我编的，工具也从库里推导出同样的结论。

## 5. 结论

**`wsum_v21` 用当前的"二维寄存器堆 + 逐笔查全表"结构，在 28HPC+ 上物理实现不可行。**

具体两条：
1. **规模**：153,735 cells（`sched18` 的 66 倍），DC 综合 515 s，FC 综合 >30 分钟未完成
2. **几何**：单个 `wt_main_reg[*]` 宽 3.08 µm = 22 个 site，产生 52 次重叠告警

**这不是工具配置问题** —— 同一套脚本、同一份 tech LEF、同一个 site row 生成逻辑，
`sched18` 3 分钟完成并 Formality `SUCCEEDED`。

## 6. 建议（需人决策）

| 方案 | 做法 | 能解决 |
|:---|:---|:---|
| **A** | 权重表改**单端口 SRAM** | ✅ 规模+几何都能解决，需 memory compiler |
| **B** | `enable_multibit_debanking=true`（已写入脚本未验证）| ⚠️ 可能解决几何，规模仍大 |
| **C** | 改算法：增量更新而非每笔查全表 | ✅ 根治，需重构 D5 + 重跑全部回归 |
| **D** | 接受：`wsum` 只做 DC 综合与功能验证 | ✅ 诚实交付 |

## 7. 交付现状

| 模块 | RTL-to-GDS | Formality | 数据 |
|:---|:---|:---|:---|
| **`sched18`** | ✅ **完整** | ✅ **SUCCEEDED 234/0/0** | 778.32 µm² / 14,899 wires / DRC 0 / +14.93 ns |
| `wsum_v21` | ❌ 38 分钟未完成 | ❌ | DC 有数据（102,202 µm²）|
| 其余 11 个 | 未做 | 未做 | DC 有数据 |

**13 个模块的 DC 综合全部完成，合计 160,951 µm²，全部满足 25 ns。**
