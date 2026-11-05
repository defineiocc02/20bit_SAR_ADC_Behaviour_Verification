# 数字部分实现状态与证据矩阵

功能与源码保留基线：`main@af8aa37d43db40eb896e369305883ec89786547b`。代码链接定位本轮注释整理后的冻结文件；存量运行身份仍以原记录的commit、源码SHA、参数与输入为准。本页逐行登记 `rtl/rtl_sources.f` 的 **26 个模块**、`ad4080_dsp/rtl` 的 **17 个模块**、数字侧 Python 及三个接口。只整理该主线已有源码与证据，不引入未提交清理树的修复、接口或新测试结果；本次文档维护没有重新运行仿真。

| 能声称 | 不能声称 |
|:---|:---|
| 行为模型、静态失配校准流程、数字控制与重构 RTL 是三项交付物；各自已有条件仿真／数字对拍证据。 | 三项交付物已经等价于原芯片完整电路、实物训练或量产数据通路。 |
| 选定工程合同下，实际开关、样本归属、冻结系数装载和整数重构可以由已有 bench 与静态 plant 留出结果检查。 | 20 位码宽等于 20 位 ENOB；40 MS/s 参数或理想 640 MHz 时钟证明目标工艺达到该速率。 |
| DSP 是已经接入 SAR 的输出处理原型；CAAZ 是默认关闭、独立验收的研究扩展。 | DSP 尚无连接，或 DSP／CAAZ 的存在使逐结果元数据、PHY、真实 AZ 存储及噪声传递自动闭合。 |
| 来源、当前实现、受测快照和未闭合项分别登记；af8受测源码及经核实的注释保持版本可在原适用域引用，不表示后者重新运行。 | 测试文件存在、静态引用通过、旧记录退出 0，或历史完成度百分比证明当前完整功能通过。 |

## 判读规则与证据入口

“当前状态”描述实际完成或缺口，不是原文对齐程度；原文披露与工程选择见[来源清单](../sources_manifest.json)、[当前范围](model_scope.md)及[ADR0021](adr/0021-structural-contract-calibration.md)。本页的“最高证据等级”只使用六类：**静态审计、smoke、bench 对拍、形式验证、综合与 STA、归档历史**。

- **静态审计**：确认源码、连接或合同；没有据此声称执行通过。
- **smoke**：有限配置／输入的基本运行检查，不能推广为完整算法或频响。
- **bench 对拍**：已有执行记录支持的数值／协议／异常对拍，限表中配置。Python 数学参考与条件行为对拍同属此类，不是电路测量。本页该等级引用的是原运行快照及其注释保持关联，不表示注释后SHA经过新bench运行。
- **形式验证**：须有对应源、参数、假设和属性的证明记录；有限 BMC 不等于无界证明。**本页不将旧 DSP BMC 升为当前形式验证通过。**
- **综合与 STA**：须匹配当前源、顶层、目标库、约束、角及阶段。**本页没有当前目标 ASIC 的此类通过声明。**
- **归档历史**：旧 bench／BMC／FPGA 或未形成当前源码身份闭环的记录；不据此覆盖当前缺口。

