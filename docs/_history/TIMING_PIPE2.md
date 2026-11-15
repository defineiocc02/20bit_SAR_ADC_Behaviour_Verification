# recon_d5_pipe2：D5 权重路径三级流水实测

> **结论：3 级流水进不了 20%。** 卡在 S1 —— `D_eff = 6·Wm/G − 3` 的除法。
> 25 ns 的可用预算是 22.7 ns，S1 实测 **421.20 ns**（未优化展开约 810 ns），差 16~32 倍。
>
> 端口按 `wm_q30/g_q30` 实现（D5 权重路径的真实接口；`k_i` 版是「除法已被移出去」的
> 名义路径，不含权重求和，与本任务的 S1 定义不自洽）。

数字全部来自 vm-meow 实跑：VCS W-2024.09-SP1、DC W-2024.09-SP3、SMIC180 `slow.db`。

---

## 1. 功能：PASS

`tb/tb_recon_pipe2.sv` + `tb/vec_recon_pipe2.txt`（398 组，由 `run_transaction` 生成）：

```
RESULT: 398 vectors, 0 golden mismatches, 0 recon_d5_w mismatches, 25 overflow cases
PASS
```

tb 同时实例化 `recon_d5_pipe2` 与 `recon_d5_w`，喂同一组 `wm/g/adc2/dither`：

- 与 **Python golden**（`run_transaction(...)['code20']`）比 → **0 失配**
- 与 **`recon_d5_w`** 二路对比 → **0 失配**（多出来的一级只改延迟、没改数值）
- 3 拍延迟成立（tb 在第 3 个 negedge 采样；若是 4 拍会 100% 失配）

向量口径：`wm = Σ w·m`（主 + 子 20 列），`g = Σ w`（仅 16 个采样主列），
代进 D_eff 与 `run_transaction` 内部的 `dac_q24 = _div_round(−K_q54, G_q30)` 逐位相同
（推导：`−K = 3·2^24·(2Wm − G)`）。覆盖 `code20` 两端饱和（0 / 1048575）与 25 组溢出。

---

## 2. 时序：S1 是灾难

| 级 | startpoint → endpoint | arrival | required | slack |
|:---|:---|---:|---:|---:|
| **S1 除法** | `g_q30[0]` → `d_q24_reg[26]` | **421.20** | 24.56 | **−396.64** |
| S2 `r̂+TΔ` | `adc2_s1_reg[1]` → `x_q24_reg[26]` | 18.54 | 24.46 | +5.91 |
| S3 `÷96`+饱和 | `x_q24_reg[26]` → `overflow_o` | 23.13 | 22.70 | −0.43 |

全设计：**setup −396.64 ns（VIOLATED）**／hold +0.42 ns（MET）／
面积 **377,290 µm²**、11,707 cells（`recon_d5_pipe` 是 50,175 µm²、1,485 cells，7.5×）。

S1 的 421.20 ns 是 `compile` 优化后的数；**同一路径在未优化的展开状态实测约 810 ns**。
两个数都不是 22.7 ns 量级的问题：810 / 22.7 ≈ 32 倍，421 / 22.7 ≈ 16.5 倍。

### 原因

DC 把它映射成 `DW_div_tc_a_width64_b_width45`（64 位 ÷ 45 位，含余数）。
阵列除法在 SMIC180 slow 上是 **O(n²) 门级展开**：关键路径要穿过约 64 行商选择，
每行又是 64 位宽的借位链，合计约 4,096 级门，单级 ~0.2 ns → 800 ns 量级。
这不是约束写错，也不是综合没尽力：`input external delay 2.00` 在报告里明确生效
（见第 3 节），起点 `g_q30`、终点 `d_q24_reg`，中间就是 `DW01_absval` + `DW01_inc` +
`DW02_mult`（×6 / ×2^24 都是移位加法）+ `DW_div_tc`。
`compile_ultra` 又跑了 90 分钟未收敛（TNS 仍在 −397 ns 附近），被 timeout 杀掉。

### 结论句

