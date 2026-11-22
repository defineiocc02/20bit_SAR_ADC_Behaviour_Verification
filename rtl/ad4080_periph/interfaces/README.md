# AD4080 参照分区的模拟与数字接口

以 `floorplan/plan.json` 中的 1500 µm × 1500 µm 裸片规划作为坐标系。现有 IP 以 `7408edf8db9a41eebdd756378b69d2775f107dec` 的 `adc_digital_ip_v21` 为准。本附件静态核对接口和几何容量，宏的电气引脚、可布通性及绝对时间由后续物理证据核定。

- [v21_ports.csv](v21_ports.csv) 逐一核对 90 个现有 RTL 顶层端口，共 1449 个逻辑位。`direction_at_ip` 以数字 IP 为观察视角，`rtl_line` 指固定提交中的声明。`window` 为本轮分配，`LOCAL_OBS`、`LOCAL_TEST`、`LOCAL_TIE` 为片内用途，`CTRL_LOCAL` 仍需选定调度器接入位置。
- [chip_integration_ports.csv](chip_integration_ports.csv) 给出 32 组顶层连接和封装功能方向，含拟定宏接口及本批独立外围适配器的接缝。标成“Rev.B 封装功能参考”的条目采用数据手册功能，坐标及 Pad 单元选择属于本工程规划。`IN_P/N` 等拼写是表中的网络别名。
- 版图参照使用 IPBrain 图确认数据接口在北侧、大片 SRAM 在其南、OTP 在东北、两处 LDO 及参考区的大致位置。LDO 与具体电源轨的对应、裸片 Pad 编号和内部走线均缺少原厂引线映射。 [原图读数与规划说明](../floorplan/README.md)

## 1. 窗口与端口量

坐标按 `[x0,y0,x1,y1]`，单位 µm。下表为逻辑边缘线段，其宽度等于该边上沿坐标的长度。数字 IP 端口与模拟宏间保留本地相序、驱动和电平转换的实际接入位置。

| IP 窗口 | 位置与长度 | 直连接入位数 | 后端用法 |
| --- | --- | ---: | --- |
| `CLK_RST` | 西边 x=145，y=945…982，37 µm | 2 | `clk/rst_n` 接时钟树与复位分发，优先留时钟屏蔽和电源回流。 |
| `SADC_RDAC`（P2） | 南边 y=900，x=520…845，325 µm | **522** | 468 位 RDAC 写总线与 54 位片组选通，按 18 个物理片分组下接。 |
| `ADC2_RSP` | 南边 y=900，x=890…1060，170 µm | 21 | 捕获及 ADC2 握手共 17 位，公共采样事件和三个回报共 4 位。RA 和 ADC2 之间的模拟余差走 `RA_ADC2`。 |
| `RESULT` | 东边 x=1095，y=925…978，53 µm | 55 | 接结果缓存/控制器，`out_tag` 与状态可就地登记。 |
| `CFG_TEST` | 北边 y=1100，x=520…675，155 µm | 92 | 正常权重系数装载和配置状态。诊断端口按实际用途就地产生或另留入口。 |
| `Q_RETURN`（P6） | 西边 x=145，y=904…938，34 µm | **31** | 16 位 SADC 预置由 IP 送往 Q，15 位 Flash/SAR 事件由 Q 回送 IP，沿 `Q_SPUR_N/S` 对接。 |
| `CTRL_LOCAL` | 待分配 | 47 | `prepare_*`、`stop_i` 建议集中于 IP 邻近的调度/相序适配器。若与 `RESULT` 合并，东侧要按 102 位重新核算。 |

25 个观测口合计 540 位，包括 `node_*` 的 272 位行为模型快照、ADC2 观测和前级事件/状态。可在外围采集少量实用状态，例如 `final_applied_o` 和 `tag_o`，并把选中的信号按增量计入对应窗口。现生产连线量按六个固定窗口统计为 723 位，另有 47 位待分配调度描述符，138 位本地测试接口和 1 位保留输入。`sched_ext_sel=0`、`dither_force_i=0` 是现生产选择。`ev_start_i` 在当前顶层声明中保留，事件上下文由内部 `commit_fire` 启动。 [固定顶层端口与内部启动逻辑](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L18)

