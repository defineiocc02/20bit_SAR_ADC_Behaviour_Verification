# 三项审计反例修复检查点

时间：2026-11-15　审计来源：`isscc9p8_audit_20261115`（基线 `0977e14`）
**状态：PASS（三项反例全部闭合）**

## 起点与复现

- 基线 HEAD：`b671671`（I4b 端到端 24 项）
- 审计脚本复现旧版三问题（实测）：

```
NODE k=519 raw=5120 expected=5120        (对)
NODE k=520 raw=5120 expected=1024        (错：j19 漏算)
GUARD_REJECT applied=1 expected=0        (错：拒绝也写了)
FINAL cmd=485 writes=8 expected=7        (错：多写一次)
POOL pre_wr=000ff post_wr=000ff overlap=000ff expected=00000  (错：全重叠)
```

## 三项修复

### ① 切片写门重叠（`POOL`）

**根因不是"取沿前还是沿后"，而是 sched18 根本不推进。**

`adv` 原来接 `commit_fire`，而 `commit_fire = do_promote` 只在
`acq_can_promote`（ACQ 已采样）时成立。**首笔 A 的 prepare 时 ACQ 槽是空的**，
`can_promote=0` → `adv=0` → sched18 不推进 → A 与 B 冻结成同一组片号
（实测都是 `0x398A418820`）。

修法：新增 `sched_adv_o = do_prepare` —— 任何被接受的 prepare 都占一个采样周期。
另把 pre 路由改为「本拍正在建立的那一笔」的片号（`do_prepare ? sched_ids : acq_ids_q`）。

**修复后**：`pre_wr=0x3BD00`（B 组）、`post_wr=0x000FF`（A 组）、**`overlap=0x00000`**。

### ② guard 拒绝 512 多写一次（`GUARD_REJECT` / `FINAL`）

方案 §8.1："512 试探**不提交**；仍 7 次，实际多一次比较"。
旧版让拒绝那次也 `apply_fire=1`，于是整笔写 8 次。

修法：`apply_fire_o = bit_valid_i & can_apply & ~guard_reject_th`，
其中 `guard_reject_th = guard_q & (exp_evt_q==2) & ~bit_i`。
**状态机仍然回退**（`exp_evt` 回 2、`guard_q` 清零、前缀保持 448），只是不写物理开关。

**修复后**：`applied=0`、`final_k=485`、**`writes=7`**。

### ③ j19 转换权重漏算（`NODE`）

旧版把 j19（子阵列平衡/guard 1Cu，名义 1/128）排除在 Wm 之外，
理由是"平衡位不贡献信号电荷"。**那是错的：j19 是真实电容，选中就计入 Wm。**

判据来自一对定向向量（同一 x、同一 word=63）：
- `k=519` → post 每片 `0x7FFFF`（j19=0）→ Wm=32.4375 → **raw=5120**
- `k=520` → post 每片 `0xFFFFF`（j19=1）→ Wm=32.5000 → **raw=1024**

旧版把两者都算成 5120，正是漏了 8 片 × 1/128 = 0.0625。

修法：节点宏与 `wsum_v21` 的 Wm 都改为 **4 位全算**（同一物理位表）。

**另一个真 bug**：`always_comb` 里 `for (int j=0;j<4;j++) w_sum[s][j]` 这种
二维数组 + 循环变量的写法，VCS 会漏掉最后一次迭代（实测 post[19]=1 但 Wm 不含它）。
改成 **4 条显式展开语句**后正确。

## 三项正式回归（`tb/v21/tb_audit3.sv`，10 项）

| 项 | 断言 |
|:---|:---|
| ① | `(pre_wr & post_wr) === 0`；pre 对应本笔 ACQ 组；post 对应本笔 CONV 组 |
| ② | 拒 512 时 `apply=0`；`final_k=485`；实际写 7 次 |
| ③ | k=519 → 5120；k=520 → 1024；y=−0.09375 V；未越窗 |

## 全部回归（修复后）

| 回归 | 结果 |
|:---|:---|
| **审计三反例** | **PASS (10)** |
| I1 两笔采样 | PASS (18) |
| I2 事件协议 | PASS (37) |
| I1+I2 完整联调 | **PASS (105)**（+1：新增"拒 512 写门为 0"） |
| I3 完整 | PASS (20) |
| I4 重构 | PASS (8213, 8209 retired) |
| I4b 端到端 | PASS (24) |
| D3D4 L1 叶子 | PASS (133376 + 512) |
| D3D4 L2/L2.5/L2.6 | PASS |
| bridge 66,688 组 | PASS |

## 统一入口

```bash
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
```

实测输出：

```
  tb_audit3.sv  PASS
  tb_i12_full.sv  PASS
  tb_i3_full.sv  PASS
  tb_i4b_e2e.sv  PASS
全部通过：审计三反例 + I1/I2 + I3 + I4b 端到端
```

## 修的一个测试自身缺陷

`tb_i12_full` 里旧断言要求"阈被拒也应 apply=1"——那正是被审计判为错误的行为。
RTL 修好后该断言反而失败。已改为"不应 apply"并新增写门检查。

**教训**：测试断言必须与方案原文（"不提交"）一致，不能照抄当时的实现行为。

## 剩余（I5 后端）

| 项 | 状态 |
|:---|:---|
| 28HPC+ 综合 | 可跑（I3 移出节点宏后 7383 cells / 4508 µm² / slack +22.15 ns） |
| 布局布线 | **缺 ICC2/Innovus**，见 `docs/v21/I5_ENVIRONMENT.md` |
| 后布线 STA / DRC / LVS / GDS | 同上，工具缺失 |
| 端口时序表 | `docs/v21/I5_PORTS.md`（79 端口，与工艺无关，40 nm 可复用） |
| 时序预算 | `docs/v21/I5_TIMING_BUDGET.md` |
