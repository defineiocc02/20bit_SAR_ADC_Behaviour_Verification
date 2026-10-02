# 2026-10-02 控制、采样、DEM、dither 与综合结构再审查

审查对象为 `71935ab54238c7ed638f5fa66c5ffb58e10287bb` 的当前 RTL。原文身份以本目录 `input_manifest.json` 为准。本文直接查看 [00] PDF p1/p2、[00_1] slides12/19/24–26/30/31/34/35，以及 [09] Fig9/10、[10] claims、[11] claims、[12] Fig8 与正文/claims、[13] Fig2/6/11/12 与 claims、[14] Fig2/8–11 与 claims 原始页面。OCR 仅作检索，图是原文依据。当前生产源码未改动；新增有限见证是外部审查附件，不能冒充新综合、STA、模拟或形式等价证据。

## 1. 判断必须分四层

- **原文明示的架构**：两套 coarse quantizer、共享 Flash、独立 RDAC、18 个 residue slices 中 8 个采集/8 个转换/2 个备用；coarse 为 9-bit；RDAC跟随已决 SAR 码；共享 RA/ADC2；相同 TP 关键下降沿及本地 EN/hold-low 支路；粗参考预充电→准确外部参考；dither 范围增强 2 bit；slice 与 horizontal/vertical DEM。
- **当前工程实现**：3-bit Flash种子+6次同步二进制SAR决策、12-bit同步 ADC2、16-clock帧、63+8单位展开、LFSR及确定性状态序列、扫描式8/18选择、±1零和bridge、Q格式/配置epoch/数字回读。9-bit coarse本身不是工程猜测；具体基数、逐拍控制和ADC2=12-bit是工程选择。PPT“>12b AC matching”是 SADC 与 RDAC 匹配，不能据此认定 ADC2 是12-bit。
- **已有但限定范围的诊断契约**：模拟bad以独立sticky保存；每结果5-bit flags只记录数字算术与clip；宏比较器valid/data必须同步；模拟错误读回须保持到quiet观察沿。上述契约不是完整模拟错误逐样本账本。
- **尚未完整覆盖或模拟宏承担**：论文dither两端不同范围、专利tracking预设、动态 ADC2采样带宽、晶体管级AUX/参考/RA/非交叠/level-shift/共享关键沿的物理实现。专利引用不能证明论文实际采用每个可选实施例。

## 2. 当前逐沿数据流程与真实预算

所有 phase 指上升沿前的旧相位；NBA 后安装下一相位。`sar_structural_ctrl.sv:281` 的 `recon_start` 是组合 wire，而不是 phase15 分支中注册的 NBA 信号。`recon_core.sv:250–260` 在该 pre-phase15 沿直接接受 Stage A；下一 pre-phase0 沿才替换 fine context。因此不能把结果接受挪到下一phase0，或把整帧16拍无条件设为所有路径的 multicycle。

