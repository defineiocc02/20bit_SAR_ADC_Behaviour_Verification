# 系统性缺陷：`set_driving_cell` 用了库里不存在的单元，静默失败

时间：2026-11-16　发现者：timing-report 子代理
影响：**本轮所有综合与 STA 的输入驱动都是缺失的**

## 1. 缺陷

`flow/synth/v21/dc_synth_v21.tcl` 原来写：

```tcl
set_driving_cell -lib_cell BUF_X2 -pin Z [all_inputs -clk]
```

**TSMC 28HPC+ 库里没有 `BUF_X2`。** 实测：

| 单元名 | `get_lib_cells */<name>` |
|:---|--:|
| `BUF_X1` | **0** |
| `BUF_X2` | **0** |
| `BUF_X4` | **0** |
| `BUFFD1BWP7T40P140` | 1 |
| `BUFFD2BWP7T40P140` | 1 |

## 2. 为什么危险：它是静默失败

DC 的报错只有：

```
Error: Cannot find the specified driving cell in memory.   (UID-993)
```

**关键：这条 Error 不抛 Tcl 异常。**

后果：
- `catch { set_driving_cell ... }` 抓不到
- 脚本照常往下跑，`STEP_COMPILE_OK`、`STEP_DONE` 全都正常输出
- 所有输入端口处于**无驱动单元**状态

这比"报错退出"危险得多：报告看起来一切正常，实际约束不完整。

## 3. 影响范围

| 项 | 影响 |
|:---|:---|
| 修复前所有综合的面积 | **不受影响**（驱动单元不改变逻辑结构） |
| 修复前所有 STA 的时序 | **偏乐观**（输入端口无驱动 → 输入路径延迟被低估） |
| 三类时间预算结论 | 需重测 |
| 关键路径 slack | 需重测 |

## 4. 修复

```tcl
set_driving_cell -lib_cell BUFFD2BWP7T40P140 -pin Z [all_inputs -clk]
set_load 0.05 [all_outputs]

# **必须回读校验**：UID-993 是静默的
set _drv_n 0
foreach_in_collection _p [remove_from_collection [all_inputs] [get_ports clk]] {
    if {[get_attribute [get_attribute $_p DrivingCell] full_name] ne ""} {
        incr _drv_n
    }
}
puts "CHK driving_cells_set=$_drv_n"
if {$_drv_n == 0} { puts "FATAL: set_driving_cell 没有生效"; exit 1 }
```

## 5. 这是一类问题，不是孤例

本阶段已经出现**三次**"工具静默失败 / 报告误导"：

| # | 现象 | 静默点 | 怎么发现的 |
|:--:|:---|:---|:---|
| 1 | v5 报 89,693 µm²、583 s"收敛" | `g_q30` 位宽不匹配（LINK-3）→ 子模块变黑盒，`STEP_COMPILE_OK` 照常 | 门级编译报 "module not found" |
| 2 | v7 报 76,222 µm²、+19.38 ns | `v1_q` 重复声明（VER-954）→ `recon_v21` 变黑盒 | 门级编译 |
| 3 | 所有 STA 输入无驱动 | `set_driving_cell` → `UID-993` 只有 Error 不打异常 | timing-report 子代理回读 `report_port` |

**共同模式**：工具给了 `Error`/`Warning`，但流程继续，产物看起来正常。

**对策**：每个关键设置之后都要**回读校验**，不能只看 `STEP_*` 打印。

## 6. 已有的三项自证要扩展

此前建立的三项（针对"子模块变黑盒"）：

```bash
grep -c "^module recon_v21 "     netlist.v   # > 0
grep -c "^module recip_rnd_v21 " netlist.v   # > 0
grep -cE "VER-954|LINK-3|LINK-5" dc.log      # = 0
```

本轮新增两项（针对 LUT 与驱动单元）：

```bash
grep -c "VER-281"    dc.log      # initial 被忽略 -> 必须 0
grep -c "u0_r_reg"   netlist.v   # LUT 寄存器 -> 必须 > 0
grep -c "UID-993"    dc.log      # 驱动单元缺失 -> 必须 0
grep -E "^CHK driving_cells_set=" dc.log   # 必须 > 0
```

## 7. 待完成

用修好的驱动单元重新综合 + STA，重测：
- 面积（预期不变，驱动单元不改逻辑）
- 全局最差 slack
- 三类时间预算
- hold（此前报 1275 条违例，最差 −0.072 ns）

在拿到新数字前，**此前所有时序数字均标注为"驱动缺失条件下的结果"**。
