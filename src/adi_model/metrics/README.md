# 静态误差、频谱与噪声指标

[metrics.py](metrics.py) 是指标实现，[metrics_lsb20.py](metrics_lsb20.py) 保留旧导入名。[benchmarks.py](benchmarks.py) 记录公开结果的比较口径。模型指标由调用方明确选择的记录计算。

| 指标 | 计算及报告口径 |
| --- | --- |
| 电平统计 | `static_test` 对每个输入电平分别记录均值误差 `mu_e` 和标准差 `sigma_e`。重复次数及输入范围由试验配置给出。 |
| INL 类残差 | `inl_from_mean_error` 从静态均值误差去除最小二乘直线，再用指定步长归一。稀疏电平扫描只覆盖实际输入点，逐码 INL 和码密度 DNL 需要相应码覆盖。 |
| SNDR、SNR、SFDR | `sine_fit_metrics` 以已知基频拟合。SNDR 保留全部残差，SNR 扣除可辨识的指定谐波，SFDR 从扣除基波后的频谱搜索。报告时注明采样率、记录长度、激励频率、窗口和可靠性标志。 |
| 幅度谱、PSD 与带内噪声 | `spectrum_dbfs` 给每个 bin 的幅度。`power_spectral_density` 给单边 `V²/Hz`，用 `integrate_noise_band` 按 bin 宽度积分。信号和谐波的剔除规则由调用方提供。 |

同一次试验应标明输入和输出节点、输出是浮点估计还是整数码解码，以及越界处理。`summarize` 要求完整有限记录，并分别统计 RA 和 ADC2 标志。样本组别、制造种子及图所用数据见 [行为验证图册](../../../docs/figures/behavioral/README.md)。

从仓库根目录检查谱量口径及相容入口：

~~~bash
PYTHONPATH=src:flow python -m pytest -q flow/tests/unit/test_spectral_contract.py
metrics_dir=$(mktemp -d)
PYTHONPATH=src python tools/check_metrics_equivalence.py --json "$metrics_dir/metrics.json"
~~~
