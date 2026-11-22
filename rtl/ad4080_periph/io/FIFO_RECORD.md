# FIFO 结果记录合同

## 原件与适用配置

Rev. B 图 87、88 和 89 的同步头均为 `0xAA`。图 87/88 分别给出单、四线 SPI `8+24N`、`8+6N` 个 DCLK。图 88 每根 SDO 都看到完整 8 周期的 `0xAA`。图 89 在 8 位头、`20N` 位载荷之后另画一个 24 位 CRC。第 51 页的 CRC 总述使用 FIFO 结果记录口径。其尾字段的算法与覆盖范围并未公开到可据以生成硅片期望值的程度。[结果 CRC 和符号扩展，第 51–52 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=51) · [FIFO 读波形，图 87–89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=59)

ADI 固件在数据 SPI 读取中对单线请求 `3N+1` 字节，对四线请求 `3N+4` 字节，在此次传输后改变片选，并从偏移 1/4 逐样本按 BE24 解析。no-OS 底层读取接口依调用方参数搬运字节。ADI 的 AXI AD408X 资料和 PHY/顶层 RTL 描述 LVDS 接收、对齐和采样链路，其中未找到 FIFO CRC24 算法或覆盖域。默认 SPI 组帧依该主机读取前缀结束，硅片在增加 DCLK 时还会送出什么需补实物或厂商记录。[ADI 固件读取函数](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L1227-L1261) · [ADI 固件 BE24 解析](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L1418-L1466) · [ADI no-OS 读取源](https://github.com/analogdevicesinc/no-OS/blob/68f9869649621441de4a1949caeef0be3f3e2692/drivers/adc/ad4080/ad4080.c#L103-L112) · [ADI AXI AD408X 文档](https://analogdevicesinc.github.io/hdl/library/axi_ad408x/index.html)

## 模式、线序与返回字数

| `mode_i` | 每 beat 出口 | 头与载荷 | 终点 |
| --- | --- | --- | --- |
| `0` 单 LVDS | `[1]` 首发下降沿，`[0]` 随后的上升沿 | 四个 `2'b10` 同步头 beat。每字照原始 transport 发 10 个 pair，从 D19 下降/D18 上升到 D1 下降/D0 上升 | 每条记录一个外部 CRC24，12 个 pair，合计 `4+10N+12` 个有效 pair |
| `1` 单 SPI | `[0]` 接 SDOB，下降沿移出 | 8 个 DCLK 同步头。每字由 `D19` 复制四次再接 D19…D0，24 个 DCLK | 默认 `8+24N` clocks，末位后主机可结束 DCS。明确绑定 SPI CRC 尾时加 24 个 clocks |
| `2` 四 SPI | `[3:0]` 对应 SDOA、SDOB、SDOC、SDOD，下降沿同时移出 | 每 lane 8 个 DCLK 同发同步头。先四个符号位，然后第 1 个数据 nibble 在物理 A/B/C/D 上为 `D16,D17,D18,D19`，最后为 `D0,D1,D2,D3`。每字 6 个 DCLK | 默认 `8+6N` clocks，随后主机可结束 DCS。明确绑定 SPI CRC 尾时加 6 个 clocks |

`N` 在记录接纳时锁定，范围为 1 至 16,384。即时触发、读最新记录时，采集控制器用约定的 WATERMARK；读整个结果存储时用 16,384，早触发导致的先前样本定义归上游记录管理。结果 SPI 的 DCS/DCLK 驱动与时序在 PHY 或主机。ADI 答复指出结果 SPI 采用 Mode 3，头的首位随第一下降 DCLK 发出，DCS 下降仅选择器件。数字 framer 的 `beat_last_o` 与 `done_o` 给 DCS 拥有者精确的结束标志。[Rev. B 表 27 与图 87–89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=57) · [ADI 结果 SPI 实测答复](https://ez.analog.com/data_converters/precision_adcs/f/q-a/603310/fifo-full-signal/597558)

## 真实装配

```text
SRAM 读回 → 已有跨域传输及 DDR 边沿排列 → pair、first、last ┐
已在 IO 域准备的 SPI 20 位样本 → sample_valid/ready/last ─┤
记录命令与长度 → start_valid/ready/accept ───────────────┤
CRC 服务 ← 会话启动及已交付头/载荷 → crc_valid/ready/i ──┤
                                                        └→ 同步头 / N 字 / 已绑定尾 → 目标 PHY
```

所有端口由 IO 域使用。`start_ready_o` 允许消费一次命令，只有 `start_accept_o` 才启动源读取和 CRC 会话。模式 3、空长度、超过 16,384 或未绑定的 profile 均拒绝。`format_bound_i` 来自产品约定。选用单 LVDS 或 `SPI_CRC_TAIL_BOUND=1` 时，`crc_service_ready_i` 必须为 1 且空闲中无旧 `crc_valid_i`。默认 SPI 由 `SPI_CRC_TAIL_BOUND=0` 选择 ADI 固件结束的前缀，启动无须 CRC 适配器。可以在专用产品配置启用 SPI 外部尾，由该产品的板级或厂商证据签署物理线序和时钟数。

CRC 服务在 `start_accept_o` 同沿建立新会话，获取模式、长度和配置。如以实际输出为校验源，可以观察 `beat_valid_o && beat_ready_i` 且 `beat_kind_o` 为头或载荷的交付 beat，再由已绑定的算法规则选择覆盖项。结果在 `crc_valid_i && crc_ready_o` 同沿捕获。CRC24 已按物理传输的时间顺序打包，首发位在 `crc_i[23]`，四 SPI 情况 `crc_i[23:20]` 就是第一拍物理 A/B/C/D。组帧器保持这种顺序。固化 `CRC poly/init/xor/reflection/coverage` 前须获得供应方或实测契约。`crc_error_i` 与响应同拍有效并废弃记录。默认 SPI 没有 CRC 握手。测试中 `A5C39E` 和复位后的 `693FA1` 是外部响应字面量，用于验证时序、位传输和旧尾隔离。

### 状态与故障

1. 空闲接受命令后进入预取。只有至少 `min(N,2)` 字已经到达发射域且 `beat_ready_i=1`，才开始同步头。LVDS 的预取数来自 transport 已同步的写指针。SPI 两字缓存由实际 `sample_valid/ready` 接受填充，`sample_last_i` 与固定字数一致。预取期间 AA、发射字装载和完成标志都保持无效，调用方可用 `abort_i` 明确撤销缺少初始供数的会话。
2. 首组 AA 至最后一组数据/SPI 前缀末位或 CRC 尾之间，在自由运行的 `clk_i` 上每拍一个实际输出 beat。已起发时撤销 `beat_ready_i` 报错 6。载荷期任一原始拍缺失数据立即报错 7，包括两字之间。两字预取只保障起发，上游仍须按接口时钟持续供给所有后续字。SPI 满缓存可在当前字最后一拍同时退役、前移和补位。
3. 需 CRC 的记录只在最终载荷 beat 实际交付当沿接受尾字。服务可预计算并持稳，或把当前最后一对位并入余数后同沿提供。CRC 缺失、失败或迟到均报错 4，事件到来后发射结束。成功记录的 CRC 下一拍开始，紧接载荷。默认 SPI 最后载荷 beat 退休即完成。`beat_last_o` 标定 profile 的最后 beat，`done_o` 从该 beat 实际接受沿寄存。
4. 掉电、复位与显式中止先切断有效输出、样本和启动握手。运行时 `power_good_i` 的撤销要保持到 IO 域有一沿可清状态，或由常开管理域直接置 `rst_ni`。故障中断不发 `done_o`，需要 CRC 的会话收到 `crc_cancel_o` 并清除响应。读回 owner 须同步作废对应 transport epoch 和已读暂存。`crc_service_ready_i` 重新置位前确认上一会话已排清。

| `error_code_o` | 条件 |
| --- | --- |
| 1 | 保留模式，或样本数不在合法域 |
| 2 | 产品格式未绑定、所需 CRC 服务不就绪、残留 CRC 响应或 LVDS 上轮 pair 仍有效 |
| 3 | SPI 源 `last` 与固定字数冲突，或 LVDS pair 标记、超量出线不符合合同 |
| 4 | 末载荷沿未收到有效 CRC，或 CRC 服务有效响应同时报告错误 |
| 5 | 会话内掉电，IO 有时钟记账 |
| 6 | 主机/上游中止，或同步头已开始后撤销 PHY 准入 |
| 7 | 载荷期在任一原始时钟上无有效字，记录不能用后续 valid 将其压缩 |

### 首末样本装载事件

`ad4080_fifo_lvds_record_link` 对外提供 `first_payload_word_load_io_o` 和 `last_payload_word_load_io_o`。两者以 transport 的 `frame_q` 真正装载资格为源，在 IO 域上升沿采纳。首样本与 AA 最后一对同沿装入，下一沿交付首载荷 pair。后续字在前一字的末 pair 同沿装入。N=1 时首末事件同沿。SRAM 请求和回答仅是预取。末样本装载还早于该样本的末位和整条记录 CRC 退休。

图 87–89 在首、末结果的移入接口边界标记 FIFO 状态。图 89 中 `FIFO_READ_DONE` 位于最后样本 D19 一带，随后的低位和 CRC 仍需发出。表 25 关于 `FIFO_READ_DONE` 的清除措辞与图示存在出入，具体 GPIO 翻转相位及同步后的软件可见时间需要厂商澄清或板上测量。首末装载信号供上层状态逻辑关联 `FIFO_FULL/READ_DONE`，并保留独立记录完成事件。[Rev. B 第 53、56、59–60 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=53)

实际目标 RX/TX、ODDR、DCS/DCLK 包脚时序及局部供电复位根据 [IO_PLAN.md](IO_PLAN.md) 的冻结条件走。连续 ADC raw lane 的已有映射继续见 [SPEC.md](SPEC.md)。

## 产品结果 SPI 的实际引脚接缝

`ad4080_fifo_lvds_record_link` 的 `SPI_RESULT_BOUND=1` 接入 [ad4080_result_spi_pins](ad4080_result_spi_pins.sv)。产品 `DATA_INTF_MODE=1` 时，同一读回实例和同一个运输 FIFO 直接交并行 20 位队首给原组帧器。两字前置寄存器、单个位组邮箱和一个引脚发射寄存器在原异步 FIFO 下游。四银行结果存储、队列指针和 24 位符号扩展/四线排列均沿用唯一逻辑。

- 主机只观察实际最后 SRAM 写入发布的 FIFO_FULL。它降 DCS 后第一个真实 DCLK 下降沿可开始同步头，允许读授权同步以及 SRAM 预取发生在随后的头部八个下降沿期间。`result_spi_selected_async_o` 只由合法首个 DCLK 实际发头后置位。FIFO 原样数据在核心两级看到后才取得读授权。未满时已经发生无效首拍的旧 DCS 窗口即使跨过真 FULL，也须先收片选再另选。内部 `result_spi_ready_io_o` 是载荷邮箱的诊断，软件无需观察。
- `result_dclk_i` 空闲高，原始每个下降沿锁存新 SDO，下一上升沿供主机采样。固定 `AA` 来自 framer 同一头定义。头末之后缺第一个或后续有效邮箱位组即中止，无法以补 bit 或延长报文成功。已入选后停 DCLK 可保留待发位组，提前抬 DCS 明确失败，部分读过的旧记录由上层作废。DCLK 令牌和位组稳定直到真实下降沿的回执，首个/最终数据字的第一物理组实际装入 SDO 后才跨回 I/O 并报装载事件，尾完成独立。
- 输出 `[3:0]` 为 A/B/C/D。单线仅 `SDOB`。四线在 DCS inactive 时仍让 `SDOD` 驱动，值保持最后锁存或复位值，属于该实现的未选输出值。其它线未选撤销 OE，失去 IOVDD 资格全部关断。Rev. B 表 6 仅给出 SDOD 的高阻例外。引脚电压、驱动强度、tDEN 以及板级采样时序须以真实宏和板测闭合。[Rev. B 第 10–11 页表 6](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=10)
- 表 2 的 DCLK 周期至少 20 ns，DCLK 高低分别至少 0.45 周期，`tDDSDO` 最长 9.6 ns。400 MHz 的理想服务时钟可在两个 50 MHz 的 DCLK 下降沿之间返回回执并补发一组。部署需要约束服务域回执、多位邮箱到 DCLK 首/下一个下降沿的建立时间、首位所依赖的 FULL/DCS 稳定路径，以及 DCLK→SDO 宏的传播时延。预取够第一位及持续的单口服务是有效帧资格。在给定核心时基、仲裁和 LP 状态下首字若不足，数字引脚报告欠载，待确定的是该实现与真实外部时序的裕量。[Rev. B 第 7–8 页表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7)

第 51 页描述每条 FIFO 数据记录附 24 位 CRC，第 59 页图 87/88 和 ADI 固件操作到 `AA + 24N` 的实际前缀为止。默认产品只闭合前缀，因此合法末组后的 DCS release 有独立成功回执。延长时钟的 silicon 数据仍未绑定。显式约定外部余数及其物理线序、让主机继续落时钟时，`SPI_CRC_TAIL_BOUND=1` 生成 24 个 SDOB 位或 6 组四线、总共 24 位的工程尾。[引脚引擎测试](run_result_spi_pins.sh)
