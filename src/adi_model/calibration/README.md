# 观测、校准与重构

本目录从已知输入和可见数字观测估计系数。训练、冻结和使用系数是三个阶段。公开资料说明片外取得系数、片上执行修正，[A]。逐码表及逐单元拟合是本工程的实现选择，[E]。

| 用途 | 实现与条件 |
| --- | --- |
| 逐码修正 | `fit_code_correction` 用训练记录估计各粗码的条件均值误差。训练与评估须匹配模式和码本。用本片、异片和独立记录分别检查系数迁移。 [实现](mask_calibration.py) · [F11](../../../docs/figures/20261117/F11_calibration_generalization.png) |
| 有效单元权重 | `fit_unit_weights` 从明确的静态电荷观测拟合每片各单元的有效 `C/Cf` 和一个全局偏移。冻结记录含规格、观测域、秩、条件数、标准误差与训练摘要。 [实现](weight_calibration.py) |
| 浮点反解 | `FrozenCalibration.reconstruct` 按活动片和同笔观测应用冻结权重。`ElementWeightCalibration` 提供显式参考轨子集的反例和计算路径。 [冻结权重](weight_calibration.py) · [显式参考轨](mask_calibration.py) |
| Python 整数输出 | `FixedPointReconstructor` 应用显式格式与冻结或名义权重，生成 20 位 offset-binary，并分别保留模拟越界和数字裁剪。 [格式与实现](../contracts/fixed_point.py) |

## 观测与辨识

批次 `DigitalObservation` 定义在 `weight_calibration.py`，典型输入包括 `slice_ids[N,A]`、主／子掩码、采样连接 `signal_mask`、已知电压项、ADC2 整数码、`sample_ids` 和溢出标志。转换开关选哪条参考轨由主／子掩码决定，采样时哪些单元接入信号由 `signal_mask` 决定。用同一份表代替这两个输入会改变重构分母。观测身份与冻结系数规格不符、增益非正或训练矩阵不足以识别参数时，入口报告错误。

取 `a` 为采样信号资格、`t` 为已知电压项。在静态线性条件下，训练式与反解为 `fine = offset + Σw·(a·x+t)` 和 `x̂ = (fine−offset−Σw·t)/Σw·a`，两者都沿本笔实际活动片求和。秩和条件数是这批观测的诊断量。能否辨识还取决于输入、实际掩码和噪声条件。

## 与 RTL 的接口

Python 的归一化 Q32 输出和 v21 的 Q24 整数反解各按自己的合同验收。v21 在主机侧准备系数，配置口写入并提交后供事务读取。旧 `recon_core` 验证夹具的结果按原设计身份留档。 [v21 合同](../../../docs/CONTRACT_CURRENT.md) · [配置接口](../../../docs/v21/CONFIG_CONTRACT.md) · [分层验证](../../../flow/verification/README.md)

从仓库根目录运行以下纯 Python 用例：

~~~bash
PYTHONPATH=src:flow python -m pytest -q flow/tests/unit/test_element_reconstruct_equation.py flow/tests/unit/test_weight_calibration_closes.py flow/tests/unit/test_fixed_point.py
~~~
