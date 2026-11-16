# 后端工作最终状态

时间：2026-11-16　基线：`aa1f956` 之后
状态：**部分完成（PARTIAL）**

## 1. 已完成的阶段

| 阶段 | 状态 | 证据/数据 |
|:---|:---|:---|
| **DC 单模块综合** | ✅ **13 个模块全部完成** | 160,951 µm²，全部满足 25 ns |
| FC `create_lib`（NDM） | ✅ | |
| FC `elaborate` / `set_top_module` | ✅ | |
| FC `read_tech_lef` | ✅ | `rc=0`（须在 `set_top_module` 之后）|
| FC TLU+ 绑定 | ✅ | `-early_spec`/`-late_spec` |
| FC `compile_fusion` | ✅ | **1,545 inst / 758.13 µm²** |
| FC `initialize_floorplan` | ✅ | floorplan 已建 |
| FC `create_voltage_area` | ✅ | `rc=0` |
| **FC `place_opt`** | ❌ **LGL-397：无 site row** | 49 次告警 |
| CTS / route / STA / DRC / LVS / GDS | ❌ 依赖 place | |

## 2. 当前阻塞的确切形态

```
Warning: No site rows found in floorplan. (LGL-397)     × 49
Warning: Technology layer 'M1'/'M2'/... 'routing-direction' is not valid (NEX-001)
Error:   Cannot find usable buffers or inverters. (OPT-045)
```

**因果链**（已由实验确认）：

```
create_lib -ref_libs <单元NDM>     → 只有单元定义，无技术层/site
read_tech_lef <site_via.lef>        → 补 site（须在 set_top_module 之后）
initialize_floorplan                  → 需要 site 才能生成 site row
  ↓ 实测：读完 tech LEF 后 LGL-397 仍有 28~49 次
place_opt 无处放 buffer → OPT-045
```

## 3. 试过的 8 个方向（全部未打通）

| # | 方向 | 结果 |
|:--:|:---|:---|
| 1 | 查库是否有 buffer/PG | 都有（15 BUFFD、839 个 VDD 引脚）|
| 2 | `set_lib_cell_purpose` | rc=0，属性仍空 |
| 3 | `set_voltage_area` 各选项 | `NDMUI-444` / `NDMUI-155` |
| 4 | 重建带 PG 的 NDM | 4 条路都不通，且**不需要** |
| 5 | 3 个 PG 相关 app option | 无效 |
| 6 | 命令式 UPF | 全部 rc=1 且**无错误消息** |
| 7 | 文件式 UPF + `check_mv_design` | **UPF 通过**（`UPF-072` committed，PD/supply set 规则无错）|
| 8 | `read_tech_lef` + `-site core7T` | LGL-397 49→28，未归零 |

## 4. 真正修好的 7 项（都是真实缺陷）

| # | 缺陷 | 正确做法 |
|:--:|:---|:---|
| 1 | TLU 绑定用错选项名 | `-early_spec`/`-late_spec`（manpage）|
| 2 | 命令式 UPF 静默失败 | 改文件式 `load_upf`，能逐条报错 |
| 3 | `set_voltage` 写进 UPF | 它是 TCL 命令，需 design 上下文 |
| 4 | `boundary` 当 2 点用 | 实际 4 角点，取 `[lindex 0]`/`[lindex 2]` |
| 5 | 先删 VA 再建 | 必须先建成功后删 |
| 6 | UPF 顺序 | `SEL-005`/`UPF-005`/`UPF-520`/`UPF-176`/`UPF-012` |
| 7 | `read_tech_lef` 位置 | 须在 `set_top_module` 之后 |

**这 7 项都不在 `docs/` 里记录过，是新发现。**

## 5. 我用错的 3 个判据（方法论教训）

| 判据 | 为什么错 | 影响轮次 |
|:---|:---|:--:|
| `get_lib_pins -of_objects` | 不含 PG 引脚 | 1-6 |
| `PD.supply_sets` 属性 | 本来就查不到；`check_mv_design` 证明 UPF 是对的 | 7-21 |
| 只看 `OPT-045` 频次 | `LGL-397` 出现 49 次才是主线 | 22-40 |

**教训**：应该先跑工具自检命令（`check_mv_design`、`check_pg_connectivity`），
并按告警**出现频次**排序排查，而不是盯着自己假设的那个原因。

## 6. 可交付内容

| 交付项 | 路径 | 说明 |
|:---|:---|:---|
| 13 模块综合报告 | `docs/audit/SYNTH_PER_MODULE.md` | 面积/时序/耗时全表 |
| 算法 vs 原文审计 | `docs/audit/AUDIT_ALGO_VS_SOURCE.md` | 12 项判定 |
| FC 流程脚本 | `flow/synth/v21/fc_sched18.tcl` | 含 7 项修正与注释 |
| 最小 UPF | `flow/synth/v21/min_single_domain.upf` | v5.0，`check_mv_design` 通过 |
| 本文档 | `docs/audit/BACKEND_FINAL_STATUS.md` | 状态与阻塞 |

## 6b. 第 41-44 轮的补充实验（全部否定了"UPF 是根因"）

| 实验 | 配置 | LGL-397 | OPT-045 | place_opt |
|:---|:---|--:|--:|:---|
| A | 有 UPF + `-site core7T` | 49 | 1 | rc=1 |
| B | 有 UPF + `-use_site_row` | 49 | 3 | rc=1 |
| C | **完全不加载 UPF** | **49** | 2 | rc=1 |

**关键结论：不加载 UPF，`LGL-397` 仍然是 49 次。**
所以 `LGL-397` 与 UPF、与 VA、与 power intent **完全无关**。

### `place_opt` 的实际行为

```
Place-opt optimization Phase 5 Iter 1   ...   1543 cells
Information: Running auto PG connection. (NDM-099)
Warning: Incomplete place_opt / final_place (FLW-8004)
```

- 单元**确实被放置了**（1543 cells）
- 但在 `final_place` 阶段被判为 **Incomplete**
- `OPT-045` 只出现 2 次，且发生在早期

### `-use_site_row` 无效

manpage 说默认建 `siteArray`、加该选项建 `siteRow`，实测：
- 加之前：49 次
- 加之后：49 次（未变）

**说明 site row 生成失败的原因不在这个选项，而在技术 LEF 本身。**

### 已彻底排除的方向

| 方向 | 排除依据 |
|:---|:---|
| UPF / power intent | 完全不加载 UPF，`LGL-397` 仍是 49 |
| VA / power domain | 同上 |
| `set_lib_cell_purpose` | 与 site row 无关 |
| PG 引脚 / 库缺 PG | 839 个 VDD 引脚都在 |
| `-use_site_row` | 加了无效 |

**剩下的唯一嫌疑：`tsmc28_7t_site_via.lef` 不是这套单元库配套的完整技术 LEF。**

## 7. 结论

**后端工作到 `place_opt` 为止，`PARTIAL`。**
综合（DC 13 模块 + FC 全套）已完成，布局优化因 site row 问题未通过。

**不宣称 tapeout signoff。** 无 CTS、无布线、无 STA、无 DRC/LVS/GDS。