窗口至对应宏矩形的最短正交距离为 `SADC_RDAC→RDAC` 22 µm、`Q_RETURN→Q` 175 µm、`ADC2_RSP→ADC2` 117 µm、`RESULT→CTRL` 20 µm、`CLK_RST→CLK` 232 µm。计算使用几何最近点，适合用作含绕宏、过孔和缓冲的实际线路下界。`IO_EAST` 自 x=1090 向东引出，在 `DIO` 东边共有 y=1360…1372 的 12 µm 接入边。 [规划坐标](../floorplan/plan.json)

## 2. RDAC 与 Q 的接入容量

每个物理片 `p=0…17` 需要 `rdac_pre_state_o[4*p+:4]`、`pre_wr_o[p]`、`rdac_post_state_o[20*p+:20]`、`post_wr_o[p]`。这些是 **18 × (4+1+20+1) = 468** 根独立数字控制线，18 个物理片均预留对应线路。三组 `acq_sel_o/conv_sel_o/idle_sel_o` 各 18 根，P2 完整直连为 **522** 根。双 SADC 的两组七位预置加两个写门为 16 根，Flash 和 SAR 合用 qid 的已决返回为 15 根，两束共 **31** 根经 P6 进出。模拟地/电源、屏蔽、`TP_CLK`、电平转换控制和可选诊断信号另行布置。 [顶层物理总线](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L52)

逻辑线数与电气扇出应分别核对。每个片的注册写门先到本片接收或驱动级。如果 `post_wr_o[p]` 直接驱动 20 位位单元，各片写门有约 20 个本地数据通道的负载，再计握手、门控和缓冲。若四位采集预置分开锁存，`pre_wr_o[p]` 有约四个本地数据通道的负载。共享 `TP_CLK` 要覆盖两套 SADC 与 18 个 RDAC 片的采样支路，具体相位、复制和缓冲由时钟/模拟宏方给出。差分开关 P/N 互补控制可以由本地驱动生成，其实际分支、驱动电容和电平转换数量取决于宏交付端口。建议以 `p` 为单位排齐相邻数据位、写门及片选，把驱动/电平转换靠近接收侧，优先核查传播延迟、支路负载及局部电源跃变。

设边长 `L`，两端禁布分别为 `g0`、`g1`，有效宽 `W=L−g0−g1`。对允许穿越该边且接得上引脚的第 `k` 个金属层，`p_k` 是轨距，`η_k` 是留出屏蔽、过孔、供电和绕行后可分给该束信号的轨道比例。保守估算如下。

`C_cut = Σ_k floor(η_k × floor(W/p_k))`

Pin access 亦按实际引脚层、pin 中心间距和可用接入形状单独计算。端到端上限取两端 pin access、各过孔降层截面及通道拐弯截面的最小值。`η` 是信号占轨假设，与 IP 标准单元利用率独立。

| `SADC_RDAC` 算例 | 每层裸轨数 | 每层可用轨数 | 并行层数 | 截面容量 | P2 现规划 522 根余量 | 原聚合 553 根余量 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 两端各留 5 µm，`η=0.60`，`p=0.64 µm` | 492 | 295 | 1 | 295 | −227 | −258 |
| 同上 | 492 | 295 | 2 | **590** | **+68** | +37 |
| `p=0.80 µm`，其余同上 | 393 | 235 | 2 | 470 | −52 | −83 |
| `p=0.80 µm`，其余同上 | 393 | 235 | 3 | 705 | +183 | +152 |
| `p=1.00 µm`，其余同上 | 315 | 189 | 3 | 567 | +45 | +14 |

现规划 522 根用单层需要 `p≤315/870≈0.362 µm`，两层均分时需要 `p≤315/435≈0.724 µm`。原先 `ADC_DIG` 向 Q 横向转接的喉口高 21 µm。以两端各留 2 µm、轨距 0.64 µm、`η=0.60`、两层估算，最多 30 根，小于所需 31 根。现在 `ADC_DIG` 的 P2 总线按物理片分组向阵列北边直下，Q 从西侧 P6 绕开该喉口。

