# SAR20 与 AD4080 数字子系统接口契约

`rtl/sar20_ad4080_top.sv` 是独立 DSP 的项目接入顶层；其 `sar_*` 端口接
`sar20_digital_core` 的 `dout`、`dout_valid`、`dout_sample_id`、
`dout_flags`，`sar_epoch_clear` 接 `cfg_clear_valid`。原 `ad4080_top` 仍可
按原来的补码接口独立使用。转换核与 DSP 是两个实例，由上层接线。

| 项 | 契约 |
|---|---|
| 时钟与复位 | 所有 `sar_*`、清除和 DSP 控制与同一 `clk` 同步；没有 CDC。复位可异步置位，系统必须同步释放并让同步复位的 SAR 采到复位。 |
| 码制 | SAR 和本封装并行输出都是 20 位 offset-binary；DSP 内部和串行 payload 为二进制补码。入口和并行出口各翻转一次最高位。 |
| 输入事件 | `sample_accepted` 和 `sample_rejected` 随输入有效事件给出；对应 ID 和标志在**输入侧**有效，不是滤波或 FIFO 输出的标签。无 ready/backpressure。 |
| 标志 | `sar_flags` 顺序为 `{clip_high, clip_low, adc2_ovf, gain_err, acc_ovf}`。任一位置位都拒绝本样本并锁存 `stream_fault`；后续事件也拒绝，`rejected_after_fault` 区分后续拒绝。该保守策略防止异常保持的旧 `dout` 当作新值、或跨缺样本继续滤波。 |
| 故障 | 有错误的同一周期以及故障保持期间，封装抑制并行输出 valid、FIFO 读使能及串行输出；先前已进入 DSP 但尚未输出的数据也不再承诺交付。`sample_rejected` 不能由软件错过，若需软件读取须在系统外实现事件队列。 |
| 重启/换配置 | `dsp_restart` 或 `sar_epoch_clear` 置位会复位整个 DSP，包括滤波、FIFO、寄存器和输入故障；输入事件在复位期间忽略。释放后须重新配置 DSP。此契约不提供无损切换。 |
| DSP 饱和 | `correction_clip_low/high` 是**校正级**同拍事件；经过抽取和 FIFO 后不携带逐结果标签。它们与 SAR 输入侧标志分开。 |
| 滤波和 FIFO | 每个 DSP 输出可能聚合多个输入或经 FIFO 延迟；当前没有随输出对齐的 sample ID、输入 flags 或完整窗口标记。使用这些模式必须由上层自行保留/解释元数据，不能假定一个输出对应一个输入。 |
| 速度 | `40 MS/s` 是系统目标设置；SAR 结构使用 16 相位时要求相应时钟预算。该封装没有目标工艺综合、STA 或 CDC/RDC 签核，不保证 40 MS/s 实际时序。 |

接口验证包含码制边界、正常输入、各错误位、故障保持、重启和换配置等场景。
实际验证记录应引用运行日志，不把该文档视为验证结果。
