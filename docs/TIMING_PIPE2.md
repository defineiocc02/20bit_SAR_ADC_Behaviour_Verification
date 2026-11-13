# recon_d5_pipe2：D5 权重路径三级流水的功能与时序实测

> 结论先说：**没有进 20%，而且是差 16 倍，不是差一点。**
> 卡在任务书指定的 S1 —— `D_eff = 6·Wm/G − 3` 的除法。
> 25 ns 的可用预算是 22.7 ns，S1 实测 **421.20 ns**。

本文所有数字都来自 vm-meow 上的实跑（VCS W-2024.09-SP1 / DC W-2024.09-SP3，
SMIC180 `slow.db`），没有估算、没有外推。

---

## 1. 电路

### 1.1 端口：与任务书有一处必须说明的偏离

任务书写「S1: `D_eff = 6·Wm/G − 3`（除法在此）」，同时要求「端口与 `recon_d5_pipe` 相同
（`k_i`）」。这两条互斥：

| 模块 | 输入 | 里面有没有除法 |
|:---|:---|:---|
| `recon_d5_pipe` | `k_i[8:0]` | **没有**。`D_q24 = −3·2^24 + 196608·k_i`，是已经除完的线性 DAC |
| `recon_d5_w` | `wm_q30[43:0]`, `g_q30[43:0]` | **有**。`D_eff = (6·Wm − 3·G)·2^24 / G` |
| `recon_d5_pipe2`（新） | `wm_q30`, `g_q30` | **有** |

`k_i` 里根本没有 W / G，没法在 S1 做 `6·Wm/G`。任务书自己的验证要求也指向同一结论
（「该向量是给 `recon_d5_w` 的（输入 wm/g），你需要自己生成 wm/g 向量」）。
因此 `recon_d5_pipe2` 的端口取 **`recon_d5_w` 的算术端口 + 时钟**：

```systemverilog
module recon_d5_pipe2 (
    input  logic        clk, reset_n,
    input  logic [43:0] wm_q30, g_q30,
    input  logic [11:0] adc2_i,
    input  logic [5:0]  dither_i,
    output logic [19:0] code_o,
    output logic        overflow_o
);
```

如果把 `k_i` 那条要求当准，那这个模块就退化成 `recon_d5_pipe`（只有 2 段有效逻辑），
没有「S1 除法」可言。两条路选一条，我选了能同时满足「S1 做除法」和「向量用 wm/g」的那条。

### 1.2 级划分

| 级 | 内容 | 打入寄存器 |
|:---|:---|:---|
| S1 | `num = (6·Wm − 3·G)·2^24`，`den = G`，`D_eff = (±(num_a + den/2))/den`（半数远离零）；同时把 `adc2_i`/`dither_i` 打一拍 | `d_q24[27:0]`、`adc2_s1[11:0]`、`dither_s1[5:0]` |
| S2 | `r̂_q24 = ((2a+1)·144+2)·838861 >> 22`，`T_q24 = t_num·24576`，`x = D_eff + r̂ + T_q24` | `x_q24[26:0]` |
| S3 | `q = round(x/96)`（`|x|·44739243 + 2^31 >> 32`，负数对称）→ `+2^19` → 饱和到 `[0, 2^20−1]`，同时出 `overflow_o` | `code_o`、`overflow_o` |

位宽（按实际范围收窄，不是拍脑袋 64 位）：

| 量 | 上界 | 位宽 |
|:---|---:|:---|
| `6·Wm − 3·G` | `3G < 2^38`（`G = 8·16·w`, `w < 2^31`） | 64（含 ×2^24 后的 2^62） |
| `D_eff` | `3·2^24 = 5.0e7` | 28 有符号 |
| `r̂` | 235,901 | 19 有符号 |
| `T_q24` | `63·24576 = 1,548,288` | 22 有符号 |
| `x_q24` | `5.2e7 < 2^26` | 27 有符号 |

`r̂` 用的是**截断**的倒数乘（`>>22`，无 `+2^21`），与 `run_transaction` 的
`((2a+1)·144+2)//5` 逐位一致；`recon_d5_pipe` 那里多加了 `+2^21` 做二次舍入，
在 398 组向量里有 3 组会因此偏 1 个 LSB（0.2% 量级，但确实是偏）。
本版对齐 Python，因此三路（本文 RTL / `recon_d5_w` / Python golden）全 0 失配。

