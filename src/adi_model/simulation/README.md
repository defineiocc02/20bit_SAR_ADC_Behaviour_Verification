# Python 仿真入口与状态归属

静态试验从 [default_model](../defaults.py) 起步，得到固定制造种子、名义译码器和可重放的噪声与调度。需要实际掩码、逐事件参考载荷或自定义节点条件时，使用 [run_pipeline](pipeline.py) 显式提供输入。使用说明见 [快速指南](../../../docs/guide/QUICKSTART.md)。

| 阶段 | 同一笔记录中的数据 |
| --- | --- |
| 采样与判决 | 波形、样本号、采集片、初始主节点／子节点电压与采样时刻。将 SADC 输入量化为粗码。 |
| 片组与选择 | [PhysicalSlicePool](slice_pool.py) 管理物理片的采集和转换资格。Mapper、DEM 状态、采样连接及掩码由各 binding 提供。 |
| 残差与细码 | 用实际掩码和参考轨求电荷与残差。RA 与 ADC2 输出细码，以及各自限幅或窗口越界标志。 |
| 数字输出 | [SimResult](sim.py) 保存观测、有效配置和标志。浮点诊断与整数 `to_codes` 对应不同输出域。后者需要显式格式和冻结或显式系数。 |

[引擎](pipeline_engine.py) 在推进之前校验输入。`dither_program` 与 `dither_port` 二选一并须与开关标志一致。动态参考、RA、ADC2 选项要求对应电路绑定和显式初态。连续采集、动态串扰或带 TRACK/AUX 的组合需要匹配的采集态接口。缺失条件会显式拒绝。

制造电容跟随模型实例，`Config` 属于调用方，`DigitalState` 保存数字估计与冻结校准。每次显式运行重置池的电气资格，生成逐笔采集记录。名义标量诊断超出适用条件时，`out` 可包括 `NaN`，并由 `nominal_diagnostic_available` 与 `nominal_diagnostic_reasons` 给出原因。动态电气观测仍可从相应 trace 阅读。

[事务轨迹](transaction_trace.py) 支持 `(epoch, sample_id, event_index)` 键和样本计数守恒。`build_trace_from_result` 目前从普通结果导出每笔一行终态码，`event_index=0`。逐周期握手和实际逐事件采样由专门夹具验证。 [RTL 证据边界](../../../flow/verification/README.md)

从仓库根目录检查模型接口和事务键：

~~~bash
PYTHONPATH=src:flow python -m pytest -q flow/tests/integration/test_physical_pipeline.py flow/tests/unit/test_transaction_trace.py
~~~
