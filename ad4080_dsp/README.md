# AD4080 核心校准算法 RTL（`y = m·x + c` + 两点校准）

> 目标：把 AD4080 数据手册 Rev.B p.71 的 **System Error Correction** 落成可综合 RTL，而不是只写接口。
>
> 依据：
> - AD4080 Data Sheet Rev.B, p.71 “SYSTEM ERROR CORRECTION COEFFICIENTS”
> - 寄存器映射 p.74：`OFFSET` 0x25/0x26（12-bit 二进制补码）、`GAIN` 0x27/0x28（10-bit 直接二进制，默认 0x200）
> - ISSCC 2024 9.8 PPT p.19–22：“Offset and gain – on-chip calibration”；p.28：`ΔVOS=47 ppb/°C`、`ΔGain=70 ppb/°C`

## 1. 算法

```text
y = m·x + c

m = 1 + (GAIN − 512) · (0.015594 / 512)
  = 1 + (GAIN − 512) · 3.045703125e−5

c = OFFSET · LSB      （12-bit two's complement，−2048..+2047）
```

- `GAIN`：10-bit，默认 `0x200`（512）= 不校正；范围 ±1.5594% FS；分辨率 `1.5594%/512 = 0.00305%`。
- `OFFSET`：12-bit 补码，1 LSB = ADC LSB；`VREF=3.0 V` 时约 ±11.71 mV。

两点校准：

```text
m = (y2 − y1) / (x2 − x1)
c = y1 − m·x1
GAIN   = 512 + (m − 1) / 3.045703125e−5
OFFSET = round(c)
```

## 2. RTL 模块

| 文件 | 作用 | 延迟 |
|:---|:---|:---:|
| `rtl/sys_error_corr.sv` | 逐样本 `y=m·x+c`，2 级流水，饱和 + clip 标志 | 2 clk |
| `rtl/cal_two_point.sv` | 两点校准：由 `(x1,y1),(x2,y2)` 算 `GAIN/OFFSET` | 3 clk |
| `rtl/sinc1_decim.sv` | SINC1 块平均抽取，D=2..1024，`filter_sync` 清零 | D+1 clk |
| `rtl/sinc5_decim.sv` | SINC5（5 级 CIC）抽取，D=2..256，位增长 5·log2(D) | D+1 clk |
| `rtl/result_fifo.sv` | 16K×20-bit 同步 FIFO，full/empty/watermark/overflow | 1 clk 读 |
| `rtl/ad4080_dsp_chain.sv` | 顶层链路：校正 → SINC1/SINC5/bypass → 可选 FIFO | — |
| `sim/tb/ad4080_cal_tb.sv` | 两点校准 + 校正应用自检 | — |
| `sim/tb/sinc5_decim_tb.sv` | SINC5 DC 冒烟 | — |
| `sim/tb/result_fifo_tb.sv` | FIFO 满/空/溢出/顺序 | — |

### `sys_error_corr` 定点

```text
delta = (x·(GAIN−512)·511 + 2^23) >>> 24
y     = x + delta + OFFSET
```

- `511 ≈ 3.045703125e−5 × 2^24`；`SHIFT=24`。
- `x` 20-bit 无符号；`delta` 16-bit 有符号；`y` 饱和到 `[0, 2^20−1]`。
- `clip_low/clip_high` 逐拍输出，不静默回绕。

### `cal_two_point` 定点

假设两点测试码 `x1/x2` 是**编译期常数**（默认 10%/90% 满幅），于是除法变成常数乘法：

```text
X1=104858, X2=943718, DX=838860
INV_DX_K = round(2^20 / (DX·3.045703125e−5)) = 41041, SHIFT_K=20
INV_DX   = round(2^30 / DX) = 1280,                 SHIFT_O=30

GAIN   = clamp(512 + ((dy−DX)·INV_DX_K + 2^19) >>> 20, 0, 1023)
OFFSET = clamp(((y1·DX − dy·X1)·INV_DX + 2^29) >>> 30, −2048, 2047)
```

若 `x1/x2` 需要运行时配置，必须另加除法器/倒数 LUT；当前版本不覆盖。

### SINC1 抽取

```text
y[k] = (1/D) · Σ_{i=0}^{D−1} x[kD+i]
D = 2,4,...,1024
```

