# RTL工程入口、样本归属与调试

> ABI2的8文件包、两采集端口、calobs v2及整数规则已经按实际源码复核。指定DUT有限窗见[B表](../archive/digital_status_matrix.md)；本页流程不代替宏时序或工艺证据。

## 入口与模块分工

[rtl_sources.f](../../rtl/rtl_sources.f)含20个模块：16 core、2 top、2个可选CAAZ叶模块。外部`source_control_binding`和`source_acquisition_binding`随输入包提供，不计为仓库新增的第21／22个模块。[现行接口与算术合同](CURRENT_INTERFACE_AND_ARITHMETIC.md)是正式冻结入口，[A表](../archive/current_digital_source_map.md)和[B表](../archive/digital_status_matrix.md)分别说明来源与证据。

| 实现 | 职责 | 首选观察 |
| :--- | :--- | :--- |
| analog_phase_ctrl | 注册相位与TP／参考／RA边界使能 | phase、quiet_sample、bank_advance、residue_capture |
| sar_trial_ctrl／sadc_enc | seeded逐位比较、已决码、Flash编码 | trial、resolved_valid、resolved_code、done、cancel |
| sar_structural_ctrl／slice_pool_ctrl | 当前coarse／前代fine与实际采集组；source控制资格 | acquiring／current word、state、slice IDs、conversion_valid |
| cal_sample_context／rdac_drv／unit_therm | 冻结已接受mask、已知公共项与residue→fine归属 | accepted_update、mask、known_common_q、residue／fine ID |
| weight_store／calib_regs | 权重、完整位图、精确行和、三个标量与配置生效 | written、row_total、scalar_written、cfg_ready、err_code |
| cal_weight_reduce／adc2_dec／cal_residue_mac／div_floor | 权重归约、码仓中心、受检乘加及floor除法 | T／G／rails、fine_q、numerator、overflow、quotient |
| recon_core／cal_output_stage／status_regs | 接受、快照、MAC、除法、退休及状态 | start／busy、sample ID、result_flags、status_word |
| caaz_cycle_ctrl／caaz_pair_processor | 可选极性／AZ资格与完整码后配对 | chop、AZ代、raw／pair ID、fault |
| sar20_digital_core | 配置总线、结构核、重构、观察与扩展集成 | source_control_ready、acquisition shifts、cal_obs、dout |

## 输入包、单位与两个采集端口

完整核不自选模拟网络或原文物理mapper。Config、format、binding与两只实际source模块导出8文件；本地参数头只是这些输入的精化结果。source绑定只能读数字尺寸、码、word／state和实际ID，不能读模拟电容、增益或保持电压。

归一化电压使用明确的V/Vfs有符号Q格式，SADC／RDAC采集偏移是两个不同端口。它们由已冻结的acquiring word／state和物理组生成；宏必须在实际采集期间保持该关系。当前转换的RDAC更新随后使用current word／state；其已知公共项必须与同笔RDAC采集偏移一致，不能用未来SAR决定反推采集时未知的mask。

Fig.19工程路径用同一字派生L／T／U，SADC与RDAC分别接受负L／负T，实际码为c−U，重构不能再改一次U。负极性、低位4:2:1/7的Q32和目标18/8 common-voltage绑定是受限静态假设；指定Python／RTL静态工作点已对拍。完整物理网络、动态和源3D DEM不因此完成。

## 前沿、启动与异常

核心均在`clk`上升沿读取旧状态，`rst_n`同步低有效。本文phase p指上升沿到来前的phase；同沿非阻塞更新在之后生效，不能把波形显示的新phase当作旧接受条件。比较器和source控制属于本时钟合同，未加入CDC来替代真实接口设计。

| 默认前沿 | 主要事务 | 样本关系 |
| :--- | :--- | :--- |
| phase0 quiet | 旧residue转fine，旧acquiring控制转current，清本周期资格 | 读取旧包；当前coarse和前代fine不同笔 |
| phase1 advance | 组晋升，接受下一acquiring控制，启动有资格的coarse／fine | source_control_valid／ready只对应新采集组 |
| phase2–8 | 消费已决粗SAR更新，校验binding并注册实际RDAC事件 | 重复码是独立加载；不加载暂态trial |
| phase8 | 粗转换截止及取消 | 缺少有效完成不制造好residue |
| phase9 capture | 捕获实际已接受mask、word／state、公共项和当前异常 | 保存给下一周期fine；默认参考死区 |
| phase14 | fine截止 | 处理早完成保留／未完成拒绝，不能复用旧码 |
| phase15 | context、fine及重构空闲满足时启动重构 | 输出采用被冻结的前代context ID |

