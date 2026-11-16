# 后端阻塞的最终判定：NDM 库没有 PG 引脚

时间：2026-11-16　基线：`f1f17e3`
相关：`FC_TLU_BLOCKER.md`（已解决）、本文（未解决）

## 1. 决定性证据

```tcl
set all [get_lib_cells -quiet */*]     ;# 839 个 cell
# 逐个查 get_lib_pins，找名字含 VDD/VSS 的
-> cells_with_VDD_pin = 0
```

**839 个标准单元里，没有一个有 VDD/VSS 引脚。**

单看 `BUFFD2BWP7T40P140`：

```tcl
lib_pins = tcbn28hpcplusbwp7t40p140/BUFFD2BWP7T40P140/I
           tcbn28hpcplusbwp7t40p140/BUFFD2BWP7T40P140/Z
```

**只有 I 和 Z 两个信号引脚。**

## 2. 库清单

| NDM | 是否有 PG |
|:---|:---|
| `tcbn28hpcplusbwp7t40p140.ndm` | ❌ 无（839 cell 全无 VDD）|
| `tcbn28hpcplusbwp7t40p140hvt.ndm` | 未测（同类）|
| `tcbn28hpcplusbwp7t40p140lvt.ndm` | 未测（同类）|
| `tphn28hpcpgv18.ndm`（IO 库）| ✅ 有（名字含 pg）|

**标准单元库只有这一套，且无 PG 版本。**

## 3. 为什么这必然导致 OPT-045

`place_opt` 要插入 buffer/inverter 修复时序与 DRC。插入前后它需要：

1. 确认该 buffer 属于哪个电压域
2. 把 buffer 的电源脚连到 VA 的电源网

**没有 PG 引脚，第 2 步无法完成**，于是：

```
Number of supply Nets    = 2
Number of used supplies  = 0      <- 无法连
Warning: Cannot find default buffer/inverter for VA DEFAULT_VA (OPT-043)
Error:   Cannot find usable buffers or inverters. (OPT-045)
```

## 4. 试过的所有方法（全部失败，逐条附证据）

| # | 方法 | 结果 |
|:--:|:---|:---|
| 1 | 检查库是否有 buffer | 有（15 个 BUFFD），但无 PG |
| 2 | `set_lib_cell_purpose -include {...}` | 返回 0，`purpose` 属性仍空 |
| 3 | `set_voltage_area -add_power_domains` | 成功，但 `VA_power` 仍空 |
| 4 | `set_voltage_area -add_cells` | `NDMUI-155`（循环约束）|
| 5 | `set_voltage_area -power/-ground` | `NDMUI-444`（DEFAULT_VA 电源网不可改）|
| 6 | `mv.upf.enable_missing_voltage_area true` | OPT-045 从 3 次变 4 次，无效 |
| 7 | 查 `is_black_box` / 只读层次 | 均为 false，不是原因 |

## 5. 已确认可以做的部分

| 阶段 | 状态 |
|:---|:---|
| `create_lib`（NDM） | ✅ |
| `elaborate` / `set_top_module` | ✅ |
| TLU+ 绑定（`-early_spec/-late_spec`） | ✅ 已解决 |
| `create_clock` / IO 约束 | ✅ |
| **`compile_fusion`** | ✅ **ER 1545 inst / 758.13 µm²** |
| **`initialize_floorplan`** | ✅ |
| `place_opt` | ❌ **OPT-045** |
| `clock_opt` / `route_auto` | ❌ 依赖 place |
| 后布线 STA / DRC / LVS / GDS | ❌ 依赖 route |

**综合和布局都成功了，卡在布局优化。**

## 6. 三个可选的下一步

| 选项 | 做法 | 代价 |
|:---|:---|:---|
| **A** | 生成带 PG 的 NDM（用 `lm_shell` 从 LEF 造 PG 版本）| 需要 PG LEF；本机 PDK 里要确认有没有 |
| **B** | 用 ICC2（如果有另一套库）| 库问题相同 |
| **C** | 只交付 DC 综合结果（13 个模块），后端标注"库无 PG，无法继续"| 无版图数据 |

**选项 A 是唯一可能真正解决的**：需要确认 PDK 里是否有带 PG 的 LEF（通常文件名含 `_pg` 或放在 `lef_pg/` 目录）。

## 7. 复现

```bash
ssh vm-yian 'export PATH=/opt/synopsys/fusioncompiler/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost
  fc_shell -f /tmp/fc_sched18.tcl 2>&1 | grep -E "^STEP_|OPT-045"'
# 预期：STEP_SYNTH_OK、STEP_FLOORPLAN_OK，然后 OPT045=4
```
