# Formality 结果分析：20 个 failing 的根因是时钟门控

时间：2026-11-17　基线：`2620093` 之后

## 1. 结果

```
Verification FAILED
  86 Passing compare points
  20 Failing compare points (20 matched, 0 unmatched)
  128 Unverified compare points
```

| 类型 | Passing | Failing | Unverified |
|:---|--:|--:|--:|
| Port | 86 | 0 | 80 |
| DFF | 0 | **20** | 48 |

## 2. 根因（`analyze_points -failing` 给出）

```
Found 1 Unmatched Cone Input
--------------------------------
i:/WORK/sched18/clock_gate_acq_q_reg/*lat.00*
    Is globally unmatched affecting 20 compare point(s):
        r:/WORK/sched18/acq_q_reg[0]   ... acq_q_reg[17]
        r:/WORK/sched18/conv_q_reg[0]  conv_q_reg[1]
```

**是一组 CTS 插入的时钟门控单元（clock_gate）**，
它的内部锁存器 `*lat.00*` 在 RTL 侧不存在，因此在网表侧 unmatched。

**这不是设计错误，是时钟门控插入的固有结构差异。**

## 3. 为什么 SVF 没解决

SVF 已生成（24,576 字节，二进制约 56 行），并已按正确顺序
（`read_db` 之后、任何 `read_*verilog` 之前）加载：

```
STEP_DB_OK
STEP_SVF_OK      ← set_svf 成功
STEP_REF_OK
STEP_IMPL_OK
STEP_MATCH_OK
Verification FAILED    ← 结果与无 SVF 时完全相同
```

**`set_svf` 顺序的错误已修正**（此前放在 `match` 前会报 `FM-256`），
但 failing 数没变。

**可能原因**：
- FC 的 SVF 只记录综合期变换，不含 CTS 插入的时钟门控信息
- 时钟门控需要在 Formality 里用专门设置（如 `set_clock_gate` 或识别为等价）

## 4. 已尝试的修正（按时间）

| # | 尝试 | 结果 |
|:--:|:---|:---|
| 1 | 无 SVF | 86 / 20 / 128 |
| 2 | SVF 放在 `match` 前 | `FM-256`（必须早于 design read）|
| 3 | **SVF 放在 `read_db` 后、design read 前** | **仍是 86 / 20 / 128** |

## 5. 关键判断

**20 个 failing 全部是 `acq_q_reg[*]` 和 `conv_q_reg[*]`，且都由同一个时钟门控单元导致。**

这**高度可能**是时钟门控的等价性建模问题，而非功能缺陷。依据：
- 20 个点全部来自同一个 unmatched cone input
- 该单元名为 `clock_gate_acq_q_reg`（时钟门控）
- 其余 86 个 Port 点全部 passing

**但我不能仅凭"看起来像"就断言等价。**

## 6. 必须说明的不确定性

| 判断 | 状态 |
|:---|:---|
| 20 个 failing 由时钟门控导致 | ✅ **已由 `analyze_points` 确认** |
| 时钟门控是否保持功能等价 | ❌ **未验证** |
| 网表与 RTL 是否功能等价 | ❌ **未通过 Formality** |

**所以：不能说"Formality 通过"。当前是 FAILED。**

## 7. 下一步可试

| 方案 | 做法 |
|:---|:---|
| A | 查 Formality 的时钟门控设置选项（`set_clock_gate` / `set_flatten_model` 等）|
| B | 综合时禁用时钟门控（`set_clock_gating_style -none`），重跑全流程 |
| C | 用 `-clock_gating` 相关 verify 选项 |
| D | 接受当前结果，如实报告 20 个 failing 未闭环 |

## 8. 复跑

```bash
# 已保存脚本
/tmp/fm_run3.tcl    # SVF 顺序正确版
# analyze_points 版本
/tmp/fm_ap.tcl
```