P6 至 Q 采用西侧连续折线走廊。`Q_SPUR_N` 为 `[121,900,145,940]`，东侧直接接 P6；`Q_SPUR_S` 为 `[121,660,146,900]`，东侧在 y=660…730 接 Q 西边。按截面两端各留 2 µm、`p=0.64 µm`、`η=0.60`，每个方向以两层可接入为算例。

| P6 至 Q 截面 | 几何长或宽 | 每层裸轨数 | 每层可用轨数 | 两层容量 |
| --- | ---: | ---: | ---: | ---: |
| IP 西边窗口 P6 | 34 µm | 46 | 27 | 54 |
| 北段及南北接界的横截面 | 24 µm | 31 | 18 | **36** |
| 南段横截面 | 25 µm | 32 | 19 | 38 |
| 南段与 Q 西边共有接边 | 70 µm | 103 | 61 | 122 |

P6 至 Q 四处截面的算例取最小值 **36 根，对 31 根剩 5 根**。若瓶颈方向只可用一层，则为 18 根。P2 与 P6 的实际容量还要取宏 LEF 的 pin access、方向相容金属层及过孔拐角的最小值，并核对 Pad 环、屏蔽、电源条和宏方禁布。若实际宏接口在接收侧由 ACQ/CONV 重建 IDLE，可在适配层按合同核实后另算 P2 穿越数。

在同一 0.64 µm 算例中，东 `RESULT` 窗留两端各 5 µm 后为 40 根/层，两层 80 根。当前 55 位结果可按双层去评估，若同窗再引 47 位调度描述符则缺 22 根，应另设调度入口或在外围就地登记。

## 3. 一笔采样经过哪些接口

