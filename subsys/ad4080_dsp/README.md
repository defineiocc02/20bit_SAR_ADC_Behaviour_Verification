# AD4080 风格并行 DSP 扩展

本目录为独立的 `sar20_digital_core` 路线提供并行数字后处理。`sar20_system_top` 将 SAR 数字核与 DSP 组合。系统运行须提供完整输入包、明确的 source binding 和模拟宏返回信号。 [系统顶层](rtl/sar20_system_top.sv) · [SAR core](../../rtl/top/sar20_digital_core.sv) · [接口细节](docs/SAR20_INTERFACE.md)

| 模块组 | 职责与输入条件 |
| --- | --- |
| 系统校正 | [sys_error_corr](rtl/sys_error_corr.sv) 应用显式的增益与失调，标记裁剪。系数由外部取得。经 [适配层](rtl/sar20_ad4080_top.sv) 进入 DSP 时将 20 位 offset-binary 映射为补码。 |
| 输出滤波 | [SINC1](rtl/sinc1_decim.sv)、[SINC5](rtl/sinc5_decim.sv) 和 [补偿 FIR](rtl/sinc5_comp.sv) 提供独立数值路径。补偿系数需要显式绑定。 |
| 捕获 | [result_fifo](rtl/result_fifo.sv) 与 [fifo_capture](rtl/fifo_capture.sv) 定义记录状态、再 arm、读取和清除行为。 |
| 配置与 I/O | [SPI](rtl/spi_slave.sv)、[寄存器](rtl/ad4080_regs.sv)、[事件](rtl/event_detect.sv) 和 [GPIO](rtl/gpio_ctrl.sv) 接收配置与状态。结果数据使用并行接口，器件 PHY 和实际 CNV 仍需独立实现和验证。 [追溯表](docs/ad4080_traceability.md) |

已报告的上游转换故障会锁停适配层，并阻止后续有效 raw 进入。配置 epoch 清除和有效系数重载决定恢复资格。软件复位还要求外部 idle 和事务条件成立。完整握手、复位优先级、异常状态及覆盖范围见 [SAR20 接口说明](docs/SAR20_INTERFACE.md)。

## 怎样验证

从仓库根目录，通过显式 runner 选取需要的组件。示例运行 FIFO 测试，需要已安装的仿真器：

~~~bash
dsp_run_dir=$(mktemp -d)
python tools/run_open_rtl.py --tops result_fifo_tb --out "$dsp_run_dir/fifo"
~~~

系统级输入包、支持的组件及配置见 [复现说明](docs/tooling.md)。handoff003 归档记录 14 个指定场景和 5 个 lint profile，包含同一 FIFO 测试台的两个深度。系统级数字对照覆盖表中所列配置和故障恢复序列。完整滤波选择、逐输出元数据及 CAAZ 与 DSP 的组合仍需各自的证据。 [handoff003 记录](../../docs/_history/archive/digital_status_matrix.md#15个dsp扩展模块)

[历史仿真](docs/verification_sim.md)、[BMC](docs/formal.md) 和 [ECP5](docs/synthesis_ecp5.md) 保留各自的源版本与范围。当前仓库的 v21 数字 IP 状态见 [项目状态](../../docs/STATUS.md)。
