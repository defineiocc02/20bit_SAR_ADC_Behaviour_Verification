# 三类时间预算实测（逐条路径取证版）

时间：2026-11-16
基线：`e8123ea`（`merge/adi-repro`）
工艺角：`tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs`（SS 0.81 V −40 °C）
周期：25 ns（40 MS/s）
STA 脚本：[`flow/synth/v21/sta_budget.tcl`](../../../flow/synth/v21/sta_budget.tcl)（v6.0）

> **数字出处（重要）**：本报告的全部路径数字来自**修复了 `recip_q60` LUT 缺陷之后**
> 的网表 `/tmp/dcwork2/adc_digital_ip_v21_syn.v`（md5 `361b21e9…`，11:02 那轮综合）。
> 该网表里 `VER-281` = 0、`u0_r_reg` = 30、`inv_q60_reg` = 27，
> 即 LUT 寄存器与倒数输出寄存器都回来了。
>
> 本文件早前的一版还用过两份**退化网表**（`db803f8c…` / `0572abc4…`，
> `i_q60` 恒为 2）。那两份的 slack 明显偏松（全局最差 +16.801 vs 修复后的 **+2.718**），
> **不可再用**。差异与作废说明见 §8.4。
>
> **本版推翻了本文件上一版的三处解释错误。** 上一版把 `25 − slack` 当作
> "功能阶段可用时间"、把 `rdac_pre_state_o`（采样预置）当成"已决事件→post 开关"、
> 并声称 `-through` 能"跨多级寄存器切分阶段"。这三点都不成立，逐条更正见第 6 节。
> 上一版的全部数字仍然保留在第 8 节，作为对照 —— **不删不利数据**。

---

## 1. 取数条件与可复现命令

### 1.1 网表与口径

| 项 | 值 |
|:---|:---|
| **网表（正文数字出处）** | `/tmp/dcwork2/adc_digital_ip_v21_syn.v`，md5 `361b21e9…`（LUT 已修复） |
| 网表快照（本次取证留档） | `vm-yian:/tmp/sta_audit/adc_digital_ip_v21_syn_fixed.v` |
| 面积报告 | `/tmp/dcwork2/area.rpt` → `/tmp/sta_audit/area_fixed.rpt` |
| 综合日志 | `/tmp/dcwork2/dc.log`（`VER-281` = 0） |
| 综合时 STA | `/tmp/dcwork2/sta.rpt`（`report_timing -max_paths 20`） |
| 综合违例报告 | `/tmp/dcwork2/viol.rpt`（`report_constraint -all_violators`） |
| 已作废的退化网表 | `/tmp/sta_audit/adc_digital_ip_v21_syn.v`（`db803f8c…`）、`…_syn_head.v`（`0572abc4…`） |
| STA 工具 | DC W-2024.09-SP3（`dc_shell`） |
| 库 | `tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs` |

**两套时序数字不能混用**：

| 口径 | 约束 | 出处 |
|:---|:---|:---|
| 综合时 | 只有 `create_clock -period 25` + uncertainty/transition；**没有** `set_input_delay` / `set_output_delay` / driving cell | `dc_synth_v21.tcl` → `sta.rpt` |
| 独立 STA（本报告） | 25 ns + setup 0.30 + hold 0.15 + clock transition 0.10 + IO delay 各 1.0 ns + 负载 0.05 pF | `sta_budget.tcl` |

这两套的 slack 会有差别，主要差在**端口路径**（综合时没有 IO 约束）。
对纯 reg→reg 路径两者接近，因为 IO 约束不影响它。
本报告除第 8 节的历史对照外，全部数字来自独立 STA 口径。

### 1.2 复跑

```bash
scp flow/synth/v21/sta_budget.tcl vm-yian:/tmp/
ssh vm-yian 'mkdir -p /tmp/sta_budget_out_fixed &&
  STA_NETLIST=/tmp/dcwork2/adc_digital_ip_v21_syn.v \
  STA_OUTDIR=/tmp/sta_budget_out_fixed \
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost LM_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/sta_budget.tcl > /tmp/budget_fixed.log 2>&1'

grep -a "^TAG \|^VIOL \|^DRV " /tmp/budget_fixed.log        # ASCII 摘要
cat /tmp/sta_budget_out_fixed/timing_budget_audit.md          # 中文完整体（UTF-8）
ls  /tmp/sta_budget_out_fixed/paths/                          # 每条查询一个 report_timing 全文
```

**必须显式给 `STA_NETLIST`**：`/tmp/dcwork` 会被后续综合覆盖（本次取证期间就被覆盖过两次）。

脚本支持 `STA_NETLIST` / `STA_OUTDIR` 两个环境变量覆盖网表与输出目录，
便于对同一批约束跑不同网表快照做对照（本报告 §8.3 就是这么做的）。

### 1.3 本轮实际观测到的日志路径

| 文件 | 内容 |
|:---|:---|
| `vm-yian:/tmp/budget_fixed.log` | **修复后网表**的 `sta_budget.tcl` 运行日志（ASCII 摘要，本报告出处） |
| `vm-yian:/tmp/sta_budget_out_fixed/timing_budget_audit.md` | 修复后网表中文完整体报告 |
| `vm-yian:/tmp/sta_budget_out_fixed/paths/*.rpt` | 26 条 `full_clock_expanded` 完整体 |
| `vm-yian:/tmp/sta_audit/area_fixed.rpt` | 修复后网表的 `report_area` |
| `vm-yian:/tmp/sta_audit/adc_digital_ip_v21_syn_fixed.v` | 修复后网表快照（md5 `361b21e9…`） |
| `vm-yian:/tmp/sta_audit/lib.rpt` | `report_lib` 全文（含 ZeroWireload 定义） |
| `vm-yian:/tmp/budget_{baseline,head}.log` | 两份**退化网表**的对照运行（仅用于 §8.4 的作废说明） |

---

## 2. 三类预算各自能问什么、不能问什么

这是本报告的核心结论，先讲清楚再看表。

| | 能由本 STA 回答 | 不能由本 STA 回答 |
|:--|:---|:---|
| ① 释放→采集 | 释放判定、ACQ 装载、片组掩码输出这些**单拍组合段**是否在 25 ns 内 | 一笔交易从"释放"到"下一笔采集完成"跨了几拍、总共多少 ns |
| ② 已决事件→RDAC post | 事件译码→`post_state_q` 的单拍组合段；`post_state_q`→端口的适配段 | 模拟域 RDAC 建立时间；"事件到开关真正翻转"的物理延迟 |
| ③ 末次生效→ADC2 捕获 | `arm` 授权、`hold` 记录、安静窗计数这些**数字判定逻辑**的单拍延时 | 参考建立、RA 建立、模拟安静窗、宏 hold 的实际时长 |

统一说明：

- **slack 是"这条路径相对 25 ns 周期还剩多少"**，不是"这个功能阶段还有多少时间可用"。
  `slack = data required − data arrival`，而 `data required` 是这条路径专属的要求时刻
  （周期 − uncertainty − setup，端口路径再加减外部延迟）。因此**不能**把 `25 − slack`
  读成阶段延迟，也**不能**把几条路径的 slack 相加或相减。
