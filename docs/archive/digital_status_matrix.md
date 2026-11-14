# 数字工作状态与证据矩阵

本表覆盖[主清单](../rtl/rtl_sources.f)20模块、15个DSP模块、Python机制和三个跨层接口。[A表](current_digital_source_map.md)给原文位置，[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)给接口，[复现](REPRODUCING.md)给实际命令。当前受测字节见相邻源／工具SHA，af8／9bdf的历史结论不自动继承。

| 能声称 | 不能声称 |
| :--- | :--- |
| 指定无dither Python静态校准有独立留出；当前DUT的指定静态闭环有同池、同序计划与独立整数对拍。 | 20位ENOB、完整动态／含噪／端点／INL-DNL、真实独立参考及硬件观察输送已经通过。 |
| Fig.19受限等效的双采集端口、64字、实际prefix和一次数字重构在指定工作点核验；早prefix非法被拒。 | 原文3D DEM、专用dither电容／开关网络、参考建立、连续全幅或真实CAAZ组合已经实现／验证。 |
| DSP指定14场景、5严格profile与已报告控制fault锁停／恢复有实际证据。 | 所有器件模式／整链滤波路由、任意未报告ID缺口、无损停采、物理数据PHY或CAAZ+DSP已经闭合。 |
| 组件、完整DUT有限窗和归档证据各自标识版本、输入与适用域。 | 由仿真、静态守卫、有限BMC、旧FPGA或代理记录宣称综合／STA、CDC/RDC、PEX或signoff。 |

证据等级只用**静态审计／smoke／bench 对拍／形式验证／综合与 STA／归档历史**。bench 对拍指所列实际用例及其输出、接口或检查点；整链对拍不证明单模块每个分支。CAAZ两叶在主学习用例未激活；当前独立8包数学组件有实际数字对拍，旧7包数字见证不提升当前等级。

