# 时序报告解释修正 · 交接说明

时间：2026-11-16
仓库：`/Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification`（分支 `merge/adi-repro`）
远端：`vm-yian`（`/opt/synopsys/syn/W-2024.09-SP3`）
**RTL 一行未改**；只改了 STA 脚本 + 重写了报告 + 新增本说明。

> 提交状态：`sta_budget.tcl`(v6.0) 与 `TIMING_BUDGET_FINAL.md` 已被
> `a237626` 一并提交；本文件（`TIMING_BUDGET_HANDOFF.md`）是新增、**未提交**。

---

> 本文中所有 `§x.y` 均指
> [`docs/v21/TIMING_BUDGET_FINAL.md`](TIMING_BUDGET_FINAL.md) 的小节。

## 1. 交付物

| # | 文件 | 状态 |
|:--:|:---|:---|
| 1 | [`flow/synth/v21/sta_budget.tcl`](../../../flow/synth/v21/sta_budget.tcl) | 重写（v3.0 → v6.0），26 条查询，逐条输出完整体 |
| 2 | [`docs/v21/TIMING_BUDGET_FINAL.md`](../../../docs/v21/TIMING_BUDGET_FINAL.md) | 重写（13 节） |
| 3 | 本文件 | 说明实测结果、纠正点、STA 答不了的部分 |

---

## 2. 实测到了什么

### 2.1 三条真实瓶颈（都在 25 ns 内）

全部在**修复后的网表** `/tmp/dcwork2/adc_digital_ip_v21_syn.v`（md5 `361b21e9…`）上取得：

| 预算 | 瓶颈查询 | 起点 → 终点 | arrival | required | slack |
|:---|:---|:---|---:|---:|---:|
| ① 释放→采集 | `B1C_ACQ_IDS_REG2REG` | `u_sched/acq_q_reg[0]/CP` → `u_ctx/acq_ids_q_reg[0]/D` | 3.211 | 24.670 | **+21.459** |
| ② 已决事件→post | `B2B_POST_D_REG2REG` | `u_evt/exp_evt_q_reg[2]/CP` → `post_state_q_reg[356]/D` | 2.839 | 24.716 | **+21.878** |
| ③ 末次→arm（数字侧最深） | `B3F_FINAL_POST_REG2REG` | `final_post_q_reg[113]/CP` → `u_wsum/Wm_q30_reg[47]/D` | 4.765 | 24.683 | **+19.919** |
| （三类之外）全局最差 | `GLOBAL_REG2REG` | `u_recon/u_recip_rnd/u_recip/u0_r_reg[29]/CP` → `u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]/D` | 22.005 | 24.724 | **+2.718** |

**三类预算都不是瓶颈，`recip_q60` 的 Newton 迭代才是（只剩 2.718 ns / 10.9% 余量）。**

> **重要更正**：本文件早前版本报的全局最差 **+16.801 ns** 出自**退化网表**
> （`recip_q60` 的 LUT 被综合丢弃，`i_q60` 恒为 2）。修复后同一条设计的最差路径
> 变成 **+2.718 ns**，余量缩小 14 ns。见下方 §2.6。

### 2.2 驱动单元（审查第 5 点）

| 问题 | 实测答案 |
|:---|:---|
| `set_driving_cell -lib_cell BUF_X2` 生效了吗 | **没有**。本库无 `BUF_X2`（只有 `BUFFD0..20` / `BUFTD*`），报 `UID-993` |
| 为什么一直没发现 | 这条 Error **不抛 Tcl 异常**，`catch` 走成功分支。必须回读 `report_port -verbose` 的 "Driving Cell" 栏 |
| 回读结果 | 不设驱动 → 该栏空；设 `BUFFD2BWP7T40P140` → 出现 `BUFFD2BWP7T40P140/Z` |
| 影响量化（同端口 `ev_bit_valid_i`） | 无驱动 slack +22.495（arrival 1.204）vs 有驱动 +22.489（arrival 1.211），差 **0.006 ns** |
| 能否推广 | **不能**。该路径只有 2 级逻辑；扇出大的端口会更敏感。所有端口起点 slack 应视为**乐观上界** |
| 有没有端口既无驱动也无 input delay | **没有**。`set_input_delay 1.0` 覆盖全部 14654 个非 clk 端口，逐条可验证 |