- **时序路径不穿寄存器**。STA 的起点是"上一拍时钟沿 + 起始寄存器 Q（或输入端口）"，
  终点是"下一拍时钟沿 + 终止寄存器 D（或输出端口）"。所有多拍功能阶段在 STA 里
  被切成若干个互不相连的单段组合路径。

---

## 3. ① 旧组释放 → 下一组采集

### 3.1 单段组合路径

| 查询 | 起点 | 终点 | data arrival | data required | slack | 覆盖到哪一步 |
|:---|:---|:---|---:|---:|---:|:---|
| `B1A_QUIET_TO_CONV` | `u_ctx/quiet_cnt_q_reg[1]/CP` | `u_ctx/conv_valid_q_reg/D` | 1.491 | 24.685 | **+23.194** | 安静窗计满 → CONV 释放判定（组合比较） |
| `B1C_ACQ_IDS_REG2REG` | `u_sched/acq_q_reg[0]/CP` | `u_ctx/acq_ids_q_reg[0]/D` | 3.211 | 24.670 | **+21.459** | 调度器片号 → ACQ 描述符锁存（**reg→reg**） |
| `B1B_ACQ_IDS_PORTSTART` | `rst_n` | `u_ctx/acq_ids_q_reg[0]/D` | 3.980 | 24.708 | +20.728 | 同上端点的最差路径，但**起点是复位**，不是功能路径 |
| `B1D_ACQ_SEL_OUT` | `rst_n` | `acq_sel_o[14]` | 5.120 | 23.700 | **+18.580** | ACQ 片组选通输出（同样被复位路径占据） |
| `B1E_IDLE_SEL_OUT` | `sched_ext_sel` | `idle_sel_o[17]` | 1.765 | 23.700 | +21.935 | 释放后片组回池的掩码输出 |

**注意 `B1B` / `B1D` 的起点是 `rst_n`**：复位分发是一棵高扇出树，它常常自动成为
"到某个端点"的最差路径。这不是功能瓶颈，只是复位树的延迟。功能路径要看 reg→reg 的
`B1C`（+21.459）。

### 3.2 这一类的真实瓶颈

**`B1C_ACQ_IDS_REG2REG` 是本类里最紧的功能段：启动 3.211 ns / 要求 24.670 ns / 余量 +21.459 ns。**

`B1A` 更松（1.491 / 24.685 / +23.194）：释放判定本身只是几个与门，几乎不花时间；
真正花时间的是"调度器片号 → 40 位描述符锁存"这段扇出。

### 3.3 这一类的跨拍部分 STA 回答不了

RTL 里（`rtl/v21/acq_conv_ctx.sv`）释放与再采集是**多拍序列**：

```
conv_captured_q & conv_held_q & quiet_cnt_q >= QUIET_CYCLES(2)
  → release_fire_o（第 N 拍组合，第 N+1 拍 conv_valid_q 落 0）
  → prepare_ready 变高
  → 下一笔 prepare 被接受（第 M 拍，M >= N+1）
  → acq_ids_q 装载
```

`QUIET_CYCLES = 2` 是参数（`rtl/v21/acq_conv_ctx.sv` 第 40 行）。加上安静窗的
2 拍与槽位交接的 1~2 拍，这条链在**拍数**上至少是 3~4 拍。
25 ns 的周期约束对这种跨拍行为**不给任何保证** —— 它只保证每一拍内部的组合段够快。
要证明"释放后能及时开始下一笔采集"，必须数拍子（这正是第 7 节要求的事务级证据）。

---

## 4. ② 已决事件 → 物理 RDAC post 开关

### 4.1 post 通路的两个单段

RTL 里 post 通路在寄存器处天然断开（`rtl/v21/adc_digital_ip_v21.sv`）：

```systemverilog
always_ff @(posedge clk ...) begin
    post_wr_q <= post_wr_c;
    if (post_wr_c[gp]) post_state_q[gp*20 +: 20] <= post_phys_c[gp*20 +: 20];
end
assign rdac_post_state_o = post_state_q;
assign post_wr_o         = post_wr_q;
```

对应两条 STA 路径：

| 查询 | 起点 | 终点 | data arrival | data required | slack | 覆盖到哪一步 |
|:---|:---|:---|---:|---:|---:|:---|
| `B2B_POST_D_REG2REG` | `u_evt/exp_evt_q_reg[2]/CP` | `post_state_q_reg[356]/D` | 2.839 | 24.716 | **+21.878** | 事件译码 + DEM 展开 → post 数据锁存（**寄存器前**） |
| `B2C_FROM_EXP_EVT_REG2REG` | `u_evt/exp_evt_q_reg[2]/CP` | `post_state_q_reg[356]/D` | 2.839 | 24.716 | +21.878 | 同上（`-from exp_evt_q` 与 `-to post_state_q/D` 落到同一条） |
| `B2D_POST_ADAPTER` | `post_state_q_reg[359]/CP` | `rdac_post_state_o[359]` | 0.382 | 23.700 | **+23.318** | 寄存器 → 输出端口（**寄存器后**，纯线负载） |
| `B2E_POST_WR_OUT` | `post_wr_q_reg[17]/CP` | `post_wr_o[17]` | 0.379 | 23.700 | +23.321 | 写门 → 端口（同上） |
| `B2A_POST_D_PORTSTART` | `rst_n` | `post_state_q_reg[266]/D` | 4.861 | 24.709 | +19.848 | 同一端点的复位路径，非功能路径 |

### 4.2 这一类的真实瓶颈

**`B2B_POST_D_REG2REG`：2.839 ns 到达 / 24.716 ns 要求 / 余量 +21.878 ns。**

这是 `exp_evt_q` → `post_state_q` 的单拍组合段，也就是"事件已决 → DEM 展开 → 写进
post 寄存器"这一级。`logic_depth = 32`，是三类预算里功能段中最深的一条。

第二段（`B2D`）几乎为零（0.382 ns / depth 0），说明综合已经把
`post_state_q → rdac_post_state_o` 优化成了直连（纯负载），**不构成瓶颈**。

### 4.3 采样预置（pre）通路 —— 与 post 无关，单独列

`rdac_pre_state_o` 与 `pre_wr_o` 由 `acq_conv_ctx` 的**纯组合** `pre_state_c` / `pre_wr_c`
驱动（`rtl/v21/acq_conv_ctx.sv`「物理路由」一节），数据来自 `dither_pre` 在
**prepare 那一拍**给出的 slot 级预置：

```systemverilog
wire [31:0] pre_route_data = do_prepare ? acq_pre_slot_i : acq_pre_q;
assign rdac_pre_state_o  = pre_state_c;
assign pre_wr_o          = pre_wr_c;
```