| 旧phase沿 | 该沿的数字行为 | NBA后状态/归属 | 审核位置 |
|---|---|---|---|
| 15→0 | TP寄存器拉低；上一笔fine context由当沿Stage A接受（如launch=1） | phase0 quiet窗口，fine context仍旧 | analog_phase:42–47；structural:281；recon:250 |
| 0→1 | quiet_sample冻结当前acquiring dither/injection；旧residue packet转为fine packet；coarse bank翻转 | 新fine context、fine/acq读回bad已冻结；两个SAR尚未start | structural:315–320；context:64–68 |
| 1→2 | 旧acquiring slices晋升converting；从补集选下一acquiring组；sharedFlash种子启动选中coarse；上笔context启动fine | coarse busy、trial MSB待比较；fine busy；acquisition EN更新；DEM/dither取旧输出后推进 | pool:66–81；structural:122–165/322–328 |
| 2→3 | coarse第1次决定；RDAC此沿先装上一沿start产生的已决seed | resolved更新，本次accepted bit待下一沿装RDAC | trial:54–75；structural:236–250 |
| 3–7 | coarse继续低6bit逐次比较；fine继续12bit比较 | 旧resolved_valid驱动RDAC，trial不直接驱动RDAC | 同上 |
| 7→8 | coarse最后一次有效比较可使done NBA变1；registered coarse cancel NBA变1 | coarse compare enable关闭，最后resolved已就绪 | structural:312；trial:69–75 |
| 8→9 | 读取旧coarse_done；装最终resolved至RDAC；coarse SAR同步取消 | coarse_ok冻结；ref_precharge关闭，准确reference尚未打开 | structural:238/330–332；phase:44–47 |
| 9→10 | 冻结当前RDAC物理ID/掩码/轨/injection及rdac_over；准确参考/RA开始 | residue packet属当前样本，fine packet仍前一笔 | context:53–57；phase:45–47 |
| 13→14 | fine最后一次有效比较可done；fine acquisition注册打开 | fine_done NBA变1，fine cancel NBA变1 | structural:307/313；trial:69–75 |
| 14→15 | 读取旧fine_done/fine_resolved并锁存fine_code | fine_ok/code稳定；细SAR取消；phase15组合launch可为1 | structural:334–337 |
| 下一15→0 | Stage A接受冻结fine packet及fine_code | context不会在此沿被换成下一笔 | structural:281；context:64；recon:250 |

在期望 ASIC640MHz 时钟预算 `T=1.5625ns` 下，以下只是**源码逐沿换算**，不是时序或模拟实测：

| 窗口/事件 | 精确时钟距离 | 预算换算 | 能否等同论文 |
|---|---:|---:|---|
| coarse start pre1→done NBA pre7 | 6T | 9.375ns | 不等于论文约7ns coarse conversion |
| coarse start→上游看到done/最终RDAC装载 pre8 | 7T | 10.9375ns | 包含NBA/driver的一拍跟随 |
| TP下降 pre15→随后 coarse start pre1 | 2T | 3.125ns | Flash seed/start前数字等待 |
| TP下降→coarse done NBA | 8T | 12.5ns | 对采样沿的完整数字延迟 |
| TP下降→最终RDAC装载 | 9T | 14.0625ns | 不是论文7ns的RDAC已决过程 |
| Flash acquisition重新打开 pre1→post2，距此前TP下降 | 2T | 3.125ns | 不能支持论文Flash<1ns返回的声明 |
| 新acquiring_mask安装 pre1→下一TP下降 pre15 | 14T | 21.875ns | 帧为25ns，但该使能可用窗不能直接称整25ns采集 |
| 新acquiring_mask安装→下一quiet数字观察 pre0 | 15T | 23.4375ns | 与真实TP关键沿仍有1T差别 |
| 最终RDAC装载 pre8→residue packet捕获 pre9 | 1T | 1.5625ns | 是数字上下文裕量，不证明模拟建立 |
| 准确reference/RA注册打开 pre9→TP下降 pre15 | 6T | 9.375ns | 真实采样宏若由TP关键沿关断，建立预算取这里 |
| 准确reference/RA注册打开→quiet数字锁存 pre0 | 7T | 10.9375ns | 数字观察晚1T，不能无说明代替模拟采样时间 |
| fine context更新 pre0→Stage A接受 pre15 | 15T | 23.4375ns | 仅相应稳定context路径候选；尚未实施MCP |
| fine_code锁存 pre14→Stage A接受 pre15 | 1T | 1.5625ns | 不可跟着context设成15拍 |

原文 [00] Fig9.8.1、PPT12 的完整周期采集和约7ns coarse、Flash<1ns要求与上述同步安排不相同。即使ASIC数字 STA 后续达到640MHz，也不能由此证明采样带宽、Flash返回、RDAC建立、RA/参考20bit精度或论文指标已经复现。模拟宏应给出对应控制边界的真实采样关断时间、门极本地时序及建立曲线。