这些相位是工程选择。复位后的phase0注册使能全低，稳态phase0电平在首次回绕后形成；pool与context需要预热，不能配置完成即要求输出。enable撤销／clear优先取消资格；缺valid、绑定错误、公共项不一致或码事件越界均显式报错，不能夹码／跳过事件后继续称通过。

cal_sample_context若capture与quiet同沿，fine读旧residue，residue_valid随后清零；没有同沿直通。重构提交与下一次接受也按旧ID／旧done关系解释。实际门极reset／evaluate、非交叠、本地保持及模拟建立仍须宏验证。

## 配置与整数退休

| 地址或动作 | 当前语义 |
| :--- | :--- |
| `0x0000+8*u` | 当前物理slice的正有效权重，地址／高位／索引均需合法 |
| `0x2000+0x100*s` | 选择物理slice写窗；先写窗，再写权重 |
| `0x1000／0x1008／0x1010` | offset／ADC2 min／max；有符号归一化电压，当前epoch须写全 |
| `0x1018` | 已退役；不存在旧DEM／bridge／两类dither控制别名 |
| `cfg_validate` | 空闲且权重／三个标量完整、窗口合法才生效；与写／忙冲突拒绝 |
| `cfg_clear_valid` | 撤销ready与完整性，取消在途和pair；保留系数／行和数值但要求下一epoch完整重载 |

权重为有效C/Cf，当前核明确使用Q30／48位、电压Q32／64位、96位受检累加和20位输出。整数化、half-even、负数floor、溢出与饱和按最终[现行算术合同](CURRENT_INTERFACE_AND_ARITHMETIC.md)绑定；[旧合同](RTL_ARITHMETIC_CONTRACT.md)仅保留af8／9bdf历史假设，不能沿用其旧mode／packet接口。

Python数字观察可表达明确的信号系数a与已知电压项；当前RTL／解包器只接受已声明并可逐unit证明的fold子集。不能把一般a／电压项数组从旧schema补造出来，也不能用模拟真值代替缺失数字连接。

`dout_valid`表示退休，不保证数值无错。需同时消费ID、五个结果flags和`dout_analog_bad`；acc／gain异常可保留旧合法码。粘滞模拟状态只供保守停流，不是逐笔归因或无损事件历史。

## calobs v2与验证

calobs v2从高位到低位包含sample ID、coarse、state、control word、ADC2原码、已知公共项、slice IDs、主mask、子mask、analog_bad；slot0／unit0按schema规定处于低位。包宽由字段宽度求和，不再固定748位。CAAZ侧带与raw输出另有自身资格，不自动是可读出接口。

观察在重构接受沿产生，reset／epoch clear优先取消。schema、规格、两只source源码和binding摘要均须一致；报文没有配置epoch、实际模拟状态、ready、FIFO或外部输送。捕获、独立参考对齐、缺样、带宽和配置切换仍是系统职责。

[控制组件示例](../REPRODUCING.md#5-显式导出与rtl组件执行)先导出8文件包，再显式提供权重、标量和32 frames；[CAAZ数字组件](../REPRODUCING.md#caaz数字组件)还核配对与归属。组件必须实际编译／运行并保留工具版本、源SHA、参数、输入、marker和计数；完整核不套用旧P1/P2/P3、34bench默认集合或“已知失败即通过”。当前静态闭环、有限窗与拒绝反例见[B表](../archive/digital_status_matrix.md)；公共重跑及同池重放见[复现](../REPRODUCING.md#5-显式导出与rtl组件执行)。

旧BMC、Vivado／XSim／OOC和代理记录只支持原版本及其限制。当前目标库综合／STA、形式等价、CDC/RDC、晶体管／PEX没有通过声明。
