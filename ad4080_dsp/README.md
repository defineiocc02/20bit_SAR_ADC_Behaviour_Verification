# AD4080 风格数字子系统与 SAR 工程接入

这是独立的数字处理原型：系统失调/增益校正、SINC 抽取、FIFO 捕获、SPI 寄存器、事件检测与串行帧逻辑。它与主工程共享验证环境，但不是 AD4080 芯片实现，也不包含模拟 ADC、DDR 差分 PHY、工艺 SRAM 或 ASIC 时序签核。

## 与本工程连接

项目组合顶层 [`sar20_system_top.sv`](rtl/sar20_system_top.sv) 同时实例化真实 SAR 数字核和 DSP 接入层；也可单独实例化 [`sar20_ad4080_top.sv`](rtl/sar20_ad4080_top.sv)，从外部连接 SAR 的输出码、valid、sample ID 和结果标志。两侧同 `clk`，不包含 CDC。入口把工程的 20 位 offset-binary 转成 DSP 的二进制补码；并行输出转回 offset-binary，串行 payload 仍为补码。原 [`ad4080_top.sv`](rtl/ad4080_top.sv) 仍可单独使用，其输入和输出均为补码。

SAR 任一结果标志置位时，接入层拒收该样本并锁存 `stream_fault`；此后输入被拒收，结果 valid 和串行输出受到抑制。恢复需要 `dsp_restart` 或 SAR 的 `cfg_clear_valid` 接到 `sar_epoch_clear`。**这会复位整个 DSP，并清空 FIFO、滤波状态和寄存器配置**。输入接受/拒收事件携带对应 ID，但滤波或 FIFO 输出尚无逐结果 ID 和输入标志。完整时序、复位优先级与适用范围见 [SAR20 接口契约](docs/SAR20_INTERFACE.md)。

## 数字功能

| 模块 | 当前代码功能 | 边界 |
|---|---|---|
| [`sys_error_corr`](rtl/sys_error_corr.sv) | 20 位补码输入；增益、失调、饱和及校正级 clip 事件 | 定点系数和舍入按代码合同，需结合系统误差预算 |
| [`cal_two_point`](rtl/cal_two_point.sv) | 基于固定测试点的两点系数计算 | 测试点是编译期常量；它是单独模块，并未连接为顶层自动学习控制器 |
| [`sinc1_decim`](rtl/sinc1_decim.sv)、[`sinc5_decim`](rtl/sinc5_decim.sv)、[`sinc5_comp`](rtl/sinc5_comp.sv) | 直通、SINC1、SINC5 和 SINC5+额外 2 倍抽取结构 | SINC5 补偿 FIR 默认系数 `(1,0,0)` 为占位，不代表真实器件频响 |
| [`result_fifo`](rtl/result_fifo.sv)、[`fifo_capture`](rtl/fifo_capture.sv) | FIFO 数据与捕获模式原型 | 与实际 SRAM 时序及器件所有边界条件尚未等价验证 |
| [`spi_slave`](rtl/spi_slave.sv)、[`ad4080_regs`](rtl/ad4080_regs.sv) | Mode 3 SPI、寄存器、CRC 和 streaming 等数字逻辑 | 多字节访问顺序和 CRC 后续种子等仍需外部证据确认 |
| [`lvds_framer`](rtl/lvds_framer.sv) | 单/双 lane 数字帧和 SDR 串行逻辑 | 不含 DDR、差分驱动、Manchester 电气实现 |
| [`ad4080_top`](rtl/ad4080_top.sv) | 将上述处理、控制和事件逻辑集成为数字顶层 | 尚无目标工艺综合和 STA；不能由仿真时钟证明 40 MS/s 时序 |

`sys_error_corr` 的代码公式为 `delta = (x × (GAIN − 512) × 512 + 2^23) >>> 24`，然后加补码 OFFSET 并饱和。默认 `GAIN=512`、`OFFSET=0`。原有旧版说明中的无符号码制和 `511` 系数不适用于当前源码。

## 验证

```bash
# 在仓库根目录：SAR 结构核接入 DSP 的实际集成测试
python tools/run_open_rtl.py --tops sar20_dsp_integration_tb sar20_system_top_tb

# 独立的 5 个 DSP 自检台（需要 Icarus Verilog）
BUILD_DIR="${TMPDIR:-/tmp}/ad4080-dsp-build" bash ad4080_dsp/sim/run_all.sh

# Python 定点参考模型
python ad4080_dsp/model/ad4080_cal_model.py
```

集成测试使用实际 `sar20_digital_core` 与项目接入顶层，另注入边界码和五类 SAR 结果错误，检查故障与重启。独立测试覆盖校正、SINC5、FIFO、SPI 和 DSP 顶层；通过状态以实际运行日志为准。[历史完成度记录](docs/status_completion.md) 与 [历史仿真记录](docs/verification_sim.md) 是产生时的归档，含旧版评估和工具环境，不替代当前回归或 ASIC 签核。
