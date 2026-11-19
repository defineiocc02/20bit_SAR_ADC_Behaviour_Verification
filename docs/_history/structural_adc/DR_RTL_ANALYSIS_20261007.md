# 20位 SAR ADC：Dr. RTL 方法下的功能、综合和时序分析

日期：2026-10-07，Asia/Shanghai。源基线：`01ed2b3e4fc4c7c9efd2153cfbfa5d456b10fb74`。顶层：`sar20_digital_core`。本轮是分析任务，生产 RTL 保持原字节；不启动 RTL 优化循环，不晋级任何未证明的改写。

## 1. 结论与本轮新增证据

当前设计已有多项有界、可追溯的数字验证，静态物理权重重构的数学与整数合同保持一致。当前最需要处理的是系数工作域、模拟接口的逐样本异常归属、物理 ID/权重选择的布线成本和实际时序验收条件。现有证据不能支持全参数无错误、论文电路全部复现、ASIC 640 MHz 达标或全面 PPA 优势。

本轮按本机 `asic-rtl-timing` skill 使用 Dr. RTL 的路径诊断和证据门控方法，分别完成源码功能复核、算术 cone 的实际 generic synthesis、历史布线检查点的真实远端重新导出，以及约束/候选改写审核。不是运行上游默认的 Nangate45/0.1 ns 商业流程，也没有执行 RTL-Timer 预测模型。

| 证据 | 本轮实际完成 | 适用范围 |
|---|---|---|
| 输入身份 | 24个生产SV、两个参数头SHA冻结；与10/02见证源字节全部相同 | 同源历史功能见证可追溯，不能称本轮重跑 |
| 硬件来源桥 | 26份源与历史P5源包剔除注释/空白后词法tokens一致；源包成员SHA核验 | 有限语义来源桥，不是新综合或SEC |
| 当前CI记录 | 读取并封存对应当前HEAD的10/02 CI，9项全部SUCCESS | 含一项RTL job及软件/安装等检查；不是本轮重触发，不提供STA |
| 新结构综合 | Yosys0.66：ADC2、MAC、divider P5/P7，全部check通过 | generic原语、拓扑深度；没有Liberty面积或延迟 |
| 新远端STA导出 | Vivado2018.3重开旧P5 routed DCP，setup/hold/扇出/约束重新导出 | 原器件、原25ns和原物理实现；不是当前默认P7新布线 |
| 输入保持 | DCP分析前后SHA一致；无新综合、place/route、约束编辑 | 保证本轮报告来自同一冻结检查点 |

本机没有Verilator，定向RTL仿真在工具探测时退出1；原始命令与日志保留。缺工具不记作RTL失败，也不记作新PASS。实际功能动态依据为同源历史见证和当前HEAD的CI。用户既定“不在本机布置STA”边界得到保持：本轮真实STA导出在已有Windows Vivado上执行，没有安装STA。

本轮还取回该CI的原始RTL job与Python3.11日志：后者987 passed、3 deselected；RTL job含273条流程单测、P5/P6/P7各32样本的15/13/11T协议检查，以及独立算术审计MAC40010例、ADC40000例、divider10048例和8组flag序列。它们在10/02执行并绑定审查HEAD，不与本轮4个generic综合case或此前有界见证计数相加；通过不等于无限SEC或模拟性能验证。

CI实际checkout是PR自动合并提交 `86499bbfa99c942a319c398151335648cdd4b114`，不是分支提交本身。已核对Git树：它与审查HEAD仅README不同，26个RTL/头文件以及软件、测试、工具和流程文件全部相同；完整关系保存在 `baseline/ci_tree_binding.json`。不能只凭PR绿标声称测试了某份源字节。

## 2. 固定条件：三个对象必须分别评价

当前生产源默认 `P_STRUCTURAL=1`、`P_RECON_STAGES=7`、ADC2=12 bit、输出20 bit、18 slice、每笔8 active、每slice 63 main+8 sub、16相位帧、权重Q30、电压Q32。比较器与配置输入必须与 `clk` 同步；所有生产时序状态使用同步低有效reset。