### 2.3 wire load

| 问题 | 实测答案 |
|:---|:---|
| 用的什么模型 | `ZeroWireload`（库中唯一可选，`report_lib` 显示 Fanout/Length/Points/Average Cap 全 0） |
| 日志里 "Changed ... from '(none)' to 'ZeroWireload'" 是什么 | DC 给子模块补库默认模型的过程信息（`OPT-170`），**不是错误**，也不是数字变化的原因 |
| 乐观还是悲观 | **乐观**。互连电容/电阻按 0 计；实际布线后会额外增加 RC 延迟 |
| 旁证 | `area.rpt` 的 `Net Interconnect area: undefined (Wire load has zero net area)` |

### 2.4 面积（两份网表快照各自复算）

| 指标 | **修复后 `361b21e9`（采用）** | 退化 `db803f8c` / `0572abc4`（作废） |
|:---|---:|---:|
| Total cell area | **108,951.11 µm²** | 90,287.30 / 90,303.28 µm² |
| cells | **165,374** | 142,900 / 142,921 |
| 时序单元 | **2,176** | 2,088 |
| 未时钟寄存器 | 0 | 0 |

- `97,554.69 / 151,346 / 2,116`（`eebbc2d` 那轮）在留存文件里**都搜不到**，标记为"现有证据不足"；
- `90,287 / 90,303 µm²` 可复现，但出自**退化网表**，同样不可用；
- **本报告以修复后的 108,951.11 µm² / 165,374 cells / 2,176 寄存器为准。**

与原型 60,000 µm² 的对比保留原文：**口径不同（版图区域 vs 标准单元面积），
不能解读成"差 1.5 倍"**。

### 2.5 hold（上一版漏报）

| 检查 | 违例 | 最差 |
|:---|---:|---:|
| setup | **0** | — |
| hold | **1275** | **−0.072 ns** @ `u_recon/err1a_q_reg/CP → u_recon/err1_q_reg/D` |

综合网表 + 理想时钟（零 skew）下的 hold 未收敛，需 CTS 后重跑。

### 2.6 网表功能缺陷（决定性发现）

`recip_q60` 的 64 项 LUT 用 `initial` 初始化，DC 直接丢弃（`VER-281`）：

| | 退化网表 | 修复后网表 |
|:---|:---|:---|
| `VER-281` | 1 | **0** |
| `u0_r_reg` 在网表 | 0 | **30** |
| `inv_q60_reg` 在网表 | 0 | **27** |
| `i_q60` 的功能值 | 恒 **2** | 随 G 变化 |
| 全局最差 setup | +16.801 | **+2.718** |
| 面积 | 90,303 µm² | **108,951 µm²** |

**影响**：LUT + 整张查表逻辑没进网表，`recon` 的乘法器一侧又被常量折掉，
于是最重的 Newton 迭代段被"藏起来"，余量凭空多出 14 ns。
已由 `878a348` 修复，`/tmp/dcwork2` 是修复后产物。

**这个缺陷没有改变三类预算的结论**：逐条对照（报告 §8.3）显示，
除全局最差外 20 条查询的 slack 变化都在 0.4 ns 以内、绝大多数为 0.000，
因为三类预算的路径落在 `ctx`/`evt`/`wsum`/`adc2` 上，与 `i_q60` 无关。

---

## 3. 纠正了哪几点

### 3.1 审查指出的五点

