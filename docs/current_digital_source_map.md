# 当前数字实现与原文对照

> 现行对象是20个主清单RTL模块、15个DSP模块和Python数字机制。af8功能合同／9bdf文档作为历史参照；原文对照为静态审计，执行结果与范围单列在[B表](digital_status_matrix.md)。

对齐状态只使用**披露复现／机制对齐／工程选择／未实现**。机制对齐限对应行的原理或关系，不自动覆盖电路、参数与性能；没有直接原文定位的实现写工程选择（本工程自定）。[ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)记录现行字段、单位与整数关系；[机制账本](../mechanism_inventory.json)保留逐项验收范围与尚缺证据，运行收据另列B表。

## 1. 20个主清单RTL模块

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `adc2_dec` | 无直接原文依据；工程选择（本工程自定） | [adc2_dec.sv](../rtl/core/adc2_dec.sv)：`adc2_dec` | 工程选择 | 静态审计 | 名义ADC2码仓中心与Q格式、舍入、端点检查；12位与窗口是所选规格。 |
| `caaz_cycle_ctrl` | [PKU论文](#ref-pku-paper) PDF2 Fig.5/6；讲稿PDF34/35 | [caaz_cycle_ctrl.sv](../extensions/caaz/rtl/caaz_cycle_ctrl.sv)：`caaz_cycle_ctrl` | 机制对齐 | 静态审计 | 仅输入极性、AZ代和偶更新／奇保持资格；精确相位为工程选择，不实现CBIAS保持。 |
| `caaz_pair_processor` | PKU论文 PDF2 Fig.5/6；讲稿PDF35 | [caaz_pair_processor.sv](../extensions/caaz/rtl/caaz_pair_processor.sv)：`caaz_pair_processor` | 机制对齐 | 静态审计 | 完整重构码后配对和ID／极性／AZ代检查；没有H/K或实际存储噪声观测。 |
| `analog_phase_ctrl` | 无直接原文依据；工程选择（本工程自定） | [analog_phase_ctrl.sv](../rtl/core/analog_phase_ctrl.sv)：`analog_phase_ctrl` | 工程选择 | 静态审计 | 16相位及注册使能、启动清零是工程时序；真实非交叠、比较器和参考建立由宏验收。 |
| `cal_output_stage` | 无直接原文依据；工程选择（本工程自定） | [cal_output_stage.sv](../rtl/core/cal_output_stage.sv)：`cal_output_stage` | 工程选择 | 静态审计 | 提交或保持候选码，随同ID和flags退休；不重算训练或模拟正确性。 |
| `cal_residue_mac` | 无直接原文依据；工程选择（本工程自定） | [cal_residue_mac.sv](../rtl/core/cal_residue_mac.sv)：`cal_residue_mac` | 工程选择 | 静态审计 | 重构乘加、受检位宽和溢出处理；不读取物理电容真值。 |
| `cal_sample_context` | 无直接原文依据；工程选择（本工程自定） | [cal_sample_context.sv](../rtl/core/cal_sample_context.sv)：`cal_sample_context` | 工程选择 | 静态审计 | 冻结同笔slice、mask、控制word／state、已知项与前代fine归属；不同采集端口不得混笔。 |
| `cal_weight_reduce` | 无直接原文依据；工程选择（本工程自定） | [cal_weight_reduce.sv](../rtl/core/cal_weight_reduce.sv)：`cal_weight_reduce` | 工程选择 | 静态审计 | 按实际物理mask归约有效权重；实现组合算术，不包含系数学习器。 |
| `calib_regs` | 无直接原文依据；工程选择（本工程自定） | [calib_regs.sv](../rtl/core/calib_regs.sv)：`calib_regs` | 工程选择 | 静态审计 | 标量、完整性验证和epoch撤销；0x1018旧模式地址退役，不提供别名。 |
| `div_floor` | 无直接原文依据；工程选择（本工程自定） | [div_floor.sv](../rtl/core/div_floor.sv)：`div_floor` | 工程选择 | 静态审计 | 有符号floor除法与异常合同；精确数值和延迟需独立整数／RTL检查。 |
| `rdac_drv` | 无直接原文依据；工程选择（本工程自定） | [rdac_drv.sv](../rtl/core/rdac_drv.sv)：`rdac_drv` | 工程选择 | 静态审计 | 注册外部绑定给出的实际mask和slice归属；数字意图不代替模拟开关或电平转换。 |
| `recon_core` | [00](#ref-00) PDF1仅披露外部求系数、片上校正 | [recon_core.sv](../rtl/core/recon_core.sv)：`recon_core` | 工程选择 | 静态审计 | 具体分子、Q30/Q32、96位检查、流水和错误退休为本工程选择。 |
| `sar_structural_ctrl` | [00] PDF1、PDF2 Fig.9.8.1；[09](#ref-09) 并行残差DAC主题 | [sar_structural_ctrl.sv](../rtl/core/sar_structural_ctrl.sv)：`sar_structural_ctrl` | 机制对齐 | 静态审计 | 双coarse、共享Flash、已决RDAC事件和前代fine的结构关系；截止沿、12位fine与16tick为工程选择。 |
| `sar_trial_ctrl` | 无直接原文依据；工程选择（本工程自定） | [sar_trial_ctrl.sv](../rtl/core/sar_trial_ctrl.sv)：`sar_trial_ctrl` | 工程选择 | 静态审计 | seeded逐位试探、已决码、valid／done／cancel协议；不推断真实比较器完成沿。 |
| `slice_pool_ctrl` | [00] PDF1；[00_1](#ref-00-1) PDF29–31的8/18 | [slice_pool_ctrl.sv](../rtl/core/slice_pool_ctrl.sv)：`slice_pool_ctrl` | 机制对齐 | 静态审计 | 8采集／8转换／2空闲及因果归属；具体选择器和启动预热为工程规则，非时偏学习器。 |
| `status_regs` | 无直接原文依据；工程选择（本工程自定） | [status_regs.sv](../rtl/core/status_regs.sv)：`status_regs` | 工程选择 | 静态审计 | 数字错误与粘滞状态读回；不是器件全部寄存器映射或事件队列。 |
| `unit_therm` | 无直接原文依据；工程选择（本工程自定） | [unit_therm.sv](../rtl/core/unit_therm.sv)：`unit_therm` | 工程选择 | 静态审计 | 检查／传递实际unit mask；已移除旧缺格SID及桥接译码，不宣称源3D DEM。 |
| `weight_store` | [00] PDF1：外部系数与片上DAC权重校正 | [weight_store.sv](../rtl/core/weight_store.sv)：`weight_store` | 机制对齐 | 静态审计 | 仅系数应用位置对齐；物理索引、格式、bitmap及行和缓存为工程实现。 |
| `sadc_enc` | 无直接原文依据；工程选择（本工程自定） | [sadc_enc.sv](../rtl/top/sadc_enc.sv)：`sadc_enc` | 工程选择 | 静态审计 | Flash温度计编码与尺寸守卫；不验证门限、气泡物理成因或孔径。 |
| `sar20_digital_core` | 无直接原文依据；工程选择（本工程自定） | [sar20_digital_core.sv](../rtl/top/sar20_digital_core.sv)：`sar20_digital_core` | 工程选择 | 静态审计 | 完整核依赖显式参数、外部source_control_binding和calobs v2；接口并非原文公开RTL。 |

完整核还需要调用方提供的`source_control_binding`、`source_acquisition_binding`及导出参数包；这两只外部模块不计入仓库`.f`的20个模块。该绑定必须有实际来源、SHA与声明的word／state／采集端口规则。数学夹具不获得原芯片矩阵的身份。

## 2. Python机制与数字边界

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| 集中默认与覆盖 | 无直接原文依据；工程选择（本工程自定） | [defaults.py](../src/adi_model/defaults.py)：DefaultParameters／default_model／simulate | 工程选择 | 静态审计 | 名义网络、噪声、基本译码和随机资源计划均带推断表；默认名义系数未经训练。 |
| 低层配置 | [00] PDF1及[00_1]架构锚点；其余为工程选择 | [config.py](../src/adi_model/simulation/config.py)：Config／rtl_structural | 工程选择 | 静态审计 | Config是不完整9位规格；rtl16标签不补齐ADC2、物理网络或source mapping。 |
| CLI与结果容器 | 无原文依据；工程选择（本工程自定） | [cli.py](../src/adi_model/cli.py)、[sim.py](../src/adi_model/simulation/sim.py) | 工程选择 | 静态审计 | simulate实际运行名义码；validate-config只检查输入。输出码宽、异常和假设分开。 |
| 真实执行与归属 | [00] PDF1–2 Fig.9.8.1；[09]独立量化器／并行DAC主题 | [pipeline.py](../src/adi_model/simulation/pipeline.py)、[pipeline_engine.py](../src/adi_model/simulation/pipeline_engine.py)：execute_pipeline | 机制对齐 | 静态审计 | 显式pool、mapper、sample IDs、状态、采集坐标与实际／名义轨；不是旧隐式构造器。 |
| 前后级静态码表 | 无具体门限、后级窗口或端点算法披露；工程选择（本工程自定） | [sadc.py](../src/adi_model/behavioral/sadc.py)：SADC／build_first_stage_quantizer；[adc2.py](../src/adi_model/behavioral/adc2.py)：ADC2 | 工程选择 | 静态审计 | SADC显式513阈值；ADC2使用所选码仓、窗口和端点。夹码仍保留对应节点异常；不是连续时间ADC2或器件DNL／INL。 |
| 整数码与实际mask | 无直接原文依据；工程选择（本工程自定） | [mapper.py](../src/adi_model/topology/mapper.py)、[dem.py](../src/adi_model/control/dem.py) | 工程选择 | 静态审计 | 码表、0/1 mask、Signal／RefLo／RefHi采集连接及调用方binding；不自造源DEM。 |
| 资源调度与事件 | [00] PDF1及[00_1] PDF29–31，仅支持资源架构 | [scheduler.py](../src/adi_model/simulation/scheduler.py)、[timing.py](../src/adi_model/control/timing.py) | 机制对齐 | 静态审计 | 使用明确采集／转换计划和事件表；实际选择策略与phase单位为工程接口，不估计时偏。 |
| Fig.19字分组与改码 | [10](#ref-10) PDF22 Fig.19、PDF33；[00_1] PDF17/18 | [dither.py](../src/adi_model/control/dither.py)：Fig19DitherProgram／Fig19CommandTrace | 机制对齐 | 静态审计 | 只对齐同字的lower／upper分组、T=L+U·Δ与实际命令c−U；不能逐样本替换成固定dR=4dQ。 |
| Fig.19采集端口的静态等效 | 同上原文作为比较；目标等效绑定是工程假设 | [sampler.py](../src/adi_model/behavioral/sampler.py)、[sampling_charge.py](../src/adi_model/behavioral/sampling_charge.py)、defaults.py | 工程选择 | 静态审计 | SADC采样x−L、RDAC保存x−T、已知公共项−T；负极性、Q32及7-slice例4:2:1/7映射到18/8均属受限工程静态等效。指定Python及RTL有限窗已对拍；完整物理网络另验。 |
| 静态学习与固定系数 | [00] PDF1仅披露片外求系数、片上应用的分工 | [weight_calibration.py](../src/adi_model/calibration/weight_calibration.py)：DigitalObservation／fit_unit_weights／FrozenCalibration | 工程选择 | 静态审计 | 独立已知输入SVD、秩／条件数、schema2、留出方法均为工程选择。观察包含明确a与已知电压项，不读取电容真值。 |
| 数学可辨识性 | 无直接原文依据；工程选择（本工程自定） | [calib.py](../src/adi_model/calib.py)：observability／ridge_fit | 工程选择 | 静态审计 | 数学助手和样本预算；不等同原芯片训练控制器。 |
| 整数重构 | 无直接原文依据；工程选择（本工程自定） | [fixed_point.py](../src/adi_model/contracts/fixed_point.py)、[reconstruction.py](../src/adi_model/calibration/reconstruction.py) | 工程选择 | 静态审计 | 显式format和名义／固定系数，完整规格与观察域绑定；Frozen模型schema2与独立register schema1分开，均须完整新规格。 |
| calobs v2解包 | 无原文依据；工程选择（本工程自定） | [rtl_observation.py](../src/adi_model/rtl_observation.py)：observations_from_rtl | 工程选择 | 静态审计 | schema、word／state及连接文档SHA绑定；只接受声明的fold子集，不从历史748位包猜连接。 |
| 双source绑定与输入包 | 无原文接口依据；工程选择（本工程自定） | [导出器](../tools/export_rtl_params.py)、[控制绑定示例](../sim/fixtures/fig19_ideal_ports/source_control_binding.sv)、[采集绑定示例](../sim/fixtures/fig19_ideal_ports/source_acquisition_binding.sv) | 工程选择 | 静态审计 | 两模块与五份原始输入各自绑定字节；实际mask与两采集偏移由调用方明确提供。Fig.19夹具是受限等效，非器件源mapper。 |
| 物理／数字边界 | [00]及[09]的架构仅作比较 | [slice_pool.py](../src/adi_model/simulation/slice_pool.py)、[dac_arch.py](../src/adi_model/topology/dac_arch.py) | 工程选择 | 静态审计 | 实际C、G和保持电压用于模拟被测ADC；数字码表与训练观测独立。63+8是推断网络。 |
| 独立电荷与动态核 | 无直接原文依据；工程选择（本工程自定） | [charge_ref.py](../src/adi_model/behavioral/charge_ref.py)、[input_network.py](../src/adi_model/behavioral/input_network.py)、[reference_charge.py](../src/adi_model/behavioral/reference_charge.py)、[conversion.py](../src/adi_model/behavioral/conversion.py) | 工程选择 | 静态审计 | 节点方程、源阻抗、参考电荷及有限信号响应只支持明确输入和假设；不等于原文参考回路、真实开关噪声或PEX。 |
| CAAZ样本域 | [PKU论文](#ref-pku-paper) PDF1/2 Fig.5/6；讲稿PDF34/35 | [chopping_az.py](../extensions/caaz/model/chopping_az.py)：run_caaz_pipeline／pair_readout | 机制对齐 | 静态审计 | 显式输入等效AZ记录、偶更新／奇保持及完整码后配对；保持／H/K是外部输入或待验条件，不自动生成理想共享噪声。 |
| 码历史预跟踪 | [12](#ref-12) PDF12权利要求主题 | [pretracking.py](../src/adi_model/behavioral/pretracking.py)：QuantizedPretracker | 机制对齐 | 静态审计 | 数学策略只使用到时可见的转换结果；主链尚缺真实采集状态绑定，活跃pretrack／AUX请求拒绝。不是CICC后级平均或时偏学习。 |
| 来源与维护工具 | 无原文依据；工程选择（本工程自定） | [provenance.py](../src/adi_model/provenance.py)、[inventory_gate.py](../src/adi_model/inventory_gate.py)、[serialization.py](../src/adi_model/simulation/serialization.py)、[metrics.py](../src/adi_model/metrics/metrics.py) | 工程选择 | 静态审计 | 参数等级、文件身份、记录结构和评分；静态守卫不证明机制复现或signoff。 |

## 3. 15个DSP扩展模块

以下页码均为[AD4080 Rev.B](#ref-ad4080)的PDF页码。手册用于外部功能比较，不反推芯片内部ADC、DEM或位权学习。模块代码存在不等于全部模式与器件协议已经通过。

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `ad4080_dsp_chain` | Rev.B PDF64–71 Fig.96–109、Tables28–30 | [ad4080_dsp_chain.sv](../ad4080_dsp/rtl/ad4080_dsp_chain.sv)：`ad4080_dsp_chain` | 工程选择 | 静态审计 | 组合校正、SINC与并行输出；不再保留第二条隐藏FIFO链。 |
| `ad4080_regs` | PDF73–94寄存器表；PDF27/34复位、37/81实体访问 | [ad4080_regs.sv](../ad4080_dsp/rtl/ad4080_regs.sv)：`ad4080_regs` | 工程选择 | 静态审计 | 实体宽度、访问支持域、真实状态与配置拒绝；无法确证的参数须显式绑定。 |
| `ad4080_top` | PDF34–71外部数字功能，仅作比较 | [ad4080_top.sv](../ad4080_dsp/rtl/ad4080_top.sv)：`ad4080_top` | 工程选择 | 静态审计 | 集成一条并行链与捕获；不包括数据PHY或实际CNV启动。 |
| `cnv_ctrl` | PDF19 Fig.31/32、PDF33；PDF7–8 Table2 | [cnv_ctrl.sv](../ad4080_dsp/rtl/cnv_ctrl.sv)：`cnv_ctrl` | 工程选择 | 静态审计 | 解码后逻辑代理；不是差分接收器、tCONV测量或SAR启动源。 |
| `event_detect` | PDF54–56 Tables25/26、Fig.83–85；PDF90 Table55 | [event_detect.sv](../ad4080_dsp/rtl/event_detect.sv)：`event_detect` | 工程选择 | 静态审计 | 阈值、迟滞、路由与事件计数；物理CNV时间基准须另验。 |
| `fifo_capture` | PDF56–63 Table27、Fig.86–95 | [fifo_capture.sv](../ad4080_dsp/rtl/fifo_capture.sv)：`fifo_capture` | 工程选择 | 静态审计 | 捕获生命周期、早事件历史及读出资格；实际SRAM与器件全边界不由模块名证明。 |
| `gpio_ctrl` | PDF52–56 Tables24–26；PDF87–90 Tables52–55 | [gpio_ctrl.sv](../ad4080_dsp/rtl/gpio_ctrl.sv)：`gpio_ctrl` | 工程选择 | 静态审计 | 输入回读、SDO输出使能及ALERT路由；pad与电气未实现。 |
| `result_fifo` | PDF56–63的16k FIFO外部功能 | [result_fifo.sv](../ad4080_dsp/rtl/result_fifo.sv)：`result_fifo` | 工程选择 | 静态审计 | 通用队列与完成记录读出；存储宏、时序和门级物理实现需另验。 |
| `sar20_ad4080_top` | 无原文依据；工程选择（本工程自定）；码制比较PDF20 Table7 | [sar20_ad4080_top.sv](../ad4080_dsp/rtl/sar20_ad4080_top.sv)：`sar20_ad4080_top` | 工程选择 | 静态审计 | 码制、接受／拒绝、flags／sticky及独立conversion_fault锁停；无raw不造ID，输入事件不等于输出元数据或任意ID缺口检测。 |
| `sar20_system_top` | 无原文依据；工程选择（本工程自定） | [sar20_system_top.sv](../ad4080_dsp/rtl/sar20_system_top.sv)：`sar20_system_top` | 工程选择 | 静态审计 | 真实SAR核加DSP封装，要求显式完整核输入与外部idle／ready；CAAZ默认未启用。 |
| `sinc1_decim` | PDF64–70 Fig.96–107、Tables28–30；PDF94 Table62 | [sinc1_decim.sv](../ad4080_dsp/rtl/sinc1_decim.sv)：`sinc1_decim` | 机制对齐 | 静态审计 | 输出SINC1抽取机制；内部valid推进不等于器件CNV建立时钟。 |
| `sinc5_decim` | 同上 | [sinc5_decim.sv](../ad4080_dsp/rtl/sinc5_decim.sv)：`sinc5_decim` | 机制对齐 | 静态审计 | 五阶积分／梳状输出抽取；频响、群延迟及所有配置需对应检查。 |
| `sinc5_comp` | PDF64–70补偿通带及额外2倍抽取主题 | [sinc5_comp.sv](../ad4080_dsp/rtl/sinc5_comp.sv)：`sinc5_comp` | 工程选择 | 静态审计 | 显式有效FIR系数才允许启用；取消单位占位系数不表示通带已验收。 |
| `spi_slave` | PDF34–42 Fig.53–66、Tables14–19；PDF37/81 strict | [spi_slave.sv](../ad4080_dsp/rtl/spi_slave.sv)：`spi_slave` | 工程选择 | 静态审计 | Mode3、实体提交、SINGLE_INST、CRC与取消按受支持域实现；完整器件符合性另验。 |
| `sys_error_corr` | PDF71 Fig.108/109、PDF92–93 Tables60/61 | [sys_error_corr.sv](../ad4080_dsp/rtl/sys_error_corr.sv)：`sys_error_corr` | 机制对齐 | 静态审计 | 仅系统增益／失调校正；非unity增益律需显式绑定，不含逆向两点系数求解。 |

## 4. 未实现范围与退役方案

| 机制 | 原文位置 | 当前实现边界 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| 原芯片3D DEM／完整binary-to-unary矩阵 | [00_1] PDF29–31；[11](#ref-11) 多级洗牌与附加低位主题 | 只有显式mask／状态／资源接口；默认canonical译码 | 未实现 | 静态审计 | 旧8×8缺格、SID轮转和±1交换已退役；不得用旧偏置图声称新DEM效果。 |
| 完整Fig.19电容／开关、噪声和动态网络 | [10] PDF22 Fig.19、PDF33；[00_1] PDF17/18 | Python／RTL指定静态工作点已核；专用物理网络未实现 | 未实现 | 静态审计 | 两端口的负极性与Q32是明确假设；原例7-slice权重不证明目标18/8电路。 |
| CAAZ模拟宏与H/K | PKU论文PDF1/2 Fig.2/5/6；讲稿PDF28–35 | 仅显式误差记录、数字控制与pair | 未实现 | 静态审计 | FGA／CP-FIA／CBIAS、保持相关性与归一化传递仍待模拟证据。 |
| CICC连续时间后级／CINC／replica kickback | [CICC论文](#ref-cicc-paper) PDF1 §II Fig.1、PDF2 §III.B Fig.2/3；讲稿PDF17–22、47 | 当前ADC2为静态量化，fine SAR无该连续模拟路径 | 未实现 | 静态审计 | 前级预跟踪不替代连续时间后级。 |
| CICC 8次LSB重复／7只反馈电容 | CICC论文PDF2 §III.A/B Fig.2/3；讲稿PDF47 | 无转换内反馈与重复判决网络 | 未实现 | 静态审计 | 输出抽取或CAAZ pair不等于LSB重复。 |
| CICC SINC² tracking averaging | CICC论文PDF1–2 §II Fig.1；讲稿PDF24–32 | 无该重复比较、两次累积和匹配反馈／抽取 | 未实现 | 静态审计 | 输出SINC1／SINC5不是此机制。 |
| 专利AUX与低功耗参考完整回路 | [13](#ref-13) PDF16–17、[14](#ref-14) PDF23–25权利要求主题 | 通用输入／参考电荷算子只接受显式网络；旧理想自举与比例servo移出 | 未实现 | 静态审计 | 不把通用RC或数字strobes认定为专利完整电路。 |

退役的6个core模块（`ctrl_fsm`、`dem_addr_gen`、`dem_state_gen`、`dither_gen`、`slice_alloc`、`swap_decode`）、`cal_two_point`／`lvds_framer`及旧unary、`range`、固定4倍扰动和KTC支路，只能在[9bdf旧表](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/docs/current_digital_source_map.md)和对应完整快照中引用；不作为当前兼容配置恢复。

## 5. 来源目录及引用边界

以下覆盖 [NOTICE](../NOTICE) 的22个有号来源；其中两份CICC原件由原有references清册同步列入。表中列出的论文／学位论文DOI已按官方Crossref或学校记录核对；讲稿未核实独立DOI时不套用论文DOI。六份仓库原件的身份见 [references/source_manifest.json](../references/source_manifest.json)，其余已登记本机原件按 [sources_manifest.json](../sources_manifest.json) 的文件名、SHA和`ADC_SOURCES_DIR`定位。PDF页码从文件第一页起计，不与印刷页或讲稿页脚编号混用。

| 来源编号 | 题录／专利权利要求主题 | 本表定位或角色 |
| :--- | :--- | :--- |
| <a id="ref-00"></a>[00] | Rares Bodnar等，*A 9.3nV/rtHz 20b 40MS/s 94.2dB DR Signal-Chain Friendly Precision SAR Converter*，ISSCC 2024，9.8；[DOI](https://doi.org/10.1109/ISSCC49657.2024.10454329) | [论文原件](../references/ISSCC2024_PAPER.pdf)：PDF1架构与外求系数／片上校正，PDF2 Fig.9.8.1–3；不是本工程定点／训练细节来源。 |
| <a id="ref-00-1"></a>[00_1] | 同[00]作者，ISSCC 2024 Session9.8讲稿；未见独立DOI／待确认 | [讲稿原件](../references/ISSCC2024_SLIDES.pdf)：PDF17/18注入图、29–31 DEM／桥接、20–22交织误差。 |
| <a id="ref-09"></a>[09] | US10516408B2：分离量化器与匹配采集时间常数的并行DAC、控制字及备用slice交换 | 本机登记原件PDF32的权利要求主题；用于架构对照，不证明16相位或63+8。 |
| <a id="ref-10"></a>[10] | US10505561B2：采样DAC注入dither、SADC不接全部dither、改码及后级去除 | 本机登记原件PDF22 Fig.19、PDF33及35–36；分位字受限工程静态路径已实现，完整物理网络未实现。 |
| <a id="ref-11"></a>[11] | US10511316B2：共享／附加字、多级洗牌及低位跨slice控制 | 本机登记原件PDF46的权利要求主题；当前映射只是工程候选。 |
| <a id="ref-12"></a>[12] | US10707889B1：交织采集／转换／跟踪、采集前用其他转换结果更新、多笔加权及随机调度 | 本机登记原件PDF12的权利要求主题。**身份待确认**：[00] PDF3印`10,797,889`，本机封面与NOTICE为`10,707,889`；不静默视为同号。 |
| <a id="ref-13"></a>[13] | US10541702B1：辅助输入绕RC供应开关内部负载／采样预充电 | 本机登记原件PDF16–17的权利要求主题；辅助输入行为近似不等于模拟电路复现。 |
| <a id="ref-14"></a>[14] | US10826519B1：比较器控制内部参考充电、相位切换、阈值调整及负载补偿 | 本机登记原件PDF23–25的权利要求主题；数字strobes和比例伺服不能证明原闭环。 |
| [01] | Christopher Peter Hurrell等，*An 18 b 12.5 MS/s ADC With 93 dB SNR*，JSSC 2010扩展版；[DOI](https://doi.org/10.1109/JSSC.2010.2075310) | 背景／未用于本表当前模块的机制归属。NOTICE中的ISSCC前身与本机JSSC扩展版分开理解。 |
| [02] | Ahmed ElShater等，*A 10-mW 16-b 15-MS/s Two-Step SAR ADC With 95-dB DR Using Dual-Deadzone Ring Amplifier*，JSSC 2019扩展版；[DOI](https://doi.org/10.1109/JSSC.2019.2943935) | 背景／未用于当前数字模块的机制归属；不把双死区环放叠加为目标芯片要求。 |
| [03] | Manxin Li等，*A Rail-to-Rail 12 MS/s 91.3 dB SNDR 94.1 dB DR Two-Step SAR ADC With Integrated Input Buffer Using Predictive Level-Shifting*，JSSC 2023扩展版；[DOI](https://doi.org/10.1109/JSSC.2023.3308121) | 背景／未用于当前数字模块的机制归属；不能用它证明当前预跟踪电路已实现。 |
| [04] | Alan Bannon等，*An 18 b 5 MS/s SAR ADC with 100.2 dB Dynamic Range*，VLSI Circuits 2014；[DOI](https://doi.org/10.1109/VLSIC.2014.6858371) | 背景／未用于当前数字模块的机制归属。 |
| [05] | Analog Devices，*LTC2387-18*产品手册；未见自身DOI／待确认 | 背景／商用对照；未用于当前数字模块的机制归属。 |
| [06] | Jesper Steensgaard等，*A 24b 2MS/s SAR ADC with 0.03ppm INL and 106.3dB DR in 180nm CMOS*，ISSCC 2022；[DOI](https://doi.org/10.1109/ISSCC42614.2022.9731652) | 背景／未作为当前SVD训练或原芯片内部矩阵的依据。 |
| [07] | Texas Instruments，*ADC358x*产品手册Rev.A；未见自身DOI／待确认 | 背景／商用对照；未用于当前数字模块的机制归属。 |
| [08] | Junhua Shen等，*A 16-bit 16-MS/s SAR ADC With On-Chip Calibration in 55-nm CMOS*，JSSC 2018；[DOI](https://doi.org/10.1109/JSSC.2017.2784761) | 背景／未实现其片内校准算法；不能以已有SVD训练替代其机制。 |
| <a id="ref-ad4080"></a>[AD4080_REVB] | Analog Devices，*AD4080*数据手册Rev.B，95页；[官方PDF](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)，未见自身DOI／待确认 | §3逐模块给出页／图／表定位；仅用于外部数字功能，不反推内部ADC拓扑。 |
| [HUANG2024_THESIS] | Huang，*Advanced Clock Multiplier and SAR ADC Design Techniques for High-Resolution Signal Chain Systems*，香港科技大学学位论文，2024；[学校DOI](https://doi.org/10.14711/thesis-991013340444103412) | 背景／未作为当前已知输入SVD学习、RTL训练器或SRM实现依据。 |
| <a id="ref-pku-paper"></a>[PKU_VLSI2025_C32_1_PAPER] | Zongnan Wang、Haoyang Luo、Rongjia Bao、Lu Jie、Xiyuan Tang，*An 88.8dB-SNDR 6-MS/s Pipelined SAR ADC with a Closed-Loop Dynamic Amplifier Featuring Highly-Linear Full-Scale Output Swing*，VLSI Technology and Circuits 2025，C32-1；[DOI](https://doi.org/10.23919/VLSITechnologyandCir65189.2025.11075015) | [论文原件](../references/PKU_VLSI2025_C32_1_PAPER.pdf)：PDF1机制说明、PDF2 Fig.5/6；可选扩展，非ISSCC目标芯片披露。 |
| <a id="ref-pku-slides"></a>[PKU_VLSI2025_C32_1_SLIDES] | 同上作者／会议的42页C32-1讲稿；未见独立DOI／待确认 | [讲稿原件](../references/PKU_VLSI2025_C32_1_SLIDES.pdf)：PDF28–35，页脚Slide27–34；CBIAS、输入斩波与完整输出配对。 |
| <a id="ref-cicc-paper"></a>[CICC2026_13_6_PAPER] | Congyu Jin等，*A 3.6mW 5MS/s 100dB-SNDR 2-Step SAR ADC with Continuous-Time Second-Stage and Tracking Averaging*，CICC 2026，13-6；[DOI](https://doi.org/10.1109/CICC65509.2026.11509489) | [论文原件](../references/CICC2026_13_6_PAPER.pdf)：PDF1–2 §II/III及Fig.1–3；背景／对应需求未实现。 |
| <a id="ref-cicc-slides"></a>[CICC2026_13_6_SLIDES] | 同上作者／会议的62页13-6讲稿；未见独立DOI／待确认 | [讲稿原件](../references/CICC2026_13_6_SLIDES.pdf)：PDF17–32及47；背景／对应需求未实现。原件入库不等于全部62页均已完整技术审阅。 |

原文的lower／upper是注入位置与码字分配关系，不能用单一倍率、相同模块名或结果图相似替代核对。本表不以历史结果代替当前源验证；完整来源、参数与执行身份分别保留。