论文 Fig. 9.8.1–3 公开两套 SADC、共享 Flash、18 片 RDAC 池及同时采输入和上笔余差的公共安静沿。按 40 MS/s 的目标，公共沿间隔为 25 ns。前级约 7 ns 是论文中所述的转换量级，不能直接作为数字拍数。内部敏感采样为 TP 下沿，数据手册对外转换启动为 CNV 上升沿，两者之间的相位与抖动预算由时钟宏关闭。 [ISSCC 2024 9.8，p.182–183](https://doi.org/10.1109/ISSCC49657.2024.10454329) [AD4080 Rev. B，p.33](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

1. **受理和预置。** `prepare_valid && prepare_ready` 在 `clk` 上升沿分配一笔 `prepare_tag`，冻结 `qid`、相位、预置字和 8 个物理片号。首笔 `qid=0`，随后只在受理时交替。`sadc_pre_state_o` 的两组数据独立保持，`sadc_pre_wr_o` 受理后对当笔一组有效一拍。RDAC 的 `pre_wr_o` 是随 ACQ 片权的组合资格电平，可以连续保持，并能在 CONV 空闲的受理前建立。宏按采集相位和使能使用四位采集预置。冷启动的候选 `acq_sel_o` 还可能先于受理出现，采样授权仍由协调器控制。 [预置和片权 RTL](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/acq_conv_ctx.sv#L371) [SADC 预置 RTL](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L220)
2. **公共沿登记。** 假设 A 已为 CONV，B 已为 ACQ。公共沿上两路可同时发生，B 锁存输入，ADC2 捕获 A 已建立的余差。向 IP 同一拍交付 `sample_event` 与各自成功位 `input_captured`、`residue_captured`，这两个标记只给确实发生的操作。冷启动可以只有 ACQ，停收末尾可以只有余差捕获。普通换笔在 B 已标记采样、CONV 空闲或正在释放时由后续受理提升 B，并创建 C 的 ACQ。普通模式的同沿释放加提升以新 CONV 为准。 [上下文仲裁和释放](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/acq_conv_ctx.sv#L159)
3. **Flash、SAR 与 Follower。** `ev_flash_valid_i/ev_flash_code_i/ev_qid_i` 对齐当前采样上下文，可在公共沿至事件 1 同沿更新该笔真实 F。首提交后该笔的迟到 Flash 不再修改前缀。`prepare_flash` 可供预先给出的 F，真实宏接入时仍应保证真实 F 赶上事件 1。转换上下文随提升启动，启动沿不同时发判决。`ev_bit_valid_i` 提交已决结果，事件 1 提交 Flash，普通事件 2…7 按 bit5…0。当前工程最高区间增加 bit_index=9 的 512 阈试探。接受后提交 bit2…0 完成，拒绝时该试探不写 post，序号 2 重新用于 bit5。`ev_qid_i` 为 Flash 与 SAR 共用，若 B 的 Flash 和 A 的 SAR 返回需要并发，适配器先暂存，再以整字按时限仲裁。控制 SADC 试探、比较器请求和完成归并应由一处时序适配负责。 [Flash 启动与绑定](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L301) [SAR 事件资格](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/sar_evt_v21.sv#L89)
4. **物理 post 与 RA 建立。** 合法应用沿后，`post_wr_o[p]` 和新的 20 位数据同拍注册，连续高拍可代表连续两次写，未选片保持旧状态。最终沿把本笔最后应用的 slot 状态冻结给权重计算，与采样时冻结的 pre 和物理片号配合。实际开关和参考电压建立后，宏按相序做 coarse→fine、AZ、宽带建立与窄带保持。`REF_FINE_SEL`、RA 相序和 `RA_SETTLED` 由相序控制或模拟宏交付，现顶层无这些信号的专用端口。`final_applied_o` 是数字写入状态，可进入受控适配器，敏感相位由宏方给出。差分余差从 RDAC 到 RA、再到 ADC2，全程在模拟宏间连线。 [post 及最终状态 RTL](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L413) [参考与 RA 工程合同](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/v21/PHYSICAL_INTERFACE.md#L266)
5. **沿前授权、沿后保持。** `adc2_capture_arm_o` 在公共沿前给出，当前顶层条件包括 `conv_valid`、`final_applied`、`wsum_valid`、本笔尚未登记和 ADC2 owner 槽资格；RA 实际稳定及宏采样使能由外层共同核实。宏完成后报 `sample_event && residue_captured`，IP 据此登记该 CONV 的 owner。`hold_ack` 可同拍或随后到，必须属于仍持有片组的 CONV。真正回池要求该笔已捕获、已确认 hold，且从捕获后已累计当前默认 2 个数字 `clk` 的安静计数。`adc2_release_ok_o` 只观测 ADC2 叶子的 hold 锁存值，不能单靠它重新分配片组。 [沿前授权](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L460) [回池条件](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/acq_conv_ctx.sv#L177) [hold 观测](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc2_capture.sv#L199)
6. **ADC2 回送与同笔退休。** 宏主动给 `adc2_valid_i` 及 `{adc2_raw_i[12:0],adc2_range_err_i}`。IP 允许一笔已捕获待回送 owner。当前 `adc2_ready_o = !owner_valid || adc2_valid_i`，valid 高时 ready 必然高，不能以单看 ready 判断已登记 owner。同沿有 A 的旧 raw 和 B 的新捕获时，raw 读沿前 A 的 `tag/qid/G/Wm/Wp/epoch`，沿后登记 B。`qid` 重用不改变 A 的 owner。raw 口自身无 tag，宏/适配器必须按登记顺序一捕获一返回并冲刷复位前残包；空 owner 时 raw 丢弃、置粘滞 late；若迟包恰逢新 owner，IP 无外部 ID 可自行鉴别。工程 ADC2 为 13 位 offset-binary，差分半开范围 `[-0.1875,+0.5625) V`，物理宏需确认窗口和标志。重构输出 `out_valid/out_code/out_sat/out_err/out_tag`，现顶层 `adc2_rsp_range_o`、`adc2_late_err_o`、`err_o` 与 D5 的 `out_err` 分开。 [owner 与回送 RTL](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc2_capture.sv#L110) [ADC2 工程坐标](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/v21/PHYSICAL_INTERFACE.md#L229) [D5 接线](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv#L650)

`stop_i=1` 立即封住新受理。已接收未采的 ACQ 继续给输入采样；待旧 CONV 释放且槽已空，已采 ACQ 可单独提升，随后需要纯余差公共沿。用受理 `prepare_tag` 与退休 `out_tag` 的计数或队列核销，核销后才重装权重。新表需写 360 个不同地址，在下一独立沿 `w_cfg_commit`，确认 `w_cfg_valid` 后启动。`cfg_epoch/b_q24_cfg` 在排空后切代，运行期保持稳定。非法事件会封住该笔后续前级应用，现顶层无单笔 abort 端，外围应设超时，并定义进入安全状态、统一冲刷后恢复的流程。 [排空及装载合同](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/v21/CONFIG_CONTRACT.md#L24) [事件错误路径](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/sar_evt_v21.sv#L217)

## 4. 时序、CDC 与 RDC 签收

`clk` 是现数字 IP 唯一时钟。物理宏相位、外部转换启动、PHY 数据时钟和低速配置时钟由集成方分别登记。对每个时钟关系明确共同源/相位或异步，并基于真实数据约束到达和采样。把公共采样边沿记作 `E_n`，必须能同时证明下表中的模拟时间条件和数字事件关系。

| 边界 | 可先冻结的约束/握手 | 宏方必须交付的量 |
| --- | --- | --- |
| 采样前 `prepare` → `E_n` | SADC 和 RDAC 的数据、使能及端口电平稳定后才捕获，不能允许 `pre_wr_o` 的组合跳变直接控制 TP。时序适配输入握手整笔保持。 | PVT 下预置数据建立/保持、TP 非交叠、最大采样孔径偏斜、时钟及输入负载、安静窗。 |
| Q → `ev_*` → RDAC | 同域走 `clk` 的 input/output delay 与数据/写门同步约束。异步时在源侧锁存 `{qid,event_index,bit_index,bit}` 或 `{qid,flash_code}`，用确认式 mailbox/队列或有已知相位的高速局部域。高位吞吐需带上往返延迟。V21 无每事件 ready。 | 每次试探到判决的最坏延迟、完成脉宽、返回最迟时间、post 数据/写门 skew、驱动负载及时序图。 |
| 最终 `post` → fine/RA → `E_(n+1)` | 末次应用先走 `clk→Q + 电平转换 + 线延迟 + 宏接收`，再满足开关、参考、RA 的联合建立。必须同时满足 `t_arm_at_macro ≤ E_(n+1)−t_setup_arm`、`t_residue_stable ≤ E_(n+1)−t_setup_analog`；两条路径各计抖动、失配和 PVT。 | 参考切换/建立误差曲线、RA AZ/宽带/窄带相序、允许共模/残差预算、ADC2 拍前后 setup/hold、最坏捕获间隔。 |
| 公共沿和 `hold_ack` → IP | 采样协调器在源侧打包该沿实际发生的两个回报，异域可用带确认的事件翻转或异步队列，进 IP 拍成为 `sample_event` 与同拍负载。hold 用保持电平或握手脉冲，按当前 CONV 记账。保留 `E_n` 的同笔关系和宏间物理同步。 | 源时钟相位、回报最早/最迟时刻、可允许的事件积压及超时、回池前安静窗的真实绝对时间。 |
| ADC2 raw → IP | 异域返回时整体锁存 13+1 位，跨域同步控制位或用异步队列，到 `clk` 域产生合法的一次 valid。两级同步器只处理适合的单比特事件/状态，不能逐位同步后拼总线。 | 响应顺序、最大转换/回送延迟、复位后是否还有旧响应、范围/饱和与 raw 的关系。 |
| OTP/配置 → IP | 镜像先在装载域验收，整表或逐字握手过域，在 `clk` 域执行排空、写入、单沿提交。 | OTP 读保护、测试/编程权限、时钟和复位、低压时可读性、读请求/响应时序。 |
| IP → FIFO/PHY | `out_valid` 每拍均可能需要承接，IP 无 `out_ready`。同域先锁存整束状态和数据，跨异步串行/存储域时可加握手或独立 CDC FIFO。若用异步 FIFO，写读指针 Gray 化并约束跨域 skew；现四银行 `result_store` 是本域单口存储接缝。满前把未退休事务和停收响应延迟算入余量，模式策略另定。 | 存储宏延迟、仲裁读写冲突、PHY 位序/时钟边沿、空满策略和直读模式承载量。 |
| 各域复位及电源 | v21 `rst_n` 低有效，释放在本域同步处理；对每个独立域处理孤立复位、复位前在途数据和复位期间输出钳位/隔离。恢复先建立电源/时钟，确认上下游冲刷完成和配置有效，再允许采样。 | 电源意图、隔离值和源/目的端供电组合、reset min pulse 与 recovery/removal、宏 POR/PGOOD 生成及稳定要求。 |

对每个正式宏补齐一份 LEF/Liberty 或等效时序抽象和 PVT 时序表，至少列出每束端口的逻辑位序、坐标和引脚层、接收门阈、驱动上限、负载、setup/hold、有效最短脉宽、传播最坏值、复位安全状态。时钟层保留 CNV→TP 风险预算。用实物库约束实际时序路径和跨域例外，采样/RA 关键窗口由宏瞬态和顶层提取走线建立验证接力。

## 5. 封装、数据及电源入口

| 功能组 | 参考功能方向和邻接 |
| --- | --- |
| 模拟输入、参考 | `IN±/AUXIN±` 输入、`REFIN` 输入、`CMO` 模拟输出。模拟 Pad 优先靠 Q/RDAC 或 REF 所在静区，`REFGND` 和相邻回流独立布置。 |
| 转换和数据时钟 | `CNV±` 来自 Host，默认 CMOS 模式由 CNV+ 驱动且 CNV− 接 IOGND，可配 LVDS。数据钟 `CLK±` 同样从 Host 输入，数据 SPI 时改作 `DCLK` 和 `DCS`。`DCO±` 是输出的可选回送时钟。 |
| 数据与状态 | `DA±` 必选主 LVDS 通道，`DB±` 第二通道可选。数据 SPI 时共享这四个脚作 `SDOA..SDOD`，单通道用 SDOB。各路 OE 依模式与时序控制，复核 Rev. B 对四通道 SDOD 非选通状态的高阻例外。数据 PHY 优先靠北/东北的 DIO 窗。 |
| 配置与控制 | `SCLK/CS/SDI` 配置输入，`GPIO0` 缺省作配置 SDO，`GPIO[3:0]` 可复用状态。配置 SPI 与数据 SPI 各有帧控制。 |
| 电源及返回 | `VDD33` 供模拟域，`VDD11` 标称 1.1 V 供核心，`IOVDD` 标称 1.1 V 供接口。内置模式可用 `VDDLDO` 1.5…2.7 V 输入供两路 LDO，VDD11/IOVDD 作去耦节点且不并联外驱；外供模式 VDDLDO 留空。裸片侧为 GND/REFGND/IOGND 各自规划回流，复核封装和 ESD 回路。Rev.B 板级连接用公共低阻地平面。 |

数据手册 p.45–48 给出 LVDS DDR 位序、回送时钟和对齐约束。20 位 × 40 MS/s 的净码率为 800 Mb/s，一通道 DDR 理想需要 400 MHz，双通道每通道 400 Mb/s、DDR 200 MHz。帧、训练和模式切换所需余量应另计。AD4080 对外为二补码，v21 `out_code` 为本工程的 offset-binary。若选择对外二补码，等量程 20 位编码转换可在外围对 MSB 异或 1，量程、饱和、位序及模式需统一验证。16K 结果 SRAM 的 4×4096×20 是本规划的银行切分，结果标签/错误标记另由控制器处理。 [AD4080 Rev. B，p.5、10–12、33、45–48、56–57、72](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) [SRAM 接口](../rtl/result_store.sv)

差分 Pad/ESD 接入以目标 PDK 和封装为准。以相邻配对、同层成对引线、对称过孔及相近 Pad/ESD 寄生为布局起点，把 DA/DB 与 DCO 的相对传播偏斜也放进 PHY 约束。AD4080 参考接法中输入侧的 LVDS `CLK±`、启用 LVDS 的 `CNV±` 采用近端 100 Ω 差分终端，DCO/数据对在 Host 接收边核对终端。主输入、辅助输入和 REFIN 采用合格的低泄漏低寄生模拟 Pad，ESD 回路以对应电源/地的短低阻路径闭合，并检查输入共模、电源断开条件、反灌与跨电源钳位。数字高速出线远离采样和参考，保留地回流和屏蔽；模拟差分对、参考和敏感采样边的干扰以提取后的误差预算核验。电源 Pad 数、Pad 框架间距、裸片至封装引线以及端口保护等级待符合工艺的 Pad 库和封装图。内置和外供模式分别遵循 Rev.B p.25 的上电顺序，目标电源时序由 POR/隔离策略另行确认。 [AD4080 Rev. B，p.10–12、24–25、33、45–48、72](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

## 6. 联调需要覆盖的用例

| 用例 | 观察关系 |
| --- | --- |
| 冷启动、正常 A/B 交替 | 完整装载后首笔 qid=0。SADC/RDAC 预置在实际采样前稳定，B 的输入与 A 的余差在同一公共沿成功捕获。每笔采用自己的 `out_tag`，前级、ADC2 和结果侧分别记账。 |
| 共用事件 qid | A 在 CONV 等 SAR、B 采样时返回 Flash，两组异 qid 有效事件逐项仲裁。检查 F 与事件 1 同拍时正确取 F，迟于首提交不会篡改已决前缀。 |
| guard 与写门 | 512 阈接受/拒绝两条路径都走一遍。拒绝阈值的该拍 `post_wr_o=0`，合法相邻写拍各自到达，未选片保持。 |
| 余差归属与保持 | 旧 raw 与新捕获同沿按旧/新 owner 分配。先 raw 后 hold、先 hold 后 raw 各走一次。hold 缺失时，旧 CONV 仍持有片组；安静窗计满前无回池。 |
| 反压、晚报、复位 | 旧 owner 忙且无旧 raw 时不发下一实际捕获。无 owner 的 raw 触发 late，复位/掉电前的迟包在适配器处清除。验证错误分配不会被静默当成正常 `out_code`。 |
| 停收、窄余量和配置切代 | 已受理的 ACQ 仍推进至最后余差捕获；确认 out tag 最终核销才切表。加入 FIFO 快满与 `out_valid` 连续场景，确认本域入队容量、越界事件和停止策略。 |

以上为宏集成联调计划。本轮工作验证到固定源端口映射和规划截面，未产生硅宏、走线、时序仿真或签核证据。

## 7. 可复制的只读核对

以下命令从固定提交读取 RTL 声明，逐项比对 CSV 方向、位宽、符号和声明行，读取规划窗口并复算直连容量。CSV 中的 `LOCAL_*` 可用于审查调试位有没有被错误计入模拟窗。

```sh
# 在解压后的交付根目录运行；ADC_FUNCTION_REPO 指向含下述提交的本地只读检出。
: "${ADC_FUNCTION_REPO:?请先 export ADC_FUNCTION_REPO 指向数字功能工程的本地路径}"
python3 - <<'PY'
import csv
import json
import os
import math
import re
import subprocess
from pathlib import Path

# 只对冻结 Git 对象取证，工作区现有改动不参与比对。
repo = os.environ['ADC_FUNCTION_REPO']
commit = '7408edf8db9a41eebdd756378b69d2775f107dec'
source = 'rtl/sar_core/adc_digital_ip_v21.sv'
rtl = subprocess.check_output(['git', '-C', repo, 'show', f'{commit}:{source}'], text=True)
lines = rtl.splitlines()
start = next(i for i, line in enumerate(lines) if line.startswith('module adc_digital_ip_v21'))
pattern = r'(input|output)\s+logic\s+(?:(signed)\s+)?(?:\[(\d+):\s*(\d+)\]\s+)?(\w+)\s*,?'

# 一行一端口，带符号的偏置与其它无符号数据一并核对。
ports = []
for number, line in enumerate(lines[start + 1:], start + 2):
    declaration = line.split('//', 1)[0].strip()
    if declaration == ');':
        break
    if declaration:
        direction, sign, high, low, name = re.fullmatch(pattern, declaration).groups()
        width = abs(int(high) - int(low)) + 1 if high is not None else 1
        ports.append((name, direction, width, sign or 'unsigned', number))

with Path('interfaces/v21_ports.csv').open(encoding='utf-8', newline='') as handle:
    rows = list(csv.DictReader(handle))
metadata = [(r['port'], r['direction_at_ip'], int(r['width_bits']),
             r['signedness'], int(r['rtl_line'])) for r in rows]
assert metadata == ports and len(ports) == 90
assert sum(p[2] for p in ports) == 1449

# 物理片控制、SADC 预置与判决回送独立计数，保留拆窗前总压力作为交叉检查。
write_ports = {'rdac_pre_state_o', 'pre_wr_o', 'rdac_post_state_o', 'post_wr_o'}
assert sum(p[2] for p in ports if p[0] in write_ports) == 468
expected = {'CLK_RST': 2, 'SADC_RDAC': 522, 'ADC2_RSP': 21, 'RESULT': 55,
            'CFG_TEST': 92, 'Q_RETURN': 31}
for window, count in expected.items():
    assert sum(int(r['width_bits']) for r in rows if r['window'] == window) == count
assert expected['SADC_RDAC'] + expected['Q_RETURN'] == 553
assert sum(expected.values()) == 723

plan = json.loads(Path('floorplan/plan.json').read_text())
port_lines = {item['group']: item['line'] for item in plan['ports']}
channel_boxes = {item['id']: item['rect'] for item in plan['channels']}
assert set(expected) <= set(port_lines)
p2 = port_lines['SADC_RDAC']
p6 = port_lines['Q_RETURN']
assert p6 == [145, 904, 145, 938]
north = channel_boxes['Q_SPUR_N']
south = channel_boxes['Q_SPUR_S']
assert p6[0] == north[2] and north[1] <= p6[1] < p6[3] <= north[3]
assert north[1] == south[3]
assert channel_boxes['IO_EAST'] == [1090, 1360, 1380, 1376]

# 过北段、接界、南段以及 Q 西边时，分别计算有效几何截面。
q_rects = next(item['rects'] for item in plan['partitions'] if item['id'] == 'Q')
q_contact = sum(max(0, min(south[3], b[3]) - max(south[1], b[1]))
                for b in q_rects if south[2] == b[0])
join_width = min(north[2], south[2]) - max(north[0], south[0])
q_spans = [math.dist(p6[:2], p6[2:]), north[2] - north[0], join_width,
           south[2] - south[0], q_contact]
assert q_spans == [34, 24, 24, 25, 70]
p2_span = math.dist(p2[:2], p2[2:])
assert p2_span == 325

# 两个等轨距层各预留屏蔽/过孔后只用 60% 的轨道，结果仍须做实际 pin access 检查。
p2_capacity = 2 * math.floor(0.60 * math.floor((p2_span - 10) / 0.64))
q_capacities = [2 * math.floor(0.60 * math.floor((span - 4) / 0.64)) for span in q_spans]
assert p2_capacity == 590 and p2_capacity - expected['SADC_RDAC'] == 68
assert q_capacities == [54, 36, 36, 38, 122] and min(q_capacities) - expected['Q_RETURN'] == 5
print(f'{len(ports)} ports, 1449 bits; RDAC writes 468; P2 {expected["SADC_RDAC"]}/{p2_capacity}; '
      f'P6 {expected["Q_RETURN"]}/{min(q_capacities)}')
PY
```

可单独运行 `shasum -a 256` 校核来源。Rev. B 本地 PDF SHA-256 为 `f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1`，IPBrain 图为 `689cd44fc8afd8f23ced86f6f1beaad5b8e5687fdea67176ee2866a6bdfcc25c`。原始证据索引包括《ISSCC 2024 9.8》DOI `10.1109/ISSCC49657.2024.10454329` 和 Rev. B 图 2 / 表 6。 [规划坐标](../floorplan/plan.json) [模拟接口合同](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/v21/PHYSICAL_INTERFACE.md) [开关规则](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/v21/SWITCHING_RULES.md)
