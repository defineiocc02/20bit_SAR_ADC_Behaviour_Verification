# 片选、逐事件跟随、DEM 与扰动

这个包保存 Python 控制算法、专利机制对照和可插入仿真的显式 binding。模型与 v21 在几何、事件入口和 dither 符号上各有合同，实验图注应注明使用哪条路径。 [v21 工程合同](../../../docs/CONTRACT_CURRENT.md) · [机制索引](../../../docs/mechanisms/README.md)

<a id="schedule"></a>
## 片组调度

18 片池的活动组每组 8 片。采集与转换的物理片号由调度计划显式给出。`round_robin_plan` 是行为对照用轮转计划。v21 `sched18` 的整数镜像另见 [sched18_rtl.py](../sched18_rtl.py)，其已比对的输入向量和图中采用的周期见 [F01](../../../docs/figures/20261117/README.md)。失配绑定物理片 ID，不能用逻辑 slot 的覆盖率代替物理覆盖率。 [计划](dem3d.py)

<a id="follower"></a>
## 逐事件跟随

SADC 和 RDAC 在论文架构中分开，[A]。`follower.py` 接收指定的已决前缀，验证事件输出不会随未来位改变，并计算同一终态的参考电荷。轨电压、实际片组、初态和事件时间属于试验条件。净电荷比较与参考轨峰值负载是不同量。Python 管线中需要调用者提供逐事件码表的路径使用 [Mapper](../topology/mapper.py)。v21 的事务事件由 [sar_evt_v21.sv](../../../rtl/v21/sar_evt_v21.sv) 独立处理。 [跟随模型](follower.py) · [图中算例与数据](../../../docs/figures/20261117/README.md)

<a id="dem"></a>
## 阵列映射与 DEM

- `fig11_dem.map_fig11` 是受限映射例，`dem3d.fig11_masks` 接收批次、相位与片身份。行为默认模型挂载后者作为 17 主位／5 子位的工况映射。默认 `dem_enable` 关闭。 [单笔映射](fig11_dem.py) · [批次掩码](dem3d.py) · [默认配置](../defaults.py)
- `reserved_column.py`、`shuffle_gate.py` 和 `cross_slice_mux.py` 提供对专利 Fig.19–21 的保留列、信号门及跨片列分配的分立计算，默认转换链使用 `fig11_masks`。覆盖率只能说明本轮访问到哪些元件，噪声整形需要频谱和相关性证据。 [专利机制对照](../../../docs/mechanisms/README.md)
- v21 的名义 3+3+3、桥接和 guard 码使用另一份整数约定。针对这份约定生成参考向量时，使用 `adi_model.dem_dither`。 [整数参考](../dem_dither.py) · [RTL](../../../rtl/dem_mask_v3.sv)

<a id="dither"></a>
## 分路扰动

Fig.19 将低段位接入前级量化器端口、上段位作用于 RDAC 命令，[P]。Python 提供两个互斥的工程选项，[E]：

| 选项 | 实际接口 |
| --- | --- |
| [Fig19DitherProgram](dither.py) | 静态等效码移位。SADC 侧用 `x−L`，RDAC 侧保存 `x−T`，命令 `k=c−U`。需要对应的码本、逐事件前缀和采集绑定。整数命令越界时拒绝，并保存诊断。 |
| [DitherPort](dither_port.py) | 显式电荷口。前端采样消费 lower 段。当前引擎将实际 RDAC 命令计算为 `c−U`，电荷和已知项按该实例单独记账。 |
| [v21 v2.1](../../../docs/ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md) | 数字工程约定为 `k=c+U`。位权、允许范围与去扰定义均按合同。 |

启用扰动前检查上下游码域及 ADC2 的工作窗口。七片专利例、Python 等效支路和八片 v21 开关合同分别说明来源。模型越界结果及适用条件见 [F03–F05 图注](../../../docs/figures/20261117/README.md)。

从仓库根目录运行控制与因果回归：

~~~bash
PYTHONPATH=src:flow python -m pytest -q flow/tests/unit/test_dem3d.py flow/tests/unit/test_follower.py flow/tests/unit/test_dither_split_causality.py flow/tests/unit/test_dither_charge_port.py
~~~
