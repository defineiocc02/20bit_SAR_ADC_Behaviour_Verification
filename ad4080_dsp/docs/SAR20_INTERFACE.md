# SAR20 与 AD4080 数字子系统接口契约

`rtl/sar20_ad4080_top.sv` 是独立 DSP 的项目接入顶层；其 `sar_*` 端口接
`sar20_digital_core` 的 `dout`、`dout_valid`、`dout_sample_id`、
`dout_flags` 和粘滞 `analog_ovf`，`sar_epoch_clear` 接 `cfg_clear_valid`。原 `ad4080_top` 仍可
按原来的补码接口独立使用。项目组合顶层 [`sar20_system_top.sv`](../rtl/sar20_system_top.sv)
实例化实际 SAR 数字核与该 DSP 接入层，并直接完成这些信号的连接；模拟比较器、
溢出及其他外部输入仍由系统提供。也可以不使用组合顶层，由系统自己接线。

| 项 | 契约 |
|---|---|
| 时钟与复位 | 所有 `sar_*`、清除和 DSP 控制与同一 `clk` 同步；没有 CDC。复位可异步置位，系统必须同步释放并让同步复位的 SAR 采到复位。 |
| 码制 | SAR 和本封装并行输出都是 20 位 offset-binary；DSP 内部和串行 payload 为二进制补码。入口和并行出口各翻转一次最高位。 |
| 输入事件 | `sample_accepted` 和 `sample_rejected` 随输入有效事件给出；对应 ID 和标志在**输入侧**有效，不是滤波或 FIFO 输出的标签。无 ready/backpressure。 |
| 标志 | `sar_flags` 顺序为 `{clip_high, clip_low, adc2_ovf, gain_err, acc_ovf}`；其中 `adc2_ovf` 是数字译码标志。任一位置位都拒绝本样本并锁存 `stream_fault`；后续事件也拒绝，`rejected_after_fault` 区分后续拒绝。 |
| 模拟异常 | SAR 现提供与结果 ID 对齐的 `dout_analog_bad`，但本 DSP 接入层尚未消费它；当前 `sar_analog_ovf` 独立于 `sar_flags`，覆盖 SAR 捕获的 RA 饱和、RDAC/ADC2 模拟越界。该粘滞位一旦置位立即停止输入和输出并锁故障；它不携带出错样本 ID，因此不能据此逐笔归因。 |
| 故障 | 有错误的同一周期以及故障保持期间，封装抑制并行输出 valid、FIFO 读使能及串行输出；先前已进入 DSP 但尚未输出的数据也不再承诺交付。`sample_rejected` 不能由软件错过，若需软件读取须在系统外实现事件队列。 |
| 重启/换配置 | `dsp_restart` 或 `sar_epoch_clear` 置位会复位整个 DSP，包括滤波、FIFO、寄存器和输入故障；输入事件在复位期间忽略。单独 `dsp_restart` 不清 SAR 的模拟粘滞位，未清时会再次阻断；`sar_epoch_clear` 须覆盖上升沿，清 SAR 状态并使 SAR 系数失效。释放后须重新配置。此契约不提供无损切换。 |
| DSP 饱和 | `correction_clip_low/high` 是**校正级**同拍事件；经过抽取和 FIFO 后不携带逐结果标签。它们与 SAR 输入侧标志分开。 |
| 滤波和 FIFO | 每个 DSP 输出可能聚合多个输入或经 FIFO 延迟；当前没有随输出对齐的 sample ID、输入 flags 或完整窗口标记。使用这些模式必须由上层自行保留/解释元数据，不能假定一个输出对应一个输入。 |
| 源文件与 FIFO | 工程组合时同时编译 `rtl/rtl_sources.f` 和 `ad4080_dsp/rtl/*.sv`，并加入 `rtl/params` 头文件目录；标准回归入口已做此事。默认 FIFO 深度为 16384；测试中较小深度只用于 FIFO 关闭的直通场景，不代表全部捕获模式可参数化。 |
| 串行吞吐 | 单 lane 每帧需要 20 次 `bit_en`，而结构核按 16 个 `clk` 周期产生一个结果；当前发送器只有一个待发数据寄存器、没有反压，连续输入可覆盖旧样本。`bit_en` 恒高时 `dco` 也恒高。FIFO 读出数据未接入串行发送器。不能把该数字原型当成 40 MS/s 无损 LVDS 接口。 |
| 速度 | `40 MS/s` 是系统目标设置；SAR 结构使用 16 相位时要求相应时钟预算。该封装没有目标工艺综合、STA 或 CDC/RDC 签核，不保证 40 MS/s 实际时序。 |

接口验证包含码制边界、正常输入、各错误位、故障保持、重启和换配置等场景。
实际验证记录应引用运行日志，不把该文档视为验证结果。