真实硬件报告属于P5，Virtex-7 `xc7vx690tffg1761-2`，Vivado2018.3，OOC，25.000ns，内部BUFG，0.05ns用户uncertainty，零I/O min/max。报告包含工具的Slow/Fast delay corner分析；这不是ASIC的PVT/MMMC覆盖。路径实际uncertainty为0.085ns，不能只取用户设置0.05ns来重算slack。

ASIC目标是640MHz、时钟周期1.5625ns、16拍对应40MS/s。该目标必须用项目实际Liberty、宏模型、模式/角、SDC、时钟与寄生验证。目前缺这些输入，因此本轮不输出ASIC WNS/TNS、面积、功耗或Fmax估计。

| 条件 | 当前RTL功能 | 旧P5物理实现重开 | ASIC目标 |
|---|---|---|---|
| divider展开数 | 默认P7；P5是可选合同 | P5 | 必须固定后重建基线 |
| 工艺/器件 | 与工艺无关的RTL语义 | Virtex-7 -2 | 项目认可Liberty/PVT待提供 |
| 时钟与帧 | 16拍；P7重构11T，P5为15T | 25ns；每帧400ns，2.5MS/s | 1.5625ns；每帧25ns，40MS/s |
| 数字功能证据 | 当前CI及同源历史RTL见证 | 旧完整映射功能见证另有身份 | 需目标网表对应验证 |
| 物理证据 | 无当前P7新物理结果 | 新导出同一个旧routed DCP | 尚未测量 |

## 3. 设计功能与校准算法

结构核实现共享3-bit Flash、双9-bit粗SAR、12-bit fine SAR、18-slice因果轮换、二维DEM、dither、RDAC已决码更新、残差上下文冻结、物理权重修正、输出ID/flags和配置状态。兼容模式接受外部现成coarse/fine码，是不同的elaboration profile，不能用它的通过替代逐位结构模式。

校准系数训练在片外，片上装载并应用系数；没有片上LMS/RLS自学习引擎。物理权重包含电容反馈比例、bridge衰减和约定有效增益，不能在重构后再次无条件乘除32。

$$F=O+Gx+R+TI,\quad T=\sum w,\quad G=\sum wa,\quad R=T-2\sum w_{on}+\sum w_r.$$

$$x=(F-O-R-TI)/G.$$

RTL按真实物理ID和已发生的DEM/RDAC/采样状态计算T/G/R，并冻结前一残差的上下文。sampling dither单元从G扣除，但其电荷仍计入T/R。这一模型依赖线性、稳定和一致的参数；真实RA非线性、记忆、参考建立和输入相关注入需要模拟模型与测量。

整数数据通路先用扩位乘加检查96-bit合同，再保留117-bit shifted，取63-bit signed numerator交给正unsigned gain的floor divider。负且非整除的结果必须向负无穷下修，不能替换为向零截断；输出先作signed边界判断再饱和成20-bit offset-binary。

### 3.1 数值合同尚需工程化的部分

ADC2实现 `min + RTE(delta)`，而不是对绝对中心整体RTE。min为奇数且增量恰为半整数时可差1个Q32整数LSB。此语义与冻结oracle一致，不是默认RTL/oracle mismatch；任何修改必须同步升级合同、软件和RTL。

`cfg_ready`证明系数装载完整和配置字段合法，不证明所有系数在全输入范围下都满足MAC工作域。例如中点需要G_Q严格小于2^43，归一化x=+1需要严格小于2^42；正常名义G_Q=32×2^30安全，但每个单元合法并不自动保证组合的全量程安全。训练/装载软件必须使用机器可检验的工作域，RTL应按明确异常合同报告超界。

`cal_weight_reduce`的缓存行和必须与同一份权重同时更新。顶层已同源连接；单独复用该模块的集成者不能提供陈旧row totals。算术压位需要证明对整个合法域保持overflow、rounding、floor和clamp，而不是只证明标称正弦数据一致。

### 3.2 控制和异常合同

粗SAR固定Flash seed的高3位，只搜索低6位。跨64码桶的seed错误无法由低位搜索纠正；popcount不检测bubble。应给Flash seed误差、valid和建立时间明确宏合同，再做offset/bubble/迟到故障扫描。

