# 片选、逐事件跟随、DEM 与扰动

生产数字事务使用本包的片内字源，并在[默认模型](../defaults.py)中连接片选、已决事件、DEM、采样预置及定点重构。专利原图研究的分立组件各按其物理几何解释。[数字合同](../../../docs/CONTRACT_CURRENT.md) · [原文差异](../../../docs/audit/AUDIT_ALGO_VS_SOURCE.md)

<a id="schedule"></a>
## 片组调度

18 片池的活动组每组 8 片。采集与转换的物理片号由调度计划显式给出。`round_robin_plan` 是行为对照用轮转计划。v21 `sched18` 的整数镜像另见 [sched18_rtl.py](../sched18_rtl.py)，其已比对的输入向量和图中采用的周期见 [F01](../../../docs/figures/behavioral/README.md)。失配绑定物理片 ID，不能用逻辑 slot 的覆盖率代替物理覆盖率。 [计划](dem3d.py)

<a id="follower"></a>
## 逐事件跟随

SADC 和 RDAC 在论文架构中分开，[A]。`follower.py` 接收指定的已决前缀，验证事件输出不会随未来位改变，并计算同一终态的参考电荷。轨电压、实际片组、初态和事件时间属于试验条件。净电荷比较与参考轨峰值负载是不同量。Python 管线中需要调用者提供逐事件码表的路径使用 [Mapper](../topology/mapper.py)。v21 的事务事件由 [sar_evt_v21.sv](../../../rtl/v21/sar_evt_v21.sv) 独立处理。 [跟随模型](follower.py) · [图中算例与数据](../../../docs/figures/behavioral/README.md)

<a id="dem"></a>
## 阵列映射与 DEM

- `fig11_dem.map_fig11` 是受限映射例，`dem3d.fig11_masks` 接收批次、相位与片身份。Fig.11 研究夹具以 17 主位／5 子位装配后者。公共生产入口默认开启二维 DEM，使用 16 主位／4 子位以及[生产掩码参考](../dem_dither.py)。 [单笔映射](fig11_dem.py) · [批次掩码](dem3d.py) · [默认配置](../defaults.py)
- `reserved_column.py`、`shuffle_gate.py` 和 `cross_slice_mux.py` 提供对专利 Fig.19–21 的保留列、信号门及跨片列分配的分立计算。三个算子用于对应图的独立实验。覆盖率只能说明本轮访问到哪些元件，噪声整形需要频谱和相关性证据。 [专利机制对照](../../../docs/mechanisms/README.md)
- 生产路径由 `digital_conversion.conversion_mask` 先调用 `post_mask` 译码，再调用 `bridge_swap` 交换选定开关；两步都在 `adi_model.dem_dither` 中实现。 [整数参考](../dem_dither.py) · [RTL](../../../rtl/dem_mask_v3.sv)

<a id="dither"></a>
## 分路扰动

生产数字参考用 [DitherWordGenerator](dither_word.py) 在每个受理沿从片内序列取字。所选 SADC 获取低段，八槽 RDAC 得到完整预置；SAR 将 U 加到已决前缀，末级只扣同一笔的实际 Wp。[开关译码](../dem_dither.py) · [数字事务](../defaults.py)

`dither.py` 与 `dither_port.py` 是专利研究使用的单独算子。它们的七片分数权及减码语义由 [Fig.11 研究夹具](../../../subsys/experiments/fig11.py)显式绑定。[原文对照与实测](../../../docs/audit/AUDIT_ALGO_VS_SOURCE.md)
