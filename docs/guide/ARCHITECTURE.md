# Python 数字模型的数据流

公开 API 和 CLI 调用同一份生产事务模型。每笔样本保留实际片号、写入命令、采样和转换开关、权重、ADC2 原码与输出。对应的生产顶层和定点格式见[工程合同](../CONTRACT_CURRENT.md)与[RTL 导航](../../rtl/README.md)。

| 环节 | 代码采用的规则 |
| --- | --- |
| 受理和片选 | 每次调用从复位和已提交的完整加载表开始。每次受理同时冻结旧 ACQ 片号并推进片池，qid 依次为 0、1。测试强制字也消费一次片内字序。[片池](../../src/adi_model/sched18_rtl.py) · [扰动字源](../../src/adi_model/control/dither_word.py) |
| SADC 与 RDAC | 理想量化器接输入和低段 L，用 Flash 及逐位门限产生已决提交。RDAC 采样时接完整 T，转换时用 c+U 经 DEM 和 bridge 生成 post。每个事件单独保留写门，拒绝 512 时无写入。[事务算术](../../src/adi_model/digital_conversion.py) · [开关参考](../../src/adi_model/dem_dither.py) |
| 宏的输入与返回 | 使用实际器件 Ceff/Cf、Vin、采样开关和转换开关正向生成余差。ADC2 取半开区间的箱号，越窗回零并另报范围标志。默认以名义权重、零失配、零噪声作为可复核工况。 |
| 数字校正与出码 | 片内加载表按同一组物理片号寻址，计算 G、Wm、Wp。使用 Q24 码箱中心和整数倒数，末级按有符号最近、恰半离零取 20 位码。数字表与实际器件表可以独立提供。[默认模型](../../src/adi_model/defaults.py) |
| 逐拍验证 | 稳态吞吐、反压、双槽同时交接、寄存延迟和排空由真实 RTL 在引脚上检查；事务层比较的是同一笔的功能和值。[逐笔对拍入口](QUICKSTART.md) · [接口回归](../../flow/sim/README.md) |

研究工具仍可使用 [Fig.11 电容研究模型](../../experiments/fig11.py)、[显式模拟装配](../../src/adi_model/simulation/README.md)和[校准研究组件](../../src/adi_model/calibration/README.md)复算原有实验。冻结的研究图保留其模拟条件。[原文与工程差异](../audit/AUDIT_ALGO_VS_SOURCE.md)