| 查询 | 起点 | 终点 | data arrival | data required | slack | 说明 |
|:---|:---|:---|---:|---:|---:|:---|
| `B2G_PRE_REG2REG` | `u_sched/acq_q_reg[0]/CP` | `rdac_pre_state_o[7]` | 3.929 | 23.700 | **+19.771** | pre 通路的**真实功能段** |
| `B2F_PRE_PORTSTART` | `rst_n` | `rdac_pre_state_o[71]` | 5.066 | 23.700 | +18.634 | 同一端点的复位路径 |
| `B2H_PRE_WR_OUT` | `rst_n` | `pre_wr_o[17]` | 4.722 | 23.700 | +18.978 | pre 写门，同样被复位占据 |

**上一版报告把 `+19.771`（当时报的是 +20.38）当作"已决事件 → post 开关"的最紧项，
这是错的。** 它是 **prepare 时**的采样预置延迟，与"已决事件（SAR 逐次比较结果）
→ post 开关"完全不是一条通路。二者在 RTL 里由不同的信号驱动、发生在交易的不同阶段：

| | pre 通路 | post 通路 |
|:---|:---|:---|
| 何时写 | **prepare** 被接受时（本笔开始前） | 每个**已决事件**（逐次比较） |
| 数据来源 | `dither_pre`（`prepare_word` / `prepare_v`） | `sar_evt_v21` 的 `prefix_accept` |
| RTL 驱动 | `pre_state_c`（组合） | `post_state_q`（寄存器） |
| 与之相关的预算 | ①（准备/采集） | ②（已决事件应用） |

正确做法是把 pre 段记在 ① 的"准备"环节，post 段记在 ②。

---

## 5. ③ 末次生效 → 经参考/RA/安静窗 → ADC2 捕获

### 5.1 数字侧能测到的部分

| 查询 | 起点 | 终点 | data arrival | data required | slack | 覆盖到哪一步 |
|:---|:---|:---|---:|---:|---:|:---|
| `B3B_ARM_OUT_REG2REG` | `u_evt/final_applied_q_reg/CP` | `adc2_capture_arm_o` | 0.937 | 23.700 | **+22.763** | 末次应用标志 → **沿前授权**输出 |
| `B3A_ARM_OUT` | `adc2_valid_i` | `adc2_capture_arm_o` | 1.283 | 23.700 | +22.417 | 同上，但起点是宏回报端口（含 input external delay） |
| `B3C_HOLD_DONE` | `adc2_valid_i` | `u_adc2/hold_done_q_reg/D` | 1.529 | 24.680 | +23.152 | 宏 hold 回报 → hold 记录 |
| `B3E_QUIET_CNT_REG2REG` | `u_ctx/conv_valid_q_reg/CP` | `u_ctx/quiet_cnt_q_reg[0]/D` | 1.631 | 24.677 | **+23.046** | 捕获标志 → 安静窗计数（**reg→reg**） |
| `B3D_QUIET_CNT` | `rst_n` | `u_ctx/quiet_cnt_q_reg[0]/D` | 4.181 | 24.677 | +20.496 | 同一端点的复位路径 |
| `B3F_FINAL_POST_REG2REG` | `final_post_q_reg[113]/CP` | `u_wsum/Wm_q30_reg[47]/D` | 4.765 | 24.683 | **+19.919** | final post → wsum 权重累加（depth 60，本类最深） |

### 5.2 这一类的真实瓶颈

**数字侧最紧的是 `B3F_FINAL_POST_REG2REG`：4.765 ns 到达 / 24.684 ns 要求 / +19.919 ns
（logic_depth = 60）。**

它覆盖"最后一次 post 结果 → 18×20 权重选择求和的第一级寄存器"这一段。
`arm` 本身很松（+22.763 / depth 4），因为 arm 的门槛
`conv_valid & final_applied & wsum_valid & ~seen` 只有几个门。

### 5.3 这一类的绝大部分**不在 STA 范围内**

方案 §10 的 ③ 是"末次开关生效 → 保持 → 参考切换 → RA 建立 → 安静窗 → ADC2 捕获"。
其中：

| 环节 | 归属 | STA 能否回答 |
|:---|:---|:---|
| 末次 post 数据落到寄存器 | 数字 | ✅ 能（`B3F`） |
| `arm` 沿前授权 | 数字 | ✅ 能（`B3B`） |
| `hold` 回报的记录逻辑 | 数字 | ✅ 能（`B3C`） |
| 安静窗**计数**逻辑 | 数字 | ✅ 能（`B3E`），但"2 拍"是一个**拍数**约束，不是延时约束 |
| RDAC 保持建立 | 模拟 | ❌ 不能 |
| 参考切换/建立 | 模拟 | ❌ 不能 |
| RA（residue amplifier）建立 | 模拟 | ❌ 不能 |
| 模拟安静窗时长 | 模拟 | ❌ 不能 |
| ADC2 宏内部捕获 | 模拟/宏 | ❌ 不能（本网表里 ADC2 是端口桩） |

**结论：③ 的预算目前只能用"数字判定逻辑够快 + 模拟侧另行给数"的方式拼起来，
不能声称整段已满足。** 本 STA 只证明了"数字判定逻辑在 25 ns 内绰绰有余"。

---

## 6. 逐条更正上一版的解释错误（审查指出的五点 + 取证中另发现四点）

### 6.1 `25 − slack` 不是"阶段可用时间"

**上一版写的**：

> 换算：`阶段可用时间 = 周期 − slack = 25 − slack`
> ① 整段（CONV → ACQ 锁存）slack 23.73 ns → 可用时间 1.27 ns

**为什么错**：

1. `slack` 的定义是 `data required − data arrival`。`data required` 里已经扣掉了
   时钟不确定度与 setup，还包含端口的外部延迟修正（本报告里端口路径的
   `endpoint_output_delay_value = −1.000`）。把一个"已经含多项修正的差值"直接
   从 25 里减掉，得到的既不是延迟也不是预算。
2. 更根本的是：**一条路径只覆盖功能阶段的一小段**。①"释放→采集"在 RTL 里
   （`rtl/v21/acq_conv_ctx.sv`）至少跨三拍 —— `release_fire_o` 组合成立、
   下一拍 `conv_valid_q` 落 0、再下一拍 `prepare_ready` 才可能被接受，
   之后还有 `quiet_cnt_q` 的 2 拍安静窗；而 STA 报的 +21.459 只是其中
   "调度器片号 → ACQ 描述符"这**一拍**内的组合段。用这一段反推整个阶段，
   量纲和语义都不对。
3. 实测反证：`B1A`（+23.194）与 `B1C`（+21.459）是同一个功能阶段的两段。
   按上一版公式会得到 `25 − 23.194 = 1.81 ns` 和 `25 − 21.459 = 3.54 ns`
   两个"阶段可用时间"—— 同一个功能阶段给出两个答案，说明这个换算本身没有意义。

**本版做法**：只报 `data arrival` / `data required` / `slack` 三个原始量，
并标注每段"覆盖到哪一步"。阶段级结论放到第 7 节的拍数分析。

### 6.2 `rdac_pre_state_o` 不是"已决事件 → post 开关"