缺比较器valid可暂停位判决，但16相位帧不延长；到deadline仍取消并drop。因此比较器建立时间与传回valid延迟直接决定实际吞吐，不能从计数器周期推出模拟转换成功。

模拟bad在规定transfer/quiet边沿采样；取消、过期或被后包替换的fault是否sticky、是否逐样本可见必须明确。当前5-bit `dout_flags`没有独立analog_bad位，sticky状态不等同逐样本fault归属。数值acc/gain异常保留上个合法码，软件必须同时读valid、ID、flags。

默认dither D=2下valid恒1；某些非默认D需要rejection重抽，调用点未统一为同一样本重抽。默认不触发，但不能宣称所有可声明参数都已验证。论文的双dither、跨ADC tracking、ADC2宽窄带切换和模拟AUX/RA/reference等结构仍有既有边界，详见10/02报告及本轮功能附录。

## 4. 当前算术cone的真实generic synthesis

采用已安装Pyosys入口，Yosys0.66，git sha `86f2ddebc`。每项都读取真实源/头文件，执行 `hierarchy -check`、`proc/opt/check -assert`、`synth -noabc`、再次 `check -assert`；保留精确命令、日志、pre JSON及generic JSON/Verilog。没有删掉不支持语言结构，没有引入替代ASIC库。

| Cone | generic组合原语 | 状态bit | 最长拓扑深度 | 说明 |
|---|---|---|---|---|
| ADC2 decoder | 6533 | 0 | fine_q=81；ovf=80 | 含range subtraction、乘法、舍入/比较/clamp |
| residue MAC | 29157 | 0 | a1=78；any_ovf=95 | 含扩位乘减和受检工作域；异常锥不能删掉 |
| divider P5 | 3862 | 317 | 状态D=140 | 5级串接borrow/sub/mux；13次迭代 |
| divider P7 | 4917 | 313 | 状态D=184 | 7级串接borrow/sub/mux；9次迭代 |

这些计数是同一generic流程下的单bit原语统计；不是LUT、标准单元面积或时延。拓扑深度以输入/FF Q为0，每个组合原语算一级；FF D/E/R分别统计，时钟不算数据路径。全部四项没有latch、memory、未解析bit或残余内部算术cell。

P7相对P5的generic组合原语增加27.3%，最大D拓扑深度增加31.4%，FF少4。原因是P5把幅值/商pad到65位，P7为63位；更多除法步在同一拍展开。这个结构结果说明吞吐/延迟与每拍工作量的权衡，不能据此断言某工艺的P7更慢31.4%。

divider已经用一次扩位subtract同时形成borrow和余数决策，不存在独立全宽compare再重复subtract。最长结构路径还包含负数floor修正、余数非零判断、商加一/取负和最终提交选择。按Dr. RTL诊断，优先定位真实divider路径的这一尾端，再考虑算术结构；不能只把源码循环拆开或随意删符号修正。

ADC2有13个独立数据位乘65-bit range表达；frontend端口缩减后显示14×65，其中多1bit是常量保护位。MAC有64×64数据变量，frontend表达为65×64保护形式。宽声明或中间$mul名字不代表同宽独立物理乘法器，更不能作为ASIC单元面积。

## 5. 完整核的存储、归约和选择成本

权重库的数据bit为18×71×47=60066；loaded bitmap1278；18行54-bit缓存972；合计62316候选状态bit。历史实测权重库FF为62316、全核FF为66492，比例93.72%。完整核历史综合为108842 LUT、66492 FF、20 DSP、0 BRAM、0 latch；这属于旧P5综合，不能转写成当前ASIC面积。

权重按物理单元并行被归约读取。on-tree有1278叶子、补齐2048叶子、11层拓扑；row cache只去掉total-tree，不去掉on-tree。ID匹配、重复检查和物理mask散射同时造成大量逻辑关系。单端口RAM无法在一拍交出同样读数；只加 `ram_style` 不会保持现有接口和周期合同。

