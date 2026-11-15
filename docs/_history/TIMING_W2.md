# recon_d5_w2：预计算倒数替换除法，D5 权重路径进 25 ns

> **结论：进了。** S1 从 pipe2 的 **421.20 ns（−396.64）** 降到 **24.46 ns（+0.002）**，
> 全设计 setup **MET**、hold **+0.42 ns MET**，面积 **166,799 µm²**（pipe2 是 377,290 µm²）。
>
> 但 +0.002 ns 这个数**不能当成「余量 2 ps」**读 —— `compile` 一旦满足约束就停手，
> 报出来的 arrival 只是「刚好够用」的那一版网表。用更紧的周期回推真实能力（第 5 节）：
> **S1 真实延时约 15.3 ns**（不含 2 ns 输入延时），25 ns 下有 **约 7 ns 实际余量**。

数字全部来自 vm-meow 实跑：VCS W-2024.09-SP1、DC W-2024.09-SP3、SMIC180 `slow.db`。

---

## 1. 改了什么

只改一处：S1 的除法。

| | pipe2 | **w2** |
|:---|:---|:---|
| D_eff | `(6Wm−3G)·2^24 / G`（64÷45 阵列除法） | `((6Wm−3G)·inv_g_q60) >> 36` |
| 除数 | 运行时从 `g_q30` 除 | `inv_g_q60 = round(2^60/G)`，标定后存寄存器 |
| 综合出的算子 | `DW_div_tc_a_width64_b_width45` | `DW02_mult_42x27`（编译后 39×27） |

`r̂`、`TΔ`、`÷96` 倒数乘、20 位饱和与 `recon_d5_w.sv` 逐字相同，只是按级切开进寄存器。

**端口**：`clk / reset_n / wm_q30[43:0] / g_q30[43:0] / inv_g_q60[63:0] / adc2_i[11:0] / dither_i[5:0] → code_o[19:0] / overflow_o`。
比派单里列的端口多了 `clk`/`reset_n` —— 因为是三级流水，而且「S1/S2/S3 各级 slack」
必须有级间寄存器才报得出来。若确实要一个纯组合版，把三个 `always_ff` 去掉即可，
算术完全一样（S1+S2+S3 串起来就是 `recon_d5_w` 的时序形状，只是延时相加）。

级划分沿用 pipe2：S1 = `6Wm−3G` 与 ×inv；S2 = `r̂+TΔ`；S3 = `÷96`+饱和。

`6·Wm − 3·G` 写成移位加法（`4Wm+2Wm−2G−G`）而不是 `*6 - *3`：DC 会把常数乘法
各推一个乘法器（第一版 S1 里确实有 `DW02_mult_A_width42_B_width4` / `_width3`，
这两段占了约 0.9 ns）。改完面积 179,672 → **166,799 µm²**（−210 cells），S1 延时基本不变
（24.45 → 24.46），所以这个改动只是省面积，不是省时间。

---

## 2. 功能：PASS

`tb/tb_recon_w2.sv` + `tb/vec_recon_w2.txt`（400 组，`tools/gen_vec_recon_w2.py` 生成）：

```
RESULT: 400 vectors, 0 mismatches, 30 overflow cases
REF   : vs recon_d5_w exact-division: 1 differ, max |delta| = 1 LSB20
PASS
```

- 期望值**用同一套倒数公式**（`((6Wm−3G)*inv)>>36`）算，不是精确除法 —— 否则测的是
  「RTL 与精确除法的差」，不是「RTL 有没有实现设计要求」。
- `wm/g` 不是硬编码：每笔事务先跑 `run_transaction`，取它**实际**分配的
  `slice_ids` 与 `masks` 再累加（`wm = Σw·m` 主+子，`g = Σw` 仅 16 个采样主列）。
  上一轮 361/400 失配就是硬编码片号造成的。
- 覆盖：512 档粗码全扫、两端饱和（0 / 1048575）、30 组溢出、±3% / ±1% / 理想三套权重。

### 与精确除法的偏差（诚实口径）

| 量 | 值 |
|:---|:---|
| D_eff 最大偏差 | **2 个 Q24 单位**（= 0.12 µV = 0.02 LSB20） |
| 非零偏差组数 | 131 / 400（都是 ±1~2 单位，`>>36` 是 floor，负数侧系统性偏 1） |
| 输出码不同的组数 | **1 / 400**，差值 1 LSB20 |

也就是说：倒数方案在 400 组里改了 1 个码的 1 个 LSB。要「与 `run_transaction` 逐位一致」
就得回到精确除法（即回到 421 ns），这是个必须挑明的取舍。

偏差随 `G` 变大而变大（`inv = round(2^60/G)` 的相对精度是 `0.5·G/2^60`）：
本仓库 `G ≈ 3.4e10` → 偏差 ≤2 单位；若标定后 `G` 涨到 5.5e11，偏差会到 13 单位
（仍是 0.14 LSB20，但比 2 单位大一个量级）。

---

## 3. 时序（25 ns，`compile`）

| 级 | startpoint → endpoint | arrival | required | slack |
|:---|:---|---:|---:|---:|
| **S1** `×inv` | `wm_q30[1]` → `d_q24_r_reg[27]/D` | **24.46** | 24.46 | **+0.002** |
| S2 `r̂+TΔ` | `adc2_s1_reg[1]` → `x_q24_reg[27]/D` | 18.88 | 24.35 | **+5.46** |
| S3 `÷96`+饱和 | `x_q24_reg[27]/CK` → `code_o[18]` | 19.98 | 22.70 | **+2.72** |

全设计：**setup +0.002 ns（MET）**／**hold +0.416 ns（MET）**。

对比 pipe2：