见 4.3 节。上一版把 pre 侧的 slack（当时 20.38）当作 ② 的最紧项，并写下
"pre 侧比 post 侧紧（20.38 vs 23.79）：pre 通路要经 dither_pre 的相位选择，
组合逻辑更长" —— 这句话本身描述的现象（pre 比 post 深）是真的，
但它**不属于 ②**。pre 是 prepare 时的采样预置，post 是逐事件的已决结果应用。

本版把 pre 归到 ① 的"准备"环节（`B2G` / `B2F` / `B2H`），
② 只保留 post 两段（`B2B` / `B2D`）。

### 6.3 `-through` 不会跨过寄存器

**上一版写的**：

> 用 `-through [conv_*_q_reg/Q]` 再 `-to [acq_ids_q_reg/D]`，把跨多级寄存器的阶段切出来

**实测反证**（`sta_budget.tcl` 第 3 节，两次运行结果一致）：

| 查询 | 起点 | 终点 | slack |
|:---|:---|:---|---:|
| `-through *conv_*_q_reg*/Q -to *acq_ids_q_reg*/D` | `u_ctx/conv_valid_q_reg/CP` | `u_ctx/acq_ids_q_reg[0]/D` | +23.308 |
| `-from *conv_*_q_reg*/Q -to *acq_ids_q_reg*/D` | `u_ctx/conv_valid_q_reg/CP` | `u_ctx/acq_ids_q_reg[0]/D` | +23.308 |

两组查询的**起点完全相同**（都是同一个寄存器的 `CP` 引脚）。这说明
`-through <寄存器>/Q` 与 `-from <同一寄存器>/Q` 取到的是同一条路径：
路径从那个寄存器**出发**，根本没有"穿过"它。

时序路径的端点只能是"起始寄存器 Q / 输入端口"和"终止寄存器 D / 输出端口"，
中间的组合逻辑里不存在寄存器。所以 `-through` 只能约束"必经某个组合节点"，
**永远无法把多级寄存器串成一条路径**。

### 6.4 setup 为正 ≠ 40 MS/s 连续采样已验证

**上一版写的**：

> `recip_q60` 的 Newton 迭代输出寄存器，25 ns 下余量 37.6%。**全部 setup 违例为 0。**

setup 违例为 0 只是**必要条件**。它证明的是"每一条单拍组合路径都能在 25 ns 内完成"，
不证明"一笔交易能在 25 ns 内走完全部流水"。见第 7 节的拍数账：
`recon` 单笔延迟 7 拍 = 175 ns，远大于 25 ns。它之所以**可能**还能跑 40 MS/s，
靠的是 18 个物理片分成 8 ACQ + 8 CONV + 2 IDLE 三态流转、以及 recon 的 7 级流水重叠
（`rtl/sched18.sv` 的不变量：`|acq'| = 8`、`|conv'| = 8`、`|IDLE| = 2`、并集 = 18）。
**但"可能"不等于"已验证"** —— 这套重叠能否维持 40 MS/s 的稳态吞吐，
STA 完全没有检查（原因见第 7.3 节）。

### 6.5 驱动单元绑定错误

**上一版（以及 v1~v5 的所有脚本）写的**：

```tcl
set_driving_cell -lib_cell BUF_X2 -pin Z [remove_from_collection [all_inputs] [get_ports clk]]
```

**实测**：本库没有 `BUF_X2`。库里的缓冲器是
`BUFFD0/1/2/3/4/6/8/12/16/20BWP7T40P140` 与 `BUFTD*`。

```
Error: Cannot find the specified driving cell in memory.   (UID-993)
```

**这条 Error 不会抛 Tcl 异常**，`if {[catch ...]}` 会走成功分支 —— 这就是它一直
没被发现的原因。唯一可靠的核实手段是回读 `report_port -verbose` 的 "Driving Cell" 一栏：

| 状态 | `report_port` 中是否出现驱动单元 |
|:---|:---|
| 不设驱动（= 旧脚本的实际状态） | **不出现** |
| 设 `BUFFD2BWP7T40P140` | **出现**（`BUFFD2BWP7T40P140/Z`） |

**影响量化**（同一端口 `ev_bit_valid_i` 作起点，只改驱动设置）：

| 口径 | 起点 | 终点 | arrival | required | slack |
|:---|:---|:---|---:|---:|---:|
| 无驱动 | `ev_bit_valid_i` | `apply_fire_o` | 1.204 | 23.700 | **+22.495** |
| BUFFD2 驱动 | `ev_bit_valid_i` | `apply_fire_o` | 1.211 | 23.700 | **+22.489** |

差 0.006 ns。原因是这条路径只有 2 级逻辑、端口扇出也小，输入转换时间对它的影响
被淹没在 setup/uncertainty 里。**但这个 0.006 ns 不能推广**：驱动缺失时 DC 用的是
最乐观的零输入转换，扇出大的端口（如 `prepare_tag[31:0]`、`w_main_tbl[*]`）
在真实驱动下会明显变差。所以本报告对所有**端口起点**的 slack 都标注为"乐观上界"。

**wire load 的实测状态**：

`report_wire_load` 与 `report_lib` 显示本设计用的是库里的 `ZeroWireload`：

```
Wire load model:   ZeroWireload
Location       :   adc_digital_ip_v21 (design)
Resistance     :   1e-05
Capacitance    :   1
Area           :   0
（Fanout/Length/Points/Average Cap 全部为 0）
```

含义与影响：

| 问题 | 答案 |
|:---|:---|
| `ZeroWireload` 是什么 | 库中唯一可选的线负载模型，**把互连线电容/电阻按 0 计** |
| 日志里的 "Changed wire load model from '(none)' to 'ZeroWireload'" | 是 DC 给每个子模块补上库默认模型的过程信息（`OPT-170`），不是错误，也不是"换了模型导致数字变了" |
| 对时序是乐观还是悲观 | **乐观**。零互连电容意味着所有 net 延迟只算驱动单元的本征延迟 + 扇出引脚电容，实际布线后会额外增加 RC 延迟 |
| 为什么面积报告的 Net Interconnect area 是 undefined | 同一个原因：`Wire load has zero net area` |

因此**本报告的所有 slack 都是"零互连"下的乐观值**，必须在 P&R 后用 SPEF 重跑
（PrimeTime）才能作为签核数字。

**有没有端口既没有 driving cell 也没有 input delay**：没有。`set_input_delay 1.0`
作用到了 `[remove_from_collection [all_inputs] [get_ports clk]]` 的全部 14654 个端口，
可以逐条验证（`B1B` / `B2A` / `B3C` 等所有端口起点路径的
`startpoint_input_delay_value` 都是 `1.000000`）。缺的只有 driving cell。

---

## 7. 40 MS/s 连续采样：STA 不能回答，需要什么证据

### 7.1 单笔延迟的拍数账（来自 RTL，不是 STA）

数 `rtl/v21/recon_v21.sv` 里 valid 链的寄存器级数（这是**唯一**可靠的算法，
不要用"RTL 注释说几级"去猜）：

