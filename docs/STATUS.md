# 当前实现、验证范围与下一步

本页是当前状态入口；首页提供阅读摘要，ADR 解释设计合同，IMPLEMENTATION_CHECKPOINT／HANDOFF 保留历史过程。实现状态、验证层级和适用配置分别判断，不用测试数量替代完成状态。

本轮从 main@1e973cb82bb6ba39dba3b7d82e43f10ec42434c3 核对。结构 dither 的有效配置报告已单独修正于8550c05：只增加报告，不改变扰动、ADC2或最终整数码。保留字段sadc_cap_ratio的不生效报告另在1017226修正，真实负载使用c_sadc；结构校准报告的均值/RMS字段由3ea94a7独立补充。当前数据与本轮执行结果见[本轮复核](reviews/20261105-readability-maintenance.md)。[8238cc3的完整CI](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/41904500234)是历史受测提交，不冒充后续提交已重新通过。

## 配置先分清

| 入口 | 用途 | 边界 |
|---|---|---|
| Config() | 历史七位一级量化／unary基准，常用于预算和早期机制实验 | 不与九位RTL逐拍混用；run_sim是静态unary入口，run_pipeline会解析成自己的split合同 |
| Config.paper_literal() | 九位、63+8完整计数split候选，dither默认关；quantizer启用时采用理想dual-port假设 | 几何与双端口是工程假设；generic调度按dem_mode与单元DEM分别控制 |
| Config.rtl_structural() | 结构数字核的九位、63+8、离散range、16相位对照 | DEM位同时控制单元置换与slice shuffle；ID从2开始；量化器dither实际由固定D=2序列驱动 |

默认采样率参数均为40MS/s。16个理想相位若各占一拍，对应640MHz预算；功能仿真不证明目标工艺能达到该时钟。20位是最终码宽，不等同ENOB。

## 四组工作

| 工作 | 当前接入 | 证据与条件 | 仍缺什么 |
|---|---|---|---|
| **转换控制与物理信号链** | 因果slice池、一级量化、残差电荷、共享RA/ADC2；数字相位、比较器接口、ID和异常上下文 | 结构样本、已决RDAC事件、物理mask与静态plant对照；generic split另有共享源阻抗、参考恢复和有限RA/ADC2动态模型 | 比较器／模拟宏真实完成沿、本地保持、非交叠与参考建立合同；晶体管、PEX和目标工艺时序未验证 |
| **随机化与失配实验** | slice调度、单元DEM、零和bridge候选、sampling/quantizer dither及逐raw重构项 | 项目mask合同已统一；三模式事件对拍；固定失配池／指定时偏行为实验。名义守恒、使用次数、总误差各有不同判据 | 63单元与bridge使用偏置、近满幅dither余量是范围限制。逐slice时偏/带宽学习、原芯片完整bridge及dual-port电路尚未实现 |
| **位权训练与定点重构** | 片外已知参考与数字观察拟合有效C/Cf；冻结、Q30/Q32装载和逐码重构 | 真实结构数字DUT与独立静态plant联合；训练/留出分开；相同raw用名义/冻结系数比较并对拍独立整数模型 | 真实参考误差、观察输送、漂移/动态训练与ADC2自身非线性。有效权重不唯一确定绝对电容F值 |
| **输出、DSP与可选扩展** | SAR—DSP码制接入；原型校正/SINC/FIFO/SPI；默认关闭CAAZ样本域与数字pair控制 | 默认/异常接口定向验证；CAAZ有held-noise、量化、保持失配及条件组合实验 | 两点方向、有效滤波系数、FIFO—串行连通、逐结果元数据、无损PHY未闭合；CAAZ真实存储与H/K未测，DSP系统顶层未启用它 |

### 校准实际做到哪一步

训练器只接收数字coarse/ADC2码、实际slice ID、DEM与开关/扰动观察，以及独立已知输入；不读取plant真实单位电容。可辨识、满秩后冻结有效C/Cf系数和后级偏移，装回同一DUT，在不同激励的留出记录上检查最终20位字。

这能处理模型中的静态有效权重、桥接衰减及相关增益项，不是片内背景学习器，也不自动估计时偏、带宽、参考动态、温漂或ADC2非线性。748位观察口当前没有FIFO/ready/外部输送；40MS/s的名义观察总线量为29.92Gbit/s，实际系统需要受控捕获、带宽预算和缺样/epoch协议。见[ADR0021](adr/0021-structural-contract-calibration.md)。

### 随机化和可选扩展的分工

- slice调度决定实际物理组；相邻转换组必须不相交。
- 单元DEM改变同一码的物理实现；名义守恒不保证每个单元等次数使用。
- dither的注入、改码、逐raw重构必须配套；结构range的dQ不能当作又应扣一次的模拟dR。
- 逐slice校正需要估计、观察和冻结应用；随机选择本身不是时偏/带宽估计器。
- CAAZ需要真实AZ状态保持及归一化噪声系数匹配；标签检查不证明CBIAS保持或H/K匹配。

机制图与条件见[DEM/dither/CAAZ专题](randomization_caaz/README.md)。CICC2026的连续时间后级、LSB重复和SINC² tracking averaging只是参考，当前未实现；现有输出SINC、预跟踪和CAAZ都不能代替它。

## 按依赖排序的未闭合项

| 类型与问题 | 影响 | 下一步动作 | 完成标志 |
|---|---|---|---|
| **模拟接口证据不足**：采样保持、AZ/普通复位、参考与ADC2关闭沿 | 裸TP/mask可能重开旧组；旧AZ可能刷新CAAZ要求保持的CBIAS；动态性能取决于真实截止 | 先冻结节点与宏时序，验证本地锁存/隔离/非交叠、建立和噪声传递 | 逐raw ID有真实保持/建立证据；CAAZ另满足预算化H/K和两周期存储相关性 |
| **工程接口未实现**：外部参考与校准观察读出 | 静态训练夹具不能直接变成芯片连续自校准 | 明确参考误差、观察带宽/FIFO或受控采集、缺样和epoch合同 | 实际可读观察与独立参考完成训练/冻结/留出；异常不被静默拼接 |
| **输出原型缺口**：两点校正、滤波、缓存和串行 | 方向错误或重复/丢样影响输出含义与吞吐 | 分别修两点逆向校正、有效系数、FIFO路径与元数据；PHY独立实现 | 码/ID/异常端到端一致，达到约定连续输出率 |
| **范围限制与可选研究** | DEM偏置、dither余量和调度取舍需可见；扩展不能自动算完成 | 保留严格诊断/当前基线；需要时分别研究独立随机化控制、逐slice学习、原芯片bridge/双端口与CAAZ/CICC电路 | 各项有独立目标、观察、适用域与组合验收；不用单个杂散下降代替总体性能 |

## 文档分工

- [首页](../README.md)：目标、结构、四组工作、代表结果及最短入口。
- 本页：当前实现/验证边界、依赖和解除条件。
- [model_scope.md](model_scope.md)：物理模型、近似和入口的适用域。
- [ADR0021](adr/0021-structural-contract-calibration.md)等ADR：设计选择与位域/码域合同。
- [IMPLEMENTATION_CHECKPOINT.md](IMPLEMENTATION_CHECKPOINT.md)、[HANDOFF](../HANDOFF.md)：历史实施记录，旧待办不自动回到当前缺陷表。
- [RTL限制](rtl/KNOWN_LIMITS_20261104.md)、[本轮复核](reviews/20261105-readability-maintenance.md)：具体反例、线索裁定和运行版本。
