# AD4080 数字子系统 RTL

> 按 **AD4080 Data Sheet Rev.B** 逐位实现的数字子系统：配置 SPI（含 CRC-8 /
> streaming / 短指令）、寄存器堆（0x00–0x29）、系统误差校正（y=m·x+c）、
> 两点校准、SINC1/SINC5/SINC5+补偿、16K 结果 FIFO（4 种触发模式）、事件检测、
> GPIO 路由、CNV 与 LVDS 并串。
>
> 与之前版本的本质区别：**全链路按手册 p.20 Table 7 的 20-bit 二进制补码处理**；
> 旧版按无符号写，负半轴会算反。

## 1. 快速开始

```bash
# 静态门禁（语法/elaboration + 文件头 + 风格，基于 pyslang）
bash scripts/lint.sh

# 定点参考模型（与 RTL 同常数、同舍入）
python3 model/ad4080_cal_model.py
```

> 本目录的开发机没有 Verilator/Icarus/VCS，**RTL 未做功能仿真**。
> `lint.sh` 通过 = 可 elaborate、风格合规，不等于功能已被仿真证明。
> `sim/tb/` 里的自检 TB 与 `sim/run_top.sh` 供有仿真器的环境运行。

## 2. 算法（手册 p.71 / p.93）

```text
y = m·x + c

m = 1 + (GAIN − 512) · (0.015594 / 511)
  = 1 + (GAIN − 512) · 3.0516634e−5

c = OFFSET · LSB   （12-bit 补码，±2048 LSB）
```

**分母是 511，不是 512**：手册 p.93 把两个端点 0x001/0x3FF 都定义成偏离
0x200 恰好 511 个码、恰好 ±1.5594%。`GAIN=0x3FF` → m=1.015594，
`GAIN=0x001` → m=0.984406，`GAIN=0x200` → m=1.0。

定点实现（`rtl/sys_error_corr.sv`）：

```text
delta = (x · (GAIN−512) · 512 + 2^23) >>> 24
y     = saturate(x + delta + OFFSET, −524288, +524287)
```

`512 = round(3.0516634e−5 × 2^24)`；`clip_low/clip_high` 逐拍输出，不静默回绕。

两点校准（`rtl/cal_two_point.sv`，编译期常数测试点，除法退化为常数乘法）：

```text
X1=−419430, X2=+419430（满量程 10%/90%）, DX=838860
GAIN   = clamp(512 + ((dy−DX)·40961 + 2^19) >>> 20, 0, 1023)
OFFSET = clamp(((y1·DX − dy·X1)·1280 + 2^29) >>> 30, −2048, 2047)
```

## 3. 模块清单

| 文件 | 功能 | 手册依据 |
|:---|:---|:---|
| `rtl/sys_error_corr.sv` | y=m·x+c，2 级流水，饱和+clip | p.71, p.93 |
| `rtl/cal_two_point.sv` | 两点校准折算 GAIN/OFFSET | p.71 |
| `rtl/sinc1_decim.sv` | SINC1，D=2..1024，settling=2+D+1 | p.64-69 |
| `rtl/sinc5_decim.sv` | SINC5（5 级 CIC），D=2..256，settling=2+5D+4 | p.64-69 |
| `rtl/sinc5_comp.sv` | SINC5+补偿（N×2 抽取，3 抽头 Q1.15），settling=2+35N+10 | p.64-69 |
| `rtl/result_fifo.sv` | 16K×20-bit 同步 FIFO | p.57 |
| `rtl/fifo_capture.sv` | FIFO 4 模式、WATERMARK 钳位、full_flag、mode 11 回写 | p.57-63 |
| `rtl/event_detect.sv` | HI/LO 阈值+迟滞，level/sticky 双通道，ALERT 低有效 | p.54-56, p.90-92 |
| `rtl/gpio_ctrl.sv` | 4 路 GPIO，4-bit SEL 按手册编码 | p.87-89 |
| `rtl/spi_slave.sv` | Mode 3、streaming、短指令、CRC-8、SEND_STATUS | p.34-43, p.75-82 |
| `rtl/ad4080_regs.sv` | 寄存器堆 0x00–0x29，W1C，SW_RESET | p.73-94 |
| `rtl/cnv_ctrl.sv` | CNV CMOS/LVDS 同步 + tCONV 违反检测 | p.33, p.45, p.83 |
| `rtl/lvds_framer.sv` | 1/2 lane 并串，空闲重复上一帧 | p.45-53, p.66 |
| `rtl/ad4080_dsp_chain.sv` | 校正 → 滤波选择 → 可选 FIFO | p.64-70 |
| `rtl/ad4080_top.sv` | 顶层集成 | p.14-95 |