| # | 上一版说法 | 本版结论 | 证据 |
|:--:|:---|:---|:---|
| 1 | `阶段可用时间 = 25 − slack` | **错**。slack 是 `required − arrival`，required 已含 uncertainty/setup/外部延迟；且一条路径只覆盖阶段的一小段。反证：`B1A`(+23.194) 与 `B1C`(+21.459) 是同一阶段的两段，按该公式得 1.81 / 3.54 ns 两个答案 | 报告 §6.1 |
| 2 | `rdac_pre_state_o` 是 ② 的最紧项 | **错**。pre 是 **prepare 时**由 `dither_pre` 给出的采样预置（`pre_state_c` 组合驱动），post 是逐事件的已决结果（`post_state_q` 寄存）。二者时机、数据源、RTL 驱动都不同 | 报告 [§4.3](TIMING_BUDGET_FINAL.md#43-采样预置pre通路--与-post-无关单独列) / §6.2 |
| 3 | `-through` 能"跨多级寄存器切阶段" | **错**。实测 `-through *conv_*_q_reg*/Q -to *acq_ids_q_reg*/D` 与 `-from` **同一组引脚**，起点都是 `u_ctx/conv_valid_q_reg/CP`、slack 都是 +23.308。路径从寄存器出发，不穿过它 | 报告 §6.3（脚本第 3 节自动生成对照表） |
| 4 | slack +9.39 = 25 ns 连续采样 ✅ | **错**。setup 为正只是必要条件。RTL 里 `recon` 单笔 7 拍（=175 ns），整笔交易 ≥20 拍。能跑 40 MS/s 靠 18 片三态 + 流水重叠，STA 完全没查这件事 | 报告 §6.4 / §7 |
| 5 | 驱动单元 `BUF_X2` 生效 | **错**。见 2.2 | 报告 §6.5 |

### 3.2 取证中另发现的四点

| # | 上一版说法 | 本版结论 | 证据 |
|:--:|:---|:---|:---|
| 6 | 面积 97,554.69 / 151,346 | 复现不出，实测 90,287 / 90,303 µm²、142,900 / 142,921 cells | 报告 §9.2 |
| 7 | "本报告只做 setup" | hold 1279 条违例，最差 −0.072 ns | 报告 §7.4 |
| 8 | 未提网表功能缺陷 | `recip_q60` 的 `initial` LUT 被综合丢弃，`i_q60` 恒为 2，**时序数字偏乐观** | 报告 §11 |
| 9 | 路径起点未区分复位/功能 | `rst_n` 起点的"最差路径"不是功能瓶颈，已改为 reg→reg 与端口起点分列 | 报告 [§3.1](TIMING_BUDGET_FINAL.md#31-单段组合路径) / [§4.1](TIMING_BUDGET_FINAL.md#41-post-通路的两个单段) |
| 10 | （本文件早前版本）全局最差 +16.801 | **出自退化网表**。修复后为 **+2.718**，余量缩小 14 ns | 报告 §8.1 / §8.3 / §11 |
| 11 | （本文件早前版本）面积 90,287/90,303 µm² | **出自退化网表**。修复后为 **108,951.11 µm² / 165,374 cells** | 报告 §9 |

---

## 4. 还有什么无法用 STA 回答

| 项 | 为什么 | 需要什么 |
|:---|:---|:---|
| **能否 40 MS/s 连续采样** | STA 只查单拍组合段，不查跨拍序列与吞吐 | 事务级延迟表（逐拍）、稳态吞吐分析、背靠背 40 MS/s 仿真、门级实测（带 SDF） |
| **① 释放→采集的整段延迟** | 跨 3~4 拍（含 2 拍安静窗） | 同上；STA 只能给每拍内部那一段 |
| **② 事件→开关真正翻转** | 模拟域 RDAC 建立 | 模拟侧 Spectre |
| **③ 参考/RA/安静窗/宏 hold** | 全部模拟域 | 模拟侧给数；数字侧只证明了判定逻辑够快 |
| **布线后是否仍成立** | `ZeroWireload` + 无驱动 | P&R + SPEF + PrimeTime |
| **hold 是否收敛** | 理想时钟，CTS 前无意义 | CTS + hold fix 后重跑 |
| ~~修复 LUT 后时序是否仍满足~~ | **已做**：修复后网表实测全局最差 **+2.718 ns**（仍满足 25 ns） | — |
| 多 PVT / 功耗 | 只做 SS 0.81V −40°C setup | 其余角、Power Compiler |

---

## 5. 复跑

**复跑必须显式指定网表**（`/tmp/dcwork` 会被后续综合覆盖，本次取证期间就被覆盖过两次）：

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
ls  /tmp/sta_budget_out_fixed/paths/                          # 26 条 report_timing 全文
```

已实测：同一网表连续两次运行，26 条 TAG 行**逐字节相同**。

换网表对照：

```bash
ssh vm-yian 'STA_NETLIST=/path/to/other_syn.v STA_OUTDIR=/tmp/out2 \
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost LM_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/sta_budget.tcl > /tmp/budget2.log 2>&1'
```

---

## 6. 原始日志路径（全部保留）

| 文件 | 内容 |
|:---|:---|
| **`vm-yian:/tmp/budget_fixed.log`** | **修复后网表运行日志（正文数字出处）** |
| **`vm-yian:/tmp/sta_budget_out_fixed/timing_budget_audit.md`** | **修复后网表中文完整体** |
| **`vm-yian:/tmp/sta_budget_out_fixed/paths/*.rpt`** | **26 条 `full_clock_expanded` 完整体** |
| `vm-yian:/tmp/sta_audit/adc_digital_ip_v21_syn_fixed.v` | 修复后网表快照（md5 `361b21e9…`） |
| `vm-yian:/tmp/sta_audit/area_fixed.rpt` | 修复后 `report_area`（108,951.11 µm²） |
| `vm-yian:/tmp/sta_audit/lib.rpt` | `report_lib` 全文（`ZeroWireload` 定义、Selection Group） |
| `vm-yian:/tmp/budget_{head,baseline}.log` | 两份**退化网表**的对照运行（仅用于作废说明） |
| `vm-yian:/tmp/sta_budget_out_{head,baseline}/` | 退化网表的完整体与 26 条 paths |
| `vm-yian:/tmp/sta_audit/adc_digital_ip_v21_syn.v` | 退化网表 `db803f8c…` |
| `vm-yian:/tmp/sta_audit/adc_digital_ip_v21_syn_head.v` | 退化网表 `0572abc4…` |
| `vm-yian:/tmp/sta_audit/area_{baseline,head}.rpt` | 退化网表的 `report_area` |
| `vm-yian:/tmp/sta_audit/constraint_{max,min}.rpt` | setup / hold 违例原文 |
| `vm-yian:/tmp/gate2/old_baseline/` | 09:34 基线综合产物原样备份（网表 + area + dc.log + svf + RTL 备份） |
| `vm-yian:/tmp/dcwork2/` | 修复后综合产物原目录（网表 + area + dc.log + sta + viol） |

---

## 7. 需要注意的三件事

1. **网表退化（已处理）**：取证途中发现 `recip_q60` 的 LUT 被综合丢弃，
   早前测得的 slack（+16.801）与面积（90,303 µm²）都偏乐观。
   `878a348` 已修，本报告已用**修复后网表重测**：
   全局最差 **+2.718 ns**、面积 **108,951.11 µm²**。
   三类预算的数字不受影响（逐条对照见报告 §8.3）。详见报告 §8 / §11。

   **任何人再引用本工程的 slack/面积，请先确认网表完整性**：
   `grep -c "VER-281" dc.log` = 0、`grep -c "u0_r_reg" netlist.v` > 0。

2. **`/tmp/dcwork` 会被覆盖**：取证期间发生过两次（09:34 → 10:17 → 11:02）。
   修复后的产物在 `/tmp/dcwork2`，也已备份到 `/tmp/sta_audit/adc_digital_ip_v21_syn_fixed.v`。
   脚本支持 `STA_NETLIST` / `STA_OUTDIR`，**务必显式指定快照路径**复跑。

3. **dc_shell stdout 不能输出中文**：本机 locale 下 `puts "中文"` 会变成 `?`
   （写入文件则是正常 UTF-8）。所以脚本把中文叙述写进
   `timing_budget_audit.md`，stdout 只留 ASCII 的 `TAG/SECTION/VIOL/DRV` 行供 grep。
