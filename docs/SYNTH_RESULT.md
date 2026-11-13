# SMIC 180 综合结果（DC W-2024.09-SP3，slow corner）

日期 2026-11-13 · 库 `/home/yian/PDK/smic180/standard_cells/synopsys/slow.db`
许可证 `SNPSLMD_LICENSE_FILE=27080@localhost`

## 1. 结果

| 项 | 值 |
|:---|---:|
| 单元数 | **1961** |
| 面积 | **38546.3 µm²**（全部组合逻辑，无时序单元） |
| 端口数 | 914 |
| **关键路径延迟** | **62.72 ns**（`adc2_i[2]` → `code_o[18]`） |

## 2. 关键路径在哪

```
adc2_i[2] → u_recon/... → MXI2X1 → AOI21X1 → NOR2X1 → AOI21X1 → OAI2BB1X1 → code_o[14]
                                                    累计 62.72 ns
```

**全部落在 `recon_d5` 内**，与 `docs/CRITICAL_PATH.md` 的结构性预测一致（排第 1 的
`adc2 → code20` 路径）。末端是 MUX/复合门链，说明除法被展开成大量组合逻辑。

## 3. 这意味着什么

- 40 MS/s 要求 25 ns。**当前实现 62.72 ns，差 2.5 倍，达不到。**
- 这还是**纯组合**设计、无时钟约束（Path Group = none），真实带寄存器时只会更慢。
- 与结构分析一致的两个长杆：`÷5` 与 `÷96` 都在这条路径上。

## 4. 不能从本结果得出的结论

- 不能说 Fmax——本设计没有 `clk` 端口，`create_clock` 未生效，62.72 ns 是
  **输入到输出的纯组合延迟**，不是建立时间余量。
- 不能说功耗——本轮未跑 `report_power`（无活动率假设）。
- 不能说满足 40 MS/s。

## 5. 下一步（按收益排序）

1. `÷96` → `>>5` + `×21845>>16`（除 3）；`÷5` → `×14745>>9`
2. `slice_pool` 的 `%18`、`dem_shuffle` 的 `%15` → 计数器 + 条件减
3. 流水化：`dither → k → mask` 与 `adc2 → r̂ → code` 拆成两级寄存器

改完必须重跑六组对拍（对拍向量已入库），确认数值不变再看新延迟。

---

## 6. 两级流水版（`recon_d5_pipe`）

| 指标 | 组合版 | 两级流水 |
|:---|---:|---:|
| 关键路径 | 62.72 ns | **45.80 ns**（次长 39.76） |
| 面积 | 38546 µm² | 78490 µm² |
| 时钟约束 | 无（组合） | 25 ns（未满足） |

流水把最长路径从 62.72 降到 45.80 ns（−27%），但**仍超 25 ns**，且面积翻倍。

原因仍是 64 位乘法器：S1 的 `×838861` 与 S2 的 `×44739243` 都在 64 位下实现。
按实际位宽（42 / 52 位）重做后才有望进 25 ns。

---

## 7. 窄位宽 + 流水（当前最优，**满足 25 ns**）

| 配置 | 关键路径 | 面积 | 25 ns |
|:---|---:|---:|:--:|
| 组合 + 除法 | 62.72 ns | 38546 µm² | ✗ |
| 组合 + 倒数乘(64 位) | 63.15 ns | 78190 µm² | ✗ |
| 流水 + 倒数乘(64 位) | 45.80 ns | 78490 µm² | ✗ |
| **流水 + 窄位宽(42/54 位)** | **24.41 ns** | **49972 µm²** | **✓** |

位宽依据（实测最坏值定界，非拍脑袋）：
- `(2a+1)*144+2 ≤ 1,179,506 < 2²¹`，乘 838861 → **42 位**
- `|x| ≤ 5.2e7 < 2²⁶`，乘 44739243 → **54 位**（53 位会溢出，已实测踩到）
- 寄存器 `x_q24` → **27 位**

功能：1987 组向量 **0 失配**（与组合版逐位一致）。

余量仅 +0.59 ns，属"刚好满足"。工艺角/布线后会吃掉大部分，
真实签核需要留更多余量或再拆一级流水。


---

## 8. 重要更正：此前"满足 25 ns"的说法不成立

取原始报告 `synth/results/timing_pipe_25ns.rpt` 核对，发现：