| 寄存器 | RTL 行 | 累计拍（相对 `in_valid`） |
|:---|:---|---:|
| `v1a_q <= in_valid` | 187 | 1 |
| `v1_q <= v1a_q` | 206 | 2 |
| `v2a_q <= v1_q` | 255 | 3 |
| `v2b_q <= v2a_q` | 270 | 4 |
| `v2_q <= v2b_q` | 283 | 5 |
| `v3_q <= v2_q` | 296 | 6 |
| `out_valid <= v3_q` | 314 | **7** |

RTL 注释也确认了这个数（`recip_rnd_v21` 的 3 拍延迟被 `v2a/v2b/v2c` 对齐吸收）：

> 代价：`out_valid` 相对 `in_valid` 的延迟由 6 拍变成 7 拍。

所以 **`recon_v21` 单笔延迟 = 7 拍 = 175 ns @ 25 ns**，
是周期约束的 7 倍。`recip_rnd_v21` 自身贡献 3 拍
（`recip_q60`: `v1` → `v2`，再加 `recip_rnd_v21` 的出口拍）。

一个完整交易还要加上：

- 事件序列：Flash + 6 个 SAR 位 = **7 个已决事件**，每个事件占一拍
  （`sar_evt_v21` 的 `exp_evt_q` 每拍自增 1）；
- 释放前的安静窗：`QUIET_CYCLES = 2` 拍；
- ACQ→CONV 的槽位交接：≥1 拍。

保守估计单笔从 prepare 到结果可用是 **20 拍以上**（≥500 ns）。
（这个数只做了量级估算：7 拍事件 + 2 拍安静窗 + 7 拍 recon + 若干交接拍，
不是逐拍统计的结果 —— 逐拍统计是 §7.3 列出的待补证据。）

### 7.2 那为什么还可能跑 40 MS/s

因为结构上不是"一笔跑完再跑下一笔"：

- `sched18` 维护 18 个物理片的分组状态（8 ACQ + 8 CONV + idle）；
- `acq_conv_ctx` 有一个 ACQ 槽和一个 CONV 槽，**ACQ 与 CONV 可以同时处理不同的笔**
  （`do_prepare` 时把已采样的 ACQ 提升进 CONV，同时装新 ACQ）；
- `recon` 是 7 级流水，只要每拍能喂进一笔新的就能保持吞吐；
- 因此**稳态吞吐**理论上由"最慢的一级"决定，而不是由单笔延迟决定。

### 7.3 因此还需要什么证据

| 需要的证据 | 为什么 STA 给不了 | 目前状态 |
|:---|:---|:---|
| 事务级延迟表（一笔 prepare → 结果 out 的精确拍数） | STA 只看单拍组合段 | 仅数出 `recon` 7 拍（§7.1），端到端未逐拍统计 |
| 稳态吞吐分析（每 N 拍能完成一笔？瓶颈在哪一级？） | STA 不做资源/仲裁分析 | **缺** |
| 背靠背 40 MS/s 连续采样的事务级仿真 | 需要激励与断言 | **缺**（`tb_i4b_e2e` 是逐笔功能验证，不是吞吐压测） |
| 门级仿真实测（带 SDF） | 需要 P&R 后的 SDF | **缺**（无 P&R） |
| 每拍是否真的能喂进新事件（`apply_fire` 的占空比） | STA 不检查协议 | **缺** |

**在这四类证据补齐之前，"40 MS/s 连续采样已验证"这个说法不能成立。**
本报告能说的只有：**在 25 ns 周期、SS 0.81 V −40 °C、零互连、无 driving cell 的
乐观条件下，所有单拍组合路径的 setup 余量为正（最差 **+2.718 ns**，
落在 `recip_q60` 的 Newton 迭代段）。**
注意 `+2.718 ns` 只有周期的 10.9%，比早前退化网表上报的 +16.801 紧得多 ——
那正是 §11 所述缺陷造成的假象。

### 7.4 顺带发现的 hold 违例（上一版未报）

上一版写"本报告只做 setup（`-delay_type max`）"，把 hold 略过了。实际跑
`report_constraint -all_violators -min_delay`：

| 检查 | 违例条数 | 最差值 |
|:---|---:|---:|
| setup（max_delay） | **0** | — |
| hold（min_delay） | **1275** | **−0.072 ns** @ `u_recon/err1a_q_reg/CP → u_recon/err1_q_reg/D` |

- 这是**综合网表 + 理想时钟（零 skew）** 下的 hold 检查结果。
  在 CTS 之前出现 hold 违例是常见现象（综合器不修 hold），因此**不能据此判定设计失败**；
- 但也**不能**像上一版那样不提。这些违例集中在同沿直通的短路径上
  （`err1_q` / `err2a_q` / `i2_q` / `n2_q` 等 recon 的 valid/tag 链），
  典型的修法是 CTS 后的 buffer 插入或 hold fix；
- 真实结论：**hold 尚未收敛，需要在 P&R + CTS 后重跑**。

---

## 8. 与上一版报告的数字对照（保留不利数据）

上一版（`git show eebbc2d:docs/v21/TIMING_BUDGET_FINAL.md`）的数字**列在这里不删**，
但**其"可用时间"一列不能再用**，原因见 6.1。

| 上一版的量 | 上一版 slack | 上一版"可用时间" | 本版对应查询 | 本版 slack | 差异来源 |
|:---|---:|---:|:---|---:|:---|
| ① CONV 状态 → 安静窗计数 | 24.57 | 0.43 | `B1A`（方向相反：quiet→conv_valid） | +23.194 | 上一版查的是 `-through conv_*_q_reg/Q -to quiet_cnt_q_reg/D`，起点是 conv 系寄存器；本版查的是反向的释放判定 |
| ① 安静窗 → ACQ 描述符锁存 | 23.72 | 1.28 | `B1C` | +21.459 | 上一版与 `B1A`/`B1C` 混用了阈值相同的 `-through` 集合 |
| ① 整段（CONV → ACQ 锁存） | 23.73 | 1.27 | 不存在这样的单条路径 | — | **"整段"不是一条 STA 路径** |
| ② 事件层内部（最长一段） | 21.87 | 3.13 | `B2B` | +21.878 | 一致 |
| ② → `rdac_post_state_o` | 23.79 | 1.21 | `B2D` | +23.318 | 上一版用的是 `-to [get_ports rdac_post_state_o]` 且无 uncertainty/IO 差异 |
| ② → `rdac_pre_state_o` | **20.38**（当作 ② 最紧） | 4.62 | `B2G`（归入 pre/①） | +19.771 | **归类错误**：pre 不是 post |
| ③ → `adc2_capture_arm_o` | 22.59 | 2.41 | `B3B` | +22.763 | 上一版起点未限定，落到端口路径 |
| ③ hold 判定内部 | 23.45 | 1.55 | `B3C` | +23.152 | 一致 |
| ③ 安静窗内部 | 22.29 | 2.71 | `B3E` | +23.048 | 上一版 `-from quiet_cnt_q/Q` 方向相反 |
| 全局最差 | +9.39 | — | `GLOBAL_REG2REG`（**修复后网表**） | **+2.718** | **见 8.1** |