---

## 2. 功能验证（VCS）

### 2.1 向量怎么来的

`tb/vec_recon_pipe2.txt`，398 组，由 `run_transaction` 直接生成：

- 权重：18 片 × 20 单元，Q30 名义值 `0.25·2^30 = 268435456`，每项 ±0.6% 随机扰动
- `g_q30 = Σ w`（仅 16 个采样主列，8 片）
- `wm_q30 = Σ w·m`（主 + 子阵列全部 20 列）
- 已核对：`wm/g` 代进 `recon_d5_pipe2` 的 `D_eff`，与 `run_transaction` 内部的
  `dac_q24 = _div_round(-K_q54, G_q30)` **逐位相同**（推导：`−K = 3·2^24·(2Wm−G)`）
- golden `code20` 取 `run_transaction(...)['code20']`，`slice_ids` 用 DUT 返回的实际值
- 覆盖：`k`/`adc2` 全 0 与全 1 边界、`code20` 两端饱和（0 与 1048575）、25 组溢出

### 2.2 结果（实跑输出）

```
RESULT: 398 vectors, 0 golden mismatches, 0 recon_d5_w mismatches, 25 overflow cases
PASS
```

tb 同时实例化 `recon_d5_pipe2` 与 `recon_d5_w`，喂同一组 `wm/g/adc2/dither`：

- 与 **Python golden** 比 → 0 失配（主判据）
- 与 **`recon_d5_w`** 比 → 0 失配（证明多出来的一级只改延迟、没改数值）
- 延迟确实是 3 拍（tb 在第 3 个 negedge 采样，如果是 4 拍会 100% 失配）

复现：

```bash
ssh vm-meow 'cd /root/timing/vcs && cp ../tb/vec_recon_pipe2.txt . && \
  export SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 && \
  $VCS_HOME/bin/vcs -full64 -sverilog -timescale=1ns/1ps -o simv \
    ../rtl/recon_d5_pipe2.sv ../rtl/recon_d5_w.sv ../tb/tb_recon_pipe2.sv && ./simv'
```

---

## 3. 时序（DC，SMIC180 slow，25 ns）

### 3.1 约束（与 `recon_d5_pipe` 那一轮完全一致）

```tcl
create_clock -name clk -period 25 [get_ports clk]
set_clock_uncertainty -setup 0.3 [get_clocks clk]
set_clock_uncertainty -hold  0.1 [get_clocks clk]
set_input_delay  -max 2.0 -min 0.5 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay -max 2.0 -min 0.5 -clock clk [all_outputs]
```

**注意**：这台机的 dc_shell（W-2024.09-SP3）不接受上面那种 `-max ... -min ...` 写在同一行，
会报 `CMD-012` 并且**静默丢掉整条约束**（详见第 8 节）。实际生效的写法是拆成两行：

```tcl
set_input_delay  -max 2.0 -clock clk $ins
set_input_delay  -min 0.5 -clock clk -add_delay $ins
set_output_delay -max 2.0 -clock clk [all_outputs]
set_output_delay -min 0.5 -clock clk -add_delay [all_outputs]
```

语义与任务书那四行等价。综合命令 `compile`（见 3.5 的说明）。

### 3.2 全设计结果

| 项 | 值 |
|:---|:---|
| **Setup slack** | **−396.64 ns（VIOLATED）** |
| Setup 关键路径 | `g_q30[0]` → `d_q24_reg[26]`（**S1 的除法**） |
| Setup arrival / required | 421.20 / 24.56 ns |
| **Hold slack** | **+0.42 ns（MET）** |
| Hold 关键路径 | `adc2_i[4]` → `adc2_s1_reg[4]`（输入直进 S1 寄存器） |
| 面积 | **377,290 µm²** / 11,707 cells（其中时序单元 72） |

### 3.3 逐级拆开看

