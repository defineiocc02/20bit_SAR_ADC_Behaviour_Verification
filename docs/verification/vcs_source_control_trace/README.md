# 2026-11-12 source-control 组件 trace

这组三份 CSV 保存一次 VCS W-2024.09-SP1 的数字组件仿真。运行记录为 `SOURCE_CONTROL_COMPONENT_COMPLETE observations=56 outputs=54 checks=368 epochs=2`，仿真间隔 6.725 µs。夹具声明 18 个片、每次 8 个活动片、每片 2 个主单元和 1 个子单元，以及 6 位 ADC2。通过范围是这些输入上的样本归属、组件协议和算术。

| 文件 | 内容 |
| --- | --- |
| [controls.csv](controls.csv) | 控制请求、消费值和采集／转换掩码 |
| [events.csv](events.csv) | 观测、退休、标志及状态字 |
| [residues.csv](residues.csv) | 余差对应的片组和实际开关掩码 |

上述 CSV 属于该次历史运行。后续检查器的表头要求 `input_chop`，旧表应按当次源与格式解读。检查器的后续版本可用 `git show 674b0d8:tools/check_source_control_trace.py` 查看。保留的 [独立组件夹具](../../../flow/sim/fixtures/source_control_component/) 供输入包和算术检查使用。当前集成 ADC 的运行与证据见 [v21 验证入口](../../../flow/verification/README.md)。
