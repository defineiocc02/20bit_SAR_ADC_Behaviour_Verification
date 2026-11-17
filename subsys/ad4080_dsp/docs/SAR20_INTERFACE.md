# DSP 数字输入接口

[sar20_ad4080_top](../rtl/sar20_ad4080_top.sv) 从调用方接收已经重构的 20 位 offset-binary 码、样本 ID、有效位和故障标志。它完成码制转换与停流控制，再把有效码送到 [ad4080_top](../rtl/ad4080_top.sv)。当前 [v21 顶层](../../../rtl/v21/adc_digital_ip_v21.sv) 的交付范围是 ADC 数字链。DSP 的测试台通过独立的数字输入驱动适配层。

| 事项 | 适配层合同 |
| --- | --- |
| 时钟与复位 | 转换结果与控制输入均在 `clk` 域。`rst_n`、`sar_epoch_clear` 和 `dsp_restart` 共同控制 DSP 复位，系统负责安全释放。 |
| 码制 | 进入 DSP 时翻转最高位，将 20 位 offset-binary 转为补码。并行输出再翻转一次。 |
| 接受与拒绝 | `sample_accepted`、`sample_rejected` 和对应 ID 随输入事件给出。调用方按有效位采样，入口没有反压。 |
| 停流 | 有效结果的任一 flags、模拟 sticky 错误或 `sar_conversion_fault` 会立即门控接受、输出与 FIFO 读出，并在下一沿锁存 `stream_fault`。转换故障可在没有结果时输入。 |
| 恢复 | epoch clear 或 restart 清 DSP 状态。若上游故障仍有效，当拍继续禁止流入并在下一沿再锁。清除会丢弃在途滤波和捕获状态。 |
| SPI 软件复位 | 用系统提供的 `adc_idle`、捕获完成情况和正在使用的数据接口决定资格。寄存器 SPI 与并行结果口各有独立职能。 |
| 输出资格 | 滤波和 FIFO 可跨多笔输入，入口 ID 和 flags 只描述输入事件。窗口、逐输出来源 ID 与实际接口时序仍需由接入方绑定。 |

已登记的数字适配测试为 [sar20_dsp_integration_tb](../sim/tb/sar20_dsp_integration_tb.sv)，覆盖合成输入的码制、握手和故障恢复。所用命令见 [组件验证](tooling.md)。原 SAR→DSP 整机试验的源、输入条件及计数见 `git show 674b0d8:subsys/ad4080_dsp/docs/SAR20_INTERFACE.md`。