`P_RESIDUE_CAPTURE` 和 `P_REF_ON` 只移动部分参考/AZ/context事件。合法条件为 capture>=9、ref_on>capture、ref_on<16（analog_phase:22–24），而 coarse_deadline=8、fine_deadline=14、launch=15 保持固定：

- capture延后使最后RDAC pre8→capture的数字距离为 `(capture-8)T`；准确reference打开沿是 `pre(ref_on-1)`，距最终RDAC为 `(ref_on-9)T`。
- ref_on= capture+1 时，捕获context与开启准确reference发生在同一上升沿，但context捕获的是数字开关决策，不是已经建立的模拟余差。
- ref_on增加会压缩参考/RA到下一TP下降的窗口：`(16-ref_on)T`；例如 ref_on=15 只有1T。参数合法性不是模拟裕量通过。
- 不能仅移动P_REF_ON/P_CAPTURE而把细ADC的12次判决窗、quiet末端读回、deadtime及实际sampling edge认为自动闭合。

## 3. 原文机制到RTL的逐项判定

| 来源与原文定位 | 原理 | 当前对应代码（当前行号） | 结论/条件 |
|---|---|---|---|
| [00] p1、Fig9.8.1；PPT12；[09] Fig9/10 | 小匹配SAR决定，大RDAC跟随已决结果、共享RA | structural:122–142 两coarse；236–250 driver跟随resolved_valid；trial:34–38/60–75 | 架构对应；当前同步binary控制不是原芯片晶体管/异步握手公开复刻 |
| [00] p1；[09] claims1/6/14/18–20，PDFp32/33 | 匹配slice/量化器与residue路径 | 两量化器trial输出与RDAC物理开关输出分离 | 匹配电容/晶体管比例与hold/CF完全外部；数字“分开端口”不足以证明电气匹配 |
| [00] p1、PPT12/30；[11] claim15，PDFp47 | 动态8-of-18且保留2spares | pool:36–49/66–81；slice_alloc:84–91只兼容0..15 | 结构模式因果所有权对应；PRNG+固定扫描起点是工程策略；不声称均匀全部8-of-18组合 |
| [00] Fig9.8.2；PPT19 | 相同TP关键下降沿、本地串联EN、hold-low；15×Nyquist DAC BW（PPT） | phase:33–47注册TP；structural:105–107 acquiring/hold-low；323 coarse EN | 只实现宏控制。共扩散/位置、时钟树偏差、MOS级关闭时间/BW须模拟/布局证明 |
| [00] p1dither句；[10] claims5–12/17–19，PDFp35/36 | quantizer与RDAC可用不同dither范围，论文增强2b | structural:199–207两个Q端口；214–223反号同一current_dither；sampling/quantizer配置互斥 | **明确论文未闭合项**；没有不同dQ/dR与fractional/bit-split端口，不能用当前三模式回归声称覆盖 |
| [10] claim1/16，PDFp35/36 | RDAC采样独立dither、Q不必全收到dither、后续补偿 | structural:205–207 acquisition rails；225–233 current rails；context冻结；校正采样掩码 | sampling模式为相应数字抽象；quantizer模式是另一种注入/反号命令，不能冒充同一实施例全部要件 |
| [11] claim1/17/20，PDFp46/47；PPT30/31 | 共享main shuffle、额外sub相关bit跨main，二维旋转与binary/unary bridging | dem_addr:65–103二维闭式；swap:93–109±1零和交换；unit:55–65物理掩码；sid[8] bridge约半状态 | 定性层次对应；63+8每slice不是论文图中8×8总体6bit阵列的逐晶体管数量复原；±1/四正四负是一种工程实现 |
| 实际附件 [12] US10707889B1 Fig8、PDFp10/12/13claims1/12/23 | tracking阶段先用另一ADC最新（可部分/加权）转换信息预设待acquire ADC | pool只有acquiring→converting所有权；trial start只用Flash seed；无cross-bank history preload | **专利覆盖缺口**；不能仅因论文引用判定论文芯片必须采用这一实施例。论文p3参考[12]编号疑似笔误须另行登记 |
| [13] Fig2/6；claim1/9/12，PDFp16 | 旁路输入RC，AUX给寄生/BG/boost充电 | structural:103–106 `aux_charge_enable=acquiring_mask` | 只是控制标签；RTL不含VIN_AUX电压节点、boost储能/非交叠/RC绕开；这些在模拟宏 |
| [13] Fig11/12（PDFp10/11）与p16描述 | 可选AUX同时/分开为采样电容预充电 | 无附加sampling-cap-precharge端口或路径 | 该实施例未完整实现；不能把一般aux_enable称图11/12全部复刻，也不必无依据把所有替代实施例同时拼进论文 |
| [00] Fig9.8.3；PPT24–26；[14] claim1/8及其它实施例 | coarse reference充电，residue阶段连accurate Vref，内部比较/控制或慢放大器/储能方案 | phase:46/47 两个registered reference EN及死区 | 对论文phase-level控制有对应；没有阈值补偿、内部Vref反馈、比较器/充电PMOS、battery cap等模拟实现，专利替代结构需选型 |
| PPT34/35 | gmR+OTA、C_AZ及ADC2 BW先宽后窄 | ra_az/ra_amplify/fine_acquire_enable | PPT35的S1为AZ、S2旁路R_BW给宽带建立、S3经R_BW给窄带降噪；ra_az表达S1，fine_acquire只是采集窗口，没有独立S2/S3控制。可由模拟宏内部定时，但当前RTL接口和测试不证明该切换，需内部时序模型或新增档位控制 |

