# ECP5 综合 / 布局布线 / 时序结果

> 日期：2026-10-19 · 工具：Yosys 0.70 `synth_ecp5` + nextpnr-ecp5 0.11.1
> 目标：ECP5-85K，100 MHz 约束，`--lpf-allow-unconstrained`（无板级引脚约束）
> **这是 FPGA 数据，不是 ASIC 签核数字**（无 PDK / 标准单元 / STA）。

---

## 1. 结果对比（优化前 → 优化后）

| 指标 | 优化前 | 优化后 | 变化 |
|---|---:|---:|---:|
| 顶层 Fmax | **35.58 MHz** | **73.59 MHz** | **2.07×** |
| SINC5 单模块 | 31.11 MHz | 82.41 MHz | 2.65× |
| SINC1 单模块 | 84.88 MHz | 130.36 MHz | 1.54× |
| LUT4 | 5,655 | 6,894 | +22% |
| 触发器 | 2,129 | 3,175 | +49% |
| DP16KD BRAM | 20 | 20 | 不变 |
| 100 MHz 约束 | FAIL | FAIL | 未收敛 |

其余模块单独综合均 **PASS 100 MHz**：`sys_error_corr` 126 MHz、
`result_fifo` 108 MHz、`spi_slave` 125 MHz、`ad4080_regs` 677 MHz。
顶层掉到 73.6 MHz 的原因就是 SINC5 只有 82 MHz，加上布线后进一步下降。

## 2. 关键发现：16K FIFO 能正确映射 BRAM

`result_fifo` 的 `16384 × 20` 存储体在 ECP5 上映射为 **20 × DP16KD**
（20 × 18 Kbit = 360 Kbit ≥ 327.68 Kbit），没有退化成触发器。
前提是读口**不能带异步复位**——这是本轮修的（`rd_data` 只在 `rd_valid` 窗口有效）。

## 3. 做了什么（按模块）

### SINC1（85 → 130 MHz）
原来的组合路径是“32-bit 累加 → 变量桶形移位 → 输出”。改成两级：
第 1 拍只做累加并锁存 `sum_r` 与 `l_sum`；第 2 拍只做 `sum_r >>> l_sum`。
延迟 +1 拍，功能不变（TB 的 4 点平均仍为 1000）。

### SINC5（31 → 82 MHz）
1. **积分器逐级打拍**：`integ[i] <= integ[i] + integ[i-1]`，
   每级只做一次 61-bit 加法，链上不再串 5 个。
2. **抽取对齐**：第 D 个样本要 5 个 valid 沿才到 `integ[4]`，
   抽取脉冲改 5 位移位，`dec_evt = valid_in && dec_pipe[4]` 做单拍事件。
3. **收缩式梳状器**：每个抽取事件所有级同时算一次，级间只差一个减法器。
4. **归一化拆三拍**：`out_r` → `+舍入` → `>>> shift` → 饱和，各自打拍。
5. **settled 重新定义**：按“已输出个数 ≥ ceil(settling/D) + 流水深度”判定，
   否则标志会比实际输出提前。

## 4. 为什么还是到不了 100 MHz

优化后顶层最长路径仍是一条 **61-bit 进位链**（CIC 的一级减法 + 布线）：
ECP5 的 carry chain 约 0.1 ns/bit，61 bit ≈ 6-7 ns，加上布线与前后逻辑
≈ 13.6 ns → 73.6 MHz。要继续提升只有两条路：

| 方案 | 代价 | 状态 |
|---|---|---|
| **Hogenauer 位宽裁剪**：积分器前几级只用 `IN_W + log2(D)` 位 | 需要逐级噪声/溢出预算，改变数值 | 未做（会动数值，需重新验证） |
| 换更快工艺/更宽 carry（ASIC） | 需要 PDK | 工具链限制 |

**结论：在当前 ECP5 + 全宽 CIC 结构下，73.6 MHz 就是工具链能给出的上限；
100 MHz 需要 Hogenauer 裁剪或 ASIC 流程。**

## 5. 复现命令

```bash
export PATH=$HOME/.workbuddy/tools/oss-cad-suite/bin:$PATH

# 单模块
yosys -p "read_verilog -sv rtl/*.sv; synth_ecp5 -top sinc5_decim -json s5.json"
nextpnr-ecp5 --85k --json s5.json --lpf-allow-unconstrained \
             --freq 100 --timing-allow-fail --report /dev/null

# 顶层（约 4-5 分钟）
yosys -p "read_verilog -sv rtl/*.sv; synth_ecp5 -top ad4080_top -json top.json"
nextpnr-ecp5 --85k --json top.json --lpf-allow-unconstrained \
             --freq 100 --timing-allow-fail --report report.json
```