| | pipe2 | **w2** | 变化 |
|:---|---:|---:|:---|
| S1 slack | −396.64 | **+0.002** | 除法没了 |
| S2 slack | +5.91 | +5.46 | −0.45（x 加宽到 28 位） |
| S3 slack | −0.43 | **+2.72** | +3.15（DC 换了映射，见下） |
| setup（全设计） | −396.64 | **+0.002** | MET |
| 面积 | 377,290 µm² | **166,799 µm²** | **−56%** |
| cells | 11,707 | **4,940** | −58% |

S3 从 −0.43 变 +2.72 有点反直觉（`x_q24` 从 27 位加宽到 28 位，乘法应该是 28×27 更大）。
两版约束逐字相同，所以这不是「放宽约束换来的」。**大概率是映射差异**：同一段 RTL 在
15 ns 约束下 S3 只到 14.88 ns，说明 pipe2 那一版的 23.13 ns 是映射偏保守，而不是逻辑本身慢。
这一点没做逐 cell 对比，只作为观察记录，不作为结论。

`grep DW_div dc_w2.log` = **0**：除法器确实从网表里消失了。

---

## 4. 约束生效证据（不然结论无效）

```tcl
set_input_delay  -max 2.0 -clock clk $ins
set_input_delay  -min 0.5 -clock clk -add_delay $ins      # 必须分行，同行会 CMD-012 静默丢弃
set_output_delay -max 2.0 -clock clk [all_outputs]
set_output_delay -min 0.5 -clock clk -add_delay [all_outputs]
```

报告里的实证（`grep` 输出，不是「应该生效」）：

```
setup.rpt:  input external delay    2.00       2.00 r      ← 出现 10 次
stage1.rpt: input external delay    2.00       2.00 r
stage3.rpt: output external delay  -2.00      22.70        ← required = 25 − 0.3 − 2.0
setup.rpt:  clock uncertainty      -0.30      24.70
```

`check_timing` 无 `no_input_delay` / `unconstrained_endpoints` 告警，
`partial_input_delay` 一项也没有条目。

---

## 5. 「+0.002 ns」到底算不算过？—— 用更紧的周期回推真实能力

`compile` 的语义是「满了就停」，所以 25 ns 下报 +0.002 只说明**这一版网表刚好够**，
不说明结构极限。同一份 RTL、同一套约束，只改周期：

| 周期 | S1 arrival | S1 slack | 全设计 setup |
|---:|---:|---:|---:|
| 25 ns | 24.46 | +0.002 | MET |
| 20 ns | 19.35 | +0.001 | MET |
| 15 ns | **17.31** | −2.72 | VIOLATED |

15 ns 下 DC 已经全力优化仍到 17.31 ns，所以 **S1 的实际能力上限约 17.3 ns（含 2 ns 输入延时），
即纯逻辑约 15.3 ns**。25 ns 的真实余量约 **7 ns**，不是 2 ps。

同理 S2/S3 在 15 ns 下的 arrival 是 16.00 / 14.88（不含输入延时，reg→reg），
折算到 25 ns 的真实余量约 8.3 / 7.8 ns。

**注意口径**：第 3 节表格是「25 ns 综合出来的那一版网表」的 slack，第 5 节是「这个结构能跑多快」。
两个数都对，回答的是不同问题。要拿去流片就用第 5 节的能力，别信 2 ps。

---

## 6. 精度与位宽契约

```
inv_g_q60 = round(2^60 / G)          # 26 bit（G≈3.4e10 时 3.35e7）
D_eff_q24 = ((6·Wm − 3·G) · inv) >> 36
```

| 量 | 实测最大 | RTL 位宽 | 余量 |
|:---|---:|---:|:---|
| `6Wm − 3G` | 37 bit | 39 bit signed | 4× |
| `inv_g_q60` | 3.36e7（26 bit） | 27 bit signed | 2× |
| 乘积 | 62 bit | 66 bit（只用 <2^63 的部分） | 满足 |
| `x_q24` | 3.754 V（26 bit） | 28 bit signed | 2× |

`inv` 只取低 27 位 → 要求 `G ≥ 2^34`；乘积必须 <2^63 → 要求 `|D_eff| ≤ 4.5`（Q24 即 7.5e7）。
超出这两条属于标定失效，模块不做钳位、也不报错，这是接口契约写在 RTL 头注释里。

**没有做的事**：没有为了凑 slack 删逻辑、没有放宽 constraint、没有把 `÷96` 或饱和挪到模块外。
唯一被「优化」的是把 `×6/×3` 从常数乘法器改成移位加法，算术上完全等价，VCS 重跑仍是 0 失配。

---

## 7. 文件与复现

| 文件 | 状态 |
|:---|:---|
| `rtl/recon_d5_w2.sv` | 新增 |
| `tb/tb_recon_w2.sv` | 新增 |
| `tb/vec_recon_w2.txt` | 新增（400 组） |
| `tools/gen_vec_recon_w2.py` | 新增（向量生成 + 自检） |
| `synth/dc_w2.tcl` | 新增（DC 脚本） |
| `docs/TIMING_W2.md` | 新增（本文） |

未修改任何既有文件，未 `git commit`。远端：`vm-meow:/root/timing/w2/{rtl,tb,vcs,dc}`。

```bash
# 功能
ssh vm-meow 'cd /root/timing/w2/vcs && ./simv'

# 时序（compile，35 s）
ssh vm-meow 'cd /root/timing/w2/dc && export SNPSLMD_LICENSE_FILE=27080@localhost && \
  /opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell -no_gui -f dc_w2.tcl'
```

报告：`/root/timing/w2/dc/{setup,hold,stage1,stage2,stage3,area,check_timing}.rpt`。
15 ns 探针的报告在 `rpt_t15/`（被后一次运行覆盖前拷出来的）。
