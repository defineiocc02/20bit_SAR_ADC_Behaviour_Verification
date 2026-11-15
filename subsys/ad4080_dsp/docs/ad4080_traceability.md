# AD4080扩展：现行功能与来源追溯

> 本表覆盖现行15模块。旧`cal_two_point`、LVDS、`settled`及固定增益常数以[9bdf原表](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/ad4080_dsp/docs/ad4080_traceability.md)解释。handoff003的14场景与5严格profile只支持[B表](../../docs/archive/digital_status_matrix.md)所列数字范围。

页码均为已登记AD4080 Rev.B的PDF页码，身份见[A表](../../docs/archive/current_digital_source_map.md#ref-ad4080)。手册说明的是外部器件功能，不公开本工程内部算法。没有唯一来源绑定的字段不得通过默认值假装已确证。

| 功能／模块 | 手册位置 | 当前实现与检查边界 | 状态 |
| :--- | :--- | :--- | :--- |
| 码制与封装 | p20 Table7 | SAR offset-binary与DSP补码在边界翻转MSB；入口ID／flags只作输入事件 | 工程接入，逐输出元数据未完成 |
| 系统校正：sys_error_corr | p71 Fig.108/109；p92–93 Tables60/61 | 显式增益律、失调和clip；非unity映射／零码端点需声明支持 | 机制接口已有，精确数值与器件符合性分别验证 |
| 两点系数学习 | p71 Fig.108/109 | 旧固定横坐标正向拟合退役；当前没有自动逆向系数求解控制器 | 未实现；不等于上游C/Cf训练 |
| SINC1／SINC5 | p64–70 Fig.96–107、Tables28–30；p94 Table62 | 数学积分／梳状、按valid推进；不把valid或计数写成真实CNV建立时间 | 机制实现，全配置频响／时间基准待验证 |
| SINC5补偿 | p64–70补偿通带及额外2倍抽取 | P_COMP_COEFF_VALID及明确三系数才允许；缺失拒绝，无单位占位回退 | 系数绑定与实际通带证据待验 |
| 16k结果FIFO／四捕获模式 | p56–63 Table27、Fig.86–95 | 完成／未完成记录、早事件、读出和再arm分别管理 | 生命周期实现；SRAM及器件全部边界另验 |
| 事件、状态与路由 | p54–56 Tables25/26、Fig.83–85；p90 Table55 | 阈值／迟滞、HI／LO路由、ALERT与真实状态；物理CNV延时不由滤波valid替代 | 数字实现，真实时间基准待验 |
| GPIO与SDO使能 | p52–56、p87–90 Tables52–55 | 输入电平回读，输出使能随实际SDO／路由资格 | 数字实现；pad／电气未实现 |
| SPI实体访问 | p34–42 Fig.53–66、Tables14–19；p37/81 strict | entity宽度、原子提交、SINGLE_INST、partial／CRC／取消及拒绝原因 | 受支持域已实现；短地址／写相SDO等未知项须绑定 |
| 复位／就绪 | p27/34软件复位；p73–94寄存器 | 外部adc_idle／interface_ready、未完成捕获与活动读写资格；完成记录不因配置软件复位丢弃 | 最终真实停流／恢复收据待补 |
| CNV代理 | p19 Fig.31/32、p33；p7–8 Table2 | 只接收解码后的逻辑控制，不生成差分接收器或实际SAR启动 | 独立代理，物理启动未实现 |
| 结果数据接口 | p45–50 Fig.74–81、Tables20/21；p60 Fig.89 | 当前只提供并行处理／捕获读口；旧SDR串行器移除 | 数据PHY／结果SPI／持续吞吐未实现 |

## 明确的工程绑定

| 输入或选择 | 当前规则 | 原文／实现边界 |
| :--- | :--- | :--- |
| P_GAIN_MAP_VALID、K_GAIN、SHIFT、零增益码支持 | 非unity律和端点由调用方明确声明；无绑定不启用相关能力 | 手册正文与寄存器端点的口径须独立确认，不硬选旧511／512常数 |
| 补偿系数 | 只有明确有效系数与对应选择允许 | FIR结构存在不证明真实器件通带被补平 |
| 字节顺序、地址／ID、短指令位数及写相SDO | 使用显式参数与支持域；未绑定或不支持拒绝 | 不从旧代码或孤立图示推导器件唯一实现 |
| 工程cfg_reject与processing_reject | 为本工程诊断，单独输出原因 | 不擅自占用／重定义厂商保留寄存器位 |

代码定位以[15模块A表](../../docs/archive/current_digital_source_map.md#3-15个dsp扩展模块)和[接口](SAR20_INTERFACE.md)为准；此处不保留会随整合漂移的旧行号。当前最高执行层级及未完成项由[B表](../../docs/archive/digital_status_matrix.md)按最终源收据更新。

明确controller／CAAZ fault由core conversion_fault经system sar_conversion_fault进入adapter；对已报告的13／7／8故障有真实fail-stop／restart阻断／epoch重载恢复。该设计不检测未报告的任意ID跳号，也不声明CAAZ＋DSP实际运行。源SHA、14场景及5profile的限定范围见[DSP说明](../README.md#怎样验证)。