**参考号注意**：根审查直接查看论文 [00] PDFp3后发现参考[12]印为 US10,797,889，而附件标题/作者/编号是 US10,707,889B1。tracking判断依实际附件及原claims；不要静默改原论文打印值，亦不要写成编号完全一致。该项需保留两种值并注明疑似文献笔误。

## 4. 每模块功能与综合电路估算

下列为声明状态bit及静态结构估计；**不是mapped FF/LUT/gate/面积/功耗报告**。综合可常量折叠、共享、合并等价FF、裁剪观测端口。未看到当前模块级综合报告，不能报精确PPA。

| 模块/当前行 | 功能与边界 | 状态与组合结构 | 可能综合/布线问题与建议 |
|---|---|---|---|
| ctrl_fsm:83–113 | 兼容模式16拍节拍，不能套到结构模式 | 默认4bit相位+32bit计数=36声明FF；计数加1、比较器、16路译码/onehot移位常量化 | sample_idx加法/控制扇出；P_STRUCTURAL为elaboration常量，检查不用的兼容逻辑确被trim；不要把组合phase decoder接模拟门极 |
| analog_phase_ctrl:28–57 | 结构模式相位和registered模拟宏enable、quiet/advance/capture数字脉冲 | 4bit phase+5controls=9声明FF；小增量器/比较译码 | ref/AZ/TP高扇出会需缓冲和本地宏时序；注册并不能证明钟边沿/enable跨模拟后无毛刺或deadtime在PVT下够用 |
| sar_trial_ctrl:24–75 | flash seed、trial与resolved分开、cmp_valid推进、deadline cancel | `2*P_BITS+clog2(P_BITS)+3`：coarse25×2，fine31，共81声明FF；变量bit write与next trial onehot/移位mux、小减1 | 动态bit-index译码与bit选择mux；保持resolved输出只描述accepted，不能把trial直连RDAC；动态比较器reset/eval脉冲需宏补足 |
| sar_structural_ctrl:283–344 | coarse current/fine previous双事务，bank/dither/context/错误对齐 | 自身143声明bit；另含上述子模块；32bit sticky error只部分低位实际用，可能trim | enable/reset与load_rdac多扇出；coarse/fine deadline固定，busy start被拒；error overwrite有优先级但不是异常FIFO，不保留所有fault IDs |
| slice_pool_ctrl:18–81 | 上period acquire晋升convert，从10个补集选8个，2个spare参与 | IDs80+mask36+PRNG32+sampleID32+cursor5+primed/valid2=187声明FF；8展开LFSR反馈；18候选dependent扫描、count索引写、常数mod18 | scan/count/动态数组写可能形成长组合链；candidate/count为integer32bit须查看综合是否精简，不能假定for是18拍FSM；可预排下次selection或并行prefix选择，但保持因果所有权/ID序列并验证 |
| slice_alloc:69–90 | 兼容静态0..7/8..15轮换，16/17spare不用 | n32+seen1=33FF；bank=n[0]；ID常量译码 | 成本极低，但不是完整18pool；不要为了省门默认切回该模式后仍称完整动态shuffle |
| dem_state_gen:73–94 | 两bank独立sid，旧sid先使用再+433 mod512 | 18FF，2个9bit常数加法器+enable/load mux；mod512截位无通用除法 | sid广播到63+8图案；保证每bank走512states，不能全局每两sample更新造成半空间 |
| dem_addr_gen:65–103 | physicalID→logical thermometer position，8×8旋转过滤cell63，sub环 | 0FF；输出378main+24sub=402bit；63个cell compare/correct，常量3bit row/col减法可共享为8类；sid切片+模8截位 | rh/ch扇出多、71logical结果给8活动lane比较；p/8与p%8为gen常量，不能误估成63个动态除法；不能改为mod63一维环 |
| dither_gen:36–75 | 32bit LFSR8步展开，映射[-2,2]端点半概率 | 32FF；默认D2 raw%8取低3位、valid恒1、加1/右移/减2；8步反馈XOR | 不需要DSP/通用除法；默认PMF与PCG序列不同。若单独改D为非2幂且valid可0，caller必须按拒绝契约重抽，当前结构默认D2不触发该问题 |
| swap_decode:67–109 | coarse与signeddither组合、范围flag、clip、8lane零和bridge、主子计数 | 0FF；默认UNITS_PER_LSB1=1，不产生一般乘法；signed18bit加/范围/clip、8lane±1、bit slice除8；rank mod8低3bit | resolved bank mux→signed command→clip→bridge→unit compares→scatter是一条连续组合链，须看coarse-update到driver STA；宽integer表达式最终能否收敛看mapped报告 |
| unit_therm:55–65 | physical permutation+count→实际ON及采样dither轨 | 0FF；504main comparisons、64sub comparisons、4rail comparisons（默认几何） | 568开关数据并行不是568cycle；逻辑位置扇出8、count广播63unit。共享相同count或局部译码可能减少面积/长线，但不可换成不同物理unit映射 |
| rdac_drv:52–75 | 8lane命令按物理ID散射到18行，load更新其它slice清零 | 声明18×(1+63+8+4)=1368FF；潜在18×8=144个5bitID匹配及每行75bit的8候选选择 | variable-index writes可能降低为priority mux/decoder；重复ID是“最后lane赢”，不能无证明改OR。无共享时1350bit×7个2:1 mux是9450个bit mux结构量级，仅粗略上限/形状，不是门数；load/reset/ID匹配扇出可成拥塞；可显式local译码但须严格保持重复ID契约/完整回归 |
| sadc_enc:40–60 | comparator thermometer→popcount，共享Flash实例P_B1=3 | 0FF；实际7个输入补8leaves，7个tree add nodes、3层深度；standalone default511input对应512leaves、511nodes、9层，不是生产共享Flash资源 | 未做bubble/therm合法性；current seed依赖宏有效温度计及阈值正确。树无寄存，不能单独加流水而不调整Flash接收沿 |
| cal_sample_context:32–68 | current residue数字packet→previous fine数字packet，保留ID/物理mask/rail/injection | packet CW=709bit，双slot1418+4valid/bad=1422声明FF | 上下文布线占控制侧状态大头；只在capture/quiet更新，可考虑专用稳定路径budget，但可用MCP需验证reset/clear/enable及具体端点；fine_code不在该packet且只有1T |