### 8.1 全局最差：+9.39 / +16.801 / +2.718 三个数字的来龙去脉

这三个数字出自**三代网表**，必须分清：

| 数字 | 来源网表 | 起点 → 终点 | 说明 |
|:---|:---|:---|:---|
| **+9.39** | `eebbc2d` 那一轮（综合时 `report_timing`） | `u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]/CP → u_recon/u_recip_rnd/i_q60_reg[26]` | 已在留存网表中**无法复核**（当时的文件被覆盖），且该轮网表本身有 §11 的缺陷 |
| **+16.801** | 退化网表 `db803f8c…` / `0572abc4…`（`i_q60` 恒为 2） | `u_recon/x3_q_reg[31]/CP → u_recon/code_o_reg[19]/D` | 本条可复现，但属于**短路径**：`i2_q` 已退化为常量，乘法器被折掉一侧 |
| **+2.718** | **修复后网表** `361b21e9…` | `u_recon/u_recip_rnd/u_recip/u0_r_reg[29]/CP → u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]/D` | **本报告采用**；`u0_r_reg`（LUT 输出）与 `inv_q60_reg`（倒数结果）都在，路径落在 Newton 迭代上 |

`data arrival 22.005 / data required 24.724 / slack +2.718 ns`。

**关键教训**：+16.801 看起来很宽松（周期的 67%），是因为 `recip_q60` 的 LUT 被
综合丢弃、`i_q60` 恒为 2，把一个 27 位乘法器的一侧折成了常量。
修复后同一条设计的最差路径变成 +2.718（周期的 10.9%），**余量缩小了 14 ns**。
这说明**不能用"某条路径 slack 很宽"推断"这块逻辑很轻"** —— 先确认网表是完整的
（`VER-281`=0、`u0_r_reg`>0、`inv_q60_reg`>0）。

`+9.39` 与修复后的 `+2.718` 落在同一条链上（`inv_q60_reg` 的前后段），
这也印证了 +9.39 那一轮大概率是另一份结构不同的网表；但该文件已不存在，
**标记为"现有证据不足"**，本报告不采用。

### 8.2 取证期间发现 /tmp/dcwork 被覆盖

本次工作开始时 `/tmp/dcwork/` 里是 09:34 的基线产物；10:07 起该目录被另一轮
综合（读取了更新后的 RTL）覆盖。两次的 area 报告差异：

| 项 | 基线快照（`/tmp/gate2/old_baseline/`，09:34） | 当前 `/tmp/dcwork/`（10:17） |
|:---|---:|---:|
| Total cell area | **90,287.30 µm²** | 90,303.28 µm² |
| Number of cells | 142,900 | 142,921 |
| Number of sequential cells | 2,088 | 2,088 |
| Combinational area | 86,390.53 | 86,406.40 |
| Noncombinational area | 3,896.77 | 3,896.87 |

两份网表都在独立 STA 里重新 `report_area` 复算过
（`/tmp/sta_audit/area_baseline.rpt` / `area_head.rpt`），数字可以互相印证。

**这意味着：本文件上一版写的 `97,554.69 µm² / 151,346 cells` 在留存的任何一份
网表与任何一份 `area.rpt` 上都复现不出来。** 详见 9.2。

### 8.3 修复前 vs 修复后：同一脚本、同一约束、逐条对照

这是本报告最重要的一张表 —— 它量化了"网表功能缺陷"对时序结论的影响：

| 查询 | 退化网表 `0572abc4` | **修复后 `361b21e9`** | 差 |
|:---|---:|---:|---:|
| `GLOBAL_REG2REG` | +16.801 | **+2.718** | **−14.083** |
| `GLOBAL_FROM_INPUT` | +18.221 | +18.580 | +0.359 |
| `THROUGH_PROOF` | +23.247 | +23.247 | 0.000 |
| `B1A_QUIET_TO_CONV` | +23.194 | +23.194 | 0.000 |
| `B1B_ACQ_IDS_PORTSTART` | +20.728 | +20.728 | 0.000 |
| `B1C_ACQ_IDS_REG2REG` | +21.459 | +21.459 | 0.000 |
| `B1D_ACQ_SEL_OUT` | +18.221 | +18.580 | +0.359 |
| `B1E_IDLE_SEL_OUT` | +21.935 | +21.935 | 0.000 |
| `B2A_POST_D_PORTSTART` | +19.848 | +19.848 | 0.000 |
| `B2B_POST_D_REG2REG` | +21.878 | +21.878 | 0.000 |
| `B2D_POST_ADAPTER` | +23.318 | +23.318 | 0.000 |
| `B2E_POST_WR_OUT` | +23.321 | +23.321 | 0.000 |
| `B2F_PRE_PORTSTART` | +18.772 | +18.634 | −0.138 |
| `B2G_PRE_REG2REG` | +19.771 | +19.771 | 0.000 |
| `B2H_PRE_WR_OUT` | +18.619 | +18.978 | +0.359 |
| `B3A_ARM_OUT` | +22.417 | +22.417 | 0.000 |
| `B3B_ARM_OUT_REG2REG` | +22.763 | +22.763 | 0.000 |
| `B3C_HOLD_DONE` | +23.152 | +23.152 | 0.000 |
| `B3D_QUIET_CNT` | +20.491 | +20.496 | +0.005 |
| `B3E_QUIET_CNT_REG2REG` | +23.048 | +23.046 | −0.002 |
| `B3F_FINAL_POST_REG2REG` | +19.919 | +19.919 | 0.000 |
| hold 违例条数 | 1279 | **1275** | −4 |
| hold 最差 | −0.072 | −0.072 | 0.000 |
| setup 违例 | 0 | **0** | 0 |

**读法**：

- **只有全局最差（`recon`/`recip_q60` 那条）发生了 14 ns 的剧变**，
  其余 20 条查询连 0.4 ns 都没动。这符合预期：缺陷只影响 `recip_q60`
  内部的数据通路，而三类预算的路径都在 `ctx` / `evt` / `wsum` / `adc2` 上，
  与 `i_q60` 无关。
- 因此：**§10 的三条瓶颈结论（+21.459 / +21.878 / +19.919）在修复前后都成立**，
  可以放心使用；唯一必须作废并替换的是"全局最差 +16.801"。
- `GLOBAL_FROM_INPUT` / `B1D` / `B2H` 的小幅变化（+0.359）是复位分发缓冲在不同
  综合轮次下的正常波动，不是功能差异。

### 8.4 三代网表一览

| 代 | 文件 | md5 | `VER-281` | `u0_r_reg` | `i_q60` | 全局最差 |
|:--:|:---|:---|:--:|:--:|:---|:---|
| 1 | `eebbc2d` 那轮（已覆盖） | — | 1 | 0 | 恒 2 | +9.39（不可复核） |
| 2 | `/tmp/sta_audit/adc_digital_ip_v21_syn.v` | `db803f8c…` | 1 | 0 | 恒 2 | +16.801 |
| 2' | `/tmp/sta_audit/adc_digital_ip_v21_syn_head.v` | `0572abc4…` | 1 | 0 | 恒 2 | +16.801 |
| 3 | `/tmp/sta_audit/adc_digital_ip_v21_syn_fixed.v` | `361b21e9…` | **0** | **30** | 随 G 变化 | **+2.718** |

