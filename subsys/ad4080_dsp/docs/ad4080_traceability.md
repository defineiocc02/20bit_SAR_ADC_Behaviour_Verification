# AD4080扩展：现行功能与来源追溯

> 本表覆盖现行 DSP 与相关产品集成。旧`cal_two_point`、LVDS、`settled`及固定增益常数以[9bdf原表](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/ad4080_dsp/docs/ad4080_traceability.md)解释。handoff003的14场景与5严格profile只支持[handoff003 归档 B 表](../../../docs/_history/archive/digital_status_matrix.md)所列数字范围。

页码均为已登记AD4080 Rev.B的PDF页码，身份见[归档 A 表](../../../docs/_history/archive/current_digital_source_map.md#ref-ad4080)。手册说明的是外部器件功能，不公开本工程内部算法。没有唯一来源绑定的字段不得通过默认值假装已确证。

| 功能／模块 | 手册位置 | 当前实现与检查边界 | 状态 |
| :--- | :--- | :--- | :--- |
| 码制与封装 | p20 Table7 | SAR offset-binary与DSP补码在边界翻转MSB；入口ID／flags只作输入事件 | 工程接入，逐输出元数据未完成 |
| 系统校正：sys_error_corr | p.64 Fig.96、p.71 式 (2)、p.92–93 Tables 60/61 | 21 位符号和先加 OFFSET，再按绑定的增益缩放及舍入。完整结果判定两侧饱和，接受至输出相隔两个核心时钟间隔 | 图 96 运算顺序经定向回放。非单位增益律、零码支持与器件内部位宽须分别绑定 |
| 两点校准 | p71 Fig.108/109 | 主机求解系统失调和增益，再经 SPI 下发；数字核仅应用该配置 | [ADCToolbox 及 RTL 回放](../../ad4080_peripherals/calibration/README.md) |
| SINC1／SINC5 | p64–70 Fig.96–107、Tables28–30；p94 Table62 | 数值按输入有效推进；产品定相路径用实际逻辑 CNV 给出公布与写入的边界 | [频响和独立回归](../../ad4080_peripherals/interfaces/dsp_review/README.md) |
| PF1 | p64–70 Fig.105–107、Table28；p94 Table62 | 共用 SINC5，叠加 35 抽头 Q22 对称 FIR 与二倍抽取。总倍率 D=2N，以 2+35D+10 解释 RTL 的首公布边沿 | 八档真 RTL 频响对比；厂商原始系数和文中 D 所指由器件证据确认 |
| 16k结果 FIFO／四捕获模式 | p56–63 Table27、Fig.86–95 | 完成及未完成记录、早事件、读出和再布防分别管理；产品绑定唯一四银行存储 | [产品全记录与接口事务](../../ad4080_peripherals/integration/product/README.md) |
| 事件、状态与路由 | p54–56 Tables25/26、Fig.83–85；p90 Table55 | 阈值／迟滞、HI／LO路由、ALERT与真实状态；物理CNV延时不由滤波valid替代 | 已验证逻辑 CNV 与结果/GPIO 的对应；器件物理时间待验 |
| GPIO与SDO使能 | p52–56、p87–90 Tables52–55 | 输入电平回读，输出使能随实际SDO／路由资格 | 数字实现；pad／电气未实现 |
| SPI实体访问 | p34–42 Fig.53–66、Tables14–19；p37/81 strict | entity宽度、原子提交、SINGLE_INST、partial／CRC／取消及拒绝原因 | 受支持域已实现；短地址／写相SDO等未知项须绑定 |
| 复位／就绪 | p27/34软件复位；p73–94寄存器 | 外部adc_idle／interface_ready、未完成捕获与活动读写资格；完成记录不因配置软件复位丢弃 | 产品支持 AON 供电资格、真实软复位及初始化 poll |
| CNV代理 | p19 Fig.31/32、p33；p7–8 Table2 | 只接收解码后的逻辑控制，不生成差分接收器或实际SAR启动 | 独立代理，物理启动未实现 |
| 结果数据接口 | p45–50 Fig.74–81、Tables20/21；p60 Fig.89 | DSP 提供并行结果、捕获读口和连续数据；产品接单线及四线结果 SPI、记录 LVDS 和连续 LVDS | [逻辑 PHY 与板级待确认项](../../ad4080_peripherals/integration/product/README.md) |

## 明确的工程绑定

| 输入或选择 | 当前规则 | 原文／实现边界 |
| :--- | :--- | :--- |
| P_GAIN_MAP_VALID、K_GAIN、SHIFT、零增益码支持 | 非unity律和端点由调用方明确声明；无绑定不启用相关能力 | 手册正文与寄存器端点的口径须独立确认，不硬选旧511／512常数 |
| PF1 系数 | 内置八档公开响应约束的固定系数，选择随 FILTER_SYNC 锁存 | [可复现全抽头表](../design/sinc5_comp_coefficients.json) [响应原始测量](../../ad4080_peripherals/interfaces/dsp_review/evidence/filter_response.csv) |
| 字节顺序、地址／ID、短指令位数及写相SDO | 使用显式参数与支持域；未绑定或不支持拒绝 | 不从旧代码或孤立图示推导器件唯一实现 |
| 工程cfg_reject与processing_reject | 为本工程诊断，单独输出原因 | 不擅自占用／重定义厂商保留寄存器位 |

逐模块的手册定位和历史路径见[归档 A 表](../../../docs/_history/archive/current_digital_source_map.md#3-15个dsp扩展模块)。当前代码按[接口说明](SAR20_INTERFACE.md)查找。handoff003 的执行层级及未完成项见[归档 B 表](../../../docs/_history/archive/digital_status_matrix.md)。

明确controller／CAAZ fault由core conversion_fault经system sar_conversion_fault进入adapter；对已报告的13／7／8故障有真实fail-stop／restart阻断／epoch重载恢复。该设计不检测未报告的任意ID跳号，也不声明CAAZ＋DSP实际运行。源SHA、14场景及5profile的限定范围见[DSP说明](../README.md#怎样验证)。
