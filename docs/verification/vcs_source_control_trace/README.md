# 2026-11-12 source-control 组件 trace

这组三份 CSV 保存一次 VCS W-2024.09-SP1 的数字组件仿真。运行记录为 `SOURCE_CONTROL_COMPONENT_COMPLETE observations=56 outputs=54 checks=368 epochs=2`，仿真间隔 6.725 µs。夹具声明 18 个片、每次 8 个活动片、每片 2 个主单元和 1 个子单元，以及 6 位 ADC2。通过范围是这些输入上的样本归属、组件协议和算术。

| 文件 | 内容 |
| --- | --- |
| [controls.csv](controls.csv) | 控制请求、消费值和采集／转换掩码 |
| [events.csv](events.csv) | 观测、退休、标志及状态字 |
| [residues.csv](residues.csv) | 余差对应的片组和实际开关掩码 |

当前 [trace 检查器](../../../tools/check_source_control_trace.py) 要求 `controls.csv` 带有 `input_chop` 列，归档表使用更早的表头。复核旧运行请使用当时的输入和检查器。新跑请按 [组件夹具](../../../flow/sim/fixtures/source_control_component/) 和 [CI 步骤](../../../.github/workflows/ci.yml) 导出输入包、采集新 trace，再用对应的输入和检查器验收。更完整的工作流见 [验证目录](../../../flow/verification/README.md)。
