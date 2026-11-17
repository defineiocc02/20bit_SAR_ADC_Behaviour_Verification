# Fig.19 理想端口测试输入

这组固定输入为 `sar20_digital_core` 的 Fig.19 静态组件提供可重复的量化器与 RDAC 节点激励。它以 [fixture_manifest.json](fixture_manifest.json) 声明范围，几何为 63+8，ADC2 为 12 位、0.45 V 跨度。采样节点与参考为理想模型，寄生与噪声置零，系数由名义电容生成。采用 Fig.19 的负等效节点极性。[P] 的源定位与 [E] 的几何、调度及量化器假设见清单。

## 输入之间的约束

| 文件 | 用途 |
| --- | --- |
| [config.json](config.json) · [format.json](format.json) · [binding.json](binding.json) | 固定几何、ADC2 窗、定点格式和实际源码绑定。 |
| [rdac_prefix_table.json](rdac_prefix_table.json) · [homogeneous_code_reference.json](homogeneous_code_reference.json) | 已决前缀、名义码及理想端口参考。 |
| [registers_nominal.json](registers_nominal.json) · [weights_nominal.hex](weights_nominal.hex) · [scalars_nominal.hex](scalars_nominal.hex) | 名义寄存器内容及装载数据；尚未经过这次回放的系数训练。 |
| [source_control_binding.sv](source_control_binding.sv) · [source_acquisition_binding.sv](source_acquisition_binding.sv) | DUT 消费的转换、采集控制逻辑。 |

该输入组与 v21 交付合同的 16+4 开关掩码、13 位 ADC2 分属两条路线。它也不同于行为模型中的 17/5 几何。前缀表、绑定、定点格式和寄存器内容一起决定可比较的轨迹，单独替换几何或位宽会破坏对应关系。 [工程合同](../../../../docs/CONTRACT_CURRENT.md) · [RTL 导航](../../../../rtl/README.md)

## 回放步骤

1. 在仓库根目录按 [CI 工作流的 Fig19 static component and public replay](../../../../.github/workflows/ci.yml) 运行 [测试模块](../../../tests/integration/test_source_bound_replay.py)。它需要 Verilator，执行 64 个扰动字的静态 DUT 捕获、同一模型回放和损坏轨迹拒绝测试。
2. 单独生成组件输出时，可使用 [run_fig19_rtl_calibration.py](../../../../tools/run_fig19_rtl_calibration.py) 的 `--component-only`，显式指定新建的仓库外 `--out`。脚本的默认 `--fixture-dir` 为本目录。
3. 若要训练或替换 fixture，使用一致的几何、前缀、绑定、定点系数和装载映像，分别验证生成、训练与留出集。现有静态回放只能支持其显式输入范围。
