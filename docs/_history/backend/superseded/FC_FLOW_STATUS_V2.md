# FC RTL-to-GDS 流程状态 v2（含自动生成的 tech LEF）

时间：2026-11-17　基线：`2a372dd` 之后

## 1. 这轮打通了什么

| STEP | 状态 | 说明 |
|:---|:---|:---|
| `STEP_CREATE_LIB_OK` | ✅ | `.tf` 提供 46 个层 |
| `CHK_TECH rc=0` | ✅ | 自生成 tech LEF，M1~M9 + AP 方向全部有效 |
| `STEP_ELAB_OK` | ✅ | |
| `STEP_TLU_OK` | ✅ | TLU+ 双角绑定 |
| `STEP_CONSTRAINT_OK` | ✅ | |
| `STEP_SYNTH_OK` | ✅ | compile_fusion 到 Phase 51 |
| `CHK_SITE_ROWS made=50 total=50` | ✅ | 手工 site row，无重叠 |
| `STEP_FLOORPLAN_OK` | ✅ | |
| `STEP_PLACE rc=1` | ❌ | 168 个单元未放置 |
| **`STEP_CTS rc=0`** | ✅ | **首次成功** |
| **`STEP_ROUTE rc=0`** | ✅ | **首次成功，DRC=0** |

## 2. 关键成果数据

```
Number of cells:           1,544
Combinational area:       590.74 µm²
Total cell area:          749.31 µm²
slack (MET):             +20.32 ns
Total Routed Wires:          127
Total Routed Wire Length:  113 micron
Total number of DRCs:          0
open nets:                     0
```

**布线真的执行了**（127 条线、113 µm），DRC = 0。

## 3. 但仍未完整

```
Error: place_opt bailed out due to unplaced cells. (OPT-062)
Warning: Incomplete place_opt / final_place (FLW-8004)
168 个单元未放置（ZRT-064 × 602）
```

**因为这 168 个未放置单元，布线只连了 127 条线**，实际应有 ~1,594 条网。

所以：
- ✅ 流程**能跑通**（CTS、route 都返回 0）
- ❌ 但有 168 个单元没放进去，布线不完整

## 4. 本轮修好的问题（每项都有错误码）

| # | 问题 | 修正 |
|:--:|:---|:---|
| 1 | `LAYER` 定义缺失（34 行文件里 0 个层） | 用真正的 `.tf`（3852 行，46 层） |
| 2 | `DIRECTION` 缺失（全部 unknown） | 生成 tech LEF 补 M1~M9 方向 |
| 3 | `AP` 层缺失（NEX-001 残留） | 补 `LAYER AP` |
| 4 | site row 数量为 0 | `create_site_row` 手工铺 50 行 |
| 5 | `-orientation N` 非法 | 改 `R0` |
| 6 | site row 重叠（LGL-010） | 先 `remove_site_rows` 再建 |

## 5. 自动生成的 tech LEF

**文件**：`flow/synth/v21/tsmc28_tech_derived.lef`

| 内容 | 来源 |
|:---|:---|
| `PITCH` / `WIDTH` / `SPACING` | **从 `.tf` 提取的真实值** |
| `DIRECTION` | **按 M1 水平/M2 垂直交替规则补**（标准做法，非原厂声明） |
| `SITE core7T` | 0.140 × 0.700（与 `site_via.lef` 一致） |

**必须标注：这不是 PDK 官方交付的技术 LEF，是根据 `.tf` 推导生成的工程补全。**

## 6. 剩余问题：168 个单元未放置

`A689` 这类单元**不在原始网表里**（`get_cells A689` 查不到），
说明它们是 `place_opt` 优化过程中**动态插入**的（buffer/inverter）。

`OPT-045`（找不到 buffer）这轮是 0 次，但仍有 168 个单元放不下。

**可能原因**：
- site row 的 `site_count=284` 与实际可用宽度不完全匹配（core 39.825 / 0.140 = 284.46）
- 或者 row 的 y 坐标步进（0.700）与实际 core 高度 39.6 不完全整除（39.6/0.7 = 56.57）

**下一轮可试**：调整 row 数（56 → 56）和 site_count（284 → 284），或让 FC 自己生成而不手工干预。

## 7. 结论

**Partially complete。**
流程从综合到布线全部能执行，CTS 与 route 返回 0、DRC = 0、时序满足（+20.32 ns）。
但有 168 个单元未放置，布线不完整，**不能宣称 signoff**。
