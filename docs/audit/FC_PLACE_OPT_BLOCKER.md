# `place_opt` 的 OPT-045：连续 17 轮未突破，正式判定 BLOCKED

时间：2026-11-16　基线：`1e62684` 之后
状态：**BLOCKED**

## 1. 当前现象

```
Warning: Cannot find default buffer/inverter for VA DEFAULT_VA (OPT-043)
Error:   Cannot find usable buffers or inverters. (OPT-045)
Number of supply Nets   = 2
Number of used supplies = 0
```

## 2. 已确认成立的事实（全部有实测）

| 事实 | 证据 |
|:---|:---|
| 库里有 buffer | `get_lib_cells */*BUFFD*` -> 15 个 |
| BUFFD2 可用 | `is_buffer=true`、`area=0.392`、`dont_use=false` |
| **库里有 PG 引脚** | `get_lib_pins */*/VDD` -> **839 个**，`dir=in` |
| 设计可修改 | `is_black_box=false` |
| `compile_fusion` 成功 | 1,545 inst / 758.13 µm² |
| `initialize_floorplan` 成功 | floorplan 已建 |
| `boundary` 是 4 个角点 | `{{0 0} {0 39.6} {39.825 39.6} {39.825 0}}` |
| 建 VA 成功 | `create_voltage_area -region {{0 0} {35.1 35.1}}` -> rc=0 |

**所以：库、buffer、PG 引脚、设计可修改性、floorplan、VA 创建 —— 全部正常。**

## 3. 十七轮尝试清单（按时间）

| 轮 | 尝试 | 结果 |
|:--:|:---|:---|
| 1 | `read_db` | `CMD-005`（FC 用 NDM）|
| 2 | `set_lib_cell_purpose -include {...}` | rc=0，`purpose` 仍空 |
| 3 | `set_voltage_area -add_cells` | `NDMUI-155` 循环约束 |
| 4 | `set_voltage_area -power/-ground` | `NDMUI-444` DEFAULT_VA 不可改 |
| 5 | `mv.upf.enable_missing_voltage_area` | OPT-045 3→4 |
| 6 | 重建带 PG 的 NDM（4 条路）| 全不通；后证实**不需要** |
| 7 | `mv.upf.enable_pg_pin_reconnection` | 仍 4 |
| 8 | `plan.pgroute.auto_connect_pg_net` + `snap_stdcell_rail` | 仍 4 |
| 9-12 | 命令式 UPF（`create_supply_*`）| 全部 rc=1 且**无错误消息** |
| 13 | 文件式 `load_upf` | **rc=0**，OPT-045 4→2（首次真正减少）|
| 14 | UPF `-domain .` | `SEL-005` |
| 15 | UPF 域相关网 + supply set | `UPF-005` |
| 16 | UPF 缺电压/驱动 | `UPF-520` / `UPF-176` / `UPF-012` |
| 17 | `set_voltage` 写进 UPF | `CMD-005`（是 TCL 命令）|
| 18 | `set_voltage` 在 TCL 侧（design 上下文）| **rc=0** |
| 19 | 建新 VA（用 `boundary`）| region 退化，`NDMUI-100` |
| 20 | 先删旧 VA 再建新 | 建失败 → VA 全无，OPT-045 2→6 |
| 21 | 改 `core_area` | 属性为空 |
| 22 | 用 `boundary` 对角点 | **`create_voltage_area` rc=0** |
| 23 | 删 DEFAULT_VA 只留 VA_TOP | 日志仍引用 `VA DEFAULT_VA` |
| 24 | 保留 DEFAULT_VA + 建 VA_TOP | 两个都在，`CHK_VA_BIND power=` 仍空 |
| 25 | UPF v5：`create_power_domain -supply` | `PD.supply_sets` 仍查不到 |

## 4. 核心未解点

```
PD.supply_sets          = 空      （supply_sets_all 有 SS_PRIMARY）
VA.power / VA.ground    = 空      （create_voltage_area -power 传了但没生效）
Number of used supplies = 0
```

**man `create_voltage_area` 原文**：

> `-power supply_net`
> Specifies the power supply net for voltage area.
> **The net must match one of the available power supply nets on the power domains.**

所以 `-power` 没生效**是因为 PD 上没有"可用的 power supply net"**。

而 PD 的 supply 为什么是空，我用过的三种写法都没成功：

| 写法 | `PD.supply_sets` |
|:---|:---|
| `create_power_domain` + `associate_supply_set` | 空 |
| `create_power_domain -supply {primary SS}` | 空 |
| `set_domain_supply_net` | `UPF-012`（重复指定）|

## 5. 我的判断（含不确定性）

**判断**：`PD.supply_sets` 在这个 FC 版本里可能本来就是查询不到的空属性（不是真的没绑），
真正的失败点在别处，只是我一直用这个属性当判据。

**不确定**：TPD 的 supply 到底有没有绑上 —— 我没有找到一个能独立验证的方法
（`report_power_domain` 的 `-verbose` 选项不存在，`list_attributes -class power_domain` 返回空表）。

## 6. 已可交付的部分（不受阻塞）

| 阶段 | 状态 | 数据 |
|:---|:---|:---|
| DC 单模块综合 | ✅ **13 个模块全部完成** | 160,951 µm²，全部满足 25 ns |
| FC `compile_fusion` | ✅ | 1,545 inst / 758.13 µm² |
| FC `initialize_floorplan` | ✅ | floorplan 已建 |
| `create_voltage_area` | ✅ | rc=0 |
| `place_opt` 及之后 | ❌ **BLOCKED** | |

## 7. 结论

**这 17 轮里我解决了 6 个真实配置错误**（TLU 绑定、UPF 加载、电压设置、
region 坐标、VA 创建、破坏性操作顺序），每一个都有明确的错误码和修复。

**但 `OPT-045` 仍然存在**，且我无法确定它是由"PD supply 未绑定"还是
"`PD.supply_sets` 属性查询不准"导致的。

**继续在同一路径上试参数的边际收益已经很低。**
建议：交付 DC 13 模块综合 + FC 综合/布局结果，后端标注 BLOCKED 并附本文档。
