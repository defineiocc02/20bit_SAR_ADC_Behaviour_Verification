# AD4080 DSP 配置、滤波、事件与读出实测

从 [DSP 源锁定文件](../../evidence/dsp-revision.txt) 指定的唯一 Git 提交导出 `rtl/ad4080_dsp` 后，用 Verilator 5.30 执行。[独立台架](tb_dsp_path.sv)从配置 SPI 引脚写入，送入已完成转换的 20 位补码，经原 DSP 校正、滤波、阈值和单个 FIFO，再接本工程的结果 SPI 组帧器。**独立接收端收到 80 位 `AA 00075D 0009E7 FFF30D`**，其中三个 24 位补码为 1885、2535、−3315。组帧器只从实际 FIFO 读口接样本，台架桥接最多暂存一字。

## 1. 固定数字链通过的用例

| 接点 | 施加条件与实测 |
| --- | --- |
| 配置 SPI → 用户校正 | 长指令、降序、严格双字节由真实 `CS_N/SCLK/SDI/SDO` 交付，并读回 GAIN 和 OFFSET。复位 `GAIN=0x200`；写 OFFSET 12 位补码 `0xFC0` 即 −64 LSB。工程绑定 `K_GAIN=1, SHIFT=14` 并写 `GAIN=0x300`，图 96 的先加后乘将 −1024 输入变为 −1105，在首输入为第 1 拍时第 3 个核心 `clk` 出数。关闭非单位增益映射后，非单位增益写入被 SPI 路径拒绝并保留旧值，单位增益输入 −1024 经 −64 偏置得到 −1088。PF1 的系数由各档固定表提供。 |
| SINC1 → 阈值和 GPIO | SPI 写 `FILTER_CONFIG=0x09`，SINC1 的 N=4。GPIO1 低有效信号重置滤波，GPIO0 配为低有效 ALERT。首组应用校正后平均为 195，尽管有单笔未滤波结果越过 `4×256=1024`，事件仍保持未触发。第 4 个接受输入进入后才完成整个均值块，首输入为第 1 拍时第 8 个核心 `clk` 得到 195。第二组平均 1105 触发 HI，GPIO0 变低，模式 10 同时进入等待后续结果状态。 |
| 事件 → 单 FIFO → 真实组帧器 | SPI 配置 `WATERMARK=3`、`INT_EVENT_EN/HI_ROUTE` 和 FIFO 模式 10。实际捕获从触发后的下一组滤波值开始，记录 `[1885,2535,-3315]`。负样本使 ALERT 解除，HI 状态仍保持。首次读清协议 FULL，第三次 `dout_valid` 响应使读完状态有效。`ad4080_fifo_record_framer` 处于单 SPI、默认无 CRC 尾，独立接收只采 `beat_o[0]`、逐位左移，核对首尾边界、80 位全长及负码 `FFF30D` 符号扩展。 |
| SINC5 启动与倍率 | SPI 写 `0x0A`，N=4。连续输入 −1024，修正后为 −1105。首数值在第 11 个核心沿，依次输出 −60、−552、−1045、−1105，第 23 个核心沿出现首个完整窗结果。随后每 4 拍输出一次。 |
| SINC5 启动码进入 FIFO 的反例 | 模式 01、W=1 同步后直接送样，独立并行读口得到部分窗 **−60**。保持 FIFO 未启动先送 48 个已知输入，再布防并送新数据，读回 **−1105**。48 是此台架选择的对照窗口。 |
| PF1 补偿 | SINC5 与 PF1 共用一次五阶 CIC，PF1 对偶数 CIC 输出应用 35 抽头对称 Q22 FIR。基础 N=4 时，首数值在第 17 个核心沿，首码 3，第 161 个核心沿首次进入 −1105 的稳定整数平台，输出相隔 8 拍。台架比较 25 个实测字与独立完整点积。 |
| 停机与配置接缝 | `adc_idle=0` 且恰好无 `valid_in` 时，仍可真实写入并读回 `FILTER_CONFIG`。原生 `ad4080_top_tb` 同时实跑软件复位资格：2 次保留已完成记录的复位、3 次拒绝、61 次读比对、11 次停流断言、3 次路由检查，合计 1290 项检查。 |