简单串行单端口读568个所需物理权重需要568次访问；18个单端口bank逐行读71次。这只是朴素方案的计数，不是所有RAM架构的不可突破下界。真正的存储优化需要先从样本捕获窗、物理ID和开关规则导出足够的分组/预归约/缓存，再证明所有事务的结果与时序合同。

面积目标应按数据系数、row cache、选择/归约、MAC、divider、控制分别分解。FPGA rebuilt层级可能移动归属，不能把parent+child资源重复相加。没有活动注入，功耗缺真实SAIF/VCD和宏功耗模型，本轮不报功耗优势。

## 6. 新导出的真实布线STA与路径

远端主机别名 `windows-codex`，机器REEDZHAO，Vivado2018.3。输入是原P5布线DCP，SHA `fba86312b0c96add78ca388b972a52ab77c1b781eec34c1b91027b1ea7358072`。只执行 `open_checkpoint` 和报告/约束导出；没有综合、placement、route或例外/时钟编辑。执行记录证明输入前后SHA相同，原始路径与本轮指标一致。

| 检查范围 | setup WNS | hold WHS | 结论 |
|---|---|---|---|
| 全部OOC路径 | +0.946ns，TNS=0 | -2.278ns，THS=-110433.469ns | 65048个hold failing endpoints，整体未闭合 |
| register clock→register data | +0.946ns | +0.052ns | 该范围25ns setup/hold满足；不是完整外部接口 |
| 脉宽 | WPWS=12.100ns | TPWS=0 | 对应该时钟/器件检查 |

最大核心路径为 `u_structure/u_context/fine_slices_reg[1][3]/C` 到 `u_recon/rails_s_reg[65]/D`，requirement25.000ns，data23.914ns，其中cell5.291ns、route18.623ns，40个器件逻辑level，route占77.875%。它连接物理slice选择到权重/rail归约快照；应优先审查ID解码扇出、权重阵列布局和归约布线。单纯优化一个加法表达式很难解释这里主要的18.623ns连线。

最小核心路径为 `residue_reg[570]/C` 到 `fine_main_reg[6][59]/D`，data0.263ns，cell0.118ns、route0.145ns，0个器件逻辑level，hold +0.052ns。这里0 logic levels不等于0 cell delay，FF clock-to-Q仍计入路径。默认phase9 residue_capture到下一phase0 quiet_sample约7T，属于另一组精确transfer MCP候选；仍需证明clear/disable/first-frame、capture和下一次改动，并配对hold，不能直接设置7T例外。

最差全接口hold为 `cfg_wdata[3]` 到 `u_wstore/w_q_reg[13][36][3]/D`，slack -2.278ns。当前零I/O min假设配合OOC外部时钟边界，不能保证外部最早到达与内部capture时钟的关系。不能删除该路径、凭空加输入min或全局false path来宣称通过；必须取得真实发送端clock-to-out、封装/走线/宏相位和接收端预算，再按真实条件实现并验收。

实际时钟端点有66508个clock pins，D/E/R数据集合199440个pins；这个过滤统计与66492个FF资源不是同一对象，不能要求两数相等。大集合触发工具性能warning，报告已完成；不把性能提示当时序违规或忽略部分端点。

## 7. 新的高扇出实测：控制和配置也有物理成本

| routed net | fanout | 驱动类型 | 优化关注 |
|---|---|---|---|
| u_wstore/SR[0] | 61236 | LUT1 | 权重库同步复位控制的全局负载 |
| u_wstore/u_idx[2] | 6776 | LUT6 | 权重写单元译码跨大量bank负载 |
| u_calib/phase0 | 1864 | LUT2 | 配置阶段/共同条件扇出 |
| u_structure/u_phase/phase_reg[3]_2[0] | 1369 | LUT5 | frame相位控制负载 |
| u_calib/cfg_ready_reg_4[0] | 1368 | LUT2 | epoch/enable控制负载 |
| cfg_wdata[46] | 1322 | PORT | 配置输入物理分发 |
| 其余多条cfg_wdata | 1321 | PORT | 同类配置路径，含最差hold源 |

