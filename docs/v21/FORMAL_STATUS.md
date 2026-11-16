# 形式化等价（Formality）状态

时间：2026-11-16　基线：`738b35f` + 本轮
工具：Formality W-2024.09-SP3

## 1. 流程已打通（本轮主要进展）

Formality 的完整流程**首次跑通**：

```
STEP_DB_OK            读库成功
STEP_SVF_OK           加载 DC 写出的 SVF 成功
STEP_SUPPRESS_OK      抑制良性 RTL 消息成功
STEP_REF_READ_OK      14 个 RTL 文件全部读入（fail=0）
STEP_REF_TOP_OK       set_top r:/WORK/adc_digital_ip_v21
STEP_IMPL_TOP_OK      set_top i:/WORK/adc_digital_ip_v21
STEP_MATCH_OK         匹配通过
STEP_VERIFY_OK        verify 执行（但超时，见 §3）
```

## 2. 打通流程踩过的 6 个坑（全部实测）

| # | 错误做法 | 正确做法 / 现象 |
|:--:|:---|:---|
| 1 | `set_app_var link_library` | 不存在（CMD-104），用 `read_db` |
| 2 | `set_top_designs` | 不存在（CMD-005），用 `set_top` |
| 3 | `set_container` | 不存在（CMD-005） |
| 4 | `create_container -type design` | 不支持 `-type`（CMD-010） |
| 5 | 一次读多个 RTL 文件 | 只接受一个（CMD-012），必须逐个读 |
| 6 | 网表不带 `-container i` | 会读进当前容器 r，`set_top i:` 报 FM-036 |

另外两个必要的设置：

- **必须抑制良性消息**：`FMR_VLOG-091`（函数用非局部变量，
  `wsum_v21` 的 `f_Wm`/`f_Wp` 读模块级 `w_sub_tbl`）、
  `FMR_VLOG-057`/`-063`（有符号/无符号混用）。
  不抑制时 link 直接失败（FM-089/FM-262）。
- **必须加载 SVF**：DC 会做极性变换（用 `DFCNQD1` 反相输出触发器）、
  寄存器合并、常量传播。没有 SVF 时 20 个 failing compare point
  **全部集中在 DFCNQD1 寄存器上**（`quiet_cnt_q` / `conv_valid_q` /
  `conv_captured_q` / `conv_held_q`）—— 这是极性失配，不是功能差异。

## 3. 当前状态：verify 正在长时间计算

| 阶段 | 结果 |
|:---|:---|
| 读库 / SVF / 读 RTL / set_top / match | **全部通过** |
| `verify` | **仍在计算**（已 >11 min CPU，`fm_shell_exec` 稳定 94% CPU） |

**这是真实计算，不是卡死**：`ps` 显示 `fm_shell_exec` 持续 94% CPU，
日志停在 `Status: Verifying...`（Formality 在 verify 期间不输出中间进度）。

**加载 SVF 之后计算量显著增加** —— 与"SVF 让 Formality 复现更多优化、
需要更多证明"一致。对照：

| 条件 | verify 耗时 | 结果 |
|:---|:---|:---|
| 无 SVF | ~180 s | `FAILED`，20 failing（全在 DFCNQD1 寄存器上）—— **极性失配的快速失败，不是真实结论** |
| 有 SVF | >850 s（首轮超时）→ 本轮 >11 min 仍在跑 | 待定 |

所以"无 SVF 的 20 failing"不能当作等价性结论，已被正确排除。
本轮用 2300 s 预算重跑，等结果。

## 4. 待做

1. **用更长预算重跑 verify**：本轮 850 s 超时，需要 ≥1800 s。
   若仍超时，用 `set verification_time_limit` 分段验证，
   或先做分模块（block-level）等价。
2. **若能出结果**：报告 Passing / Failing / Unverified 的确切数字，
   并对 failing 点跑 `analyze_points`。
3. **门级仿真等价已完成**（`GATE_EQUIV_PASS`，1464 拍差异 0），
   这是当前可用的等价证据。形式化是更强的补充，不是替代。

## 5. 复跑

```bash
# 需要先有 SVF（DC 综合时自动生成）
scp flow/synth/v21/dc_synth_v21.tcl vm-yian:/tmp/
ssh vm-yian 'rm -rf /tmp/dcwork && mkdir -p /tmp/dcwork &&
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth_v21.tcl > /tmp/dcwork/dc.log 2>&1'
ls -la /tmp/dcwork/*.svf     # 应有 ~130 KB

# Formality（建议放到后台，预留 30 min）
scp flow/synth/v21/fm_equiv.tcl vm-yian:/tmp/
ssh vm-yian 'PATH=/opt/synopsys/fm/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  fm_shell -f /tmp/fm_equiv.tcl > /tmp/fmv.log 2>&1'
grep -E "^STEP_|Verification " /tmp/fmv.log
```

## 6. 结论（如实）

**形式化等价流程已打通，但尚未取得等价结论。**
当前可用的等价证据是门级仿真（1464 拍差异 0）。
不能声称"形式化已验证"。