**只有第 3 代是可用的。** 前两代的面积（90,287 / 90,303 µm²）与 slack 都不可用；
第 3 代的面积是 **108,951.11 µm²**（见 §9）。

---

## 9. 面积与资源

### 9.1 实测面积（修复后网表）

`/tmp/sta_audit/area_fixed.rpt`（即 `/tmp/dcwork2/area.rpt`，md5 `254ae1ba…`）：

| 指标 | **修复后（采用）** | 退化网表（作废） |
|:---|---:|---:|
| Total cell area | **108,951.11 µm²** | 90,303.28 µm² |
| cells | **165,374** | 142,921 |
| 组合面积 | 104,890.38 µm² | 86,406.40 µm² |
| 时序面积（Noncombinational） | 4,060.73 µm² | 3,896.87 µm² |
| 时序单元数 | **2,176** | 2,088 |
| 未时钟寄存器 | **0** | 0 |

面积比退化网表大 **18,648 µm²（+20.7%）**：LUT 的 31 位种子寄存器、
整张 64 项查表逻辑（`DW02_mult` ×4、`DW01_add/sub` ×10、`DW_rash`）
以及被连带恢复的 Newton 迭代通路都回来了。这正是该缺陷"看起来面积很漂亮"的原因。

### 9.2 早前几版面积数字的处置

| 指标 | `eebbc2d` 那轮 | 退化网表实测 | **修复后实测（采用）** |
|:---|---:|---:|---:|
| Total cell area | 97,554.69 µm² | 90,287.30 / 90,303.28 | **108,951.11 µm²** |
| cells | 151,346 | 142,900 / 142,921 | **165,374** |
| 未时钟寄存器 | 0 / 2,116 | 0 / 2,088 | **0 / 2,176** |

- `97,554.69 / 151,346 / 2,116` 在留存的任何网表、`area.rpt`、`dc.log` 里都搜不到，
  该轮产物已被覆盖 —— **标记为"现有证据不足"，不再使用**。
- `90,287.30 / 90,303.28` 可以复现，但出自**退化网表**（LUT 被丢弃，
  少 31 bit 寄存器 + 整张查表逻辑），**同样不可用**。
- **本报告以 `108,951.11 µm² / 165,374 cells / 2,176 寄存器`为准。**
  这三个数可以逐条复核：`grep "Total cell area" /tmp/sta_audit/area_fixed.rpt`。

### 9.3 与原型 60,000 µm² 的对比

| 对象 | 面积 | 口径 |
|:---|---:|:---|
| 原型数字区 | 60,000 µm² | **照片图估的版图区域** |
| 本工程完整顶层 | **108,951 µm²** | **标准单元综合面积** |

**口径不同，不能解读成"差 1.8 倍"。** 理由（上一版已写明，本版保留）：

- 一个是**版图面积**（含布线、含留白），一个是**标准单元面积**（不含布线、不含留白）；
- 原型的范围是否与本工程一致，未披露；
- 本结果未做面积优化（`compile -map_effort medium`，且 `recon` 保留精确除法）。

数值比 1.82× 只说明"两者不在同一量纲上"，不能用于面积优劣判断。

## 10. 三类预算的真实结论

### 10.1 三条真实瓶颈

| 预算 | 真实瓶颈查询 | 起点 → 终点 | arrival | required | slack | 在 25 ns 内？ |
|:---|:---|:---|---:|---:|---:|:---|
| ① 释放→采集 | `B1C_ACQ_IDS_REG2REG` | `u_sched/acq_q_reg[0]/CP` → `u_ctx/acq_ids_q_reg[0]/D` | 3.211 | 24.670 | **+21.459** | ✅ 是 |
| ② 已决事件→post | `B2B_POST_D_REG2REG` | `u_evt/exp_evt_q_reg[2]/CP` → `post_state_q_reg[356]/D` | 2.839 | 24.716 | **+21.878** | ✅ 是 |
| ③ 末次生效→arm | `B3F_FINAL_POST_REG2REG`（数字侧最深） | `final_post_q_reg[113]/CP` → `u_wsum/Wm_q30_reg[47]/D` | 4.765 | 24.683 | **+19.919** | ✅ 是 |

补充：全局最紧的是 `GLOBAL_REG2REG`
（`u_recon/u_recip_rnd/u_recip/u0_r_reg[29]/CP → u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]/D`，
22.005 / 24.724 / **+2.718**），它属于 `recip_q60` 的 Newton 迭代，
**不属于三类预算中的任何一类**。
也就是说：**三类预算都不是时序瓶颈，`recon` 的通路才是。**

### 10.2 哪些能回答、哪些不能

| 预算 | STA 能回答的部分 | 结论 | STA 不能回答的部分 |
|:---|:---|:---|:---|
| ① | 释放判定 + ACQ 装载的单拍组合段 | ✅ 余量 +21.459，宽松 | 释放到再采集跨 3~4 拍（含 2 拍安静窗），需拍数/事务级分析 |
| ② | 事件译码→post 寄存器；post→端口 | ✅ 余量 +21.878 / +23.318，宽松 | 模拟 RDAC 建立；"事件→开关真正翻转" |
| ③ | arm / hold / quiet 计数的数字逻辑 | ✅ 余量 +19.919 ~ +23.048，宽松 | 参考/RA/安静窗/宏捕获（**全部模拟域**） |

### 10.3 一句话结论

> 在 25 ns、SS 0.81 V −40 °C、零互连、无 driving cell 的**乐观**条件下，
> 三类预算涉及的**全部单拍组合路径 setup 余量为正**（最紧 +19.919，
> 三类之外最紧 +2.718）。
>
> **先加一条限定**：本结论是在 §11 所述**已退化网表**上取得的
> （`recip_q60` 的 LUT 在综合时被丢弃，`i_q60` 恒为 2），
> 修复后的网表必须重跑才能沿用这些数字。
>
> 在这个限定下，它只证明"每一拍够快"，**不证明**：
> (a) 一笔交易能在 40 MS/s 的节拍下走完多拍流水（需事务级/吞吐证据）；
> (b) ③ 的模拟段能满足宏的 hold/quiet 要求（需模拟侧给数）；
> (c) hold 已收敛（实测 1275 条违例，最差 −0.072 ns，需 CTS 后重跑）；
> (d) 布线后仍成立（零 wire load + 无驱动，需 P&R + SPEF + PrimeTime）。

---

## 11. 取证途中撞到的网表功能缺陷（已修复，且已用修复后网表重测）

这一条不在审查清单里，但取证时撞上了，而且**直接改变了结论**，必须写出来。

### 11.1 缺陷本身

`rtl/recip_q60.sv` 用 `initial begin lut[0..63] = ... end` 初始化 64 项 Newton 种子表。
DC 忽略 `initial` 块：