fanout数量不是延迟，最高fanout网也不一定出现在最差数据路径。它提供了配置/复位域物理规划的新证据：可以评估局部译码、布局区域与物理buffer/复制；需要检查实际arrival、slew/cap和拥塞后决定。

不能直接取消权重reset：当前reset后的配置读回、未载入数据、row total和bitmap具有确定合同。若改为只reset metadata，必须定义未载入读回及写入增量更新语义，并同时验证软件、formal与映射；这属于具体候选架构变化，不是本轮已执行的优化。

## 8. SDC/XDC审查与多周期边界

新导出的exception报告显示false path、clock groups、multicycle、max/min delay六类计数全部0。当前真实STA使用一拍约束，没有通过例外掩盖关键路径；zero uncovered categories只说明在所记录OOC假设下约束覆盖，并不证明预算来自真实外部电路。

样本context在pre-phase0更新，重构Stage-A在pre-phase15接受，相隔15T；fine code在pre-phase14更新，仍只有1T。16相位帧不是整个核的16周期例外。只有源稳定性、最早捕获、下一次改动、clear/enable/cancel、权重epoch、ID同行和具体网表端点都证明后，才可评估精确MCP。setup和hold移动关系需要同时检查，不能只设置setup。

目前有限wrapper有57笔接受的15T/1T证据，但不含真实完整归约通路，也不是全序列形式证明，未覆盖全配置clear/re-enable。该证据提供候选依据，不足以立即套例外。本轮没有增加任何MCP或false path。

同步reset作为普通同域数据控制检查；没有异步reset recovery/removal问题的同等证据义务。若后续顶层输入成为异步reset/比较器/配置域，则必须另外引入同步/CDC合同与相应物理检查，不能把当前接口默认当已跨域安全。

## 9. Dr. RTL策略如何具体应用到本设计

| 真实瓶颈/证据 | 策略类别 | 可评估候选 | 必须保持/证明 |
|---|---|---|---|
| context ID→rails，77.875% route | fanout/local decode；策略7/8/21 | 物理分区、局部ID谓词、控制复制、缩短权重到归约距离 | 同沿ID/DEM/采样/RDAC一致；reset/使能优先级；复制未被综合合并；实际route收益 |
| weight reset61236、unit index6776 | 高扇出控制，策略7/8 | 局部译码与工具物理buffer；评估metadata reset候选 | 原reset/readback/cache合同；不能增加未批准延迟；面积/hold/拥塞 |
| divider串接borrow及floor尾端 | arithmetic策略9/10；宽度策略28 | 真实carry/borrow结构优化，分组数P5/P6/P7同条件比较 | signed最小值、负非整除、zero/large gain、padding、start/cancel/latency |
| MAC any_ovf最长generic锥 | width明确化策略28 | 先证明系数域，再分别评估数据和overflow计算 | 异常可见性、超界分类、符号/截断/舍入；不得删除检查取得快路径 |
| 62kFF系数存储 | 结构架构评估 | 分组/预归约/多bank与时间窗计划 | 等价读数、所有物理组合、配置epoch、16拍和事务ID |
| context 15T、fine code1T | 约束证明，先于RTL改写 | 精确MCP形式不变量及端点清单 | 稳定/捕获/next-change；配对hold；无wildcard blanket exception |

共享谓词/CSE并非一定更好：route主导时，全局共享可能增加负载。局部复制也可能被综合重新合并、增加状态/面积或恶化拥塞。Dr. RTL“高置信度”是经验分类，必须通过真实工具，而不是把源文件换写法算优化成功。

禁止未经证明压窄系数或乘加器、重排有符号溢出运算、去掉floor修正、强迫已有平衡树变成自定义mux，或新增流水级却沿用旧ID/valid。低功耗也需要真实样本活动/clock enable和芯片宏功耗证据，不能仅从generic gates推出。

## 10. 已有候选为何不能晋级

旧P7 640MHz OOC实验同时改了reducer与divider，LUT171246→199039（+16.23%），setup WNS -17.426→-17.112ns（+0.314ns），hold TNS反而恶化0.528ns。它是旧输入、估计互连且联合改动，不能归因单模块；没有全面PPA优势或640MHz闭合。