补充顶层与状态连接（校准数据通路其它模块由单独审查归档）：

| 模块/当前行 | 控制职责/综合结构 | 风险与契约 |
|---|---|---|
| sar20_digital_core:556–619、638–667、680–707 | 按elaboration参数选择结构/兼容路径；将冻结fine context、fine_code送recon；将外部模拟读回和数字结果组装状态。结构选择是静态mux，应由综合常量裁剪 | physical switch多bit顶层输出若要求寄存全部会限制裁剪；各类cfg_ready/epoch/clear协调影响首笔启动，不能只凭子控制器波形签核全顶层；模拟bad未进入结果5flags，top:686仅launch门控入sticky |
| status_regs:71–95、98–105 | 4sticky+2clip共6声明FF；4个OR保持及clear mux；err_code低26bit组合透传，CLR_MASK常量 | clear同沿优先于新异常，clip不随clear清；不是fault FIFO，不保留ID，状态组合字段需按上游读回契约解释；虽小成本，可按产品诊断需要扩展带ID事件，但当前行为符合既有明确契约 |

默认结构控制域上述有状态子模块合计约 **3260声明bit**（143+9+187+81+18+32+1368+1422），不含顶层配置、校正数据通路和weight_store，不把兼容ctrl_fsm/slice_alloc再算入。两个context与RDAC输出占2790/3260≈85.6%。这能说明控制的主要状态存在哪，不能与历史全核66492FF做简单相加或相减。校准存储及归约的实测/估算另由综合审查归档。

