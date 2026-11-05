# 当前数字实现与原文对照

功能与原文核查基线：`main@af8aa37d43db40eb896e369305883ec89786547b`。当前代码保留该基线功能，仅更新注释与排版；已用逐文件Python AST及SystemVerilog非注释token／pragma比较建立静态保持关联。RTL 清册仍以 [rtl_sources.f](../rtl/rtl_sources.f) 的 26 个模块为准；DSP 扩展另列 17 个模块。`Config.rtl_structural()` 是本表的结构对照配置：9 位前级、63+8 分裂阵列、12 位后级、18/8 slice 池和 16 相位。63+8、后级窗口及相位预算属于工程选择，20 位表示输出码宽。

对齐状态只取 **披露复现／机制对齐／工程选择／未实现**。机制对齐只表示表中限定的原理或数据关系有依据；无具体原文位置的实现统一列“工程选择（本工程自定）”。本页对应关系的直接证据等级为**静态审计**；注释保持检查不增加行为实验或形式验证等级。现存运行记录及最高证据等级见 [数字工作状态表](digital_status_matrix.md)。历史 PASS、代理时序和功能仿真均不等于芯片复现或 signoff。

## 1. 26 个主清单 RTL 模块

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `adc2_dec` | 无直接原文依据；工程选择（本工程自定） | [adc2_dec.sv](../rtl/core/adc2_dec.sv)：`adc2_dec` | 工程选择 | 静态审计 | 将原始后级码译成名义码仓中心；12 位、量程、Q32和舍入规则不是 [00] 披露值。 |
| `caaz_cycle_ctrl` | [PKU论文](#ref-pku-paper) PDF2 Fig.5/6；[PKU讲稿](#ref-pku-slides) PDF34/35，页脚Slide33/34 | [caaz_cycle_ctrl.sv](../rtl/control/caaz_cycle_ctrl.sv)：`caaz_cycle_ctrl` | 机制对齐 | 静态审计 | 仅输入极性、偶更新／奇保持和AZ代标签；phase1..8窗口、确认沿及raw40/pair20是工程选择。未实现CBIAS模拟存储。 |
| `caaz_pair_processor` | PKU论文 PDF2 Fig.5/6；PKU讲稿 PDF35，页脚Slide34 | [caaz_pair_processor.sv](../rtl/calibration/caaz_pair_processor.sv)：`caaz_pair_processor` | 机制对齐 | 静态审计 | 完整重构码后作偶减奇、除2并检查归属；没有`H_AZ`或噪声传递输入，不能验证共享噪声或`H_AZ/K`相等。 |
| `analog_phase_ctrl` | 无直接原文依据；工程选择（本工程自定） | [analog_phase_ctrl.sv](../rtl/core/analog_phase_ctrl.sv)：`analog_phase_ctrl` | 工程选择 | 静态审计 | 16tick、注册TP／RA／参考使能、启动时清使能均为项目时序合同；不证明非交叠、本地保持或参考建立。 |
| `cal_output_stage` | 无直接原文依据；工程选择（本工程自定） | [cal_output_stage.sv](../rtl/core/cal_output_stage.sv)：`cal_output_stage` | 工程选择 | 静态审计 | 提交或保持上游20位候选字，同笔更新ID／flags；饱和由`recon_core`完成，本级不重算校准。异常语义按[算术合同](rtl/RTL_ARITHMETIC_CONTRACT.md)。 |
| `cal_residue_mac` | 无直接原文依据；工程选择（本工程自定） | [cal_residue_mac.sv](../rtl/core/cal_residue_mac.sv)：`cal_residue_mac` | 工程选择 | 静态审计 | 实现本工程重构分子与溢出检查；Q30/Q32及96位累加器未由原文披露。 |
| `cal_sample_context` | 无直接原文依据；工程选择（本工程自定） | [cal_sample_context.sv](../rtl/core/cal_sample_context.sv)：`cal_sample_context` | 工程选择 | 静态审计 | 锁存ID／coarse／sid／dither、物理mask／rail、已知注入及CAAZ标签；此处无ADC2码，待fine完成后由上层配对重构。 |
| `cal_weight_reduce` | 无直接原文依据；工程选择（本工程自定） | [cal_weight_reduce.sv](../rtl/core/cal_weight_reduce.sv)：`cal_weight_reduce` | 工程选择 | 静态审计 | 对所选物理权重作纯组合归约，无时钟；组合树、配置期行和的使用策略属于工程实现，结果在上层接受start时锁存。 |
| `calib_regs` | 无直接原文依据；工程选择（本工程自定） | [calib_regs.sv](../rtl/core/calib_regs.sv)：`calib_regs` | 工程选择 | 静态审计 | 配置装载、完整性验证、冻结及epoch撤销；不含片内系数学习器。 |
| `ctrl_fsm` | 无直接原文依据；工程选择（本工程自定） | [ctrl_fsm.sv](../rtl/core/ctrl_fsm.sv)：`ctrl_fsm` | 工程选择 | 静态审计 | 兼容码域路径的固定节拍；结构模式使用独立`analog_phase_ctrl`，不能把两套相位表混用。 |
| `dem_addr_gen` | [00_1](#ref-00-1) PDF29–31；[11](#ref-11) 的共享／附加字及多级洗牌主题仅作比较 | [dem_addr_gen.sv](../rtl/core/dem_addr_gen.sv)：`dem_addr_gen` | 工程选择 | 静态审计 | 当前8×8删cell63、3b子阵列旋转为项目映射；不是已恢复的原芯片矩阵。 |
| `dem_state_gen` | 无直接原文依据；工程选择（本工程自定） | [dem_state_gen.sv](../rtl/core/dem_state_gen.sv)：`dem_state_gen` | 工程选择 | 静态审计 | 每bank独立模512状态推进；全状态周期不等于物理单元等使用或统计白化。 |
| `dither_gen` | 无直接原文依据；工程选择（本工程自定） | [dither_gen.sv](../rtl/core/dither_gen.sv)：`dither_gen` | 工程选择 | 静态审计 | PRNG、种子、固定D=2及离散分布未由 [00]/[10] 给出；原文注入机制与本发生器分开判断。 |
| `div_floor` | 无直接原文依据；工程选择（本工程自定） | [div_floor.sv](../rtl/core/div_floor.sv)：`div_floor` | 工程选择 | 静态审计 | 有符号向负无穷取整及精确商按项目算术合同；不能引用论文替代整数对拍。 |
| `rdac_drv` | 无直接原文依据；工程选择（本工程自定） | [rdac_drv.sv](../rtl/core/rdac_drv.sv)：`rdac_drv` | 工程选择 | 静态审计 | 把逻辑slot掩码路由到实际slice；端口是数字开关意图，不是电平转换／模拟开关模型。 |
| `recon_core` | [00](#ref-00) PDF1披露外部求系数、片上权重校正；未披露本算式和位宽 | [recon_core.sv](../rtl/core/recon_core.sv)：`recon_core` | 工程选择 | 静态审计 | 具体Q格式、除法、异常语义与流水均为工程选择；与原文的对应仅到系数应用目的。 |
| `sar_structural_ctrl` | [00] PDF1及PDF2 Fig.9.8.1；[09](#ref-09) 的分离量化器／并行DAC主题 | [sar_structural_ctrl.sv](../rtl/core/sar_structural_ctrl.sv)：`sar_structural_ctrl` | 机制对齐 | 静态审计 | 双coarse、共享Flash、RDAC跟随与前代fine的结构关系；精确截止沿、12位fine、16tick及错误协议为工程选择。 |
| `sar_trial_ctrl` | 无直接原文依据；工程选择（本工程自定） | [sar_trial_ctrl.sv](../rtl/core/sar_trial_ctrl.sv)：`sar_trial_ctrl` | 工程选择 | 静态审计 | 逐比较有效／已决位／done协议由本项目定义；比较器真实完成时序仍需外部验收。 |
| `slice_alloc` | 无直接原文依据；工程选择（本工程自定） | [slice_alloc.sv](../rtl/core/slice_alloc.sv)：`slice_alloc` | 工程选择 | 静态审计 | 兼容策略只使用0..7与8..15，16/17不参与；不能写成原文18池的动态选择。 |
| `slice_pool_ctrl` | [00] PDF1、PDF2 Fig.9.8.1；[00_1] PDF29–31明确8/18 | [slice_pool_ctrl.sv](../rtl/core/slice_pool_ctrl.sv)：`slice_pool_ctrl` | 机制对齐 | 静态审计 | 对齐8采集／8转换／2备用及不消耗另一组电荷；具体随机调度和预热规则为工程选择。 |
| `status_regs` | 无直接原文依据；工程选择（本工程自定） | [status_regs.sv](../rtl/core/status_regs.sv)：`status_regs` | 工程选择 | 静态审计 | 运行、错误及粘滞状态可读；不是AD4080产品状态寄存器映射。 |
| `swap_decode` | [00_1] PDF31 bridging图；[11] 跨slice低位洗牌主题仅作比较 | [swap_decode.sv](../rtl/core/swap_decode.sv)：`swap_decode` | 工程选择 | 静态审计 | `sid[8]`门控、4正／4负的±1交换和0/511不交换是工程候选；名义零和不证明失配电荷零和。 |
| `unit_therm` | [10](#ref-10) 的采样期DAC开关注入主题仅作比较 | [unit_therm.sv](../rtl/core/unit_therm.sv)：`unit_therm` | 工程选择 | 静态审计 | 63+8、4只预留dither单元和温度计掩码为项目绑定；未证明原专利具体电容／端点规则。 |
| `weight_store` | [00] PDF1：外部导出系数并片上应用DAC权重校正 | [weight_store.sv](../rtl/core/weight_store.sv)：`weight_store` | 机制对齐 | 静态审计 | 仅系数应用位置对齐；物理索引、Q30/48位、完整位图与行和缓存为工程选择，不自动获得训练能力。 |
| `sadc_enc` | 无直接原文依据；工程选择（本工程自定） | [sadc_enc.sv](../rtl/top/sadc_enc.sv)：`sadc_enc` | 工程选择 | 静态审计 | 温度计码编码与合法域由项目定义；共享3位Flash的架构依据不能证明此编码器／气泡处理已由原文披露。 |
| `sar20_digital_core` | 无直接原文依据；工程选择（本工程自定） | [sar20_digital_core.sv](../rtl/top/sar20_digital_core.sv)：`sar20_digital_core` | 工程选择 | 静态审计 | 组合上述工程模块、配置、ID、结果标志和可选748位观测；原文不披露此顶层接口。 |

## 2. Python数字机制与关键差异

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| 结构配置及名义参数 | 无直接原文依据；工程选择（本工程自定） | [config.py](../src/adi_model/config.py)：`Config.rtl_structural`、`paper_literal` | 工程选择 | 静态审计 | 9位前级、40MS/s、±3V可分别由 [00] 核对；63+8、12位后级窗口、`range`及16相位不因此成为披露参数。`Config()`和`paper_consistent()`保留各自历史口径。 |
| 公共pipeline入口 | 无直接原文依据；工程选择（本工程自定） | [pipeline.py](../src/adi_model/pipeline.py)：`run_pipeline` | 工程选择 | 静态审计 | 当前是转发壳，实际执行在下一行的`execute_split`；旧`pipeline.py:348–354`不能定位当前实现。 |
| 结构行为执行及事件归属 | [00] PDF1及Fig.9.8.1；[09] 匹配采样通路／并行残差DAC主题 | [pipeline_engine.py](../src/adi_model/pipeline_engine.py)：`execute_split` | 机制对齐 | 静态审计 | 在行为域保持采集电荷与物理组归属；参考负载、动态节点与噪声仍是所选模型假设。 |
| 数字状态与粗细码合并 | 无直接原文依据；工程选择（本工程自定） | [reconstruction.py](../src/adi_model/reconstruction.py)：`DigitalState`、`reconstruct`、`Calibrator` | 工程选择 | 静态审计 | 合并、已知扰动扣除和标量校准按工程合同；不是片内背景学习器。 |
| unary兼容数字封装 | 无直接原文依据；工程选择（本工程自定） | [digital_core.py](../src/adi_model/digital_core.py)：`DigitalCore`、`PipelineState` | 工程选择 | 静态审计 | split生产引擎不实例化此类；不能用此接口说明当前结构核已逐拍完成。 |
| DEM码与状态映射 | [00_1] PDF29–31及 [11] 多级洗牌主题仅作比较 | [mapper.py](../src/adi_model/mapper.py)：`Mapper`、`dem_state_sequence`、`unit_rank_arrays` | 工程选择 | 静态审计 | 状态序列和64等权几何助手为工程定义；状态覆盖不证明当前63+8或物理slice等使用。 |
| split掩码与bridge候选 | [00_1] PDF31；[11] 跨slice低位分配主题仅作比较 | [dem.py](../src/adi_model/dem.py)：`split_switch_command` | 工程选择 | 静态审计 | 8×8删除cell63、子阵列轮转及`sid[8]`±1交换；有名义守恒与使用偏置，未复原原披露矩阵。 |
| 8/18因果调度 | [00] PDF1；[00_1] PDF29–31 | [scheduler.py](../src/adi_model/scheduler.py)：`Scheduler`、`ShuffledScheduler`、`make_scheduler` | 机制对齐 | 静态审计 | 只对齐架构选择与归属；generic调度策略与RTL规则不是同一个随机算法。 |
| RTL调度及dither序列镜像 | 无直接原文依据；工程选择（本工程自定） | [timing.py](../src/adi_model/timing.py)：`build_rtl_slice_plan`、`rtl_dither_codes`、`build_slice_plan` | 工程选择 | 静态审计 | 与项目RTL的ID、种子、预热和事件规则对照，不是原芯片内部随机源重建。 |
| 采样dither路径 | [10] PDF35–36：采样期开关注入、相关改码及后级去除主题 | [sampler.py](../src/adi_model/sampler.py)；[mapper.py](../src/adi_model/mapper.py)：`dither_transfer_code` | 机制对齐 | 静态审计 | 限采样开关注入／配对处理的机制；预留4单元、衰减、量程和端点仍为工程选择，不等于完整lower/upper电路。 |
| 采样rail与已知扰动电荷 | [10] PDF35–36仅提供采样开关注入主题；具体单位网络为工程选择 | [sampling_charge.py](../src/adi_model/sampling_charge.py)：`sampling_dither_injection` | 工程选择 | 静态审计 | 计算本工程所绑定采样mask的电荷；不能据此证明原文具体电容数、衰减及端点已复原。 |
| 结构`quantizer/range`路径 | 无直接原文依据；工程选择（本工程自定） | [config.py](../src/adi_model/config.py)：`rtl_structural`、`dither_rdac_ratio`；[sampler.py](../src/adi_model/sampler.py)；[mapper.py](../src/adi_model/mapper.py)：`dither_transfer_code` | 工程选择 | 静态审计 | SADC看`x+d_Q`、RDAC保存`x`，`d_R=0`；改码`−round(d_Q/ΔDAC)`，最终扣除`d_R=0`。[00_1] PDF17/18双注入图不能证明该简化即原实现。 |
| 理想`dual_port`路径 | [10] PDF22 Fig.19及PDF33仅作结构比较 | [config.py](../src/adi_model/config.py)：`paper_literal`、`dither_rdac_ratio`；[mapper.py](../src/adi_model/mapper.py)：`dither_transfer_code` | 工程选择 | 静态审计 | 当前固定`d_R=4d_Q`不是专利分位字披露。Fig.19全字进RDAC，部分位进SADC，另部分位用于改码；两者不能逐样本等同。 |
| 训练输入及静态有效权重 | [00] PDF1仅披露外部求系数、片上校正的分工 | [weight_calibration.py](../src/adi_model/weight_calibration.py)：`DigitalObservation`、`fit_unit_weights`、`FrozenCalibration`、`run_with_split_calibration` | 工程选择 | 静态审计 | 已知输入SVD、1278权重+1偏移、满秩判断、冻结和留出均是工程方法；不读取plant真实电容，不等同原文未公开的估计器。 |
| 可辨识性与回归数学助手 | 无直接原文依据；工程选择（本工程自定） | [calib.py](../src/adi_model/calib.py)：`use_matrix`、`observability`、`ridge_fit`、`required_samples` | 工程选择 | 静态审计 | 数学工具和样本数建议不是论文原算法或芯片训练控制器。 |
| 748位RTL观察解包 | 无直接原文依据；工程选择（本工程自定） | [rtl_observation.py](../src/adi_model/rtl_observation.py)：`observations_from_rtl`；[ADR0021](adr/0021-structural-contract-calibration.md) | 工程选择 | 静态审计 | 解包并核对ID／mask／模式；无捕获FIFO、ready及片外输送，不保证40MS/s连续观察可读。 |
| 整数重构及码流 | 无直接原文依据；工程选择（本工程自定） | [fixed_point.py](../src/adi_model/fixed_point.py)：`FixedPointFormat`、`FixedPointReconstructor`、`CodeStream` | 工程选择 | 静态审计 | Q30/Q32、48/64/96位、round-even、floor和20位offset-binary由[算术合同](rtl/RTL_ARITHMETIC_CONTRACT.md)定义。 |
| SADC及ADC2名义码合同 | 无直接原文依据；工程选择（本工程自定） | [sadc.py](../src/adi_model/sadc.py)：`build_first_stage_quantizer`；[adc2.py](../src/adi_model/adc2.py)：`ADC2` | 工程选择 | 静态审计 | 前级9位有 [00] 依据；阈值网格、后级静态量化、窗口、饱和／端点语义须按具体配置理解，不构成CT后级。 |
| 结果容器及显式整数出口 | 无直接原文依据；工程选择（本工程自定） | [sim.py](../src/adi_model/sim.py)：`SimResult.to_codes`、`run_sim`、`run_with_calibration`；[sim_split.py](../src/adi_model/sim_split.py)：`run_sim_split_reference` | 工程选择 | 静态审计 | `run_pipeline.out`是浮点诊断，20位整数需显式调用`to_codes()`；历史静态unary／split参考不替代当前结构DUT。 |
| CAAZ样本域及完整码配对 | PKU论文 PDF1/2 Fig.5/6；PKU讲稿 PDF34/35，页脚Slide33/34 | [chopping_az.py](../src/adi_model/chopping_az.py)：`run_caaz_pipeline`、`pair_readout` | 机制对齐 | 静态审计 | 仅输入交替极性、偶更新／奇保持、完整码后偶减奇。当前误差在RA输出注入，保持／传递比为假设；不等同原图A1输入侧噪声及CBIAS电路。 |
| 码历史预跟踪 | [12](#ref-12) PDF12：采集前由其他转换信息更新及多笔加权主题 | [pretracking.py](../src/adi_model/pretracking.py)：`QuantizedPretracker` | 机制对齐 | 静态审计 | 以真正可用的coarse码生成目标，保留来源和时刻；具体来源模式、权重与有限网络为工程实现。不是时偏学习器或CICC后级平均。 |
| 独立交织电荷比较 | [12] PDF12：预跟踪主题 | [interleave_tracking.py](../src/adi_model/interleave_tracking.py)：`InterleavedSAR`、`TrackPolicy` | 工程选择 | 静态审计 | 使用理想输入结果的独立比较，不进入生产预跟踪；相邻顺序交换不是物理18/8分配。 |
| 物理池与名义／实际DAC边界 | [00] PDF1及 [09] 并行残差DAC主题仅作架构比较 | [slice_pool.py](../src/adi_model/slice_pool.py)：`PhysicalSlicePool`；[dac_arch.py](../src/adi_model/dac_arch.py)：`SplitChip`、`SplitDAC` | 工程选择 | 静态审计 | 这些属于模拟被测ADC的行为模型。实际电容、持有电压及真实增益不能冒充数字训练器可读观测；63+8及桥接网络是工程候选。 |
| 电荷独立参考 | 无直接原文依据；工程选择（本工程自定） | [charge_ref.py](../src/adi_model/charge_ref.py)：`ref_ra_charge`、`ref_ra_charge_nodal`、`ref_ra_charge_dither_closed`、`ref_ra_charge_dither_nodal` | 工程选择 | 静态审计 | 闭式／节点法是独立数学核对工具；数值闭合不表示SPICE、噪声或物理签核。 |
| 辅助输入电荷近似 | [13](#ref-13) PDF16–17：辅助输入绕RC供内部负载／预充电主题，仅作比较 | [aux_input.py](../src/adi_model/aux_input.py)：`AuxInputStage` | 工程选择 | 静态审计 | 一阶电荷与理想自举对照，不证明真实背栅、恒定VGS或电气收益；不是数字校准器。 |
| 参考阈值伺服近似 | [14](#ref-14) PDF23–25：比较器控制参考与阈值调整主题，仅作比较 | [ref_track.py](../src/adi_model/ref_track.py)：`RefTrackSim` | 工程选择 | 静态审计 | 比例恢复／跨周期更新是行为近似，不能当成原比较器延迟回路或参考—RA—ADC2联合电路已验证。 |
| KTC研究支路 | 无原文依据；工程选择（本工程自定） | [ktc.py](../src/adi_model/ktc.py)：`KTCBranch`；[noise_phase.py](../src/adi_model/noise_phase.py) | 工程选择 | 静态审计 | NOTICE明确为本工程研究扩展，不归属 [00]/[09]–[14]；不进入当前接受的整数观察接口。 |
| 来源、文件和结果维护 | 无原文依据；工程选择（本工程自定） | [provenance.py](../src/adi_model/provenance.py)、[inventory_gate.py](../src/adi_model/inventory_gate.py)、[serialization.py](../src/adi_model/serialization.py) | 工程选择 | 静态审计 | 参数分级、文件身份及记录结构检查，不是电路机制正确或signoff证明。 |
| 发表结果参考 | 无直接原文依据；工程选择（本工程自定） | [benchmarks.py](../src/adi_model/benchmarks.py)：`PublishedBenchmark` | 工程选择 | 静态审计 | 保存发表结果的比较入口；它不是本工程实测、器件参数辨识或性能保证。 |
| 实验组织、评分与显示 | 无原文依据；工程选择（本工程自定） | [experiments.py](../src/adi_model/experiments.py)、[closure_experiments.py](../src/adi_model/closure_experiments.py)、[acceptance.py](../src/adi_model/acceptance.py)、[metrics.py](../src/adi_model/metrics.py)、[reporting.py](../src/adi_model/reporting.py)、[cli.py](../src/adi_model/cli.py) | 工程选择 | 静态审计 | 这些是复现和评分工具，不进入数字学习器运行时；旧unary、generic split与结构实验不能相互替代证据。 |

现存行为证据入口为 [结构校准结果及原始源SHA](readme/data/structural_calibration_summary.json)、[CAAZ存量运行记录](caaz/verification.json)、[结构样本合同](adr/0021-structural-contract-calibration.md) 和 [工作状态表](digital_status_matrix.md)。各记录的受测源码SHA保留原值，适用激励与模型范围不变；当前注释更新只通过逐文件静态保持检查与基线关联，不表示当前文件仍与旧SHA逐字节相同，也不表示重跑上述实验或通过SEC。

## 3. 17 个DSP扩展模块

AD4080手册用于核对公开外部功能；它不披露本工程内部CDAC、DEM、dither、桥接或位权学习算法。下表页码均为 [AD4080 Rev.B](#ref-ad4080) 的PDF页码。模块存在及定向bench通过，不等于完整器件协议符合。

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `ad4080_dsp_chain` | p64–71 Fig.96–109及Tables28–30：输出滤波与系统校正 | [ad4080_dsp_chain.sv](../ad4080_dsp/rtl/ad4080_dsp_chain.sv)：`ad4080_dsp_chain` | 工程选择 | 静态审计 | 组合校正／抽取／可选FIFO；内部valid和配置切换合同由项目定义，不是厂商内部结构。 |
| `ad4080_regs` | p73–94寄存器表；p27/34软件复位；p37/81严格实体访问 | [ad4080_regs.sv](../ad4080_dsp/rtl/ad4080_regs.sv)：`ad4080_regs` | 工程选择 | 静态审计 | 有寄存器映射，软件复位序列、partial访问及GPIO真实输入回读仍有差异；保存配置不等于对应通路已实现。 |
| `ad4080_top` | p19/33转换控制、p34–71接口及DSP功能，仅作外部功能比较 | [ad4080_top.sv](../ad4080_dsp/rtl/ad4080_top.sv)：`ad4080_top` | 工程选择 | 静态审计 | 工程原型顶层；FIFO到串行未连通，不能称器件完整数字子系统。 |
| `cal_two_point` | p71 Fig.108/109：系统两点校正要求，仅作比较 | [cal_two_point.sv](../ad4080_dsp/rtl/cal_two_point.sv)：`cal_two_point` | 工程选择 | 静态审计 | 固定横坐标的正向仿射拟合，未接顶层学习器；不能替代固定已知输入／可变raw码的逆向系数求解。 |
| `cnv_ctrl` | p19 Fig.31/32、p33转换控制；物理时序见p7–8 Table2 | [cnv_ctrl.sv](../ad4080_dsp/rtl/cnv_ctrl.sv)：`cnv_ctrl` | 工程选择 | 静态审计 | 数字CNV代理，不是差分接收器；组合系统的SAR转换源并未由该CNV启动。 |
| `event_detect` | p54–56 Tables25/26、Figs83–85；p90 Table55 | [event_detect.sv](../ad4080_dsp/rtl/event_detect.sv)：`event_detect` | 工程选择 | 静态审计 | 有阈值／计数／事件逻辑；计数时间基准及ALERT路由与器件要求未完整一致。 |
| `fifo_capture` | p56–63 Table27、Figs86–95 | [fifo_capture.sv](../ad4080_dsp/rtl/fifo_capture.sv)：`fifo_capture` | 工程选择 | 静态审计 | 四种捕获模式已有代码；模式11首次绕回前事件的完整16k读出仍有缺口，且未连接结果串行协议。 |
| `gpio_ctrl` | p52–56 Tables24–26；p87–90 Tables52–55 | [gpio_ctrl.sv](../ad4080_dsp/rtl/gpio_ctrl.sv)：`gpio_ctrl` | 工程选择 | 静态审计 | GPIO功能选择已有逻辑；输入回读、SDO输出使能和ALERT路由不能由端口名称推定完成。 |
| `lvds_framer` | p45–50 Figs74–81、Tables20/21；FIFO帧另见p60 Fig.89，仅作比较 | [lvds_framer.sv](../ad4080_dsp/rtl/lvds_framer.sv)：`lvds_framer` | 工程选择 | 静态审计 | 当前为SDR数字串行候选，单lane20次使能／帧；DCO为注册bit_en。DDR、echo-clock、FIFO帧和无损40MS/s输出未实现。 |
| `result_fifo` | p56–63的16k FIFO外部功能，仅作比较 | [result_fifo.sv](../ad4080_dsp/rtl/result_fifo.sv)：`result_fifo` | 工程选择 | 静态审计 | 通用同步队列的full/empty/overwrite实现，存储器结构与SRAM映射是项目选择。 |
| `sar20_ad4080_top` | 无直接原文依据；工程选择（本工程自定）；码制对照p20 Table7 | [sar20_ad4080_top.sv](../ad4080_dsp/rtl/sar20_ad4080_top.sv)：`sar20_ad4080_top` | 工程选择 | 静态审计 | offset-binary／补码转换及SAR错误门控；入口ID未随滤波、FIFO和串行输出传播。 |
| `sar20_system_top` | 无直接原文依据；工程选择（本工程自定） | [sar20_system_top.sv](../ad4080_dsp/rtl/sar20_system_top.sv)：`sar20_system_top` | 工程选择 | 静态审计 | 真实SAR数字核加DSP原型的工程组合；CAAZ在此顶层关闭且端口未引出，CNV转换闭合仍缺。 |
| `sinc1_decim` | p64–70 Figs96–107、Tables28–30；p94 Table62 | [sinc1_decim.sv](../ad4080_dsp/rtl/sinc1_decim.sv)：`sinc1_decim` | 机制对齐 | 静态审计 | 对齐输出SINC1抽取；内部位宽、舍入和按valid推进为工程合同，非CICC转换内平均。 |
| `sinc5_decim` | p64–70 Figs96–107、Tables28–30；p94 Table62 | [sinc5_decim.sv](../ad4080_dsp/rtl/sinc5_decim.sv)：`sinc5_decim` | 机制对齐 | 静态审计 | 对齐五阶积分／梳状输出抽取；未因此证明全抽取率频响、CNV建立基准及群时延。 |
| `sinc5_comp` | p64–70的SINC5补偿通带及额外2倍抽取要求，仅作比较 | [sinc5_comp.sv](../ad4080_dsp/rtl/sinc5_comp.sv)：`sinc5_comp` | 工程选择 | 静态审计 | 默认FIR系数`(1,0,0)`为占位；补偿结构存在不表示真实通带已补平。 |
| `spi_slave` | p34–42 Figs53–66、Tables14–19；strict见p37/81 | [spi_slave.sv](../ad4080_dsp/rtl/spi_slave.sv)：`spi_slave` | 工程选择 | 静态审计 | 基础Mode3／stream／CRC及CS取消有实现；SINGLE_INST、实体原子性、真实status及读写CRC仍有明确差异，不能写完整器件协议对齐。 |
| `sys_error_corr` | p71 Fig.108/109、p92 Table60、p93 Table61 | [sys_error_corr.sv](../ad4080_dsp/rtl/sys_error_corr.sv)：`sys_error_corr` | 机制对齐 | 静态审计 | 仅20位系统增益／失调校正机制；具体整数乘法、舍入、饱和和流水是工程选择，不是内部单元失配学习。 |
| DSP整数参考 | 无直接原文依据；工程选择（本工程自定） | [ad4080_cal_model.py](../ad4080_dsp/model/ad4080_cal_model.py)：`sys_corr`、`cal_two` | 工程选择 | 静态审计 | 对照本工程算术和forward拟合；不能用同向参考证明逆向校准或器件内部算法一致。 |

## 4. 有原文依据但尚未实现的部分

| 模块／机制 | 原文依据 | 本工程实现 | 对齐状态 | 证据等级 | 备注 |
| :--- | :--- | :--- | :---: | :---: | :--- |
| lower／upper分位字注入及完整范围增强电路 | [00_1] PDF17/18；[10] PDF22 Fig.19、PDF33说明及PDF35–36权利要求主题 | `range`与固定比例`dual_port`仅为上表两种工程模型，无原图完整绑定 | 未实现 | 静态审计 | 原图lower进入SADC、RDAC采样和SAR后／DEM前数字节点；upper仅进入后两处。当前模型不能证明分位字、内部余量和端点均一致。 |
| 原芯片binary-to-unary桥接开关矩阵 | [00_1] PDF31；[11] 多级洗牌及附加低位主题 | `split_switch_command`／`swap_decode`为已对拍的工程候选 | 未实现 | 静态审计 | 工程候选的名义守恒和Python/RTL一致不能反证其等于未公开完整矩阵。 |
| CAAZ模拟宏及H/K匹配 | PKU论文 PDF1/2 Fig.2/5/6；PKU讲稿 PDF28–35，页脚Slide27–34 | 仅样本域扰动、数字控制和pair处理，无FGA／CP-FIA／CBIAS晶体管宏及H/K观测闭环 | 未实现 | 静态审计 | `chopping_az.py`、`caaz_*_tb.sv`和AZ标签不证明存储相关性、注入与保持误差或归一化噪声传递相等。 |
| CICC连续时间后级及CINC／replica kickback | [CICC论文](#ref-cicc-paper) PDF1 §II Fig.1、PDF2 §III.B Fig.2/3；[CICC讲稿](#ref-cicc-slides) PDF17–22、47 | 当前`ADC2`为静态量化；fine SAR无该连续耦合模拟路径 | 未实现 | 静态审计 | 后级位宽相近不等于CT后级；`pretracking.py`处理前级采集前预置。 |
| CICC 8次LSB重复／7只反馈电容 | CICC论文 PDF2 §III.A/B Fig.2/3；讲稿PDF47 | 无该转换内反馈及重复判决控制 | 未实现 | 静态审计 | 不能将额外输出抽取或CAAZ两样本pair称为LSB重复。 |
| CICC SINC² tracking averaging | CICC论文 PDF1–2 §II Fig.1；讲稿PDF24–32，尤其29–32 | 无重复比较DT的一、二次累积及匹配反馈／抽取复原 | 未实现 | 静态审计 | 现有输出SINC1/SINC5及前级预跟踪均不是此机制。 |

## 5. 来源目录及引用边界

以下覆盖 [NOTICE](../NOTICE) 的22个有号来源；其中两份CICC原件由原有references清册同步列入。表中列出的论文／学位论文DOI已按官方Crossref或学校记录核对；讲稿未核实独立DOI时不套用论文DOI。六份仓库原件的身份见 [references/source_manifest.json](../references/source_manifest.json)，其余已登记本机原件按 [sources_manifest.json](../sources_manifest.json) 的文件名、SHA和`ADC_SOURCES_DIR`定位。PDF页码从文件第一页起计，不与印刷页或讲稿页脚编号混用。

| 来源编号 | 题录／专利权利要求主题 | 本表定位或角色 |
| :--- | :--- | :--- |
| <a id="ref-00"></a>[00] | Rares Bodnar等，*A 9.3nV/rtHz 20b 40MS/s 94.2dB DR Signal-Chain Friendly Precision SAR Converter*，ISSCC 2024，9.8；[DOI](https://doi.org/10.1109/ISSCC49657.2024.10454329) | [论文原件](../references/ISSCC2024_PAPER.pdf)：PDF1架构与外求系数／片上校正，PDF2 Fig.9.8.1–3；不是本工程定点／训练细节来源。 |
| <a id="ref-00-1"></a>[00_1] | 同[00]作者，ISSCC 2024 Session9.8讲稿；未见独立DOI／待确认 | [讲稿原件](../references/ISSCC2024_SLIDES.pdf)：PDF17/18注入图、29–31 DEM／桥接、20–22交织误差。 |
| <a id="ref-09"></a>[09] | US10516408B2：分离量化器与匹配采集时间常数的并行DAC、控制字及备用slice交换 | 本机登记原件PDF32的权利要求主题；用于架构对照，不证明16相位或63+8。 |
| <a id="ref-10"></a>[10] | US10505561B2：采样DAC注入dither、SADC不接全部dither、改码及后级去除 | 本机登记原件PDF22 Fig.19、PDF33及35–36；完整分位字路径尚未实现。 |
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

原文的lower／upper是注入位置与码字分配关系，不能用单一倍率、相同模块名或结果图相似替代核对。本页不修改任何Python／RTL行为、配置数值、结果或图表。