旧local-row P5候选综合完成，但布线出现level-7 congestion，route_design失败。中途+2.114ns不是最终STA，不能晋级。用更多局部树/复制可能增加布线压力，候选必须重建同条件基线并保留失败证据。

本轮未产生RTL候选，因此skill的 `compare_runs.py` 不构造伪造的ASICLiberty/area输入，也不把FPGA LUT填到area_um2。该助手用于真实同条件ASIC STA候选门控；它本身不执行STA/SEC，不认证日志。后续实际优化才填写baseline/candidate，等价unknown、未约束、hold恶化、面积超预算或route未完成均不得晋级。

## 11. 下一轮实施的里程碑与验收

### 11.1 先完成可机器判断的功能与接口边界

固定配置epoch、系数可用域、模拟error的有效边沿/样本归属、Flash seed允许误差、dither参数支持域，以及structural/legacy profile。当前功能回归与有限见证应保留独立整数真值、边界和fault ID检查。明确cfg-clear丢弃在途、busy start不接受、COMMIT可接下一start、first-valid/re-enable的行为。

只进行一个具体改动的候选；端口/复位/使能/优先级/latency保持不变时建立对应SEC。形式假设包括同步输入、合法epoch和真实宏deadline；不得把async2sync抽象或有限仿真说成所有reset行为的无限等价。需要改流水/握手时先给出完整新周期与ID合同。

### 11.2 为真实ASIC STA补齐输入

锁定实际顶层/参数、Liberty各角与单位、标准单元/存储/analog macro timing、SDC、合法模式、640MHz目标与I/O min/max/负载/驱动来源。综合/elaboration检查black box、未解析/残余generic cell、latch/loop与面积；有意宏不能靠black box默认忽略延迟。

在真实链接网表加载约束，核对集合数量、有效边沿、uncertainty、latency、IO及例外覆盖。setup/max与hold/min、transition/cap、pulse/gating和适用reset检查分别保存WNS/TNS及完整路径；没有数据的指标写未测量。

### 11.3 物理实现与候选晋级

按slice/weight/归约的连通关系规划placement，避免大总线跨全核；先检查高扇出与布局拥塞，再优化算术。CTS后用实际时钟检查hold，route后用对应寄生复核setup/hold与注入覆盖、DRC、拥塞和route完整性。活动标注功耗与面积/时序一起比较。

baseline/candidate保持相同参数、库、角、SDC、工具选项及物理条件。记录原生单位差值，不用WNS百分比，也不倒数一条数据延迟称Fmax。只有必要回归/SEC通过、setup改善、hold不恶化、面积在明确预算内且route完成的候选可以晋级；未闭合仍保持未闭合标签。

## 12. 交付、原始证据与复算方式

主报告、功能/约束/结构附录、命令、SHA、generic网表和新导出的Vivado报告一起封存。`baseline/source_identity.json`连接当前源、历史功能和P5来源桥；`baseline/github_pr_ci.json`是本轮读取的当前CI状态。`baseline/reopened_route/execution.json`记录DCP前后SHA；`baseline/reopened_route/out/`保留max/min/core/exception/clock/fanout/constraints。

`cone_synthesis/`包含真实Yosys命令、4项日志和网表；其统计可以仅解析已有JSON重新计算，仍不生成STA。原执行cwd和之后搬移路径注明，不能把搬移后相对输出地址冒充原始工具入口。

正式PDF使用同目录独立LaTeX源和内嵌矢量图，按用户 `latex-macos-report-compile` 工作流导出，同时使用内置LaTeX编辑器编译核验。原始论文/专利没有重新发布；本轮是对现存RTL及已核对原理的Dr. RTL工程评价，不声称重新逐页重审16份原文。

官方约束依据：[AMD UG903 v2018.3](https://docs.amd.com/v/u/2018.3-English/ug903-vivado-using-constraints)，Chapter4 I/O delay与Chapter5 timing exceptions。实际命令能力以本轮Vivado2018.3 `help report_high_fanout_nets`和完整日志为准，不把当前2026.1网页选项无条件套到旧版本。