## 5. 关键模拟接口精度条件

Flash高3bit seed在 `sar_trial_ctrl:60–63` 固定进入resolved高位；之后仅比较bit5..0。若Flash输出跨64code区间错误，当前二进制SAR没有高位回溯/冗余修正。`sadc_enc:22–24` 对bubble输入只给popcount，未检查合法性。这是在合法宏输入契约外的精度风险，不是“给正确valid/seed也会算错”的新RTL bug。混合验证应扫描Flash threshold offset、bubble、比较器迟到/亚稳及边界输入，明确宏如何提供可信valid和可接受seed。

`compare_enable`是busy/cancel派生使能电平（trial:32），不是动态比较器的reset/evaluate时钟。每比较周期的trial建立、evaluate、复位、有效码保持依实际宏定义。默认粗SAR6次与fine12次恰好占满deadline；任一次cmp_valid丢失可导致该事务取消，不存在保持原16phase同时任意stall的裕量。下一帧可恢复不意味着缺失样本没有诊断问题。

采样dither与quantizer dither配置互斥。当前sampling通过专用units及rails+input gain mask校正；quantizer通过Q注入同一整数与RDAC减码补偿。要新增论文dR/dQ机制，先制定电压与码单位、各注入点、数字去除归属，再更新模型/物理ID系数与TB。不能直接把 `current_dither<<2` 写入RDAC而不相应修正残差与公共inj_q。

## 6. 模拟错误的有限诊断证据

实际链：top:575 输入 `ra_sat||rdac_ovf||adc2_over` → context:64–68在quiet transfer冻结，与capture时 `rdac_over`一起保存 → top:686仅 `launch_recon&&context_analog_bad` 入独立sticky（或数字rc_adc2_ovf）→ status_regs:87–90 sticky保持。`dout_flags[4:0]`由数字校正模块输出，ADR0018:78–81明确无analogbad字段；不存在自动按输出sample_id标记模拟错误的通路。

新增 `control_minimal_witness/analog_status_boundary_tb.sv` 直接例化当前production `cal_sample_context/status_regs`，用与top:686一致的 `launch&&fine_bad`接线，而非全顶层替身。Verilator5.020零延迟16个检查/16沿PASS；CSV保留全部输入、fineID/finebad、门控event与sticky：

