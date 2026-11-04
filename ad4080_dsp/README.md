# AD4080 风格数字子系统与 SAR 工程接入

> 结构 SAR 现有逐结果 `dout_analog_bad` 和可选校准观测口；本 DSP 接入层仍用模拟异常粘滞位停流，未传播该逐样本位。见 [接口合同](docs/SAR20_INTERFACE.md) 和 [ADR 0021](../docs/adr/0021-structural-contract-calibration.md)。

这是独立的数字处理原型：系统失调/增益校正、SINC 抽取、FIFO 捕获、SPI 寄存器、事件检测与串行帧逻辑。它与主工程共享验证环境，但不是 AD4080 芯片实现，也不包含模拟 ADC、DDR 差分 PHY、工艺 SRAM 或 ASIC 时序签核。

## 与本工程连接

项目组合顶层 [`sar20_system_top.sv`](rtl/sar20_system_top.sv) 同时实例化真实 SAR 数字核和 DSP 接入层；也可单独实例化 [`sar20_ad4080_top.sv`](rtl/sar20_ad4080_top.sv)，从外部连接 SAR 的输出码、valid、sample ID 和结果标志。两侧同 `clk`，不包含 CDC。入口把工程的 20 位 offset-binary 转成 DSP 的二进制补码；并行输出转回 offset-binary，串行 payload 仍为补码。原 [`ad4080_top.sv`](rtl/ad4080_top.sv) 仍可单独使用，其输入和输出均为补码。

SAR 任一结果标志或单独的模拟溢出粘滞位置位时，接入层停止接收并锁存 `stream_fault`；结果 valid 和串行输出受到抑制。`dsp_restart` 清 DSP 故障但不清 SAR 模拟粘滞位，后者未清时会再次阻断；`sar_epoch_clear` 接 SAR 的 `cfg_clear_valid`，可同时清除并使 SAR 系数失效。**两种复位均清空 DSP 的 FIFO、滤波状态和寄存器配置**。输入接受/拒收事件携带对应 ID，但模拟粘滞位本身和滤波/FIFO 输出尚无逐结果 ID。完整时序和复位优先级见 [SAR20 接口契约](docs/SAR20_INTERFACE.md)。

## 数字功能

| 模块 | 当前代码功能 | 边界 |
|---|---|---|
| [`sys_error_corr`](rtl/sys_error_corr.sv) | 20 位补码输入；增益、失调、饱和及校正级 clip 事件 | 定点系数和舍入按代码合同，需结合系统误差预算 |
| [`cal_two_point`](rtl/cal_two_point.sv) | 固定横坐标 `x1/x2` 下的两点仿射拟合 | 若横坐标是已知输入、纵坐标是测得原始码，结果是正向传递系数，不能直接用作反向校正；未连接顶层自动学习控制器 |
| [`sinc1_decim`](rtl/sinc1_decim.sv)、[`sinc5_decim`](rtl/sinc5_decim.sv)、[`sinc5_comp`](rtl/sinc5_comp.sv) | 直通、SINC1、SINC5 和 SINC5+额外 2 倍抽取结构 | SINC5 补偿 FIR 默认系数 `(1,0,0)` 为占位，不代表真实器件频响 |
| [`result_fifo`](rtl/result_fifo.sv)、[`fifo_capture`](rtl/fifo_capture.sv) | FIFO 数据与捕获模式原型 | 与实际 SRAM 时序及器件所有边界条件尚未等价验证 |
| [`spi_slave`](rtl/spi_slave.sv)、[`ad4080_regs`](rtl/ad4080_regs.sv) | Mode 3 SPI、寄存器、CRC 和 streaming 等数字逻辑 | 多字节访问顺序和 CRC 后续种子等仍需外部证据确认 |
| [`lvds_framer`](rtl/lvds_framer.sv) | 单/双 lane 数字帧和 SDR 串行逻辑 | 单 lane 20 次使能/帧，16 时钟/样本的连续输入会覆盖数据；`bit_en` 恒高时 `dco` 不翻转；FIFO 读出未接入该串行路径。不含 DDR/差分 PHY |
| [`ad4080_top`](rtl/ad4080_top.sv) | 将上述处理、控制和事件逻辑集成为数字顶层 | 尚无目标工艺综合和 STA；不能由仿真时钟证明 40 MS/s 时序 |

`sys_error_corr` 的代码公式为 `delta = (x × (GAIN − 512) × 512 + 2^23) >>> 24`，然后加补码 OFFSET 并饱和。默认 `GAIN=512`、`OFFSET=0`。原有旧版说明中的无符号码制和 `511` 系数不适用于当前源码。

AD4080 手册的两点校正以**测得原始码**为横坐标、已知输入对应的目标码为纵坐标；一般情况下原始码差是变化的分母。当前 `cal_two_point` 的分母是编译期固定值，只有调用方能提供固定原始码对应的目标码时才可按这一方式使用。实际器件的外部两点测量若采用固定输入和可变原始码，仍需逆向系数计算及验证，不能把现有正向拟合测试当成校正闭环。手册正文的每码增益分辨率按 512 描述，而寄存器表的正负端点跨 511 码；本原型选择后者，具体器件一致性仍待确认。

CNV数字代理以`cnv_p`表示逻辑正相，LVDS模式的互补引脚不能通过XOR当作数据；真实差分接收器仍在RTL之外。配置SPI现在只在同步片选有效、完整数据/CRC边界提交，片选退出会清除残帧状态；共享SCLK在同步片选取消后不得产生寄存器写。该保证以同步检测到的CS为边界，主机仍须满足过采样和片选建立/保持裕量。新自检台`cnv_ctrl_contract_tb`及`spi_abort_contract_tb`覆盖此合同，并检查长/短指令状态回传位序。

## 验证

```bash
# 在仓库根目录：SAR 结构核接入 DSP 的实际集成测试
python tools/run_open_rtl.py --tops sar20_dsp_integration_tb sar20_system_top_tb

# 独立的 7 个 DSP 自检台（需要 Icarus Verilog）
BUILD_DIR="${TMPDIR:-/tmp}/ad4080-dsp-build" bash ad4080_dsp/sim/run_all.sh

# Python 定点参考模型
python ad4080_dsp/model/ad4080_cal_model.py
```

集成测试使用实际 `sar20_digital_core` 与项目接入顶层，另注入边界码和五类 SAR 结果错误，检查故障与重启。独立测试覆盖校正、SINC5、FIFO、SPI 和 DSP 顶层；通过状态以实际运行日志为准。[历史完成度记录](docs/status_completion.md) 与 [历史仿真记录](docs/verification_sim.md) 是产生时的归档，含旧版评估和工具环境，不替代当前回归或 ASIC 签核。
