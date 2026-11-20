# AD4080 风格并行 DSP 扩展

本目录提供独立的数字后处理。它对已重构的码做增益与失调校正、滤波抽取和 FIFO 捕获，通过 SPI 接收配置。当前 ADC 交付由 [v21](../../rtl/README.md) 定义。DSP 的直接入口是 [ad4080_top](rtl/ad4080_top.sv)，[数字适配层](rtl/sar20_ad4080_top.sv)则接收外部提供的 20 位 offset-binary 码及故障标志。

| 模块组 | 职责与输入条件 |
| --- | --- |
| 系统校正 | [sys_error_corr](rtl/sys_error_corr.sv) 应用显式的增益与失调，标记裁剪。系数由外部取得。 |
| 输出滤波 | [SINC1](rtl/sinc1_decim.sv)、[SINC5](rtl/sinc5_decim.sv) 和 [补偿 FIR](rtl/sinc5_comp.sv) 提供独立数值路径。补偿系数需显式绑定。 |
| 捕获 | [result_fifo](rtl/result_fifo.sv) 与 [fifo_capture](rtl/fifo_capture.sv) 定义记录、再 arm、读取和清除。 |
| 配置与 I/O | [SPI](rtl/spi_slave.sv)、[寄存器](rtl/ad4080_regs.sv)、[事件](rtl/event_detect.sv) 和 [GPIO](rtl/gpio_ctrl.sv) 负责配置和状态。结果码走并行接口，器件 PHY 和 CNV 接入另行验证。[追溯表](docs/ad4080_traceability.md) |

适配层在上游报告转换故障时停止接收，重启及故障释放决定恢复资格。软件复位要求外部 idle 和捕获条件成立。完整的码制、握手、复位和故障语义见 [数字输入接口](docs/SAR20_INTERFACE.md)。

## 怎样验证

从仓库根选择独立测试台。以下命令用合成数字输入检查 FIFO 与 DSP 适配层，需要已安装的仿真器。

~~~bash
dsp_run_dir=$(mktemp -d)
python tools/run_open_rtl.py --tops result_fifo_tb sar20_dsp_integration_tb \
  --out "$dsp_run_dir/checks"
~~~

[工具入口与组件清单](docs/tooling.md)给出指令和运行边界。[历史仿真](docs/verification_sim.md)、[BMC](docs/formal.md)、[ECP5](docs/synthesis_ecp5.md)和 [handoff003](../../docs/_history/archive/digital_status_matrix.md#15个dsp扩展模块)保留各自的源版本和输入。