## 4. 定点参考模型验证

`model/ad4080_cal_model.py`（与 RTL 同常数、同舍入、同补码）实测：

| 场景 | GAIN | OFFSET | 应用后最大误差 |
|:---|:---:|:---:|:---:|
| 理想 | 512 | 0 | 0 LSB |
| 增益 +1% | 840 | 0 | 3 LSB |
| 增益 −1% | 184 | 0 | 5 LSB |
| 增益 ±1.5594%（满偏） | 1023 / 1 | 0 | **0 LSB**（两端点精确） |
| 失调 ±100 | 512 | ±100 | 0 LSB |
| 增益 +0.5% / 失调 +50 | 676 | 50 | 1 LSB |
| 2000 组随机（±1%, ±200 LSB） | — | — | 最大 8 LSB |

残余 8 LSB 是 10-bit GAIN 的量化（1 个 GAIN LSB ≈ 0.00305% FS ≈ 16 LSB₂₀，
半 LSB ≈ 8 LSB₂₀），与手册分辨率一致。

## 5. 文档

- `docs/coding_standard.md` — 编码与注释规范（文件头 10 字段、标签体系、禁用项）
- `docs/ad4080_traceability.md` — 手册条目 → RTL 行号逐行对照
- `docs/review_round1_datasheet.md` — 手册审查裁决
- `docs/review_round2_patents.md` — 专利审查裁决
- `docs/review_round3_paper.md` — ISSCC 2024 9.8 论文审查裁决
- `docs/review_spi_regs.md` / `review_filter_fifo.md` / `synthesis_review.md` — 独立审查原始报告
- `docs/tooling.md` — 工具链实测记录（pyslang / Verible / RTL-Coder）

## 6. 验证状态（如实声明）

| 项 | 状态 |
|:---|:---|
| pyslang 语法 + elaboration | ✅ 0 errors，5 条已标注的位宽警告 |
| 文件头/风格门禁 | ✅ 15/15 |
| 定点参考模型 | ✅ 已运行，上表 |
| CRC-8 算法一致性 | ✅ 逐位实现 vs 查表实现交叉一致 |
| RTL 功能仿真 | ❌ 未做（本机无仿真器） |
| 时序/面积/STA | ❌ 未做（属物理签核） |

## 7. 未实现 / 待确认

| 项 | 说明 |
|:---|:---|
| 0x0E STREAM_MODE 回环、0x0F TRANSFER_CONFIG | P2，未实现 |
| STRICT_REGISTER_ACCESS 部分访问检测 | P2，未实现 |
| streaming 多字节寄存器起始字节序 | [待确认]（手册 Fig.58/59 是图） |
| SINC5+comp 补偿系数 | [待确认]（手册只给响应曲线；参数占位） |
| LVDS DDR PHY / 差分驱动 / Manchester | 电气层，不属于数字 RTL |
| SPI 回读数据通道（DCS/DCLK） | 未实现 |

## 8. 关键结论

AD4080 的“片上校准”是 offset/gain 的 `y=m·x+c`，不是 DEM/dither；
DEM/dither/18-slice 交织属于模拟核（本仓库另有行为模型与 SAR 核 RTL）。
本目录实现的是手册定义的数字子系统，并经过三轮独立审查（手册/专利/论文）。
