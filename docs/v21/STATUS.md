# 复现状态（单一权威版本）

**本文件是唯一权威状态。** 其它 `docs/v21/*.md` 是专项证据记录，
`docs/v21/_history/` 是已作废的历史版本，**不得引用**。

版本：`d5a1576`　分支：`merge/adi-repro`　日期：2026-11-16

---

## 一、能复现什么

**D1–D6 数字链的 RTL 功能层：可以复现，证据完整。**

| 模块 | 内容 | 状态 |
|:---|:---|:---|
| **D1** | 18 片 8/8/2 调度（`sched18`） | ✅ 4096 拍逐位 + 20000 拍统计 |
| **D2** | Flash/SAR 逐事件 follower（`sar_evt_v21`） | ✅ I2 37 项 + I1I2 105 项 |
| **D3** | DEM 3+3+3 + bridge | ✅ 133,376 对 + 66,688 组 |
| **D4** | 分裂 dither | ✅ 512 点 + 512 笔 |
| **D5** | 定点重构（`recon_v21` + `wsum_v21`） | ✅ 8213 项 + 连续输入 5 场景 + 任意权重 364 笔 |
| **D6** | 事务/宏控制（`acq_conv_ctx` + `adc2_capture`） | ✅ 2 反例 + 20 项 + 25 项 |
| 配置 | 权重生命周期与范围验收 | ✅ 40 项 |

### 一条命令复现

```bash
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
```

预期：**9 项全绿**

```
tb_acq_ctx_fixed    PASS   两反例已修复
tb_audit3           PASS   10 checks
tb_i12_full         PASS   105 checks
tb_i3_full          PASS   20 checks
tb_i4b_e2e          PASS   25 checks   accepted=5 captured=2 output=2
tb_recip_q60        PASS   43766 笔逐位
tb_recip_rnd        PASS   20817 笔逐位
tb_analog_if        PASS   54 checks（审查方原版）
tb_cfg_lifecycle    PASS   40 checks

[A] 数学恒等式 RND(n,96)        PROVEN
[A] X 的 32 位截断              PROVEN（前提 A1-A5）
[B] 生产 RTL 的 rnd96_const      PASS（从源码抽取）
[C] VC Formal 属性证明           未完成（不计入通过）
```

**关键码值**：A `raw=5120` → `0xFFF00`；B `raw=2560` → `0x80000`。

---

## 二、不能复现什么

| 项 | 状态 | 原因 |
|:---|:---|:---|
| **同版网表** | ❌ **没有** | 顶层综合 `medium` 停在 `wsum_v21`（四组受控实验复现）；方案 B 改寄存器堆后越过该点，但停在 `recip_q60`，2500 s 未完成 |
| 门级等价（当前版本） | ❌ 未做 | 依赖网表 |
| Formality（当前版本） | ❌ 未做 | 依赖网表 |
| **`u_wsum` 20 个 failing** | ❌ **未解决** | 对应旧网表 `485abbbd`，不代表当前版本；根因未定 |
| 布局布线 / DRC / LVS / GDS | ❌ 未做 | — |
| 40 MS/s 吞吐 | ❌ 未证明 | 单路径 slack 不能证明整笔事务吞吐 |
| hold 分析 / 多 PVT 角 | ❌ 未做 | 只做 setup、SS 0.81V −40 °C 单角 |

**所以：不能声称这是一个"完整验证通过的数字 IP"。**

---

## 三、本轮修掉的三个真实缺陷

| 缺陷 | 症状 | 证据 | 提交 |
|:---|:---|:---|:---|
| **LUT 用 `initial`** | DC 报 `VER-281` → `u0_r_reg` 被删 → 网表 `i_q60` **恒为常数 2** | 12 个合法 G 扫描：RTL 12/12 对、网表 12/12 全错；网表里 `assign inv_q60[0..2] = inv_q60[26]` | `878a348` |
| **IDLE 8/8/1** | 稳定 A/B 只覆盖 17 片 | 改为补集构造后 `8/8/2`，并集恒 18、交集 0 | `6b2cc7b` |
| **recon 连续输入串样** | 10 笔中 **8 笔码错**（A 期望 `0x80000` 实得 `0x90000`） | 三处根因可逐位复算：`6×33×2^30−3×64×2^30=6442450944`、`Z=3072→N=196608→X=96→Q=1→0x80001` | `fe7535f` |