| 级 | startpoint → endpoint | arrival | required | slack | 判定 |
|:---|:---|---:|---:|---:|:---|
| S1 除法 | `g_q30[0]` → `d_q24_reg[26]` | 421.20 | 24.56 | **−396.64** | 差 16.5 倍 |
| S2 `r̂+TΔ+` | `adc2_s1_reg[1]` → `x_q24_reg[26]` | 18.54 | 24.46 | **+5.91** | **23.6%，过了 20%** |
| S3 `÷96+饱和` | `x_q24_reg[26]` → `overflow_o` | 23.13 | 22.70 | **−0.43** | 差 0.43 ns |

S2 单独看是达标的（+5.91 ns > 5.00 ns）。**S1 和 S3 都不达标**，S1 是灾难。

### 3.4 S1 为什么是 421 ns

DC 把 `num/den`（64 位 / 45 位）映射成 `DW_div_tc_a_width64_b_width45`（含余数）。
阵列除法在 SMIC180 slow 上是 O(n²) 展开：关键路径要穿过约 64 行商选择，
在未优化状态下实测 810 ns，`compile` 优化后降到 421 ns —— 仍是一个数量级的差距。
这不是约束写错、也不是综合没尽力：

- `set_input_delay` 已经生效（报告里明确有 `input external delay 2.00`）
- 路的起点是 `g_q30`、终点是 `d_q24_reg`，中间是 `DW02_mult`（×6 / ×2^24 都是移位/加法）
  加 `DW01_absval` + `DW01_inc` + `DW_div_tc`
- 用 `compile_ultra` 又跑了一轮，90 分钟超时被 kill 时 TNS 还在 −397 ns 附近缓慢爬（30 分钟时 −377 ns），
  没有任何在 25 ns 内收敛的迹象

### 3.5 关于 `compile` vs `compile_ultra`

基线 `recon_d5_pipe` 的 +0.04 ns 是 `compile_ultra`。本轮先用 `compile_ultra` 跑
`recon_d5_pipe2`，90 分钟未收敛（DC 在死磕那条 421 ns 的除法路径），被 timeout 杀掉；
于是改用 `compile`（同样约束、同样库）取数，36 分钟完成。
对 S1 的结论没有影响（400 ns 级别的差距不是综合努力能补的），但 S3 的 −0.43 ns
在 `compile_ultra` 下可能略有出入 —— 这一点如实标注，不拿它当「达标」。

---

## 4. 面积

| 版本 | cells | 面积 |
|:---|---:|---:|
| `recon_d5_pipe`（2 级，`k_i`，无除法） | 1,485 | 50,175 µm² |
| `recon_d5_pipe2`（3 级，`wm/g`，含除法） | 11,707 | **377,290 µm²** |

7.5 倍。多出来的 32 万 µm² 里绝大部分是那个 64/45 除法器（组合面积 372,214 µm²，
时序单元只占 5,076 µm²）。换句话说：这一版不是一个「多一级寄存器」的代价，
而是把 `recon_d5_w` 的除法搬进了流水线，代价直接是 7.5 倍面积。

---

## 5. 与 +0.04 ns 的对比

| 指标 | `recon_d5_pipe` | `recon_d5_pipe2` | 目标 |
|:---|---:|---:|---:|
| 级数 | 2 | 3 | 3 |
| 输入 | `k_i`（无除法） | `wm/g`（含除法） | — |
| Setup slack @25 ns | +0.04 ns（0.16%） | **−396.64 ns** | ≥ +5.00 ns（20%） |
| Hold slack | +0.58 ns | +0.42 ns | > 0 |
| 面积 | 50,175 µm² | 377,290 µm² | — |

**没有变好，变差了 4 个数量级。** 原因不在「拆得够不够细」，在两件事：

### 5.1 任务书的级划分拆错了地方

`recon_d5_pipe` 的两级其实是：

| `recon_d5_pipe` | 内容 | 实测 slack |
|:---|:---|---:|
| S1 | `D(k) + r̂ + TΔ` → 寄存 `x_q24` | +0.55 ns |
| S2 | `÷96` + 饱和 → `code_o` | **+0.04 ns** ← 关键路径 |

