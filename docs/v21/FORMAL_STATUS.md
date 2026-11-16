# 形式化等价（Formality）状态

时间：2026-11-16　基线：`054cfed`　工具：Formality W-2024.09-SP3

> **本文件只记录当前状态，不把旧轮次结论当现状。**

## 1. 最新正式结果

| 项 | 值 |
|:---|:---|
| 判定 | **`Verification FAILED`** |
| Passing | 2,589 |
| **Failing** | **20** |
| Unverified | 614 |
| 失败点 | `u_wsum/G_q30_reg[*]` 8 + `Wp_q30_reg[*]` 6 + `Wm_q30_reg[*]` 6 |
| 日志 | `/tmp/fmfix2_review_130645.log`（13:49） |
| 用的网表 | `/tmp/dcwork`（`485abbbd`） |

**注意**：该结果对应 `485abbbd` 网表，其 RTL 是 `878a348` 之前的版本，
**不代表 `054cfed`**。`054cfed` 尚未跑过 Formality（无同版网表）。

## 2. 流程已打通（可复用）

`fm_equiv.tcl` 的 8 个 STEP 全部可执行：

```
STEP_DB_OK → STEP_SVF_OK → STEP_SUPPRESS_OK → STEP_REF_READ_OK
→ STEP_REF_TOP_OK → STEP_IMPL_TOP_OK → STEP_MATCH_OK → STEP_VERIFY_OK
```

注意 `STEP_VERIFY_OK` **只表示执行到了该步骤**，不等于 `Verification SUCCEEDED`。

## 3. 命令体系的坑（已实测，写进脚本注释）

| 错误做法 | 正确做法 |
|:---|:---|
| `set_app_var link_library` | 不存在（CMD-104）→ 用 `read_db` |
| `set_top_designs` | 不存在（CMD-005）→ 用 `set_top` |
| `set_container` | 不存在（CMD-005） |
| `create_container -type` | 不支持 `-type`（CMD-010） |
| 一次读多个 RTL 文件 | 只接受一个（CMD-012）→ 逐个读 |
| 网表不带 `-container i` | 必须带，否则读进容器 r，`set_top i:` 报 FM-036 |

两个必要设置：
- **抑制良性消息**：`FMR_VLOG-091`（函数读非局部变量）、`-057`/`-063`（有/无符号混用）。不抑制时 link 直接失败。
- **加载 SVF**：DC 会做极性变换（用 `DFCNQD1` 反相输出触发器）。没有 SVF 时 failing 会集中在这些寄存器上。

## 4. 未闭环项

| # | 项 | 状态 |
|:--:|:---|:---|
| 1 | 20 个 `u_wsum` failing 的根因 | **未定位** |
| 2 | `analyze_points` 报 2833 个权重表端口"未约束"，但 `report_unmatched_points` 两侧都报 "No unmatched points" | **矛盾未解释** |
| 3 | `054cfed` 版本的 Formality | **未跑**（无同版网表） |

## 5. 已排除的做法

**不使用 `set_constant`**：`analyze_points` 会建议对未约束端口加常量来"消掉"失败。
那是把不确定输入桩死，属于裁输入域，不做。

**也不加 G 范围约束**：12 个合法 G 全在合同域 `[2^34, 2^36]` 内网表仍全错
（当年的 LUT 缺陷），域外解释不了。

## 6. 复跑

```bash
# 前置：dc_synth_v21.tcl 生成网表与 SVF
D3D4_RUNNER=vm-yian bash tools/check_formal.sh [timeout_sec]

# 三态退出码：0 PASS / 1 FAIL / 2 INCONCLUSIVE（超时不当作通过）
```
