# AD4080 DSP 配置、滤波、事件与读出实测

从 `7408edf8db9a41eebdd756378b69d2775f107dec` 导出 `subsys/ad4080_dsp` 后，用 Verilator 5.30 执行。[独立台架](./tb_dsp_path.sv)从配置 SPI 引脚写入，送入已完成转换的 20 位补码，经原 DSP 校正、滤波、阈值和单个 FIFO，再接本工程的结果 SPI 组帧器。**独立接收端收到 80 位 `AA 00075E 0009E8 FFF30E`**，其中三个 24 位补码为 1886、2536、−3314。组帧器只从实际 FIFO 读口接样本，台架桥接最多暂存一字。

## 1. 固定数字链通过的用例

| 接点 | 施加条件与实测 |
| --- | --- |
| 配置 SPI → 用户校正 | 长指令、降序、严格双字节由真实 `CS_N/SCLK/SDI/SDO` 交付，并读回 GAIN 和 OFFSET。复位 `GAIN=0x200`；写 OFFSET 12 位补码 `0xFC0` 即 −64 LSB。工程绑定 `K_GAIN=1, SHIFT=14` 并写 `GAIN=0x300`，−1024 输入变为 −1104，在首输入为第 1 拍时第 3 个核心 `clk` 出数。关闭非单位映射和 COMP 系数绑定后，两种设置均被 SPI 路径拒绝并保留旧值，单位增益输入 −1024 经 −64 偏置得到 −1088。 |
| SINC1 → 阈值和 GPIO | SPI 写 `FILTER_CONFIG=0x09`，SINC1 的 N=4。GPIO1 低有效信号重置滤波，GPIO0 配为低有效 ALERT。首组应用校正后平均为 196，尽管有单笔未滤波结果越过 `4×256=1024`，事件仍保持未触发。第 4 个接受输入进入后才完成整个均值块，首输入为第 1 拍时第 8 个核心 `clk` 得到 196。第二组平均 1106 触发 HI，GPIO0 变低，模式 10 同时进入等待后续结果状态。 |
| 事件 → 单 FIFO → 真实组帧器 | SPI 配置 `WATERMARK=3`、`INT_EVENT_EN/HI_ROUTE` 和 FIFO 模式 10。实际捕获从触发后的下一组滤波值开始，记录 `[1886,2536,-3314]`。负样本使 ALERT 解除，HI 状态仍保持。首次读清协议 FULL，第三次 `dout_valid` 响应使读完状态有效。`ad4080_fifo_record_framer` 处于单 SPI、默认无 CRC 尾，独立接收只采 `beat_o[0]`、逐位左移，核对首尾边界、80 位全长及负码 `FFF30E` 符号扩展。 |
| SINC5 启动与倍率 | SPI 写 `0x0A`，N=4。连续输入 −1024，校正后均为 −1104。数值 `chain_valid` 首次在第 15 个核心 `clk` 出现，对应 0。前五次输出均为 0，第 35 个 `clk` 才出现首个非零值 −60，随后为 −552、−1044、−1104，第 47 个 `clk` 到达此向量稳态。相邻有效输出相隔 4 拍。 |
| SINC5 启动码进入 FIFO 的反例 | FIFO 模式 01、W=1，GPIO 复位后立即送样，真实读口得到启动码 **0**。同样复位后保持 FIFO 未启动，先送 48 个已知连续输入、只改 FIFO 模式开启捕获，再送数据，W=1 读回 **−1104**。后一个测试提供项目侧的开窗对照，48 仅对该配置、台架输入和数字节拍有效。 |
| SINC5+COMP 工程绑定 | SPI 写 `0x0B`，N=4，Q15 系数 `{16384,32768,-16384}`。首次数值有效在第 25 个核心 `clk`，首五字依次 `0、0、−336、−1320、−1104`，输出相隔 8 拍。独立期望先将 N=4 五段均值的 16 个卷积权重归一成 1024，再按选中 CIC 数值流第 3、5、7、9、11 项及前两项计算三抽头并舍入。 |
| 停机与配置接缝 | `adc_idle=0` 且恰好无 `valid_in` 时，仍可真实写入并读回 `FILTER_CONFIG`。原生 `ad4080_top_tb` 同时实跑软件复位资格：2 次保留已完成记录的复位、3 次拒绝、61 次读比对、11 次停流断言、3 次路由检查，合计 1290 项检查。 |