1. residue capture冻结`current_bad=1`，下一quiet transfer后即使input低，finebad仍1；launch沿正确置sticky。
2. analogbad只在quiet transfer为1，会正确冻结到该ID；**不launch时sticky不置位**；下一个quiet/context可把坏packet替换成好packet，此后好launch不能追报旧坏事件。
3. analogbad只在capture前后给一拍而quiet转移沿低，不进入finebad。宏不保持至规定quiet锁存沿属接口契约未满足；不能据此指控模块应捕获所有脉冲。
4. enable下降同步清两个context，但sticky只有clear/reset清。

建议把逐样本analogbad携带到提交或给出带ID的fault事件/计数；对于deadline cancelled/recon_busy拒绝，定义是在context生成时记sticky还是仅已接受输出记sticky。扩大诊断不得改变正确码算术，也不应把analog越轨冒充acc_ovf。此项为明确的工程诊断改进，当前约定已写在ADR，不宣称新发现未满足旧契约的RTL功能bug。

## 7. 实际接受边沿的有限新见证

`structural_capture_trace_wrapper.sv` 只读包裹仓库现有 `structural_protocol_tb`，无生产/现有bench修改。它记录1926沿的pre/postphase、quiet、capture、contextID、fine_code、wirelaunch，并以与Stage A同类的接收FF采样ID。运行得到：

```
STRUCTURAL_PROTOCOL_COMPLETE frames=120 launches=57 expected_drops=63 checks=1920 decoder_perturbations=2
STRUCTURAL_CAPTURE_ALIGNMENT_PASS edges=1926 accepts=57 context_age=15 fine_code_age=1
```

每个成功接受都满足：prephase15、当前context_valid、旧ID在该沿正确被shadow FF接收、context更新距接受15T、fine_code更新距接受1T。87个正常coarse done脉冲在prephase8被观察，离start prephase1为7T，因此真正done NBA发生在前一prephase7，即6T。这个结果排除了错误“phase15注册start→phase0再接收”的解释。

该wrapper覆盖原bench的120frames、coarse/fine缺失决策、Flash缺失、phase译码负控制、末尾disable/reset；**没有**全顶层配置clear/re-enable、FF路径形式证明、门延迟、完整重构或模拟建立验证。它支持时序推导和MCP候选分级，不构成添加15-cycle例外的签核授权。若提出MCP，应对相应context FF→StageA FF列出精确端点和正常/首帧/clear/disable/re-enable稳定性证明，并按目标工具定义校核setup及hold约束，fine_code路径仍单周期。

证据入口为 `control_minimal_witness/result_manifest.json` 与本报告/见证清单外层 `control_audit_seal.json`、两个原始run.log与CSV、wrapper/TB源码；清单封存26生产RTL和源bench SHA。所有本轮参数换算/资源估算以这些源码为准，不挪用历史Vivado报告的head身份。


## 8. 封存与复现范围

`control_minimal_witness/execution_metadata.json` 保存两次编译/运行的工作目录、argv和工具版本。编译argv使用参数数组保存实际换行/路径，避免shell字符串转义歧义；使用的Verilator5.020本地wrapper路径属于本机环境，移机时应将argv[0]替换为可用的同版工具并重建，不能直接把二进制当可移植交付。CSV由run生成。结构测试4个WIDTH警告来自现有bench表达式，使用`-Wno-fatal`，并将UNDRIVEN/LATCH/UNOPTFLAT/SELRANGE作为error；该事实没有被“全无警告”掩盖。两个C++ build中的未知clang警告组信息来自Verilator runtime头文件。

本轮只新增两个有限外部见证，不改生产RTL、不改已有bench、不再次全回归、不新跑Vivado/STA/模拟。控制功能的正确有限场景、诊断契约的限制、论文时序/机制未闭合项、预计综合结构分开记账。所有原始PDF、原始页图及OCR留在外部审查目录，不复制到开源仓库。
