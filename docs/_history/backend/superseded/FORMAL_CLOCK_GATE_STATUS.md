# Formality 时钟门控问题：现状与根因

时间：2026-11-17　基线：`ace8bbc`

## 1. 当前结果

```
Verification FAILED
  86 Passing / 20 Failing / 128 Unverified
```

## 2. 根因（已由 analyze_points 确认）

```
Found 1 Unmatched Cone Input
i:/WORK/sched18/clock_gate_acq_q_reg/*lat.00*
  Is globally unmatched affecting 20 compare point(s):
    acq_q_reg[0..17]、conv_q_reg[0..1]
```

## 3. 网表里的时钟门控结构（实测）

```verilog
CKLNQD1BWP7T40P140 clock_gate_acq_q_reg (
    .CP ( clk ), .E ( adv ), .TE ( optlc_net_675 ),
    .Q ( clk_clock_gate_acq_q_reg ) ) ;

CKLNQD1BWP7T40P140 clock_gate_rng_q_reg (
    .CP ( clk ), .E ( N48 ), .TE ( optlc_net_675 ),
    .Q ( clk_clock_gate_rng_q_reg ) ) ;

DFCNQD1BWP7T40P140 \conv_q_reg[17] (
    .D ( acq_mask_o[17] ), .CP ( clk_clock_gate_acq_q_reg ),
    .CDN ( HFSNET_2 ), .Q ( conv_mask_o[17] ) ) ;
```

**共 2 个时钟门控单元**（`CHK_CG_COUNT 2`，实测）。

RTL 侧 `acq_q_reg[*]` / `conv_q_reg[*]` 由 `clk` 直接驱动，
网表侧改由门控时钟 `clk_clock_gate_acq_q_reg` 驱动。

**功能上等价**（`E=adv` 时开门，`E=0` 时保持），
但 Formality 需要显式 GCC 引导才能识别。

## 4. 已试过的方法

| # | 方法 | 结果 |
|:--:|:---|:---|
| 1 | 无 SVF | 86 / 20 / 128 |
| 2 | SVF 放 `match` 前 | `FM-256`（必须早于 design read）|
| 3 | SVF 放 design read 前 | **仍 86 / 20 / 128** |
| 4 | `all_clock_gating_latches` | **返回 0**（该命令不能自动识别）|
| 5 | `guide_gcc_latch_flop -latch` | 参数不对，需 `-design -from_latch -to_flop` |

## 5. 正确做法（已查明但未完成）

`guide_gcc_latch_flop` 需要三个参数：

```
-design     <设计名>
-from_latch <网表侧门控锁存器>
-to_flop    <RTL 侧对应触发器>
```

需要为每个门控单元列出它驱动的全部 flop，逐个建立映射。
2 个门控单元 → 20 个 flop（18 个 acq_q_reg + 2 个 conv_q_reg）。

**这条路径是可行的，但需要构造 20 条引导命令。**

## 6. 另一条更简单的路径：禁用时钟门控

综合时加：

```tcl
set_clock_gating_style -none
```

或在 FC 里关闭等效选项，让网表不带时钟门控。
**代价**：功耗略增（少了时钟门控省电），但 Formality 会直接通过。

## 7. 诚实结论

| 判断 | 状态 |
|:---|:---|
| 20 个 failing 由时钟门控导致 | ✅ 已由 `analyze_points` + 网表结构双重确认 |
| 时钟门控功能上是否等价 | ⚠️ **从结构看等价（标准 CKLNQD + 使能），但未形式化证明** |
| Formality 是否通过 | ❌ **FAILED** |

**不能说"等价性已验证"。当前是 FAILED，根因已明。**

## 8. 建议

| 方案 | 工作量 | 结果 |
|:---|:---|:---|
| **A** | 构造 20 条 GCC 引导命令 | 中 | 可能通过，且保留时钟门控 |
| **B** | 禁用时钟门控重跑全流程 | 低（约 5 分钟）| 大概率通过，但功耗略增 |
| C | 如实报告 FAILED + 根因 | 无 | 诚实但未闭环 |

**若要一个干净的等价结论，B 最快；若要保留时钟门控，A 是正路。**