```
Startpoint: x_q24_reg[26]  (rising edge-triggered flip-flop clocked by clk)
Endpoint:   overflow_o     (output port)
Path Group: (none)         <-- 没有时钟组
```

报告里**根本没有 `data required time`，也没有 `slack` 行**。

原因：终点是**输出端口**，而约束里没有 `set_output_delay`。
未被约束的路径只有 `data arrival time`，DC 不会算 slack。

**所以 24.41 ns 是"寄存器到输出端口的组合延迟"，不是建立时间余量。**
拿它和 25 ns 相减得出"满足 40 MS/s"是我犯的错——那不是签核，是数字巧合。

## 9. 要得到真实 slack 还缺什么

| 缺项 | 作用 |
|:---|:---|
| `set_input_delay` | 约束输入端口相对时钟的到达时间 |
| `set_output_delay` | 约束输出端口的捕获时间（否则路径无 required） |
| `set_clock_uncertainty` | 抖动/偏斜余量 |
| 输出寄存（可选） | 让终点是寄存器，路径自然进时钟组 |

补上之后 `report_timing` 才会给出 `slack`，那时说"满足/不满足"才有意义。

## 10. 当前能说的和不能说的

**能说**：组合版从 `adc2` 到 `code_o` 的组合延迟 62.72 ns；流水窄位宽版从
`x_q24_reg` 到 `code_o` 的组合延迟 24.41 ns；面积 38546 / 49972 µm²。
这些是**真实的综合数据**，有原始报告。

**不能说**：时序收敛、满足 40 MS/s、满足任何时钟周期。
因为**没有任何一条被约束路径的 slack**。

---

## 11. 补全 SDC 后的真实 STA（`recon_d5_pipe`，25 ns，SMIC180 slow）

`digital_top` 是纯组合模块、无 `clk` 端口，时钟约束无法绑定（`Path Group: (none)`），
因此真实的 setup/hold 只能在**带时钟的 `recon_d5_pipe`** 上做。

### 约束（工程假设，非签核值）

```tcl
create_clock -name clk -period 25 [get_ports clk]
set_clock_uncertainty -setup 0.3 [get_clocks clk]
set_clock_uncertainty -hold  0.1 [get_clocks clk]
set_input_delay  -max 2.0 -min 0.5 -clock clk   [数据输入，排除 clk]
set_output_delay -max 2.0 -min 0.5 -clock clk   [all_outputs]
```

### 结果

| 项 | 值 |
|:---|:---|
| **Setup** | arrival 22.66 / required 22.70 → **slack +0.04 ns（MET）** |
| **Hold** | arrival 0.65 / required 0.07 → **slack +0.58 ns（MET）** |
| 面积 | 1485 cells / **50175 µm²** |
| Path Group | `clk`（约束正确绑定） |

### 怎么读这个 +0.04 ns

**MET，但余量只有 0.16%。** 这是布局布线前、单一 slow 角、无 OCV 的结果。
布线寄生、OCV、多角检查都会吃掉这个余量，**不能据此认为 40 MS/s 已达成**。

能说的：在该组约束与库下，综合后的网表满足 25 ns 建立时间要求，余量 0.04 ns。
不能说的：布线后仍满足、留有余量、可以签核。


---

## 12. 更正：第 11 节的 +0.04 ns 只覆盖 reg→out

复查 `setup_p_pipe_constrained.rpt`：报告里只有 `output external delay -2.00`，
**没有 `input external delay`**，5 条最差路径的起点全是 `x_q24_reg`。

也就是说：**输入端到寄存器这一段没有被 2.0 ns 的 input delay 约束过。**
（当时 `set_input_delay -max` 单独写一行，但那台 dc_shell 对 `-max/-min` 的若干写法
会报 CMD-012 后静默丢弃，见 `docs/TIMING_PIPE2.md`。）

因此第 11 节的结论要收窄为：

- 能说：`recon_d5_pipe` 的 **reg→out** 路径在 25 ns、2.0 ns 输出延迟、0.3 ns 不确定度下 slack +0.04 ns
- 不能说：整块设计满足 25 ns——**输入路径未验证**

正确复跑方式（已在 `pipe2` 上验证）：
`-max` 一行，`-min` 单独一行并带 `-add_delay`。