直接读和事件端使用 `chain_valid`。前级事件比较取**滤波后的** 20 位二补码高 12 位，沿后产生电平及阈值上跳脉冲。当前 RTL 向 FIFO 交付的是该数字事件，模式 10 只在登记 `event_seen` 后采样。板级 ALERT 需另验以 CNV 为锚点的出现和恢复时间。 [原顶层连接](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_top.sv#L83) [事件比较](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/event_detect.sv#L45) [捕获与时序](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/fifo_capture.sv#L102) [Rev. B p.54–57](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

## 2. 起录、参数修改与复位保护

[可综合记录保护](./ad4080_record_guard.sv)控制同一冻结 DSP 的 `interface_ready` 和同步滤波复位入口。信任边界是受控 SPI 命令源。发送端把一条经核实的命令声明为读、配置、复位或起录，拿到 `grant_o` 后发真实 SPI，并在同步后的 CS 释放、结果已确认后结束。起录许可发出即锁定记录，包含等触发、采写、待读及读回。会话所有者确认读完或安全取消才释放锁并消耗本批资格。等待/读出期间仍可读 CSR。未获许可的物理 SPI 帧实测在原 DSP 以 `NOT_READY=8` 拒绝，寄存器保留。授权后的 CNV 资格若在帧结束前失效，许可撤销且旧帧不能重新放行，记录锁仍等真实会话所有者清理。

| 工程拒绝码 | 含义 |
| --- | --- |
| 1 | 记录锁定，禁止改配、复位、重复起录及滤波复位请求。 |
| 2 | 配置/软件复位所需的系统 ADC 停机确认缺失。 |
| 3 | CNV 到数值链的映射未绑定，或采用本接口尚未绑定起录节拍的 COMP。 |
| 4 | 尚无本批资格，例如未锚定、未到建立数、CNV 已停或批次已用。 |
| 5 / 6 | 前一 SPI 帧仍占用 / 前一许可因条件变化被撤回。 |

`epoch_start_i` 必须同时带同步后的真实 `cnv_tick_i`，滤波模式还要观察到本批实际 `FILTER_SYNC`。计数从该 CNV 记 1。CNV 停止、缺失脉冲超时、模式/倍率或实际等待绑定变化都使当前批次无资格。只有 FIFO 尚未起录时的选定链有效参与数值计数；进入会话后 `dout_valid` 具有 FIFO 读响应语义。早到或失锁的外事件显式丢弃。内部阈值触发在预热期间保持关闭，随已授权的模式 10 命令开启后才进入原 DSP 捕获链。批次 `profile_valid_i` 的责任包括真实边沿与该批每个输入/滤波输出关系、比较和 FIFO 配置以及物理时序。授权位保持各项成立后的准入资格，专用于该条起录命令。

| 路径 | 公开倍率及建立口径 | 原 DSP 数值入场条件 |
| --- | --- | --- |
| 旁路 | 无抽取。保护最少看到一个已锚定的 CNV，额外等待可由项目实际绑定提高。 | 同批首个有效新码。 |
| SINC1 | `D=N`，手册计数 `2+D+1`。图 99 是 D=2、W=3，用第 1 个 CNV 同步、3/4 号把首组两笔输入送进滤波，第 5 个 CNV 出首字，第 6 个写 FIFO。 | 首个完整 N 点均值。 |
| SINC5 | `D=N`，手册计数 `2+5D+4`。 | 等五个 comb 流水抽取点，加上覆盖 `5(N−1)+1` 点卷积的 `ceil((5N−4)/N)` 个抽取结果。对 N=2、4、≥8，最早分别在第 8、9、10 个数值输出具备完整历史。还须在手册计数及额外绑定等待都满足后核对一次选定有效。 |
| SINC5+COMP | p.94 额外 2 倍抽取，表 29 的对外 D 为 `2N`。p.69 单列 COMP 建立式 `2+35D+10`；现有三抽头工程系数和流水没有已绑定的器件逐 CNV 对齐。 | 本保护拒绝起录。数值回归通过和调通总抽取率均不能代替厂商补偿和精确起录相位。 |

保护不提供器件的逐 CNV `FILTER_RESULT_RDY`，也不复现图 99 的逐沿 FIFO 写时刻。冻结 DSP `chain_valid` 已包含数字流水的含义，图 99 的公开时序与外部实际输入交付都要纳入生产确认。为防配错，上表数值边界只对已经通过绑定验收的所选配置开放。 [Rev. B p.66–69、94](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) [原 SINC5 有效样本定义](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/sinc5_decim.sv#L82)

在冻结 DSP 上把启动 W=1 反例原样重跑。未保护的窗口读到 **0**；保护测试用独立 50 ns 周期模拟 CNV 针脚，核心 `clk` 为 10 ns，经过同步沿检测，从 E1 锚定后在 E3 才交付第一笔完成码，其后每个 CNV 交付一次。SINC5 N=4 对相同的 −1024 输入及 SPI 校正配置，在同步后计到 **44 个 CNV、第 9 个所选数值**才授权，实际受控 SPI 起录和 FIFO 读回为 **−1104**。44 是测试观察值。CNV 停止时即使运行声明保持高也会撤销旧资格；针脚恢复后仍需新批。还实测了旁路模式 10 等事件期间拒绝改配/复位，早期触发未进入 FIFO、新触发才捕获，以及改模式、未绑定和 COMP 拒绝。FIFO 与结果组帧沿用同一个冻结 DSP 的装配。

生产使用须由真实发送器落实操作分类和只发授权帧，禁用 `arm`、`FILTER_SYNC` 或原始配置 SPI 的旁路写入口。若要求任意外部原始 SPI 主机直接连接，原 DSP 所有者须在解码实体提交前提供写分类与逐沿许可，防止伪装分类或许可跨事务。若要求运行中按每个样本控制捕获、精确处理内部阈值或在已提交后断 CNV 时撤销写入，须在原 DSP 所选结果、`fifo_arm`、`fifo_capture.valid_in` 与事件路径之间增加正式握手接缝。现有模块提供可落实的起录控制和外事件门控，DSP 里已经接受的实体写无法回滚。共享系统复位必须同时管理保护状态和 DSP 会话。 [原 SPI 授权与实体提交](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/spi_slave.sv#L160) [原 DSP FIFO 模式选择](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_top.sv#L124)

## 3. 板级使用要有的确认信号

- **SPI 绑定。** 试验声明 `P_WRITE_SDO_DRIVE=0`、`P_MULTIBYTE_ORDER=0`、`P_OFFSET_LSB_ADDR=37`。完全采用 RTL 参数默认时，首个写事务首先卡在未绑定的 SDO 写阶段策略，严格双字节访问和 OFFSET 地址另有各自的绑定门。OFFSET 采用 Rev. B p.92 地址表 `0x25/0x26`，p.71 正文提到 `0x24/0x25`，应跟地址表及实板读写确认。Rev. B 给出单位增益 `0x200`、偏置码范围和名义可调范围。专项中的 `K_GAIN/SHIFT` 与三抽头系数用于检验带符号定点路径，产品增益律、端点、补偿频响和舍入需单独绑定。 [SPI 拒绝逻辑](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/spi_slave.sv#L233) [寄存器绑定](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_regs.sv#L115) [Rev. B p.71、92–93](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)
- **滤波同步与出数。** 台架的低有效 GPIO1 经两级同步产生 `filter_sync`，`0x29` 写也会发内部清流脉冲。三种滤波的输出间隔按接受样本计数，与数值流水 `clk` 边沿延迟分别计。GPIO 选择 `3` 被 `ad4080_regs` 明确拒绝。Rev. B 第 68 页的器件 `FILTER_RESULT_RDY` 是物理 CNV 时序下的低有效提示；例如 SINC1 D=2 的 FIFO 采集要求 `FILTER_SYNC` 相对首个 CNV 上升沿提前至少 15 ns，下一 CNV 前释放至少 5 ns。第 69 页按滤波种类另列 CNV 建立与填满记录的预算。实际集成需要独立的 CNV/有效窗口和开始捕获授权，避免把上表的早期 `chain_valid` 接作器件 RDY 或生产采集资格。 [滤波顶层](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_dsp_chain.sv#L47) [GPIO 功能拒绝](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_regs.sv#L110) [Rev. B p.64–70、94](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)
- **停机与切代。** 生产由转换调度器停止接受新的采样，继续排尽已接受的采样、ADC2 返回和重构，然后按 DSP/FIFO/读出状态确认安全停机。`adc_idle` 与物理的无在途转换绑定，禁止用 `!valid_in` 或 `!chain_valid` 冒充。固定 DSP 只在软件复位路径用 `adc_idle`、捕获完整性和数据口活动作资格；改滤波、偏置或增益前的停收及重启由外围保证。SINC5 的积分流水靠接受样本推进，在空拍可能仍保留滤波历史，换代时显式 `FILTER_SYNC` 清除。 [寄存器资格](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_regs.sv#L90) [SINC5 有效样本推进](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/sinc5_decim.sv#L82)
- **结果口码制。** 本台架接 `ad4080_top.dout` 的补码。若系统接的是 `sar20_ad4080_top.dout`，该封装已经在输出重新翻转了最高位，接需要补码的组帧器前应统一码制。`sar_sample_id` 描述接受的输入，抽取、事件和一条记录有多个输入样本，应另外传会话或序号。 [现有 SAR 到 DSP 适配](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/sar20_ad4080_top.sv#L65) [结果组帧](../../io/ad4080_fifo_record_framer.sv)

## 4. 统一到唯一物理 SRAM 的切点

本次台架使用 DSP 自带的 `fifo_capture.u_fifo`，深度缩小为 16 字 × 20 位，以便专注于真实写读次序。正式容量默认 16,384 × 20 位。进入产品顶层综合及目标 SRAM 宏绑定前，应把 `result_fifo` 的 RAM 访问映射到规划的 `result_store` 四银行，保留 `fifo_capture` 的模式、事件、watermark、快照读及软件复位保留语义。`result_store` 是单口同步 SRAM 接缝，物理替换时统一端口仲裁和读延迟，把读请求、读有效及源快照地址逐笔核对。进入 MBIST 前停机并清会话，确认测试退出完成后才开新捕获。不得同时把行为 FIFO 内数组和独立 `result_store` 合进产品，形成两套结果存储。重新运行本章所有读回检查，以及跨银行、读写互斥、模式 11 环形快照、复位/掉电和 MBIST 仲裁。 [原 FIFO RAM](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/result_fifo.sv#L40) [规划存储接缝](../../rtl/result_store.sv) [Rev. B p.56–63](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

实验边界为实测的固定 RTL 行为。核心 `clk` 周期 10 ns，SPI 用确定的慢边沿驱动；结果单 SPI 使用一个 `clk` 的逻辑 beat 在独立接收端采样。实物 CNV、真实 DCS/DCLK PHY、异步 MAC、实际接收端时序、CDC 签核、SPI 四通道、LVDS、CRC 绑定和全部采集模式由后续各自收敛。本次也未执行核心 ADC 转换、目标宏映射或实板波形。

## 5. 固定 ADI 开源实现提供的证据

| 固定源 | 可用结论 |
| --- | --- |
| no-OS `0e6ed1dea135ef541ad6323fc9fcad116ffce90b` 的 `ad4080.h/.c`，并复核 `68f9869649621441de4a1949caeef0be3f3e2692` 的 `ad4080.c` | 头文件定义 OFFSET `0x25`、GAIN `0x27`、FILTER_CONFIG `0x29`、模式字段、倍率编码和 RDY/SYNC 的 GPIO 功能。两个驱动 C 文件中未找到增益数值律、芯片补偿抽头或 RDY 首拍计算。 [no-OS 寄存器与字段](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L45-L122) [GPIO 功能](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L244-L256) [两次核查的驱动 C](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.c) [后续快照](https://github.com/analogdevicesinc/no-OS/blob/68f9869649621441de4a1949caeef0be3f3e2692/drivers/adc/ad4080/ad4080.c) |
| ADI Linux `47d8cd414a78abc09a62015a40cee34717b8060d` 的 `drivers/iio/adc/ad4080.c` 和 `adi-axi-adc.c` | 读取 0x29 得 `N=2^(code+1)`。SINC1 允许 N=2…1024 的幂二表，SINC5/PF1 允许 N=2…256。IIO `oversampling_ratio` 在 PF1 仍报 N，`sampling_frequency` 按 `fCNV/(2N)` 算。驱动设 GPIO1 输出转换器 RDY，后端模式选择只切 FPGA 的 `FILTER_EN`。 IIO `scale` 由满量程和码宽计算。该路径未给出 GAIN 内部乘法/舍入、PF1 数组或逐 CNV 引脚首次有效相位。 [倍率及 IIO](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/ad4080.c#L232-L309) [允许档](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/ad4080.c#L364-L420) [GPIO](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/ad4080.c#L743-L760) [后端](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/adi-axi-adc.c#L313-L324) |
| ADI HDL `7738f35fcddb44e5b55ff676a8ee166fe88b1d28` 的 `library/axi_ad408x` | 外部 `filter_data_ready_n` 接入 PHY，下降沿登记许可，由 `packed_data_valid_d` 消耗，滤波模式下两者共同限定接收结果。旁路走解帧有效。通用调节的 IQ/DC 端口在该 AXI 顶层未接，用户抽取 M/N 置 1/1。本路径用来接收芯片结果，RDY 时间由器件本身与板级接口提供。 [顶层 RDY 接线](https://github.com/analogdevicesinc/hdl/blob/7738f35fcddb44e5b55ff676a8ee166fe88b1d28/library/axi_ad408x/axi_ad408x.v#L297-L312) [PHY 接收门控](https://github.com/analogdevicesinc/hdl/blob/7738f35fcddb44e5b55ff676a8ee166fe88b1d28/library/axi_ad408x/ad408x_phy.v#L579-L593) [通用接口端口处理](https://github.com/analogdevicesinc/hdl/blob/7738f35fcddb44e5b55ff676a8ee166fe88b1d28/library/axi_ad408x/axi_ad408x.v#L174-L214) |
| ADI 参考固件 `6835d5c30d25d37499de5f210256828193d5c78e` 的 `ad4080_iio.c` | 装载模式值和 N 档号到 0x29 的位字段，公开 OFFSET 电压与码换算。固件中的 `sinc_dec_rate` 字符串是基础 N，COMP 输出倍率仍按表 62 确定。在核查文件中未找到非单位增益换算或 COMP 频响系数。 [档位表](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L327-L346) [写入 0x29](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L911-L937) |

Rev. B p.93 锚定 `0x001 → 1−0.015594`、`0x200 → 1` 并旁路校正、`0x3FF → 1+0.015594`。p.71 报告名义步长。端点和寄存器访问没有给出每码定点舍入、`0x000` 行为或旁路路径的精确内部延迟。现 DSP `sys_error_corr` 固定两个核心时钟间隔并要求显式工程 `K_GAIN/SHIFT`，本轮沿用该边界。 [ADI Rev. B p.71、93](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=93) [冻结校正源码](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/sys_error_corr.sv#L58)

## 6. 每一档实测倍率与首字

继续使用同一只冻结 `ad4080_top`。逐档经真实 SPI 写并读回 0x29，在 FIFO 模式 00 观察顶层 `dout/dout_valid`。CNV 针脚激励周期 50 ns，经同步后给计数；以模拟引脚 E1 为本批锚点，E3 起每个 CNV 交付一笔已经完成的 −262144。沿用工程增益及偏置，完整历史的目标结果为 −266304。每档比较至少三个随后结果的稳定间隔，记录首个数值有效和首次进入稳定尾段的 CNV 序号。仿真中的稳定尾段与独立支持长度推算的完整历史点重合。表示为“首个数值有效 / 首个稳态”的表项均是同步后的 CNV 序号。旁路实测 `3 / 3`，间隔 1。

| 基础 N | SINC1，间隔 N | SINC5，间隔 N | Rev. B SINC5 建立计数 `5N+6` | 工程 COMP，间隔 2N |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 5 / 5 | 10 / 24 | 16 | 14 / 30 |
| 4 | 7 / 7 | 12 / 44 | 26 | 20 / 52 |
| 8 | 11 / 11 | 16 / 88 | 46 | 32 / 112 |
| 16 | 19 / 19 | 24 / 168 | 86 | 56 / 216 |
| 32 | 35 / 35 | 40 / 328 | 166 | 104 / 424 |
| 64 | 67 / 67 | 72 / 648 | 326 | 200 / 840 |
| 128 | 131 / 131 | 136 / 1288 | 646 | 392 / 1672 |
| 256 | 259 / 259 | 264 / 2568 | 1286 | 776 / 3336 |
| 512 | 515 / 515 | — | — | — |
| 1024 | 1027 / 1027 | — | — | — |

27 个合法档与 `code=15` 的 3 个越界档均通过。SINC1 / SINC5 / 工程 COMP 的最大有效倍率分别钳为 1024 / 256 / 512，CSR 回读保持写入的原始抽取字段。每次换档先停源并排空。另保留一个 SINC1 N=8 的部分组停止 CNV，前三笔已校正为 `586`，期间无结果，恢复后五笔 `−1104` 使首个平均为 `−470`，旧批起录仍被拦截。再停机且留存部分旧组，实写 0x29 切到 N=2，测到首个新数值是纯新批 `−1104`。省略外部同步时，0x29 自身的清流仍能让冻结数值路径出新码，保护仍拒绝起录。 [分档和停切台架](./tb_dsp_path.sv) [原寄存器写后清流](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_regs.sv#L347)

SINC1 的 `N+3` 与手册建立计数一致，D=2 的例子是第 5 个 CNV；图 99 另给第 6 个 CNV 写 FIFO，表中的观测点尚未覆盖这个引脚和 FIFO 的相位。SINC5 的首个数值有效是 0，这条冻结实现在抽取节拍上推进五级 comb，完整历史推迟到表中第二个序号。例如 N=4 的手册计数为 E26，台架首个数值有效在 E12，完整历史在 E44。COMP 若按表 29/62 的总倍率 `D=2N` 使用 p.69 的 `2+35D+10`，N=4 对应 292 个 CNV；工程三抽头此时 E52 为所用定值的完整历史结果。该样本流有工程系数和本地相序，器件的 `FILTER_RESULT_RDY` 仍依赖实际板级输入与转换器输出测量。现有保护的结果历史门槛在所有 SINC1/SINC5 档达到资格，COMP 各档保持关闭。 [ADI Rev. B p.66、68–69、94](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=68)

## 7. 一条命令复现

从交付根目录运行，入参为含该 Git 对象的原工程。PATH 使用预装的 Verilator、Clang C++20、Make 和 Python 3。固定 DSP 源、当前交付的组帧器和独立台架只在新建的 ASCII `/tmp` 中参与构建。原仓的工作树保持只读。若 Verilator 是自带运行库的发行包，传入其安装根（绝对路径），脚本使用暂存别名并调用同包 `verilator_bin`。

```sh
bash interfaces/dsp_review/run.sh "$ADC_FUNCTION_REPO"
```

应看到 `PASS result SPI SDOB: beats=80 wire=aa00075e0009e8fff30e`、W=1 的 0/−1104 对照、`PASS guarded SINC5 W=1`、`AD4080_DSP_RATE_SCAN_COMPLETE legal=27 clamp=3 pause_switch=1` 与 `AD4080_TOP_CONTRACT_COMPLETE ... checks=1290`。脚本每次打印独立运行目录，`bound`、`unbound`、`native` 各有构建和运行日志，断言或输出标记缺失时非零退出。成功最终输出 `AD4080_DSP_REVIEW_COMPLETE`。此次本机用 Verilator 5.30 的合并构建模式，避开已安装发行包的拆分 PCH 参数与本机 Clang 的不匹配。

记录保护通过 `Verilator --lint-only -Wall`。独立目录用 YoWASP Yosys 0.69 执行 `read_verilog -sv`、`hierarchy -check`、`synth -noabc`、`check -assert` 和 JSON 导出，得到 **917 个通用逻辑单元**、0 条结构问题。此数量来自通用逻辑网表。分档扫描沿用该保护，源码未改。原仓 HEAD 为 `7408edf`，工作树检查为空。扫描后台架为 628 行代码及 133 行注释（21.2%），扫描前为 539/109（20.2%）。入口为 41/21（51.2%），扫描前为 40/21（52.5%）。保护为 178/69（38.8%）。