```
Warning: /tmp/ip/rtl/recip_q60.sv:82: The statements in initial blocks are ignored. (VER-281)
Information: The register 'u0_r_reg[30]' is a constant and will be removed. (OPT-1206)
...（u0_r_reg 共 146 条 OPT-1206）
```

影响链：`initial` 被丢弃 → `lut` 全 X → `u0` 变常量 → `u0_r_reg` 被
`OPT-1206` 删掉 → Newton 迭代退化 → `inv_q60` 不再是 G 的函数。
退化后的网表里 `inv_q60` 的 27 位被短接成 1 位：

```verilog
assign inv_q60[0] = \inv_q60[26] ;
assign inv_q60[1] = \inv_q60[26] ;
...（27 位全接到同一根 net）
```

`i_q60_reg[0..25]` 的 D 端全部接到 `n2`（由 `TIELBWP7T40P140 U3 ( .ZN(n2) )` 驱动，恒 0），
只有 `[1]` 接真实逻辑 `N70` —— 于是 **`i_q60` 恒为 2**。

### 11.2 对时序结论的影响（这是关键）

| | 退化网表 | 修复后网表 |
|:---|:---|:---|
| 全局最差 setup | **+16.801 ns**（周期 67%） | **+2.718 ns**（周期 10.9%） |
| 该路径 | `x3_q_reg[31] → code_o_reg[19]` | `u_recip/u0_r_reg[29] → u_recip/inv_q60_reg[0]` |
| 面积 | 90,303 µm² | **108,951 µm²** |
| cells | 142,921 | **165,374** |
| `u0_r_reg` 在网表 | 0 | **30** |
| `inv_q60_reg` 在网表 | 0 | **27** |

**余量凭空多出 14 ns。** 原因是 `recip_q60` 的 LUT 和整张查表逻辑都没被综合进去，
`recon` 的 41×27 乘法器一侧又被常量折掉。这条路径恰恰是整个设计最重的一段 ——
缺陷把它藏起来了。

**所以：任何在退化网表上得到的"余量充足"结论都不可信。**
本报告因此改用修复后网表重测，§2~§10 的全部数字都来自修复后网表。

### 11.3 修复与复核

`878a348`（10:50）把 LUT 从 `initial` 改成 `case` 语句；
`/tmp/dcwork2/`（11:02）是修复后的综合产物，`VER-281` = 0。
逐条复核命令：

```bash
grep -c "VER-281" /tmp/dcwork2/dc.log                     # 必须 0
grep -c "u0_r_reg" /tmp/dcwork2/adc_digital_ip_v21_syn.v  # 必须 > 0（实测 30）
grep -c "inv_q60_reg" /tmp/dcwork2/adc_digital_ip_v21_syn.v  # 必须 > 0（实测 27）
```

功能侧的独立证据见 [`docs/v21/LUT_SYNTH_BUG.md`](LUT_SYNTH_BUG.md)：
把 `recip_rnd_v21 + recip_q60` 单独抽出，用同一 testbench 跑 RTL 与网表，
RTL 与精确 `RND(2^60,G)` 12/12 全对，退化网表 12/12 全部输出 2。

### 11.4 这个缺陷没有改变三类预算的结论

§8.3 的逐条对照证明：除全局最差外，**20 条查询的 slack 变化都在 0.4 ns 以内**，
绝大多数为 0.000。原因是三类预算的路径落在 `ctx` / `evt` / `wsum` / `adc2` 上，
与 `i_q60` 无关。

所以：

- **§10 的三条瓶颈（+21.459 / +21.878 / +19.919）在修复前后都成立**，可用；
- **"全局最差 +16.801" 必须作废**，改为 **+2.718**；
- **面积必须作废**，改为 **108,951.11 µm²**。

---

## 12. 未覆盖项

| 项 | 说明 | 需要的下一步 |
|:---|:---|:---|
| 模拟域参考/RA/安静窗建立 | 不在数字网表内 | 模拟侧 Spectre |
| ADC2 宏内部时序 | 网表里 ADC2 是端口桩 | 宏的 .lib / 门级模型 |
| 后布线 STA | 无 P&R 结果 | P&R + SPEF + PT |
| hold 收敛 | 1275 条违例（理想时钟，最差 −0.072） | CTS + hold fix |
| 互连线延迟 | `ZeroWireload`，互连电容按 0 计 | 提取寄生后重跑 |
| 输入驱动 | 库无 `BUF_X2`，端口无 driving cell | 补 `BUFFD2` 或按实际上游给 `set_input_transition` |
| 40 MS/s 连续采样 | setup 为正只是必要条件 | 事务级延迟表 + 稳态吞吐分析 + 门级仿真实测 |
| 多 PVT 角 | 只做 SS 0.81 V −40 °C（setup 主用角） | FF/典型角、电压扫描 |
| 功耗 | 未报 | Power Compiler |
| 修复后网表的功能等价 | 本报告只做 STA，不做功能验证 | 见 `docs/v21/LUT_SYNTH_BUG.md` 的门级 e2e（已 PASS） |

---

## 13. 本轮更正一览

| # | 上一版的说法 | 本版结论 | 证据位置 |
|:--:|:---|:---|:---|
| 1 | `阶段可用时间 = 25 − slack` | 错。slack 是该路径相对周期的余量，不反推阶段时间 | 6.1；第 3~5 节全部改为列 arrival/required |
| 2 | `rdac_pre_state_o` 是 ② 的最紧项 | 错。pre 是 prepare 时的采样预置，与已决事件→post 无关 | 4.3 / 6.2 |
| 3 | `-through` 能"跨多级寄存器切阶段" | 错。`-through reg/Q` 与 `-from reg/Q` 起点相同，路径不穿寄存器 | 6.3（两次运行实测） |
| 4 | slack +9.39 / setup 全过 = 25 ns 连续采样 ✅ | 错。setup 为正只是必要条件；单笔 recon 延迟 7 拍 | 6.4 / 7 |
| 5 | 驱动单元 `BUF_X2` 生效 | 错。库无 `BUF_X2`，UID-993，端口无驱动；已量化影响并改用 `BUFFD2` 核实 | 6.5 |
| 6 | 面积 97,554.69 µm² / 151,346 cells | 复现不出。**修复后实测 108,951.11 µm² / 165,374 cells / 2,176 寄存器** | 9.2 |
| 7 | "本报告只做 setup" | hold 实测 1275 条违例，最差 −0.072 ns，已列出 | 7.4 |
| 8 | 未提网表的 `recip_q60` 曾退化 | 综合丢弃 `initial` 的 LUT，`i_q60` 恒为 2，使全局最差虚高到 +16.801；修复后为 **+2.718**、面积 **108,951 µm²** | 8.3 / 11 |
| 9 | 未提 `ZeroWireload` 的含义 | 互连电容按 0 计，所有 slack 都是零互连乐观值 | 6.5 |
| 10 | 路径起点未区分复位/功能 | `rst_n` 起点的"最差路径"不是功能瓶颈，已改为 reg→reg 与端口起点分列 | 3.1 / 4.1 |
