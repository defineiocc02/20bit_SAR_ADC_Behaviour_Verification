# Fusion Compiler TLU+ 绑定未解决（阻塞后端）

时间：2026-11-16　基线：`f1f17e3`

## 1. 现象

`compile_fusion` 报：

```
Error: No valid parasitics for LATE corner default.
       Related command: set_parasitics_parameters (SCN-1001)
Error: No valid parasitics for EARLY corner default. (SCN-1001)
Error: Flow status set to Error by step: fc/logic_opto/mcmmStart (FLW-1762)
```

## 2. 已经打通的（5 个 STEP）

```
STEP_CREATE_LIB_OK     create_lib -ref_libs <NDM> <path>
STEP_ELAB_OK           analyze + elaborate
STEP_TOP_OK            set_top_module
STEP_TLU_OK            read_parasitic_tech -tlup <cworst>
STEP_CONSTRAINT_OK     create_clock / set_input_delay / set_output_delay
```

**卡在 `compile_fusion`。**

## 3. 已确认的事实（实测）

| 事实 | 证据 |
|:---|:---|
| FC **没有** `read_db` | `CMD-005 unknown command` |
| FC **没有** `target_library` app var | `CMD-104 not an application variable` |
| FC 用 NDM，不是 .db | `create_lib -ref_libs <ndm>` 成功 |
| 综合命令是 `compile_fusion` | 不是 `compile_ultra` |
| block 建立后 corner 名确实是 `default` | `get_object_name [get_corners]` -> `default` |
| `read_parasitic_tech` **成功** | `get_parasitic_techs` 返回 `cln28hpc+_..._cworst.tluplus` |
| **但 corner 与 parasitic tech 没有关联** | 仍报 SCN-1001 |

## 4. 已试过且失败的绑定方式

| 尝试 | 结果 |
|:---|:---|
| `read_parasitic_tech -name default` | 注册成功，但报 `CSTR-015 Corner 'default' already exists`（若再 create）|
| `read_parasitic_tech -name maxTLU` + `minTLU` | SCN-1001 仍报 5 次 |
| `set_parasitics_parameters -late/-early <tlu>` | `CMD-011 ambiguous option` + `CMD-012 extra positional` |
| `set_parasitics_parameters -early_tlu_plus_file <f>` | `CMD-010 unknown option` |
| `set_parasitics_parameters -early_parasitic_tech <n>` | 同上 |
| `create_corner -name tlu_c -parasitic_tech <n>` | `CMD-010 unknown option '-name'` |
| `create_corner <n>`（单参数） | 未完成测试（本轮超时）|

**`help create_corner` 只输出一行摘要，没有选项列表。**

## 5. 这不是新问题

`flow/synth/v21/fc_rtl_to_gds.tcl` 的注释已记录同一阻塞：

> **未通过**：把名字改成 default 并不能让 FC 建立 corner 关联 ——
> 实测该轮仍报 SCN-1001 共 5 次，且没有 STEP_COMPILE_OK。

所以这是**此前就存在、一直未解决**的问题，不是本轮引入。

## 6. 影响

| 项 | 状态 |
|:---|:---|
| RTL-to-GDS 全流程 | ❌ 阻塞在 `compile_fusion` |
| 布局/CTS/布线 | ❌ 无法开始 |
| 后布线 STA / DRC / LVS | ❌ 依赖全流程 |
| 与原型面积对比（版图口径） | ❌ 无法做 |
| **单模块综合（DC）** | ✅ **已完成 13 个**，数据有效 |

## 7. 下一步的选择

| 选项 | 做法 | 代价 |
|:---|:---|:---|
| **A** | 查 FC 官方文档（`man create_corner` / SolvNet）确认正确选项 | 需要可用的文档源 |
| **B** | 先做**无寄生**的布局布线（`set_app_options` 关闭 RC 提取），得到面积/拥塞数据 | 时序不可信，但版图可用 |
| **C** | 换用 ICC2（如果许可可用） | 工具不同，脚本要重写 |
| **D** | 只做 DC 综合 + PT 用 TLU+ 估算 | 无实际版图 |

**选项 B 最快能出可审阅的布局结果**，但必须明确标注"时序不可信"。

## 8. 本轮未做的

- `create_corner` 的单参数形式未测完（会话超时）
- FC 文档未查到（本机无 SolvNet 访问）
- `report_parasitic_parameters` 的正确选项未确定（`-verbose` 报 CMD-010）