- 块平均，输出速率 `fS/D`；`filter_sync` 清空累加器/计数器。
- 累加位宽 `20+log2(D)`；四舍五入 `(sum + D/2) >> log2(D)`。
- 对应数据手册 p64–69 的 SINC1 路径。

### SINC5 抽取

```text
H(z) = ((1 − z^−D)/(1 − z^−1))^5
y[k] = (1/D^5) · 5 级积分/梳状输出
D = 2,4,...,256
```

- 5 级积分器（输入速率）+ 抽取 + 5 级梳状器（输出速率）。
- 位增长 `5·log2(D)`；D=256 时累加器 60 bit，不能省。
- 输出 `(comb5 + 2^(5log2D−1)) >> 5log2D`，20-bit。
- SINC5+comp 未实现；需要数据手册图 105–107 的补偿系数。

### 16K FIFO

- `16384 × 20 bit`，同步单时钟，读延迟 1 拍。
- `full/empty/almost_full/almost_empty`；`overflow/underflow` 粘滞。
- 复位清零；满时拒绝写、不覆盖旧数据；空时拒绝读、数据保持。
- 综合时用 `(* ram_style = "block" *)` 提示；流片应换 SRAM 宏。

### 顶层链路

```text
x → sys_error_corr → (bypass / SINC1 / SINC5) → optional result_fifo → y
```

- `FILTER_SEL`：0 bypass，1 SINC1，2 SINC5；`USE_FIFO`：0/1。
- 不含 SPI/LVDS/事件触发/跨时钟域；这些仍是独立块。

## 3. Python 定点模型验证

模型：随交付包 `ad4080_rtl/model/ad4080_cal_model.py` 提供（与 RTL 同常数、同舍入）。本 PR 只含 RTL/TB，Python 模型不进入 RTL 子树，避免与仓库 ruff/mypy 范围冲突。

| 场景 | 校准得到 GAIN | OFFSET | 应用后最大误差 |
|:---|:---:|:---:|:---:|
| 理想 | 512 | 0 | 0 LSB |
| 增益 +1% | 840 | 0 | 7 LSB |
| 增益 −1% | 184 | 0 | 11 LSB |
| 失调 +100 | 512 | 100 | 0 LSB |
| 失调 −100 | 512 | −100 | 0 LSB |
| 增益 +0.5% / 失调 +50 | 676 | 50 | 4 LSB |
| 2000 组随机（±1%, ±200 LSB） | — | — | 最大 16 LSB |

> 残余 16 LSB 不是算法错，而是 **10-bit GAIN 的量化**：1 个 GAIN LSB ≈ 0.00305% FS ≈ 32 LSB₂₀，半 LSB ≈ 16 LSB₂₀。这与数据手册给出的分辨率一致。

## 4. 验证状态

- `sim/tb/ad4080_cal_tb.sv`：自检 TB，覆盖 6 组两点校准 + 1 组校正应用。
- **本机没有 Verilator/VCS，RTL 未在本机仿真**；TB 供仓库 CI 的 Verilator/XSim 运行。
- Python 定点模型已实际运行，结果见上表。

## 5. 还没做的（不要混为一谈）

| 项 | 状态 |
|:---|:---|
| AD4080 `y=m·x+c` + 两点校准 | ✅ RTL + Python 模型 + TB |
| SINC1 抽取滤波（D=2..1024） | ✅ RTL；未仿真 |
| SINC5 抽取滤波（D=2..256） | ✅ RTL；未仿真 |
| SINC5+comp（D=4..512） | ❌ 未做（缺补偿系数） |
| 16K 结果 FIFO | ✅ RTL；未仿真 |
| 顶层 `ad4080_dsp_chain` | ✅ RTL；未仿真 |
| SPI / LVDS / 事件触发 | ❌ 未做 |
| 内部 DEM/dither/18-slice | 已有独立 SAR 核 RTL，不在本目录 |
| timing mismatch 数字校准 | ❌ 未做；ADI 原文是 “Matched clocking scheme”，不是数字校准 |
| 40 MS/s 时序 / DFT / STA | ❌ 未做；属于物理签核 |

## 6. 关键结论

AD4080 数据手册的“片上校准”核心是 **offset/gain 的 `y=m·x+c`**，不是 DEM/dither，也不是每单位权重 LMS。  
本次把这条算法做成了可综合 RTL；SINC 滤波/FIFO/SPI/LVDS 是产品级数字功能，仍需单独补齐。
