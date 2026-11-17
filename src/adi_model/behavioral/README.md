# 采样、参考与残差行为模型

本目录提供采样节点、参考轨、残差放大器、ADC2 和噪声的 Python 模型。模型形状、节点单位与异常条件见各实现。名义值与工程假设集中在 [DefaultParameters](../defaults.py)，完整记录由 [run_pipeline](../simulation/pipeline.py) 生成。

| 环节 | 输入、输出和证据范围 |
| --- | --- |
| 采样与电荷 | `sampler.capture` 生成 SADC 和 RDAC 的采样记录。采样噪声使用等效噪声电容，采样电容和码本转换电容各自入账。 [采样](sampler.py) · [电容口径](../topology/README.md#caps) |
| 参考轨 | 逐事件电荷与显式参考建立参数可用于受控实验。论文给出粗参考和精参考切换的机制，[A]。参考建立时间常数需要按输入条件给定，[E]。 [电荷](reference_charge.py) · [建立](reference.py) |
| 普通 AZ | 每笔使用自己的样本号和保持时间。`residual_v` 是加入 RA 输入的剩余误差，模型输出保留原样本数。相邻样本配对由 CAAZ 扩展提供。 [普通 AZ](ra_az.py) · [CAAZ 扩展](../../../subsys/extensions/caaz/README.md) |
| RA 与 ADC2 | 残差放大与后级量化分别计算。ADC2 静态核使用显式位数、半开或闭区间以及 midrise 箱中心。默认参数为 **13 位**，输入窗口是 `[-0.1875, +0.5625) V`。半开窗口的上端点夹到最后一码并置越界标志。 [残差放大](ra.py) · [量化](adc2.py) · [工程合同](../../../docs/CONTRACT_CURRENT.md) |
| TRACK 与 AUX | TRACK 的候选预置按源结果可用时间和预置截止时间检查。AUX 有独立模型，实际注入网络与采集绑定仍需明确提供。 [TRACK 判据](../control/track_aux.py) · [辅助输入](../control/aux_input.py) |
| 噪声 | 采样、RA 输出端和低频噪声分别建模。默认 RA 输出噪声参数是工程预算，[E]。对比时应固定片选、失配和激励，并记录启用的噪声源。 [噪声](noise.py) · [低频状态](low_frequency_noise.py) |

`DefaultModel` 提供静态基线。参考、RA、ADC2 的动态波形需要显式相位和初态。用假设极点重算的波形适合检查模型响应，器件建立精度、动态带宽降噪及跨笔记忆仍需电路或测量证据。原件对照和逐图输入见 [机制索引](../../../docs/mechanisms/README.md) 和 [行为验证图册](../../../docs/figures/20261117/README.md)。

在仓库根目录使用已安装测试依赖的 Python 环境运行：

~~~bash
PYTHONPATH=src:flow python -m pytest -q flow/tests/unit/test_ra_az.py flow/tests/unit/test_adc2_codes.py flow/tests/unit/test_reference_charge.py
~~~