默认dither与源3D单元DEM都不启用；源3D尚未实现。64字结果仅支持显式静态等效与指定计划。[13项待证分组](STATUS.md#按依赖排序的未闭合项)区分未实现、缺少整链验证和物理证据；原清单见[机制账本](../mechanism_inventory.json)。

## 三项交付物与扩展

| 对象 | 当前状态 | 最高证据等级 | 未完成项 | 入口 |
| :--- | :--- | :--- | :--- | :--- |
| ADC行为模型 | 集中推断默认可实跑；当前指定静态路径有独立留出 | bench 对拍 | 真实参考／开关、动态、噪声传递及目标工艺 | [默认层](../src/adi_model/defaults.py)、[Python数据](readme/data/source_bound_static_calibration.json) |
| 静态失配校准 | 当前Python无dither RMS85.829→2.07577 µV；当前DUT受限静态RMS97.04090→1.99287 µV，独立留出各512；中幅激励不足拒绝 | bench 对拍 | 真实观察输送、参考准确度、噪声／漂移／非线性；两种激励不用于估计dither增量 | [Python数据](readme/data/source_bound_static_calibration.json)、[DUT与同池重放](readme/data/source_bound_rtl_calibration.json) |
| 数字控制与重构RTL | 20清单模块、ABI2显式输入；当前DUT指定有限窗的两端口、事件、733位实例与整数对拍 | bench 对拍 | 真停采／排空／park／模拟idle、硬件读出、激活CAAZ、目标工艺 | [RTL](../rtl/README.md)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| 输出DSP | handoff003核15源，14指定场景与5严格-Wall profile通过；含真实controller fault／restart／epoch恢复 | bench 对拍 | 逐输出元数据、未报告ID缺口、真实停采park后保记录软件复位、实际CNV／数据PHY、逆向系数学习 | [DSP](../ad4080_dsp/README.md) |
| CAAZ | 默认关闭；8包独立组件核对有序标签、取消、无效字和数字配对 | bench 对拍 | 真实共享AZ／CBIAS／H/K及Fig.19、DSP组合，模拟噪声收益 | [组件复现](REPRODUCING.md#caaz数字组件)、[源码对照](current_digital_source_map.md) |

## 20个主清单RTL模块

| 模块 | 交付物／扩展 | 当前状态 | 最高证据等级 | 有效范围与未完成项 | 证据入口 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [adc2_dec](../rtl/adc2_dec.sv) | 交付物：数字控制与重构RTL | 当前静态ADC2原码及码仓中心参与独立整数对拍 | bench 对拍 | 名义ADC2码仓中心与Q格式、舍入、端点检查；12位与窗口是所选规格。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [caaz_cycle_ctrl](../extensions/caaz/rtl/caaz_cycle_ctrl.sv) | 扩展：CAAZ数字控制／配对 | 8包启用组件的56个标签及4个配对结果经独立检查 | bench 对拍 | 仅输入极性、AZ代和偶更新／奇保持资格；精确相位为工程选择，不实现CBIAS保持。 | [源控制bench](../sim/tb/source_control_contract_tb.sv)、[归属checker](../tools/check_source_control_trace.py) |
| [caaz_pair_processor](../extensions/caaz/rtl/caaz_pair_processor.sv) | 扩展：CAAZ数字控制／配对 | 8包组件4个实际配对；独立13周期数学夹具523 bench检查、325字段0失败 | bench 对拍 | 完整重构码后配对和ID／极性／AZ代检查；没有H/K或实际存储噪声观测。 | [配对bench](../extensions/caaz/tb/caaz_pair_vectors_tb.sv)、[整数checker](../tools/check_pair_component_trace.py) |
| [analog_phase_ctrl](../rtl/analog_phase_ctrl.sv) | 交付物：数字控制与重构RTL | 指定有限窗的采集前沿、source握手及已决事件正常 | bench 对拍 | 16相位及注册使能、启动清零是工程时序；真实非交叠、比较器和参考建立由宏验收。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [cal_output_stage](../rtl/cal_output_stage.sv) | 交付物：数字控制与重构RTL | 实际OBS先于同ID OUT；目标码和flags对拍 | bench 对拍 | 提交或保持候选码，随同ID和flags退休；不重算训练或模拟正确性。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [cal_residue_mac](../rtl/cal_residue_mac.sv) | 交付物：数字控制与重构RTL | 当前静态归约乘加参与最终整数码对拍 | bench 对拍 | 重构乘加、受检位宽和溢出处理；不读取物理电容真值。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [cal_sample_context](../rtl/cal_sample_context.sv) | 交付物：数字控制与重构RTL | 实际采集／转换有序ID与同笔mask／word／公共项对拍 | bench 对拍 | 冻结同笔slice、mask、控制word／state、已知项与前代fine归属；不同采集端口不得混笔。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [cal_weight_reduce](../rtl/cal_weight_reduce.sv) | 交付物：数字控制与重构RTL | 实际mask与名义／冻结权重参与同raw整数结果对拍 | bench 对拍 | 按实际物理mask归约有效权重；实现组合算术，不包含系数学习器。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [calib_regs](../rtl/calib_regs.sv) | 交付物：数字控制与重构RTL | 完整权重／三标量装载，指定epoch撤销与重载恢复 | bench 对拍 | 标量、完整性验证和epoch撤销；0x1018旧模式地址退役，不提供别名。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [div_floor](../rtl/div_floor.sv) | 交付物：数字控制与重构RTL | 当前受测量化输出与独立整数floor一致 | bench 对拍 | 有符号floor除法与异常合同；精确数值和延迟需独立整数／RTL检查。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [rdac_drv](../rtl/rdac_drv.sv) | 交付物：数字控制与重构RTL | 全物理行、实际已决事件及非法候选不加载已查 | bench 对拍 | 注册外部绑定给出的实际mask和slice归属；数字意图不代替模拟开关或电平转换。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[实际事件](readme/data/source_bound_fig19_evidence.json) |
| [recon_core](../rtl/recon_core.sv) | 交付物：数字控制与重构RTL | 指定N静态接受／结果ID与独立整数码对拍 | bench 对拍 | 具体分子、Q30/Q32、96位检查、流水和错误退休为本工程选择。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [sar_structural_ctrl](../rtl/sar_structural_ctrl.sv) | 交付物：数字控制与重构RTL | 两采集端口、当前coarse／前代fine及早prefix拒绝已查 | bench 对拍 | 双coarse、共享Flash、已决RDAC事件和前代fine的结构关系；截止沿、12位fine与16tick为工程选择。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[实际事件](readme/data/source_bound_fig19_evidence.json) |
| [sar_trial_ctrl](../rtl/sar_trial_ctrl.sv) | 交付物：数字控制与重构RTL | 指定Flash3/SAR6已决前缀序列和截止资格对拍 | bench 对拍 | seeded逐位试探、已决码、valid／done／cancel协议；不推断真实比较器完成沿。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[实际事件](readme/data/source_bound_fig19_evidence.json) |
| [slice_pool_ctrl](../rtl/slice_pool_ctrl.sv) | 交付物：数字控制与重构RTL | 18/8指定采集／转换有序计划及同笔归属对拍 | bench 对拍 | 8采集／8转换／2空闲及因果归属；具体选择器和启动预热为工程规则，非时偏学习器。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [status_regs](../rtl/status_regs.sv) | 交付物：数字控制与重构RTL | 正常窗口status／sticky为0；指定控制故障可见 | bench 对拍 | 数字错误与粘滞状态读回；不是器件全部寄存器映射或事件队列。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[实际事件](readme/data/source_bound_fig19_evidence.json) |
| [unit_therm](../rtl/unit_therm.sv) | 交付物：数字控制与重构RTL | 实际主／子mask的物理行和位序已核；不等于源3D DEM | bench 对拍 | 检查／传递实际unit mask；已移除旧缺格SID及桥接译码，不宣称源3D DEM。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[实际事件](readme/data/source_bound_fig19_evidence.json) |
| [weight_store](../rtl/weight_store.sv) | 交付物：数字控制与重构RTL | 实际物理索引、名义／拟合系数加载与结果对拍 | bench 对拍 | 仅系数应用位置对齐；物理索引、格式、bitmap及行和缓存为工程实现。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [sadc_enc](../rtl/sadc_enc.sv) | 交付物：数字控制与重构RTL | 指定Flash种子与实际粗码参与7事件、OBS和整数对拍 | bench 对拍 | Flash温度计编码与尺寸守卫；不验证门限、气泡物理成因或孔径。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| [sar20_digital_core](../rtl/sar20_digital_core.sv) | 交付物：数字控制与重构RTL | ABI2双绑定、calobs v2与目标退休；指定控制fault经系统验证 | bench 对拍 | 完整核依赖显式参数、外部source_control_binding和calobs v2；接口并非原文公开RTL。 | [DUT与同池重放](readme/data/source_bound_rtl_calibration.json)、[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |

外部`source_control_binding`与`source_acquisition_binding`是必须的设计输入，不计入仓库`.f`的20个模块；其word／state、真实mask、两采集端口与源SHA必须随导出包绑定。未提供或超出已声明能力时拒绝，数学组件bench不代表原文开关实现。

## 15个DSP扩展模块

| 模块 | 交付物／扩展 | 当前状态 | 最高证据等级 | 未完成项／职责边界 | 证据入口 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [ad4080_dsp_chain](../ad4080_dsp/rtl/ad4080_dsp_chain.sv) | 扩展：输出DSP | bypass链有70＋系统185次全20位比较 | bench 对拍 | 只覆盖unity／OFFSET0与bypass；filter_sel1/2/3完整数值路由未对拍，不能由独立kernel覆盖选择器 | [sar20_dsp_integration_tb](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv#L42)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [ad4080_regs](../ad4080_dsp/rtl/ad4080_regs.sv) | 扩展：输出DSP | 给定binding的实体写读、拒写保值与status检查 | bench 对拍 | 已知CSR子集；不声称完整器件寄存器符合性 | [ad4080_regs_rejection_tb](../ad4080_dsp/sim/tb/ad4080_regs_rejection_tb.sv#L20)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [ad4080_top](../ad4080_dsp/rtl/ad4080_top.sv) | 扩展：输出DSP | native模式01／11两次非空保记录软件reset与事件路由已查 | bench 对拍 | 依赖外部idle authority；不等于真实SAR停采park，整链滤波另验 | [ad4080_top_tb](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L49)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [cnv_ctrl](../ad4080_dsp/rtl/cnv_ctrl.sv) | 扩展：输出DSP | 65项单clk边沿／busy／复位无伪事件检查 | bench 对拍 | 仅已译码逻辑边沿；没有接收PHY、实际SAR启动或tCONV测量 | [cnv_ctrl_contract_tb](../ad4080_dsp/sim/tb/cnv_ctrl_contract_tb.sv#L17)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [event_detect](../ad4080_dsp/rtl/event_detect.sv) | 扩展：输出DSP | top中HI／LO阈值、路由与ALERT／FIFO有3项实际比较 | bench 对拍 | 未覆盖非零迟滞全边界、W1C同沿优先级及物理2CNV延迟 | [ad4080_top_tb](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L114)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [fifo_capture](../ad4080_dsp/rtl/fifo_capture.sv) | 扩展：输出DSP | 32／16384深度有效读／done、事件及已知值顺序检查 | bench 对拍 | 16k未写历史只查有效性／结束，不把未知RAM当零数值；真实SRAM另验 | [fifo_capture_contract_tb](../ad4080_dsp/sim/tb/fifo_capture_contract_tb.sv#L22)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [gpio_ctrl](../ad4080_dsp/rtl/gpio_ctrl.sv) | 扩展：输出DSP | top中GPIO0释放、同步输入回读和SEL6 ALERT有非零检查 | bench 对拍 | 非全SEL扫描；FILTER_SYNC／EXT_EVENT与CDC、电气未验 | [ad4080_top_tb](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L71)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [result_fifo](../ad4080_dsp/rtl/result_fifo.sv) | 扩展：输出DSP | 独立队列scoreboard，2724项逐拍读写／occupancy检查 | bench 对拍 | 受测深度、碰撞与快照合同；存储宏／门级时序另验 | [result_fifo_tb](../ad4080_dsp/sim/tb/result_fifo_tb.sv#L16)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sar20_ad4080_top](../ad4080_dsp/rtl/sar20_ad4080_top.sv) | 扩展：输出DSP | 70字队列比较／1032检查，含flags／无raw故障／restart阻断 | bench 对拍 | 不检测任意未报告ID缺口；输出窗口／ID元数据及系统无损恢复未闭合 | [sar20_dsp_integration_tb](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv#L28)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sar20_system_top](../ad4080_dsp/rtl/sar20_system_top.sv) | 扩展：输出DSP | 185字比较；错误13／7／8后12笔好raw锁停及三次epoch恢复 | bench 对拍 | 参考为实际SAR数字字，不是ADC精度oracle；CAAZ固定关闭，停采park后非空保记录reset未验 | [sar20_system_top_tb](../ad4080_dsp/sim/tb/sar20_system_top_tb.sv#L67)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sinc1_decim](../ad4080_dsp/rtl/sinc1_decim.sv) | 扩展：输出DSP | 独立block-average／延迟／输出率，643次数值比较 | bench 对拍 | 16码／N2..1024及clamp；不代替整链filter_sel或物理FILTER_RDY | [sinc_rate_oracle_tb](../ad4080_dsp/sim/tb/sinc_rate_oracle_tb.sv#L98)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sinc5_decim](../ad4080_dsp/rtl/sinc5_decim.sv) | 扩展：输出DSP | 独立有限窗五级卷积／延迟／输出率，1520次数值比较 | bench 对拍 | 16码／N2..256及clamp；物理CNV／FILTER_RDY及所有配置另验 | [sinc_rate_oracle_tb](../ad4080_dsp/sim/tb/sinc_rate_oracle_tb.sv#L107)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sinc5_comp](../ad4080_dsp/rtl/sinc5_comp.sv) | 扩展：输出DSP | 64bit FIR oracle76值、缺binding645拒绝及在途sync取消 | bench 对拍 | 给定3tap Q15／N4夹具；不证明厂商阶数、补偿频响或全链路由 | [sinc5_comp_contract_tb](../ad4080_dsp/sim/tb/sinc5_comp_contract_tb.sv#L35)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [spi_slave](../ad4080_dsp/rtl/spi_slave.sv) | 扩展：输出DSP | CRC10比较与914项实体／abort／poison／ready检查 | bench 对拍 | 只限已绑定协议子集；实体宽度和未绑定拒绝，不声明全器件符合性 | [spi_slave_tb](../ad4080_dsp/sim/tb/spi_slave_tb.sv#L83)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sys_error_corr](../ad4080_dsp/rtl/sys_error_corr.sv) | 扩展：输出DSP | 64bit floor／饱和／flags42比较、37无map拒绝及取消 | bench 对拍 | 仅KG3／SHIFT13与unity／OFFSET合同；厂家增益映射及逆两点学习未实现 | [sys_error_corr_contract_tb](../ad4080_dsp/sim/tb/sys_error_corr_contract_tb.sv#L17)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |

## Python数字与模型接口

| 实现组 | 当前状态与交付归属 | 最高证据等级 | 未完成项／有效范围 | 入口 |
| :--- | :--- | :--- | :--- | :--- |
| defaults／Config／CLI | 可运行推断默认；CLI实跑默认4096及显式参数／拒绝例，validate-config只检查 | smoke | CLI通过不等于系数训练；模型静态留出见数据账本，未覆盖动态源配置 | [默认层](../src/adi_model/defaults.py)、[CLI复现](REPRODUCING.md#1-实际运行默认模型) |
| pipeline／pipeline_engine／sampler | 当前公共producer验证实际采集／参考、raw与数字观察的指定无dither静态路径 | bench 对拍 | 仅该工作点；动态／含噪／端点不因此通过 | [执行器](../src/adi_model/simulation/pipeline_engine.py) |
| dither／Mapper | 64字静态等效＋18/8因果计划＋固定非均匀池／冻结校准已有同池对拍；早事件非法明确拒绝 | bench 对拍 | 源3D DEM未实现；v1拒绝单元DEM、动态、随机噪声和额外非零RA扰动；真实共享AZ、物理dither网络未验 | [dither](../src/adi_model/control/dither.py)、[实际事件](readme/data/source_bound_fig19_evidence.json) |
| dem／scheduler／timing | 显式mask／采集连接／资源计划／数字事件 | 静态审计 | 源3D DEM和完整桥接矩阵未实现；不包含时偏学习 | [mask接口](../src/adi_model/control/dem.py)、[计划](../src/adi_model/control/timing.py) |
| weight_calibration／calib | 当前公共producer实际独立参考、SVD满秩训练与schema2固定；中幅反例不足秩拒绝 | bench 对拍 | 仅实际训练器路径；其他数学助手、硬件读出与动态另验，Frozen旧schema1模型不兼容；register镜像独立schema1仍携带新规格 | [训练](../src/adi_model/calibration/weight_calibration.py) |
| fixed_point／reconstruction／sim | 当前公共producer同raw名义／固定系数及512码独立整数一致 | bench 对拍 | 仅当前Python静态路径；RTL、其他分支／异常与动态另验 | [定点](../src/adi_model/contracts/fixed_point.py)、[容器](../src/adi_model/simulation/sim.py) |
| rtl_observation | 实际733位OBS字段／物理连接／有序ID与输入包SHA绑定，同池重放核对 | bench 对拍 | 仅明确fold子集；外部捕获、epoch、缺样、带宽与真实独立参考另验 | [适配器](../src/adi_model/rtl_observation.py)、[DUT与同池重放](readme/data/source_bound_rtl_calibration.json) |
| chopping_az／pretracking | 扩展：显式AZ误差与数字配对、到时可见的码历史预置 | 静态审计 | 真实CBIAS／H/K与源跟踪电路；主链活跃pretrack／AUX拒绝，独立策略不等于CICC平均或时偏学习 | [CAAZ](../extensions/caaz/model/chopping_az.py)、[预跟踪](../src/adi_model/behavioral/pretracking.py) |
| slice_pool／dac_arch／charge_ref／动态核 | 模拟被测ADC与独立数学参考，不是数字可见真值 | 静态审计 | 实际网络、动态、噪声和工作域独立验证 | [电荷参考](../src/adi_model/behavioral/charge_ref.py)、[物理池](../src/adi_model/simulation/slice_pool.py) |

## 三个跨层接口

| 接口 | 交付物／扩展与当前连接 | 当前状态 | 最高证据等级 | 未完成项 | 入口 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| calobs v2（替代旧748位口） | 数字交付：实际码、mask、slice、word／state和已知公共项 | 733位Fig.19实例对拍，宽度随schema；181位为另一数学夹具 | bench 对拍 | epoch、FIFO／ready、真实输送和独立参考；不沿用固定29.92 Gbit/s | [ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)、[DUT与同池重放](readme/data/source_bound_rtl_calibration.json) |
| SAR→DSP逐结果元数据 | 输出扩展：五flags／analog sticky及独立conversion_fault live门控和锁停；错误13／7／8有实测恢复 | 入口事件不等于输出标签，无raw不造ID | bench 对拍 | `dout_analog_bad`、滤波窗口、FIFO／输出ID、配置版本、事件队列及恢复合同 | [SAR20接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| DSP并行输出链 | 输出扩展：补码处理、抽取、捕获／并行读出；旧串行候选退役 | bypass整链与独立滤波内核有对拍，filter_sel1/2/3整链数值路由尚缺 | bench 对拍 | 数据PHY、持续吞吐、实际CNV时序、输出ID／窗口／配置版本 | [DSP说明](../ad4080_dsp/README.md)、[接口](../ad4080_dsp/docs/SAR20_INTERFACE.md) |

## 历史记录与未通过层级

| 记录 | 可以保留的事实 | 不能升级的结论 |
| :--- | :--- | :--- |
| af8／9bdf静态校准、交织、旧dither／DEM图 | 原结果、输入、源码SHA和原统计口径继续保存 | 不是现行实现性能，不能只替换旧摘要SHA |
| 003中间Python训练／留出 | 维护者库外保留的真实中间静态记录 | 不作为Fig.19后最终源或当前主线性能资产 |
| [旧DSP仿真](../ad4080_dsp/docs/verification_sim.md)、[BMC](../ad4080_dsp/docs/formal.md) | 所记旧源及有限属性／用例 | 不证明当前全器件协议、16k全部数据或无界证明 |
| [旧FPGA证据](evidence/20260930/README.md) | 所绑定旧源、FPGA、25ns等原始事实 | 不证明当前ASIC640MHz；保留OOC hold／TIMING_MET限制 |
| 最终源尚缺层级 | 目标库综合／STA、SEC、CDC/RDC、晶体管／PEX没有当前通过声明 | 不用静态守卫、组件通过或图示替代signoff |

本表依据实际实现和可查收据更新。最终源、输入和运行身份改变时重新验证，不通过改写历史数值或换名继承旧通过。

DSP handoff003的原SHA256为`5daa8995a78ac9a34d51a09a4802099d3d6f6c37a0cc2f6575c7c84062a9b5f3`。14指定场景与5严格-Wall profile分开执行；系统185次20位比较、214握手、15448检查。各行锚点指实际断言／oracle。仍缺真实停采park后的非空软件复位、完整输出元数据、全链滤波选择及CAAZ＋DSP。

DUT `full_typechecked_001`的原manifest保留PENDING字段；同版5cfb的四次真实重放追加独立验证：训练4096、同一留出名义与固定各512、组件64，正常有限窗status／sticky／fault均0；两组各25类真实损坏全部拒绝。[数据与最终收据身份](readme/data/source_bound_rtl_calibration.json)。原001／002与旧均匀004的尾部SOURCE13、旧checker缺口均保留原身份。有限窗结束不证明停机、排空、park或模拟idle。

现行CAAZ组件收据SHA256为`8fda0a90eeec015f232160e97b27ada777c08e337efa10d57dbf897dccd78d92`。三top实际严格-Wall无warning／waiver；启用组件56OBS／54RAW／4配对，普通56OBS／54OUT。独立配对13周期／2case／4结果、523 bench检查与325字段对拍；修订前后对应数字轨迹字节相同。其支持域仅数字组件。

本机完整回归、云端CI与物理层级分别绑定各自的源和输入。