任务书要求的「S2 = `r̂` 与 `TΔ` 相加」，在 2 级版里**本来就属于 S1**。
按新划分把它挪到 S2 后，`recon_d5_pipe2` 的 S3 恰好等于原 S2 —— 那条 22.66 ns 的
关键路径**原封不动地保留下来了**，所以 slack 不可能改善。实测 S3 = 23.13 ns / −0.43 ns，
与 +0.04 ns 是同一水平（差异来自 `compile` 与 `compile_ultra` 的努力程度）。

要动就得动 S3 本身：≥ 27×26 乘法器（约 14 ns）+ 54 位加 + 21 位比较 + 20 位饱和 mux。
在 25 ns 里给它留 5 ns 余量，必须把乘法本身拆开（部分积寄存），那就是第 4 级。

### 5.2 S1 的除法根本放不进一级

`recon_d5_pipe` 之所以能有 0.55 ns 的 S1 余量，是因为它的 S1 里**没有除法** ——
除法被 `k_i` 这个接口移到了模块外。`recon_d5_w` 的除法单独一级就是 421 ns。
想要 25 ns 一级，只有三条路：

1. 除法拆成 ~16 级以上（那就不是「三级流水」）
2. 用倒数乘替代（只有在 G 视作常数时才成立，`G` 每笔事务差 ±0.5%，会丢逐位一致性）
3. 保持 `recon_d5_pipe` 的做法：除法在模块外、由上游/标定路径消化

**本次没有为了让数字好看做任何取舍**：约束没放宽（`input_delay 2.0` 明确生效）、
逻辑没删（除法、饱和、overflow 全在），失败就是失败。

---

## 6. 交付物

| 文件 | 状态 |
|:---|:---|
| `rtl/recon_d5_pipe2.sv` | 新增 |
| `tb/tb_recon_pipe2.sv` | 新增 |
| `tb/vec_recon_pipe2.txt` | 新增（398 组） |
| `docs/TIMING_PIPE2.md` | 新增（本文） |

没有修改任何既有文件，没有 `git commit`。

远端工作目录：`vm-meow:/root/timing/{rtl,tb,vcs,dc,dc2}`，
DC 报告在 `/root/timing/dc2/{setup,hold,stage1,stage2,stage3,area}.rpt`（`compile` 那轮）。

---

## 7. 复现

```bash
# 1) 功能
ssh vm-meow 'cd /root/timing/vcs && ./simv'          # RESULT: 398 vectors, 0/0 mismatches

# 2) 时序（compile 基线，36 min）
ssh vm-meow 'cd /root/timing/dc2 && export SNPSLMD_LICENSE_FILE=27080@localhost && \
  /opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell -no_gui -f pipe2_fast.tcl'

# 3) 时序（compile_ultra，>90 min 未收敛）
ssh vm-meow 'cd /root/timing/dc && export SNPSLMD_LICENSE_FILE=27080@localhost && \
  /opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell -no_gui -f pipe2_final.tcl'
```

---

## 8. 工具坑（会直接毁掉结论，建议进 README）

1. **`set_input_delay -max 2.0 -min 0.5` 写在一行 → 整条约束被丢弃。**
   W-2024.09-SP3 报 `Error: extra positional option '_selN' (CMD-012)`，
   Tcl `catch` 抓到的消息是空串，dc_shell 也不退出。
   症状：只有 reg→reg 路径被约束，`report_timing` 看起来很漂亮
   （我第一次跑就得到 +6.13 ns 的假结果，实际关键路径 421 ns 根本没进报告）。
   `read_sdc` 走 SDC 解析器也一样会丢。
   → 拆成 `-max` 一行 + `-min -add_delay` 一行，并**用 `check_timing` 和报告里的
   `input external delay` 行确认生效**。

2. **SDC 文件里不能用 `remove_from_collection`**（`Error: unknown command`），
   等价写法：`set_input_delay ... [all_inputs]`（对 `clk` 端口设置会被忽略）。

3. **`report_timing -to [get_pins d_q24_reg[0]/D]` 会在 Tcl 里先做命令替换**
   （`[0]` 被当成命令），报 `Value for list '-to' must have 1 elements`。
   → 模式一律加花括号：`{d_q24_reg[0]/D}`。