---

## 四、结构性变更：权重表 → 内部寄存器堆

**起因**：`wsum_v21` 的权重表原是 14,400 根端口线，顶层端口数 15,775，
查表展开成 2,304 路 × 40 位 MUX；`wsum` 单独综合 594 s / 112,371 cells（占顶层 68%）。

**现状**：

| 指标 | 改前 | 改后 |
|:---|--:|--:|
| `wsum` 端口 | 14,828 | **480** |
| 顶层端口 | 15,775 | **1,427** |
| `wsum` elaborate | 594 s | **28 s** |
| 综合停点 | `wsum_v21` | 已越过，到 `recip_q60` |

**这只是"越过原停点"，不是"综合已解决"。** 新停点在 `recip_q60`，2500 s 未完成。

配置接口约定见 [`CONFIG_CONTRACT.md`](CONFIG_CONTRACT.md)（52 位，含三态生命周期）。

---

## 五、作废的数字（不得再引用）

| 数字 | 作废原因 |
|:---|:---|
| 89,693 µm² | `g_q30` 位宽不匹配 → `recip_rnd_v21` 变黑盒 |
| 76,222 µm² | `v1_q` 重复声明 → `recon_v21` 变黑盒 |
| 97,555 µm² / +9.39 ns | LUT 被 DC 丢弃，网表功能错误 |
| 108,951 µm² / +3.02 ns | 测于 `1fb9408`，**不含**本轮三项修复 |
| "门级 1464 拍差异 0" | 基于错误网表；且 1464 是**日志行数**，仅 90 观察沿、2 笔输出 |
| "d3d4_ip 的 1770.08 µm²" | 只是那个小 IP 的数字，**不代表 v21 全链** |

---

## 六、文档索引

### 现行（权威）

| 文件 | 内容 |
|:---|:---|
| **`STATUS.md`** | **本文件，唯一权威状态** |
| `CONFIG_CONTRACT.md` | 权重配置接口约定 |
| `FORMAL_STATUS.md` | Formality 当前状态（FAILED，20 failing） |
| `SYNTH_STALL.md` | 综合停住的四组受控实验 |
| `FORMAL_DIAGNOSIS.md` | 20 failing 的诊断（含未闭环项） |
| `RECON_ALIGN_FIX.md` | 连续输入串样修复细节 |
| `LUT_SYNTH_BUG.md` | `initial` 被丢弃的缺陷链 |
| `PROOF_LEDGER.md` | `[A]`/`[B]` 证明台账 |
| `CHECKPOINT_I*.md` | 各阶段 checkpoint |
| `I5_ENVIRONMENT.md` | 远端工具环境 |

### 历史（不得引用）

`docs/v21/_history/`：
`TIMING_BUDGET_FINAL_superseded`、`GATE_EQUIV_superseded`、
`I5_PORTS_superseded`、`DRIVING_CELL_BUG_superseded`、
`AREA_VS_PPT`、`PPA_DIV_VS_RECIP`

这些含已作废的中间版本数字（如 108,951 µm² 的逐代演进），
保留仅为追溯，**任何结论不得引用**。

---

## 七、环境

- 本机 macOS，无 EDA
- 远端 `vm-yian`：`dc_shell` / `vcs` / `fm_shell` / `fc_shell` / `pt_shell` / `calibre`
- PDK：TSMC 28HPC+，库 `tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db`
- **驱动单元必须用 `BUFFD2BWP7T40P140`**（`BUF_X2` 不存在，且 `set_driving_cell`
  失败时不抛异常，只打 `UID-993`）

---

## 八、下一步（待授权）

| 任务 | 状态 |
|:---|:---|
| 综合 `recip_q60` 单独摸底 | 未批准 |
| 新结构完整综合 | 未批准 |
| 修 `check_recip_correction.py` 旧 LUT 语法（审计 R5） | 未批准 |
| Formality 20 failing 根因 | 未批准 |

**不自行推进，等待明确授权。**