| 索引 | 精确入口与允许引用范围 |
|:---|:---|
| 核 RTL 存量验证 | [CAAZ 验证记录](caaz/verification.json)：34 个 bench、8 个 lint profile，以及独立配对／上下文／共享向量计数；[旧注释保留记录](reviews/20261105-comment-preservation.json)提供原运行快照到af8部分文件的保持关联；本轮af8到注释后源码另作独立AST／功能token／工具pragma核对，**不是 SEC或新bench执行**。 |
| 静态校准联合留出 | [结构校准摘要](readme/data/structural_calibration_summary.json)：在af8基线核对时，26 个 `.f` 模块与 2 个头文件SHA均与该基线匹配；注释后字节通过本轮保持核对关联，不回填原SHA。scope 明确是数字 DUT＋独立固定失配静态 plant。没有由此验证 CAAZ 模拟电路或所有兼容分支。 |
| Python 存量验证 | [已有维护记录](reviews/20261105-readability-maintenance.md#实际执行与判据)、[独立维护记录](reviews/20261105-independent-maintenance.md#本轮验证和数值记录)、[复现入口](REPRODUCING.md)：软件回归、条件实验及独立整数／FFT核对须分别读条件，不混批次。 |
| DSP 的af8基线身份 | [验证摘要](reviews/20261105-verification-summary.json)的 `production_sha256` 在af8核对时与DSP **16/17** 文件相同；注释后新字节不再声称直接匹配该旧摘要。`sar20_system_top.sv` 为例外，不能直接套摘要 PASS。该摘要也不能把未激活的滤波／FIFO模式变成已对拍。 |
| DSP 旧执行记录 | [历史仿真](../ad4080_dsp/docs/verification_sim.md)、[历史 BMC](../ad4080_dsp/docs/formal.md)、[历史 ECP5](../ad4080_dsp/docs/synthesis_ecp5.md)分别限旧快照及所列用例。当前标准 runner 列 34 个默认 bench，独立 DSP 脚本列 7 台；旧“5/5”不可替代二者。 |
| 不作为当前完成表 | [旧完成度](../ad4080_dsp/docs/status_completion.md)的 95%／90%／35%及“基本做完”，[旧综合风险审查](../ad4080_dsp/docs/synthesis_review.md)的手算面积／延时，均为归档历史。唯一全局状态入口仍是 [STATUS](STATUS.md)。 |

本轮对af8与注释后冻结代码作了独立静态保持核对；Python检查去docstring的AST及类型注释，RTL检查功能token、预处理指令及工具pragma。此关联只延续原适用域，不是SEC、新原始源码SHA匹配或完整协议／工艺验证；未被核对覆盖的文件不据此继承结论。旧运行记录可能来自不同实际commit，不能统改为“af8新运行”。原始summary、结果JSON与旧保持记录均保持产生时字节。

可在仓库根目录查看本轮差异：

```bash
git diff af8aa37 -- src rtl sim ad4080_dsp tools tests
```

该命令仅展示差异，本身不自动证明行为等价。表中模块／函数／实例行号已按冻结源码重新定位；行号变动不改变原文PDF页码、观察包位域或运行记录身份。

## 三项交付物与扩展

| 对象 | 当前状态 | 最高证据等级 | 缺失项与边界 | 证据入口 |
|:---|:---|:---|:---|:---|
| ADC 行为模型 | 已有物理 slice／电荷／RA／ADC2 及失配机制模型，配置入口适用域不同。 | bench 对拍 | 工程参数、近似和假设不等于未公开电路事实；动态、噪声与工作域须按实际配置判断。 | [范围](model_scope.md)、[物理流水测试](../tests/integration/test_physical_pipeline.py) |
| 静态失配校准流程 | 独立已知输入＋数字观测训练有效 C/Cf，冻结装入相同 DUT，用不同激励留出。 | bench 对拍 | 不识别唯一绝对电容 F 值；真实参考、读出、漂移／动态训练和 ADC2 非线性未闭合。 | [ADR0021](adr/0021-structural-contract-calibration.md#闭环与结果)、[两芯片摘要](readme/data/structural_calibration_summary.json) |
| 数字控制与重构 RTL | 结构和兼容路径、配置／位权应用、样本上下文、整数结果及异常已实现。 | bench 对拍 | 局部数字合同不证明真实模拟关闭沿、完整原文电路、CDC/RDC或工艺速率。 | [RTL入口](../rtl/README.md)、[算术合同](rtl/RTL_ARITHMETIC_CONTRACT.md) |
| 输出 DSP | **已接入的输出原型**：校正、滤波、捕获、配置 SPI、GPIO与SDR帧。 | bench 对拍 | 此等级仅覆盖匹配存量证据的默认接入／异常合同；各模块的旧记录、缺系数、元数据和串行缺口见下表。 | [DSP说明](../ad4080_dsp/README.md)、[接口合同](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| CAAZ | 默认关闭；样本域模型与数字控制／配对有条件验证。 | bench 对拍 | 不是 ISSCC 目标芯片已实现项；CBIAS、H/K、物理 chopper／FGA／CP-FIA未验证，DSP系统封装未启用。 | [CAAZ说明](caaz/README.md)、[验证与未执行项](caaz/verification.json) |
| CICC连续时间后级／tracking averaging | 参考技术，当前未实现。 | 静态审计 | 现有输出 SINC、预跟踪、数字配对不能替代它。 | [STATUS说明](STATUS.md#随机化和可选扩展的分工) |

## `rtl_sources.f`：26 个模块逐行

以下“已实现”限选定工程合同；兼容路径不冒充默认结构路径。最高等级为存量对拍，不是本页新运行或原文完整电路复现。[编译清单](../rtl/rtl_sources.f)实际为26项，旧入口中“24模块”文字不能作为数量依据。

| 模块／声明行 | 交付归属与职责 | 当前状态 | 最高证据等级 | 缺失项／有效范围 | 证据入口 |
|:---|:---|:---|:---|:---|:---|
| [adc2_dec L11](../rtl/core/adc2_dec.sv#L11) | 重构：ADC2码仓中心与定点解码 | 已接入 | bench 对拍 | 数字栅格／舍入合同；不证明后级实际量程、非线性。 | [算术审计](../sim/tb/arithmetic_audit_tb.sv#L27)、[算术合同](rtl/RTL_ARITHMETIC_CONTRACT.md) |
| [caaz_cycle_ctrl L14](../rtl/control/caaz_cycle_ctrl.sv#L14) | 可选扩展：极性与AZ代控制 | 默认关闭，启用路径有数字验证 | bench 对拍 | AZ标签不证明真实两周期存储与隔离。 | [上下文bench](../sim/tb/caaz_context_contract_tb.sv)、[CAAZ验证](caaz/verification.json) |
| [caaz_pair_processor L14](../rtl/calibration/caaz_pair_processor.sv#L14) | 可选扩展：完整raw码后配对 | 有ID／极性／AZ资格与异常策略 | bench 对拍 | 仍缺错误统计、自动恢复、事件／pending读出；非法pair策略不自动等同Python。 | [pair bench](../sim/tb/caaz_pair_processor_tb.sv)、[共享向量](../sim/tb/caaz_python_vectors_tb.sv) |
| [analog_phase_ctrl L10](../rtl/core/analog_phase_ctrl.sv#L10) | 转换控制：注册相位与模拟宏使能 | 已接入16相工程时序 | bench 对拍 | 不证明本地保持、非交叠、真实比较器／参考建立。 | [结构协议](../sim/tb/structural_protocol_tb.sv)、[RTL时序说明](../rtl/README.md) |
| [cal_output_stage L12](../rtl/core/cal_output_stage.sv#L12) | 重构：提交／保持candidate、ID与flags | 已接入 | bench 对拍 | 数值饱和／判断在recon_core；元数据到SAR退休口，不自动贯穿DSP输出。 | [算术审计](../sim/tb/arithmetic_audit_tb.sv#L63)、[接入bench](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv) |
| [cal_residue_mac L10](../rtl/core/cal_residue_mac.sv#L10) | 重构：定点残差MAC及溢出 | 已接入 | bench 对拍 | 明确位宽／归一化，不是实际RA增益测量。 | [算术审计](../sim/tb/arithmetic_audit_tb.sv#L26)、[算术合同](rtl/RTL_ARITHMETIC_CONTRACT.md) |
| [cal_sample_context L13](../rtl/core/cal_sample_context.sv#L13) | 样本归属：residue→fine上下文 | 已保持前代ID、mask与异常 | bench 对拍 | 不锁存ADC2原码，fine完成后由上层配context；同沿优先级不证明实际输送。 | [结构观测bench](../sim/tb/structural_calibration_observe_tb.sv)、[ADR0021](adr/0021-structural-contract-calibration.md#观测口与样本归属) |
| [cal_weight_reduce L15](../rtl/core/cal_weight_reduce.sv#L15) | 校准应用：T/G/R纯组合权重归约 | 已接入物理坐标权重，无clk／流水 | bench 对拍 | 应用片外系数，不是片内学习器或绝对电容测量。 | [物理重构bench](../sim/tb/calibration_physical_tb.sv)、[归约对拍](../sim/tb/cal_weight_reduce_ppa_tb.sv) |
| [calib_regs L12](../rtl/core/calib_regs.sv#L12) | 配置：标量与validate资格 | 已有配置／异常约束 | bench 对拍 | 无损动态更新、外部总线与epoch传输仍需系统合同。 | [外设bench](../sim/tb/p2_periph_tb.sv#L200)、[配置bench](../sim/tb/review_config_tb.sv) |
| [ctrl_fsm L11](../rtl/core/ctrl_fsm.sv#L11) | 兼容路径：旧码域节拍 | 保留在`.f`，非默认结构控制器 | bench 对拍 | 不把兼容节拍作为16相结构模拟截止证据。 | [兼容外设bench](../sim/tb/p2_periph_tb.sv#L183)、[两种路径](../rtl/README.md#两种精化-profile) |
| [dem_addr_gen L13](../rtl/core/dem_addr_gen.sv#L13) | 随机化：主／子阵列地址排列 | 已有63缺格／8sub工程映射 | bench 对拍 | 名义守恒不证明等使用、误差塑形或原芯片完整映射。 | [P1 bench](../sim/tb/p1_tb.sv#L85)、[bridge枚举](../sim/tb/bridge_mask_audit_tb.sv) |
| [dem_state_gen L12](../rtl/core/dem_state_gen.sv#L12) | 随机化：bank的DEM状态 | 已接入状态流 | bench 对拍 | 状态遍历不是失配参数估计；结构DEM位还联动slice shuffle。 | [P1 bench](../sim/tb/p1_tb.sv)、[结构合同](../tests/unit/test_structural_contract.py) |
| [dither_gen L13](../rtl/core/dither_gen.sv#L13) | 随机化：离散扰动码 | 已有工程序列 | bench 对拍 | 固定结构序列不等于浮点幅度扫参或原文双端口电路。 | [dither bench](../sim/tb/review_dither_tb.sv#L12)、[状态页](STATUS.md#配置先分清) |
| [div_floor L12](../rtl/core/div_floor.sv#L12) | 重构：整数floor除法 | 已有符号／边界合同 | bench 对拍 | 纯数字算术，不含模拟精度或目标时序。 | [借位穷举](../sim/tb/divider_borrow_tb.sv)、[算术审计](../sim/tb/arithmetic_audit_tb.sv#L56) |
| [rdac_drv L11](../rtl/core/rdac_drv.sv#L11) | 数字驱动：slot散射到物理slice输出 | 已接入注册mask | bench 对拍 | 数字引脚不证明局部锁存、开关电气或保持节点。 | [外设bench](../sim/tb/p2_periph_tb.sv#L169)、[RDAC事件](../sim/tb/rdac_event_contract_tb.sv) |
| [recon_core L17](../rtl/core/recon_core.sv#L17) | 重构：归约、ADC2、MAC、除法及退休 | 已接入冻结位权 | bench 对拍 | 静态有效参数；动态建立、漂移和ADC2非线性未被自动校正。 | [恢复](../sim/tb/calibration_recovery_tb.sv)、[拟合](../sim/tb/calibration_fit_tb.sv)、[重构协议](../sim/tb/review_recon_protocol_tb.sv) |
| [sar_structural_ctrl L18](../rtl/core/sar_structural_ctrl.sv#L18) | 转换控制：双coarse／共享Flash／前代fine | 默认结构路径已接入 | bench 对拍 | 比较器valid与宏时序来自外部；不是晶体管级SAR完成证明。 | [SAR试探](../sim/tb/sar_trial_tb.sv)、[结构协议](../sim/tb/structural_protocol_tb.sv) |
| [sar_trial_ctrl L10](../rtl/core/sar_trial_ctrl.sv#L10) | 转换控制：逐位试探与已决码 | 已有握手与截止处理 | bench 对拍 | 需真实比较器完成沿及输入稳定性。 | [试探bench](../sim/tb/sar_trial_tb.sv) |
| [slice_alloc L11](../rtl/core/slice_alloc.sv#L11) | 兼容路径：固定分组 | 保留，不等于结构因果池 | bench 对拍 | 不能借此声明18/8物理采样归属已验证。 | [兼容分配实例](../sim/tb/review_leaf_tb.sv#L31)、[路径说明](../rtl/README.md#两种精化-profile) |
| [slice_pool_ctrl L14](../rtl/core/slice_pool_ctrl.sv#L14) | 物理选择：因果8/8/2及priming | 已保持实际前次采集组归属 | bench 对拍 | 分配／随机化不是逐slice时偏、带宽学习或补偿。 | [结构协议](../sim/tb/structural_protocol_tb.sv)、[观察bench](../sim/tb/structural_calibration_observe_tb.sv) |
| [status_regs L11](../rtl/core/status_regs.sv#L11) | 异常：sticky与错误状态 | 已有数字错误报告 | bench 对拍 | 状态位不代表模拟节点已被真实观测；软件事件队列另需实现。 | [外设bench](../sim/tb/p2_periph_tb.sv#L148)、[顶层协议](../sim/tb/review_top_protocol_tb.sv) |
| [swap_decode L14](../rtl/core/swap_decode.sv#L14) | 开关译码：计数、bridge、裁剪 | 已有零和bridge工程候选 | bench 对拍 | `sid[8]`规则不是讲稿`P=50%`的完整物理真值表。 | [P1 bench](../sim/tb/p1_tb.sv#L89)、[262144枚举](../sim/tb/bridge_mask_audit_tb.sv) |
| [unit_therm L12](../rtl/core/unit_therm.sv#L12) | 开关译码：物理unit mask／rails | 已有数字mask合同 | bench 对拍 | 不证明真实电容等值、失配电荷或模拟bridge。 | [P1 bench](../sim/tb/p1_tb.sv#L98)、[bridge审计](../sim/tb/bridge_mask_audit_tb.sv) |
| [weight_store L14](../rtl/core/weight_store.sv#L14) | 校准应用：系数、完整位图、row cache | 已有装载及旧值读选合同 | bench 对拍 | 不是模拟系数测量／背景学习器；传输与动态更新另验。 | [row cache bench](../sim/tb/weight_row_cache_tb.sv)、[静态装载闭环](readme/data/structural_calibration_summary.json) |
| [sadc_enc L11](../rtl/top/sadc_enc.sv#L11) | 量化接口：Flash温度计popcount | 已接入结构前端 | bench 对拍 | 不验证比较器门限、气泡模拟成因或孔径时序。 | [leaf bench](../sim/tb/review_leaf_tb.sv)、[映射bench](../sim/tb/tree_mapping_tb.sv) |
| [sar20_digital_core L24](../rtl/top/sar20_digital_core.sv#L24) | 主交付：结构／兼容控制、配置、重构、可选观测 | 已有静态数字DUT联合留出 | bench 对拍 | 调试输送、真实宏、完整动态／漂移、目标工艺及物理证据未闭合。 | [结构ADC](../sim/tb/structural_adc_tb.sv)、[observer关](../sim/tb/structural_adc_observer_off_tb.sv#L4)、[观测闭环](readme/data/structural_calibration_summary.json) |

## DSP：17 个模块逐行

本表区分“文件存在／代码接入”“匹配存量执行”“旧记录”。DSP与SAR同`clk`的接入测试不自动激活SINC/FIFO/串行全部模式；af8的16/17旧摘要匹配及本轮静态保持关联都不能证明每条未激活分支。[DSP说明](../ad4080_dsp/README.md)明确其为原型。

| 模块／声明行 | 交付归属与职责 | 当前状态 | 最高证据等级 | 缺失项／有效范围 | 证据入口 |
|:---|:---|:---|:---|:---|:---|
| [sar20_ad4080_top L24](../ad4080_dsp/rtl/sar20_ad4080_top.sv#L24) | 输出原型：码制、入口ID、flags／sticky隔离 | 已接入；无ready／反压 | bench 对拍 | 默认直通四边界码、五flag、模拟sticky、重启；无输出逐结果元数据。 | [接入scoreboard](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv#L95)、[契约](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [sar20_system_top L24](../ad4080_dsp/rtl/sar20_system_top.sv#L24) | 输出原型：真实SAR与DSP组合 | 连接存在；**af8与旧摘要的身份不闭合** | 静态审计 | af8源码`e0874217…`不等旧摘要`0d325cac…`；本轮注释保持不能补齐此前差异，已有集成bench与历史CI说明不冒充当前新执行。CAAZ未启用。 | [系统bench](../ad4080_dsp/sim/tb/sar20_system_top_tb.sv#L50)、[摘要SHA](reviews/20261105-verification-summary.json)、[历史受测说明](reviews/20261105-readability-maintenance.md#后续呈现细化成果在前结构在后) |
| [ad4080_top L26](../ad4080_dsp/rtl/ad4080_top.sv#L26) | 输出原型：处理／控制／捕获集成 | 已有默认直通与故障接入 | bench 对拍 | 此等级限默认接入；独立子功能为旧记录。FIFO数据不入串行，CNV只在DSP观察。 | [接入bench](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv)、[旧顶层记录](../ad4080_dsp/docs/verification_sim.md#3-顶层-tb-覆盖明细77-项) |
| [ad4080_dsp_chain L24](../ad4080_dsp/rtl/ad4080_dsp_chain.sv#L24) | 输出原型：校正／四选一滤波／可选FIFO | 工程top固定`USE_FIFO=0` | bench 对拍 | 默认bypass对拍；滤波组合和内部FIFO不能由此算通过，settled不是FIFO逐记录标签。 | [默认scoreboard](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv#L95)、[源码选路](../ad4080_dsp/rtl/ad4080_dsp_chain.sv#L90) |
| [sys_error_corr L25](../ad4080_dsp/rtl/sys_error_corr.sv#L25) | 输出原型：系统增益／失调／clip | 已有工程定点公式 | bench 对拍 | 当前接入对拍限unity；非unity有限向量为旧记录，精确厂商增益规律／舍入未由此证明。 | [校正bench](../ad4080_dsp/sim/tb/ad4080_cal_tb.sv#L69)、[默认接入](../ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv#L95) |
| [cal_two_point L25](../ad4080_dsp/rtl/cal_two_point.sv#L25) | 独立工具：固定横坐标正向拟合 | 未接顶层自动学习；error恒0 | 归档历史 | 不是固定输入、变化raw的逆校准器；不能替代位权SVD或完整系统校准。 | [正向六组](../ad4080_dsp/sim/tb/ad4080_cal_tb.sv#L99)、[方向反例](rtl/KNOWN_LIMITS_20261104.md) |
| [sinc1_decim L24](../ad4080_dsp/rtl/sinc1_decim.sv#L24) | 输出原型：N=2…1024块平均 | 数据路径存在 | 归档历史 | 旧N=4有限向量；无全N频响／物理建立闭环，valid计数不等于真实CNV。 | [N4用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L348)、[旧记录](../ad4080_dsp/docs/verification_sim.md) |
| [sinc5_decim L25](../ad4080_dsp/rtl/sinc5_decim.sv#L25) | 输出原型：N=2…256五阶CIC | 有五级流水／归一化 | 归档历史 | 旧smoke只测D=4、DC≈1000与settled；非当前完整幅频／群延时验证。 | [明确标smoke的bench](../ad4080_dsp/sim/tb/sinc5_decim_tb.sv#L40)、[旧结果](../ad4080_dsp/docs/verification_sim.md#2-结果总表) |
| [sinc5_comp L24](../ad4080_dsp/rtl/sinc5_comp.sv#L24) | 输出原型：三tap FIR＋额外2倍抽取 | 默认单位系数占位 | 归档历史 | `(32768,0,0)`没有补偿下垂；真实补偿系数、频响及负抽头验证未闭合。 | [默认DC用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L618)、[参数](../ad4080_dsp/rtl/sinc5_comp.sv#L24) |
| [result_fifo L25](../ad4080_dsp/rtl/result_fifo.sv#L25) | 输出原型：16k×20队列、overwrite、错误保护 | 存储体／同步读／指针存在 | 归档历史 | 有旧scoreboard与小BMC记录；BMC不证明16k内容，BRAM映射不等于ASIC SRAM宏。 | [16k随机scoreboard](../ad4080_dsp/sim/tb/result_fifo_tb.sv#L64)、[旧BMC](../ad4080_dsp/docs/formal.md#22-result_fifo容量与读写边界) |
| [fifo_capture L25](../ad4080_dsp/rtl/fifo_capture.sv#L25) | 输出原型：四模式捕获／读完／事件位置 | 控制已存在，生命周期未全闭合 | 归档历史 | mode11早事件可读空却等16k目标；非法水位取整、未读重arm、物理N+3 CNV未闭合。 | [旧模式用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L379)、[模式11](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L798)、[限制](../ad4080_dsp/docs/SAR20_INTERFACE.md) |
| [event_detect L24](../ad4080_dsp/rtl/event_detect.sv#L24) | 输出原型：阈值／迟滞／sticky／ALERT | 数字比较逻辑存在 | 归档历史 | ALERT按滤波后valid延迟；主top未对其施加HI/LO route，不能声称固定两CNV物理延时。 | [阈值用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L437)、[低阈值](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L729) |
| [gpio_ctrl L24](../ad4080_dsp/rtl/gpio_ctrl.sv#L24) | 输出原型：GPIO功能与低有效sync | 组合复用存在 | 归档历史 | SDO复用未接`sdo_oe`；输入数据回读仍是寄存值，pad／电气未验证。 | [SEL用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L717)、[复用](../ad4080_dsp/rtl/gpio_ctrl.sv#L65)、[回读](../ad4080_dsp/rtl/ad4080_regs.sv#L303) |
| [spi_slave L25](../ad4080_dsp/rtl/spi_slave.sv#L25) | 输出原型：Mode3字节协议与CS取消 | 当前项目边界有定向检查 | bench 对拍 | A6A6只证明重复字节位序；不证明手册status语义、strict实体、完整SINGLE_INST／CRC协议。 | [当前独立协议bench](../ad4080_dsp/sim/tb/spi_abort_contract_tb.sv#L51)、[旧BMC限域](../ad4080_dsp/docs/formal.md#21-spi_slave写提交地址仿真抓到过的-bug) |
| [ad4080_regs L25](../ad4080_dsp/rtl/ad4080_regs.sv#L25) | 输出原型：字节寄存器／W1C／配置 | 默认值／功能字段存在 | 归档历史 | 多byte不原子；部分位只保存；软件reset序列及FIFO交互不能称器件一致。 | [旧CSR用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L277)、[字节写路径](../ad4080_dsp/rtl/ad4080_regs.sv#L226) |
| [cnv_ctrl L24](../ad4080_dsp/rtl/cnv_ctrl.sv#L24) | 输出原型：同步边沿／busy错误代理 | 仅使用解码后的正相 | bench 对拍 | 两模式重复边沿测试不是差分接收器或tCONV测量；该pulse没有接到SAR启动。 | [数字代理bench](../ad4080_dsp/sim/tb/cnv_ctrl_contract_tb.sv#L30)、[实际逻辑](../ad4080_dsp/rtl/cnv_ctrl.sv#L39) |
| [lvds_framer L24](../ad4080_dsp/rtl/lvds_framer.sv#L24) | 输出原型：单／双lane SDR与重复帧 | 单pending、无反压 | 归档历史 | 单lane20次使能/帧对16tick输入会覆盖；DCO只是寄存bit_en；无DDR／PHY／FIFO串行。 | [旧位序用例](../ad4080_dsp/sim/tb/ad4080_top_tb.sv#L636)、[吞吐反例](../ad4080_dsp/sim/tb/lvds_cadence_tb.sv)、[限制](rtl/KNOWN_LIMITS_20261104.md#2026-11-04-rtl-与模型已知限制) |

## 数字侧 Python 与模型接口

以下按实际入口逐行列出；“bench 对拍”只指存量软件数学／条件行为检查或静态DUT联合对拍，不是Python已经变成可综合RTL。物理plant和数字观测接口分列，避免把真值当数字运行时输入。

| 入口／主要符号 | 交付归属与当前状态 | 最高证据等级 | 缺失项／有效范围 | 证据入口 |
|:---|:---|:---|:---|:---|
| [pipeline.run_pipeline L12](../src/adi_model/pipeline.py#L12) | 行为交付：统一调用壳，转execute_split | bench 对拍 | 壳存在不等于所有配置已验证。 | [物理流水](../tests/integration/test_physical_pipeline.py)、[等价检查](../tests/integration/test_pipeline_equivalence.py) |
| [pipeline_engine.execute_split L38](../src/adi_model/pipeline_engine.py#L38) | 行为／数字集成：实际信号链、数字观察与冻结应用 | bench 对拍 | `out`仍是浮点诊断；最终整数需显式to_codes。 | [流水等价](../tests/integration/test_pipeline_equivalence.py)、[静态留出摘要](readme/data/structural_calibration_summary.json) |
| [config.Config L61](../src/adi_model/config.py#L61) | 配置：旧unary／paper_literal／rtl_structural并存 | bench 对拍 | 入口不同不可混用；工程几何、dual-port等不提升为原文唯一电路。 | [配置范围](STATUS.md#配置先分清)、[结构合同](../tests/unit/test_structural_contract.py) |
| [dem.split_switch_command L54](../src/adi_model/dem.py#L54) | 数字命令：main/sub置换与零和bridge候选已接入 | bench 对拍 | 名义守恒不等于等使用、误差塑形或原芯片完整映射。 | [结构合同](../tests/unit/test_structural_contract.py)、[bridge枚举](../sim/tb/bridge_mask_audit_tb.sv) |
| [mapper.Mapper L100](../src/adi_model/mapper.py#L100)／[DitherState L205](../src/adi_model/mapper.py#L205) | 数字码域：兼容映射与已知扰动换算 | bench 对拍 | unary、generic dualport、structural range各有合同，不能重复扣dQ或混倍率。 | [RTL导出测试](../tests/unit/test_rtl_export.py)、[向量测试](../tests/unit/test_rtl_vectors.py) |
| [scheduler.make_scheduler L133](../src/adi_model/scheduler.py#L133) | 分配：generic固定／随机采集计划 | bench 对拍 | 不估计或补偿slice时偏／增益／带宽；rtl16不经本工厂。 | [池测试](../tests/unit/test_slice_pool.py)、[流水测试](../tests/integration/test_physical_pipeline.py) |
| [timing.build_rtl_slice_plan L76](../src/adi_model/timing.py#L76) | 分配：采集→转换因果计划与RTL规则Python allocator | bench 对拍 | 按RTL规则运行的Python频谱不是SV频谱或模拟宏时序证明。 | [结构合同](../tests/unit/test_structural_contract.py)、[池测试](../tests/unit/test_slice_pool.py) |
| [sampling_charge.sampling_dither_injection L17](../src/adi_model/sampling_charge.py#L17) | 数模边界：rail与已知采样扰动电荷 | bench 对拍 | 注入／改码／扣除必须配套；实际失配不由名义相消消除。 | [电荷闭合测试](../tests/regression/test_charge_parameter_closure.py)、[物理流水](../tests/integration/test_physical_pipeline.py) |
| [slice_pool.PhysicalSlicePool L101](../src/adi_model/slice_pool.py#L101) | **物理plant边界**：实际电容、保持状态和归属 | bench 对拍 | C、G、held voltage是plant状态，不是数字训练器可读真值；无PEX网络。 | [池测试](../tests/unit/test_slice_pool.py)、[物理流水](../tests/integration/test_physical_pipeline.py) |
| [dac_arch.SplitChip L92](../src/adi_model/dac_arch.py#L92)／[SplitDAC L389](../src/adi_model/dac_arch.py#L389) | **物理／名义模型边界**：分段桥接阵列 | bench 对拍 | 63+8等是工程候选；evaluate_physical不应变成数字估计器真值入口。 | [物理流水](../tests/integration/test_physical_pipeline.py)、[来源范围](model_scope.md) |
| [sadc.build_first_stage_quantizer L150](../src/adi_model/sadc.py#L150) | 量化模型：一级门限与码制 | bench 对拍 | 不证明实际比较器门限、完成沿和电气。 | [流水等价](../tests/integration/test_pipeline_equivalence.py)、[模型范围](model_scope.md) |
| [adc2.ADC2 L50](../src/adi_model/adc2.py#L50) | 量化接口：raw、箱中心与越界 | bench 对拍 | 窗口／码制为选定合同，不能倒推原器件内部披露。 | [定点测试](../tests/unit/test_fixed_point.py)、[流水等价](../tests/integration/test_pipeline_equivalence.py) |
| [reconstruction.reconstruct L59](../src/adi_model/reconstruction.py#L59)／[Calibrator L82](../src/adi_model/reconstruction.py#L82) | 数字研究：浮点组合与前台标量状态 | bench 对拍 | 不是最终整数出口，也不自动校正逐slice时偏／带宽。 | [流水等价](../tests/integration/test_pipeline_equivalence.py)、[重构镜像](../tests/unit/test_recon_mirror.py) |
| [digital_core.DigitalCore L58](../src/adi_model/digital_core.py#L58) | **旧unary参考包装**，仍可用但非9位结构主核 | bench 对拍 | 旧映射／回归结论不能移到split结构生产路径。 | [重构镜像](../tests/unit/test_recon_mirror.py)、[入口范围](STATUS.md#配置先分清) |
| [weight_calibration.fit_unit_weights L350](../src/adi_model/weight_calibration.py#L350) | 校准交付：数字观察＋独立参考→SVD有效C/Cf→冻结 | bench 对拍 | 1279未知量、激励、栅格和SVD是工程方法；不识别唯一绝对C或动态误差。 | [位权测试](../tests/integration/test_weight_calibration.py)、[独立DUT留出](readme/data/structural_calibration_summary.json) |
| [fixed_point.FixedPointReconstructor L74](../src/adi_model/fixed_point.py#L74) | 整数交付：系数量化、96位累加、除法、20位码 | bench 对拍 | Q30/Q32是工程格式；模拟越界与数字clip分开，不证明硬件时序。 | [定点测试](../tests/unit/test_fixed_point.py)、[重构镜像](../tests/unit/test_recon_mirror.py)、[算术合同](rtl/RTL_ARITHMETIC_CONTRACT.md) |
| [rtl_observation.observations_from_rtl L27](../src/adi_model/rtl_observation.py#L27) | 数字观测：解析748bit、检查mask与ID连续性 | bench 对拍 | 软件解析检查不等于硬件有FIFO／ready／epoch字段／外部输送。 | [观察测试](../tests/unit/test_rtl_observation.py)、[ADR0021](adr/0021-structural-contract-calibration.md#观测口与样本归属) |
| [calib.observability L133](../src/adi_model/calib.py#L133)／[calibrate_unit_weights L397](../src/adi_model/calib.py#L397) | 数学／旧学习工具：秩、回归、样本预算 | bench 对拍 | rank64／512等只限相应旧映射，不能代替当前1279维识别条件。 | [位权测试](../tests/integration/test_weight_calibration.py)、[校准合同](adr/0021-structural-contract-calibration.md#闭环与结果) |
| [sim.SimResult.to_codes L147](../src/adi_model/sim.py#L147) | 结果与显式整数出口；run_sim为静态unary旧入口 | bench 对拍 | 浮点诊断／旧静态入口不等于split最终20位结果。 | [定点测试](../tests/unit/test_fixed_point.py)、[适用域](model_scope.md) |
| [chopping_az.run_caaz_pipeline L254](../src/adi_model/chopping_az.py#L254) | 可选研究：完整raw重构后非重叠pair | bench 对拍 | 40raw→20pair是工程选择；H/K／CBIAS未验证，非法pair软件与RTL策略须分开。 | [CAAZ软件测试](../tests/unit/test_chopping_az.py)、[CAAZ验证](caaz/verification.json) |
| [pretracking.QuantizedPretracker L98](../src/adi_model/pretracking.py#L98) | 已入主链：使用时刻可得的历史数字决策预置 | bench 对拍 | 不是逐slice时偏／带宽学习器，不读取未来输入。 | [输入辅助测试](../tests/integration/test_input_assistance.py) |
| [interleave_tracking.InterleavedSAR L272](../src/adi_model/interleave_tracking.py#L272) | 独立机制比较：reset／own／other等预置 | bench 对拍 | 理想结果电荷核算，不进主链校准；电荷比不能直接写成带宽／噪声收益。 | [专利机制测试](../tests/unit/test_patent12_interleave_tracking.py) |
| [ref_track.RefTrackSim L281](../src/adi_model/ref_track.py#L281) | 独立参考整定机制模型 | bench 对拍 | threshold／A06回路未入主链；已有动态reference模型不等于该整定回路闭环。 | [参考机制测试](../tests/unit/test_patent14_ref_track.py) |
| [charge_ref.ref_ra_charge_nodal L93](../src/adi_model/charge_ref.py#L93) | 独立闭式／节点电荷oracle | bench 对拍 | 约1e−25 C仅数值闭合，不是电子数、噪声或SPICE精度。 | [电荷闭合测试](../tests/regression/test_charge_parameter_closure.py)、[范围](model_scope.md#evidence-and-units) |

## 三个接口必须分别闭合

| 接口 | 当前连接与能读取什么 | 当前缺口 | 最高证据等级 | 精确入口／完成条件 |
|:---|:---|:---|:---|:---|
| **748bit校准观测口** | 默认`P_CAL_OBSERVE=0`；启用时记录ADC2 raw、实际mask、slice、模式与ID，直接从SAR引出。 | 没有ready、FIFO、epoch字段或外部输送；**DSP结果FIFO没有接收此包**。40 MS/s名义29.92 Gbit/s不是可用链路证明。 | bench 对拍 | [位域与时刻](adr/0021-structural-contract-calibration.md#观测口与样本归属)、[解析](../src/adi_model/rtl_observation.py#L27)、[实际连接](../ad4080_dsp/rtl/sar20_system_top.sv#L122)。完成需实际读出、独立参考、缺样／epoch与受控捕获合同。 |
| **SAR↔DSP逐结果元数据** | 入口有码、valid、ID、五flags和独立模拟sticky；异常锁停并抑制旧在途输出，重启清全部DSP。 | `dout_analog_bad`仅观察未消费；sticky无具体坏样本ID。accepted/rejected是输入侧事件，不能当输出标签；无事件队列或反压。 | bench 对拍 | [接入逻辑](../ad4080_dsp/rtl/sar20_ad4080_top.sv#L78)、[契约](../ad4080_dsp/docs/SAR20_INTERFACE.md)。完成需明确逐结果异常、配置epoch、丢样与聚合窗口语义。 |
| **DSP输出链** | 补码校正→选滤波→并行直通或20位FIFO读口；串行器直接取chain结果。 | 没有随滤波／FIFO／串行输出对齐的ID、flags、窗口或系数版本；clip只属校正级。FIFO不接串行，单lane20使能慢于16tick输入，DCO不是真实回送时钟。 | 静态审计 | [并行选择](../ad4080_dsp/rtl/ad4080_top.sv#L258)、[串行实际输入](../ad4080_dsp/rtl/ad4080_top.sv#L298)、[契约](../ad4080_dsp/docs/SAR20_INTERFACE.md#sar20-与-ad4080-数字子系统接口契约)。默认bypass对拍不能替代带元数据持续输出／协议／PHY验证。 |

## 归档记录与未通过层级

| 记录 | 可以保留的事实 | 不可升级的结论 |
|:---|:---|:---|
| [DSP BMC记录](../ad4080_dsp/docs/formal.md) | 报告SPI60拍、FIFO40拍有限属性；FIFO小实例4bit／depth8。当前代码仍有相关断言。 | 不等于当前全协议、16k数据顺序、无界证明或形式等价；残留SBY启动日志不是成功证明。 |
| [DSP ECP5报告](../ad4080_dsp/docs/synthesis_ecp5.md) | 旧FPGA试映射顶层73.59 MHz、20 BRAM；100 MHz目标失败，无板级引脚约束。 | 不等于当前目标ASIC640 MHz或40 MS/s无损PHY、SRAM宏与多角STA。 |
| [旧P5路由记录](evidence/20260930/shared_old_trial_route_25ns/README.md) | 绑定旧`71e7d5a`、FPGA/P5、25ns；仅40MHz核／2.5MS/s。 | 不是af8结构核／ASIC640MHz；OOC hold违例和`TIMING_MET=0`不能隐藏。 |
| [旧mapped网表](evidence/20260930/shared_old_trial_full_mapped/README.md) | 对应旧快照的映射／网表资料。 | 无SDF旧网表不构成当前门级时序或物理签核。 |
| [当前未执行项](caaz/verification.json) | 目标库综合／STA、SEC、CDC/RDC、晶体管／PEX均明确NOT_RUN。 | 本页不把这些项目写为“形式验证”或“综合与 STA”通过。 |

本页以存在的模块、实际接线和证据适用域为准，不以旧百分比补齐缺口。后续功能变化须重新绑定源码／参数／bench／原始结果；不能只刷新旧摘要SHA。文档维护只更新本矩阵，不能把未提交功能树作为主线成果。
