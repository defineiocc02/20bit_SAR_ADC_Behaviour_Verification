# 门级等价：RTL 与 28HPC+ 综合网表逐拍一致

时间：2026-11-16　基线：`e54566f` + 本轮
网表：`/tmp/dcwork/adc_digital_ip_v21_syn.v`（DC W-2024.09-SP3，623 s，97,356 µm²）

## 1. 结果

```
RESULT: GATE_EQUIV_PASS (1464 拍，RTL == 28HPC+ 网表)
```

同一组激励分别驱动 RTL 与综合网表，逐拍比对 22 个关键输出，
**1464 拍差异 0**。

覆盖的信号：`prepare_ready`、`out_valid/code/sat/err/tag`、
`acq_sel_o`、`conv_sel_o`、`idle_sel_o`、`pre_wr_o`、`post_wr_o`、
`adc2_capture_arm_o`、`adc2_rsp_valid_o/tag_o`、
`apply_fire_o`、`k_o`、`final_applied_o`、`final_k_o`、
`err_o`、`done_o`、`node_valid_o`、`node_ids_o`。

## 2. 证据等级（必须注明）

**这是门级仿真等价，不是形式化等价。**

- 覆盖：施加的那一组激励（一笔完整事务：prepare → sample →
  5 个已决事件 → ADC2 返回 → hold → 释放）
- 不覆盖：其他输入组合、其他事件序列

Formality 形式等价**未跑通**：`fm_shell` W-2024.09-SP3 在本机进入
`(setup)` 模式，其命令集与 DC/PT 不同：

| 尝试 | 报错 |
|:---|:---|
| `set_app_var link_library` | `Variable 'link_library' is not an application variable` (CMD-104) |
| `set_top_designs` | `unknown command` (CMD-005) |
| `set_container` | `unknown command` (CMD-005) |
| `create_container -type design` | `unknown option '-type'` (CMD-010) |

如实记录，不编造工具能力限制。

## 3. 为什么需要自动生成门级 TB

DC 把 `w_main_tbl[18][16][40]` 与 `w_sub_tbl[18][4][40]`
展开成**扁平拼接端口**：

```verilog
.w_main_tbl({ \w_main_tbl[0][0][39] , \w_main_tbl[0][0][38] , ... })
```

- `w_main_tbl` 是 **11,520 位**的单一端口（18×16×40）
- `w_sub_tbl` 是 **2,880 位**（18×4×40）
- RTL 侧是数组端口 `[0:17][0:15]`

同一份 TB 无法同时连接两者 —— VCS 报 `PCTM`。
所以 `tools/gen_gate_tb.py` 从网表端口自动生成门级 TB：
解析端口、声明同宽扁平向量、按名义权重驱动、生成与 RTL 一致的激励。

## 4. 对齐过程中踩的三个坑

轨迹 diff 从 126 处降到 0，中间三处都是**测试不一致**，不是 RTL 或
网表的功能差异：

| # | 现象 | 根因 |
|:--:|:---|:---|
| 1 | RTL 的 `acq_sel_o` 全程 `xxxxx`，门级是 `0x3BD00` | `tb_trace.sv` 未连接 `sched_ext_sel` / `sched_acq` / `sched_conv` / `sched_idle` / `sched_ids`，输入悬空为 X |
| 2 | 数值一致但行错位（126 处） | 两侧字段输出顺序不同 |
| 3 | `err_o` RTL=1、门级=0 | `tb_trace.sv` 未设 `prepare_U=5`，与 `word=63` 派生的 U 冲突，触发 `U_conflict` |

**第 1 条特别值得记**：X 值在 diff 里表现为"值不同"，很容易被误判成
网表问题。实际是 TB 漏接输入。门级 TB 由脚本按网表端口生成，
天然接全，所以只有 RTL 侧出问题 —— 这个不对称正好暴露了它。

## 5. 复跑

```bash
# 前置：先跑综合生成网表
ssh vm-yian 'rm -rf /tmp/dcwork && mkdir -p /tmp/dcwork &&
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth_v21.tcl > /tmp/dcwork/dc.log 2>&1'

# 门级等价（一条命令）
D3D4_RUNNER=vm-yian bash tools/check_gate_equiv.sh
```

脚本内含三项网表自证：`grep '^module recip_rnd_v21'` 必须 > 0、
`grep LINK-3` 必须 = 0、门级能编译。

## 6. 当前 I5 状态

| 项 | 状态 | 证据 |
|:---|:---|:---|
| 综合收敛 | ✅ | `STEP_COMPILE_OK`，623 s |
| 网表完整性 | ✅ | `recip_rnd_v21` 定义 13 个，LINK-3 = 0 |
| 面积 | ✅ 97,356.24 µm² | `area.rpt` |
| 时序 | ✅ slack +9.07 ns @25 ns | `sta.rpt` |
| 未时钟寄存器 | ✅ 0 / 2,018 | |
| **门级等价** | **✅ 1464 拍差异 0** | 本文件 |
| 形式化等价 | ❌ 未跑通 | `fm_shell` (setup) 模式命令集不同 |
| 布局布线 | ❌ 未做 | |
| 后布线 STA | ❌ 未做 | |
| 三类时间预算实测 | ❌ 未做 | |