**按当前端口划分，3 级流水不可能进 20%**，因为 S1 单级就是 16~32 倍预算，
拆级只能改变「哪一级慢」，不能把 421 ns 变成 22.7 ns。要达标必须让除法不再占满一级：

1. **多拍迭代除法**（商一位一位地移出，约 16~32 拍）——才可能压进 25 ns，
   但流水线深度不再是 3；
2. **消除除法**：预计算 `1/G`（需把 `G` 当标定常数；实际 `G` 每笔差 ±0.5%，
   会丢掉与 `run_transaction` 的逐位一致）；
3. **回到 `k_i` 名义路径**：除法在模块外消化——这正是 `recon_d5_pipe` 能把 S1 做到
   +0.55 ns 的原因。

另外：`recon_d5_pipe` 的 S2（`÷96`+饱和）实测 +0.04 ns，而本版 S3 就是同一段逻辑，
所以**即使 S1 修好，S3 也只有 −0.43 ns**，仍需再优化（27×26 乘法约 14 ns 是主项，
要留 5 ns 余量得把部分积寄存成第 4 级）。

---

## 3. 与 `recon_d5_pipe` 的 +0.04 ns 的关系（不要混为一谈）

`setup_p_pipe_constrained.rpt` 里确实有 `output external delay -2.00` 与
`clock uncertainty -0.30`，required 22.70 与之吻合，关键路径是
`x_q24_reg[26] → overflow_o`（reg→reg 到输出端口）。也就是说**那份 +0.04 ns 的约束是生效的**，
与本次的 −396.64 ns 不存在可比性问题，不能拿「约束没生效」去解释两者的差异。

它之所以生效，是因为那几个 `set_input_delay/set_output_delay` 的 `-max` 与 `-min`
写了**两行**。写成一行就会触发下面第 4 节那个坑，而且是**静默**的 ——
我第一次跑 `recon_d5_pipe2` 就中招：只约束上了 reg→reg，`report_timing` 给出一个
假的 +6.13 ns，真正的 421 ns 路径根本没进报告。

---

## 4. dc_shell 坑（会毁结论）

1. **`set_input_delay -max 2.0 -min 0.5` 写在同一行 → 整条约束被丢弃。**
   W-2024.09-SP3 报 `Error: extra positional option '_selN' (CMD-012)`，
   Tcl `catch` 抓到的是空串，dc_shell 不退出、继续跑。
   `read_sdc` 走 SDC 解析器同样会丢。
   → 拆成 `-max` 一行 + `-min -add_delay` 一行（语义等价）：
   ```tcl
   set_input_delay  -max 2.0 -clock clk $ins
   set_input_delay  -min 0.5 -clock clk -add_delay $ins
   set_output_delay -max 2.0 -clock clk [all_outputs]
   set_output_delay -min 0.5 -clock clk -add_delay [all_outputs]
   ```
   并**用 `check_timing` + 报告里的 `input external delay` 行确认真的生效**。

2. **SDC 文件里不能用 `remove_from_collection`**（`unknown command`）。

3. **`report_timing -to [get_pins d_q24_reg[0]/D]`**：`[0]` 会先被 Tcl 当命令替换，
   报 `Value for list '-to' must have 1 elements`。模式一律加花括号 `{...}`。

---

## 5. 文件与复现

| 文件 | 状态 |
|:---|:---|
| `rtl/recon_d5_pipe2.sv` | 新增 |
| `tb/tb_recon_pipe2.sv` | 新增 |
| `tb/vec_recon_pipe2.txt` | 新增（398 组） |
| `docs/TIMING_PIPE2.md` | 新增（本文） |

未修改任何既有文件，未 `git commit`。远端：`vm-meow:/root/timing/{rtl,tb,vcs,dc2}`，
DC 报告在 `/root/timing/dc2/{setup,hold,stage1,stage2,stage3,area}.rpt`。

```bash
# 功能
ssh vm-meow 'cd /root/timing/vcs && ./simv'

# 时序（compile，36 min）
ssh vm-meow 'cd /root/timing/dc2 && export SNPSLMD_LICENSE_FILE=27080@localhost && \
  /opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell -no_gui -f pipe2_fast.tcl'
```