该独立台架关闭产品 CNV 定相，直接读和事件端使用 `chain_valid`。前级事件比较取**滤波后的** 20 位二补码高 12 位，沿后产生电平及阈值上跳脉冲。当前 RTL 向 FIFO 交付的是该数字事件，模式 10 只在登记 `event_seen` 后采样。板级 ALERT 需另验以 CNV 为锚点的出现和恢复时间。 [原顶层连接](../../../ad4080_dsp/ad4080_top.sv) [事件比较](../../../ad4080_dsp/event_detect.sv) [捕获与时序](../../../ad4080_dsp/fifo_capture.sv) [Rev. B p.54–57](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

## 2. 起录、参数修改与复位保护

[可综合记录保护](ad4080_record_guard.sv)控制同一冻结 DSP 的 `interface_ready` 和同步滤波复位入口。信任边界是受控 SPI 命令源。发送端把一条经核实的命令声明为读、配置、复位或起录，拿到 `grant_o` 后发真实 SPI，并在同步后的 CS 释放、结果已确认后结束。起录许可发出即锁定记录，包含等触发、采写、待读及读回。会话所有者确认读完或安全取消才释放锁并消耗本批资格。等待/读出期间仍可读 CSR。未获许可的物理 SPI 帧实测在原 DSP 以 `NOT_READY=8` 拒绝，寄存器保留。授权后的 CNV 资格若在帧结束前失效，许可撤销且旧帧不能重新放行，记录锁仍等真实会话所有者清理。

| 工程拒绝码 | 含义 |
| --- | --- |
| 1 | 记录锁定，禁止改配、复位、重复起录及滤波复位请求。 |
| 2 | 配置/软件复位所需的系统 ADC 停机确认缺失。 |
| 3 | CNV 到所选数值链的映射未绑定，包括 PF1。 |
| 4 | 尚无本批资格，例如未锚定、未到建立数、CNV 已停或批次已用。 |
| 5 / 6 | 前一 SPI 帧仍占用 / 前一许可因条件变化被撤回。 |

`epoch_start_i` 必须同时带同步后的真实 `cnv_tick_i`，滤波模式还要观察到本批实际 `FILTER_SYNC`。计数从该 CNV 记 1。CNV 停止、缺失脉冲超时、模式/倍率或实际等待绑定变化都使当前批次无资格。只有 FIFO 尚未起录时的选定链有效参与数值计数；进入会话后 `dout_valid` 具有 FIFO 读响应语义。早到或失锁的外事件显式丢弃。内部阈值触发在预热期间保持关闭，随已授权的模式 10 命令开启后才进入原 DSP 捕获链。批次 `profile_valid_i` 的责任包括真实边沿与该批每个输入/滤波输出关系、比较和 FIFO 配置以及物理时序。授权位保持各项成立后的准入资格，专用于该条起录命令。

| 路径 | 公开倍率及建立口径 | 原 DSP 数值入场条件 |
| --- | --- | --- |
| 旁路 | 无抽取。保护最少看到一个已锚定的 CNV，额外等待可由项目实际绑定提高。 | 同批首个有效新码。 |
| SINC1 | `D=N`，手册计数 `2+D+1`。图 99 是 D=2、W=3，用第 1 个 CNV 同步、3/4 号把首组两笔输入送进滤波，第 5 个 CNV 出首字，第 6 个写 FIFO。 | 首个完整 N 点均值。 |
| SINC5 | `D=N`，手册计数 `5D+6`。 | 五重窗口支持长 `5(N−1)+1`，N=2、4、≥8 分别从第 3、4、5 个数值开始完整。工程起录守卫至少消费五个新数值，同时等待已登记的 CNV 计数达到手册下界和额外等待，再用实际选中有效沿确认资格。 |
| PF1 | 总倍率取 `D=2N`，保护下界采用 p.69 的 `2+35D+10`。 | 守卫至少观察 35 个实际 PF1 数值，同时检查 CNV 下界、已绑定的批次关系和所选有效沿。 |

保护不提供器件的逐 CNV `FILTER_RESULT_RDY`，也不复现图 99 的逐沿 FIFO 写时刻。冻结 DSP `chain_valid` 已包含数字流水的含义，图 99 的公开时序与外部实际输入交付都要纳入生产确认。为防配错，上表数值边界只对已经通过绑定验收的所选配置开放。 [Rev. B p.66–69、94](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) [原 SINC5 有效样本定义](../../../ad4080_dsp/sinc5_decim.sv)

在同一 DSP 上重跑启动 W=1 反例。未保护的窗口读到部分卷积 **−60**；保护测试用独立 50 ns 周期模拟 CNV 针脚，核心 `clk` 为 10 ns，经过同步沿检测，从 E1 锚定后在 E3 才交付第一笔完成码，其后每个 CNV 交付一次。SINC5 N=4 对相同的 −1024 输入及 SPI 校正配置，守卫在 **第 27 个 CNV、第 6 个所选数值**授权，实际受控 SPI 起录和 FIFO 读回为 **−1105**。第 5 个数值先于手册 E26 下界到来，因此本台架等到 E27 的下一实际有效沿。CNV 停止时即使运行声明保持高也会撤销旧资格；针脚恢复后仍需新批。还实测了旁路模式 10 等事件期间拒绝改配/复位，早期触发未进入 FIFO、新触发才捕获，以及改模式、PF1 未绑定时拒绝、PF1 已绑定但无批次时拒绝。FIFO 与结果组帧沿用同一个冻结 DSP 的装配。

生产使用须由真实发送器落实操作分类和只发授权帧，禁用 `arm`、`FILTER_SYNC` 或原始配置 SPI 的旁路写入口。若要求任意外部原始 SPI 主机直接连接，原 DSP 所有者须在解码实体提交前提供写分类与逐沿许可，防止伪装分类或许可跨事务。若要求运行中按每个样本控制捕获、精确处理内部阈值或在已提交后断 CNV 时撤销写入，须在原 DSP 所选结果、`fifo_arm`、`fifo_capture.valid_in` 与事件路径之间增加正式握手接缝。现有模块提供可落实的起录控制和外事件门控，DSP 里已经接受的实体写无法回滚。共享系统复位必须同时管理保护状态和 DSP 会话。 [原 SPI 授权与实体提交](../../../ad4080_dsp/spi_slave.sv) [原 DSP FIFO 模式选择](../../../ad4080_dsp/ad4080_top.sv)

## 3. 板级使用要有的确认信号

- **SPI 绑定。** 试验声明 `P_WRITE_SDO_DRIVE=0`、`P_MULTIBYTE_ORDER=0`、`P_OFFSET_LSB_ADDR=37`。完全采用 RTL 参数默认时，首个写事务首先卡在未绑定的 SDO 写阶段策略，严格双字节访问和 OFFSET 地址另有各自的绑定门。OFFSET 采用 Rev. B p.92 地址表 `0x25/0x26`，p.71 正文提到 `0x24/0x25`，应跟地址表及实板读写确认。Rev. B 给出单位增益 `0x200`、偏置码范围和名义可调范围。专项中的 `K_GAIN/SHIFT` 用于检验有符号定点增益。PF1 使用公开频响拟合的固定系数，独立频响检查见下文。 [SPI 拒绝逻辑](../../../ad4080_dsp/spi_slave.sv) [寄存器绑定](../../../ad4080_dsp/ad4080_regs.sv) [Rev. B p.71、92–93](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)
- **滤波同步与出数。** 台架的低有效 GPIO1 经两级同步产生 `filter_sync`，`0x29` 写也会发内部清流脉冲。三种滤波的输出间隔按接受样本计数，与数值流水 `clk` 边沿延迟分别计。GPIO 选择 `3` 被 `ad4080_regs` 明确拒绝。Rev. B 第 68 页的器件 `FILTER_RESULT_RDY` 是物理 CNV 时序下的低有效提示；例如 SINC1 D=2 的 FIFO 采集要求 `FILTER_SYNC` 相对首个 CNV 上升沿提前至少 15 ns，下一 CNV 前释放至少 5 ns。第 69 页按滤波种类另列 CNV 建立与填满记录的预算。实际集成需要独立的 CNV/有效窗口和开始捕获授权，避免把上表的早期 `chain_valid` 接作器件 RDY 或生产采集资格。 [滤波顶层](../../../ad4080_dsp/ad4080_dsp_chain.sv) [GPIO 功能拒绝](../../../ad4080_dsp/ad4080_regs.sv) [Rev. B p.64–70、94](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)
- **停机与切代。** 生产由转换调度器停止接受新的采样，继续排尽已接受的采样、ADC2 返回和重构，然后按 DSP/FIFO/读出状态确认安全停机。`adc_idle` 与物理的无在途转换绑定，禁止用 `!valid_in` 或 `!chain_valid` 冒充。固定 DSP 只在软件复位路径用 `adc_idle`、捕获完整性和数据口活动作资格；改滤波、偏置或增益前的停收及重启由外围保证。SINC5 的积分流水靠接受样本推进，在空拍可能仍保留滤波历史，换代时显式 `FILTER_SYNC` 清除。 [寄存器资格](../../../ad4080_dsp/ad4080_regs.sv) [SINC5 有效样本推进](../../../ad4080_dsp/sinc5_decim.sv)
- **结果口码制。** 本台架接 `ad4080_top.dout` 的补码。若系统接的是 `sar20_ad4080_top.dout`，该封装已经在输出重新翻转了最高位，接需要补码的组帧器前应统一码制。`sar_sample_id` 描述接受的输入，抽取、事件和一条记录有多个输入样本，应另外传会话或序号。 [现有 SAR 到 DSP 适配](../../../ad4080_dsp/sar20_ad4080_top.sv) [结果组帧](../../io/ad4080_fifo_record_framer.sv)

## 4. 台架与产品存储的接缝

独立台架使用 `fifo_capture.u_fifo` 内部的 16×20 行为存储，运行上述真实读写顺序。产品装配改由唯一 `u_services.u_result_store` 接收原 FIFO 的地址和数据，含四个 4096×20 银行，保留 FIFO 模式、事件、watermark、快照读和已完成记录的软复位读回。实际单口地址受理后才推进读游标。目标 SRAM 宏、MBIST 和实现时序留待对应绑定验收。 [台架使用的 FIFO RAM](../../../ad4080_dsp/result_fifo.sv) [产品装配](../../integration/product/README.md) [存储接缝](../../rtl/result_store.sv)

本台架用 10 ns 核心周期和确定的慢 SPI 边沿，结果单 SPI 使用核心域逻辑 beat。产品 DCS/DCLK、连续 LVDS、四线 SPI、全深记录及电源复位轮询由各自产品用例验证。实物引脚电气条件、CDC 签核和目标 SRAM 宏映射仍需各自的实现证据。 [产品用例](../../integration/product/README.md)

## 5. 固定 ADI 开源实现提供的证据

| 固定源 | 可用结论 |
| --- | --- |
| no-OS `0e6ed1dea135ef541ad6323fc9fcad116ffce90b` 的 `ad4080.h/.c`，并复核 `68f9869649621441de4a1949caeef0be3f3e2692` 的 `ad4080.c` | 头文件定义 OFFSET `0x25`、GAIN `0x27`、FILTER_CONFIG `0x29`、模式字段、倍率编码和 RDY/SYNC 的 GPIO 功能。两个驱动 C 文件中未找到增益数值律、芯片补偿抽头或 RDY 首拍计算。 [no-OS 寄存器与字段](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L45-L122) [GPIO 功能](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L244-L256) [两次核查的驱动 C](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.c) [后续快照](https://github.com/analogdevicesinc/no-OS/blob/68f9869649621441de4a1949caeef0be3f3e2692/drivers/adc/ad4080/ad4080.c) |
| ADI Linux `47d8cd414a78abc09a62015a40cee34717b8060d` 的 `drivers/iio/adc/ad4080.c` 和 `adi-axi-adc.c` | 读取 0x29 得 `N=2^(code+1)`。SINC1 允许 N=2…1024 的幂二表，SINC5/PF1 允许 N=2…256。IIO `oversampling_ratio` 在 PF1 仍报 N，`sampling_frequency` 按 `fCNV/(2N)` 算。驱动设 GPIO1 输出转换器 RDY，后端模式选择只切 FPGA 的 `FILTER_EN`。 IIO `scale` 由满量程和码宽计算。该路径未给出 GAIN 内部乘法/舍入、PF1 数组或逐 CNV 引脚首次有效相位。 [倍率及 IIO](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/ad4080.c#L232-L309) [允许档](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/ad4080.c#L364-L420) [GPIO](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/ad4080.c#L743-L760) [后端](https://github.com/analogdevicesinc/linux/blob/47d8cd414a78abc09a62015a40cee34717b8060d/drivers/iio/adc/adi-axi-adc.c#L313-L324) |
| ADI HDL `7738f35fcddb44e5b55ff676a8ee166fe88b1d28` 的 `library/axi_ad408x` | 外部 `filter_data_ready_n` 接入 PHY，下降沿登记许可，由 `packed_data_valid_d` 消耗，滤波模式下两者共同限定接收结果。旁路走解帧有效。通用调节的 IQ/DC 端口在该 AXI 顶层未接，用户抽取 M/N 置 1/1。本路径用来接收芯片结果，RDY 时间由器件本身与板级接口提供。 [顶层 RDY 接线](https://github.com/analogdevicesinc/hdl/blob/7738f35fcddb44e5b55ff676a8ee166fe88b1d28/library/axi_ad408x/axi_ad408x.v#L297-L312) [PHY 接收门控](https://github.com/analogdevicesinc/hdl/blob/7738f35fcddb44e5b55ff676a8ee166fe88b1d28/library/axi_ad408x/ad408x_phy.v#L579-L593) [通用接口端口处理](https://github.com/analogdevicesinc/hdl/blob/7738f35fcddb44e5b55ff676a8ee166fe88b1d28/library/axi_ad408x/axi_ad408x.v#L174-L214) |
| ADI 参考固件 `6835d5c30d25d37499de5f210256828193d5c78e` 的 `ad4080_iio.c` | 装载模式值和 N 档号到 0x29 的位字段，公开 OFFSET 电压与码换算。固件中的 `sinc_dec_rate` 字符串是基础 N，COMP 输出倍率仍按表 62 确定。在核查文件中未找到非单位增益换算或 COMP 频响系数。 [档位表](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L327-L346) [写入 0x29](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L911-L937) |

Rev. B p.93 锚定 `0x001 → 1−0.015594`、`0x200 → 1` 并旁路校正、`0x3FF → 1+0.015594`。p.71 报告名义步长。端点和寄存器访问没有给出每码定点舍入、`0x000` 行为或旁路路径的精确内部延迟。现 DSP `sys_error_corr` 固定两个核心时钟间隔并要求显式工程 `K_GAIN/SHIFT`，本轮沿用该边界。 [ADI Rev. B p.71、93](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=93) [冻结校正源码](../../../ad4080_dsp/sys_error_corr.sv)

## 6. 每档起始数值、完整窗口与守卫资格

逐档通过真实 SPI 写入并读回 0x29。固定输入 −262144，经校正得到 −266305。CNV 周期为 50 ns，核心周期为 10 ns。E1 为已同步批次的锚点，E3 开始每个 CNV 的下一核心沿交付一笔完成码。表中的首字和完整窗是当前独立台架的数值观察点。

| 基础 N | SINC1 首字 | SINC5 首字 / 首码 / 完整窗 / 守卫准入 | PF1 首字 / 首码 / 完整窗 / 守卫准入 |
| ---: | ---: | ---: | ---: |
| 2 | E5 | E5 / −49932 / E9 / E17 | E8 / 46 / E80 / E152 |
| 4 | E7 | E7 / −14564 / E19 / E27 | E12 / 802 / E156 / E292 |
| 8 | E11 | E11 / −6437 / E43 / E51 | E20 / 625 / E324 / E580 |
| 16 | E19 | E19 / −3938 / E83 / E99 | E36 / 486 / E644 / E1156 |
| 32 | E35 | E35 / −2992 / E163 / E195 | E68 / 423 / E1284 / E2308 |
| 64 | E67 | E67 / −2585 / E323 / E387 | E132 / 256 / E2564 / E4612 |
| 128 | E131 | E131 / −2397 / E643 / E771 | E260 / 249 / E5124 / E9220 |
| 256 | E259 | E259 / −2307 / E1283 / E1539 | E516 / 245 / E10244 / E18436 |
| 512 | E515 | — | — |
| 1024 | E1027 | — | — |

旁路首字为 E3 的 −266305，间隔一笔。SINC1/SINC5 的输出间隔为 N，PF1 为 2N。27 个合法档和三个输入编码 15 的钳位档均通过。独立参考展开五重矩形卷积的阶跃权重，再用固定表执行 PF1 全整数点积。扫描逐码对照 443 个输出，并检查每字的 CNV 序号和核心时钟沿。PF1 N=2 在 E76 已进入稳定整数码，完整窗口在 E80，守卫直到 E152 收到第 37 个实际数值才给予资格。在同一基础 N 下，产品定相器以第 35 个真实 PF1 数值建立 E152 比较与公布资格，并在下一 CNV 给 FIFO。 [根定相台架](../../../ad4080_dsp/sim/tb/ad4080_cnv_phases_tb.sv) [手册 p.69](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=69)

另保留 SINC1 N=8 部分组停样用例。前三个校正码 585 与恢复后的五个 −1105 形成首个平均 −471，旧批起录仍被拦截。清流换到 N=2 后读到 −1105，再次省略外部同步时只给数值结果，守卫仍拒绝起录。[分档和停切台架](tb_dsp_path.sv)

## 7. 一条命令复现

从外围子系统根目录运行，入参为包含源锁定文件所列 Git 对象的工作树。PATH 使用预装的 Verilator、Clang C++20、Make 和 Python 3。固定 DSP 源、当前交付的组帧器和独立台架只在新建的 ASCII `/tmp` 中参与构建。原仓的工作树保持只读。若 Verilator 是自带运行库的发行包，传入其安装根（绝对路径），脚本使用暂存别名并调用同包 `verilator_bin`。

```sh
bash interfaces/dsp_review/run.sh "$ADC_FUNCTION_REPO"
```

应看到 `PASS result SPI SDOB: beats=80 wire=aa00075d0009e7fff30d`、W=1 的 −60/−1105 对照、`PASS guarded SINC5 W=1`、`AD4080_DSP_STEP_ORACLE_COMPLETE words=443`、`AD4080_DSP_RATE_SCAN_COMPLETE legal=27 clamp=3 pause_switch=1` 与 `AD4080_TOP_CONTRACT_COMPLETE`。脚本每次打印独立运行目录，`bound`、`unbound`、`native` 各有构建和运行日志，断言或输出标记缺失时非零退出。成功最终输出 `AD4080_DSP_REVIEW_COMPLETE`。构建使用 Clang C++20 与 `-O0`，并将大型台架的生成代码拆分为多个编译单元。


## 8. 滤波频响核对

[频响脚本](verify_filter_response.py)以同一固定提交的 RTL 发出真码，验证 26 个合法滤波档和三个倍率钳位档，并分别记下表 28、图 104–107 的可测解释及误差。输出包含原始频响 CSV、规格比较、图 106 留出点和逐档摘要。 [频响验收结果](evidence/filter_validation.json)

```sh
python3 interfaces/dsp_review/verify_filter_response.py --repo "$ADC_FUNCTION_REPO" --output "$RESPONSE_DIR"
```
