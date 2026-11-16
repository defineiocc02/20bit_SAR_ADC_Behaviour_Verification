# `place_opt` 的 OPT-045 未解决：8 轮尝试记录与阻塞判定

时间：2026-11-16　基线：`c236dc8` 之后
状态：**BLOCKED**（同一阻塞连续 8 轮未突破）

## 1. 现象

```
Warning: Cannot find default buffer/inverter for VA DEFAULT_VA (OPT-043)
Error:   Cannot find usable buffers or inverters. (OPT-045)
Number of supply Nets   = 2
Number of used supplies = 0
```

`compile_fusion` 与 `initialize_floorplan` 都成功，
`place_opt` 立刻报 OPT-045 并终止后续。

## 2. 已确认成立的事实（每项都有实测）

| 事实 | 证据 |
|:---|:---|
| 库里有 buffer | `get_lib_cells */*BUFFD*` -> 15 个 |
| BUFFD2 是 buffer | `is_buffer = true`，`area = 0.392`，`dont_use = false` |
| **库里有 PG 引脚** | `get_lib_pins */*/VDD` -> **839 个**，`direction = in` |
| LEF 有 PG | 883 宏，907 处 `PIN VDD` + `USE POWER` |
| 设计可修改 | `is_black_box = false` |
| VA 已绑 power domain | `set_voltage_area -add_power_domains` 成功，`VA_pd = DEFAULT_POWER_DOMAIN` |

**所以四个常见原因（库缺 buffer / 库缺 PG / 设计只读 / VA 未绑 PD）都已排除。**

## 3. 八轮尝试清单（全部失败）

| 轮 | 尝试 | 结果 |
|:--:|:---|:---|
| 1 | `read_db` 加载库 | `CMD-005` 不存在（FC 用 NDM）|
| 2 | `set_lib_cell_purpose -include {cts optimization hold power}` | rc=0 但 `purpose` 属性仍空 |
| 3 | `set_voltage_area -add_cells` | `NDMUI-155` 循环约束 |
| 4 | `set_voltage_area -power/-ground` | `NDMUI-444` DEFAULT_VA 电源网不可改 |
| 5 | `mv.upf.enable_missing_voltage_area = true` | OPT-045 从 3 次变 4 次 |
| 6 | 重建带 PG 的 NDM（4 条路）| 都走不通；后来发现**根本不需要**（库已有 PG）|
| 7 | `mv.upf.enable_pg_pin_reconnection = true` | 仍 4 次 |
| 8 | 再开 `plan.pgroute.auto_connect_pg_net` + `snap_stdcell_rail` | **仍 4 次** |

## 4. 关键未解点

`Number of used supplies = 0` 说明 VA 虽然绑了 PD，但**没有实际生效的 supply**。

| 查询 | 结果 |
|:---|:---|
| `get_supply_nets` | `VDD VSS`（存在）|
| `PD.supply_nets` | **空** |
| `PD.primary_supply_pins` | **空** |
| `VA.power` / `VA.ground` | **空** |
| `supply_net.voltage` / `is_power` | **空** |

**PD 与 supply net 之间缺少关联**，但建立关联的路径都被堵：

- `set_voltage_area -power/-ground` -> `NDMUI-444`
- `create_supply_net` / `create_supply_set` / `create_power_domain` -> 全部 rc=1
  （缺 UPF 上下文；本设计没有 UPF）
- `PD.supply_nets` 属性只读，无 setter

## 5. 我的判断与不确定性

**判断**：这个 28HPC+ 数字流程在这个 FC 版本下，
对"无 UPF 的单电源域设计"可能需要一份最小 UPF 文件才能让 `place_opt` 正常工作。

**不确定**：`create_supply_net` 等命令为什么 rc=1 而没有任何错误消息
（`catch` 到的 `e` 是空字符串）。这可能是：
① 需要先 `load_upf` 建立 UPF 环境
② 这些命令只接受特定 license
③ 我的调用参数有误

**这三条我没有区分开。**

## 6. 已可交付的部分（不受此阻塞影响）

| 阶段 | 状态 | 数据 |
|:---|:---|:---|
| DC 单模块综合 | ✅ **13 个模块全部完成** | 160,951 µm² 合计，全部满足 25 ns |
| FC `compile_fusion` | ✅ | 1,545 inst / 758.13 µm² |
| FC `initialize_floorplan` | ✅ | floorplan 已建 |
| `place_opt` 及之后 | ❌ | OPT-045 |

## 7. 建议的下一步（需人决策）

| 选项 | 做法 | 前置条件 |
|:---|:---|:---|
| **A** | 写一份最小 UPF（单域 + supply set），再跑 | 需先解决 `create_supply_*` 的 rc=1 |
| **B** | 查 `load_upf` 用法，用文件式 UPF 而非命令式 | 需 manpage 确认 |
| **C** | 接受当前状态：交付 DC 13 模块 + FC 综合/布局，后端标注 BLOCKED | 无 |
| **D** | 换 ICC2 试 | 库问题可能相同 |

**我倾向先做 B**：命令式 UPF 全部 rc=1 但无错误消息，
说明是环境问题；用文件式 `load_upf` 可能能绕过。

## 8. 方法论记录

这 8 轮的教训：我在"查证当前状态"上花了大量时间（查库、查引脚、查属性），
但对"有哪些开关能改变这个状态"查得太晚（第 7 轮才开始查 `get_app_options`）。

不过第 7、8 轮证明了开选项也不够 —— 说明这确实不是单纯开关问题。
