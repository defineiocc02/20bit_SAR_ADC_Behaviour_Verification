# Python 类型、目标配置与整数格式

这里保存模型的规格类型、事件身份、具名 profile 和整数算术接口。v21 的规格与执行优先级以 [工程合同入口](../../../docs/CONTRACT_CURRENT.md) 为准。

| 文件 | 职责 |
| --- | --- |
| [specs.py](specs.py) | `TopologySpec`、单样本 `EventRecord`、`SampleContext`、开关命令及复现身份记录。输入显式携带 profile、拓扑及连接身份。 |
| [events.py](events.py) | 事件顺序及样本关联的低层检查。 |
| [profiles.py](profiles.py) | 注册具名目标。`load_profile(name)` 按精确名称查询，未知名称抛 `KeyError`。 |
| [fixed_point.py](fixed_point.py) | 应用指定的寄存器格式和有效电容权重。批次 `DigitalObservation`、`FrozenCalibration` 从 [weight_calibration.py](../calibration/weight_calibration.py) 导入，`specs.py` 的同名类型描述单样本或系数身份，调用时按模块区分。 |

## 具名目标

| 名称 | 用途 |
| --- | --- |
| `adi_isscc2024_40m_6vpp` | 40 MS/s、±3 V、18 片池／8 片活动，Python 控制几何 17 主位／5 子位，ADC2 13 位。窗口和模型默认值见 [defaults.py](../defaults.py)。 |
| `engineering_10m_10vpp` | 10 MS/s、±5 V、12 位 ADC2 的归一化机制例。此 profile 的 `Cf=0`，只用于归一化计算。 |
| `patent_fig19_7slice` | `US10505561B2` 的七片扰动机制例。profile 中时钟、输入范围和 ADC2 位数为实验标签。 |

## 整数运算边界

`DefaultParameters.format` 选择权重 Q30（48 位）、归一化电压 Q32（64 位有符号）、96 位受检累加与 20 位 offset-binary。`FixedPointFormat` 接受显式格式。系数、电压项和 ADC2 码箱中心按 ties-to-even 量化，最终出码用 floor 并夹到合法码域。调用方若给物理增益，码箱值的增益换算也使用整数就近取偶。累加值须落在 `[-2**95, 2**95)`，越界抛错。模拟越界标志和最终码裁剪分列。

v21 的输出由自己的 [定点模型](../../../tools/recon_oracle.py) 验收。Python 格式表与历史 RTL 夹具可从 [验证入口](../../../flow/verification/README.md) 查看。
