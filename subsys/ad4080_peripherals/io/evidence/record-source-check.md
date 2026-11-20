# AD4080 FIFO 读记录原文复核

复核对象是官方 `AD4080 Rev. B` 95 页原件，SHA-256 `f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1`。页码采用文档印刷页。逐图放大复核字符、DCLK 周期计数、单样本各位及 `FIFO_READ_DONE` 位置。公开代码按指明的 commit 检查。[ADI Rev. B](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

## 原件与公开实现

| 模式 | 线上的头与数据 | 图示长度 | 证据 |
|---|---|---|---|
| FIFO 单线 CMOS SPI | 物理 `SDOB` 先送固定 `0xAA`，每字随后按 `D19` 连续五次、`D18` 至 `D0` 输出。五次中的前四次为符号扩展 | `8 + 24N` 个 DCLK | p51–52、表 22/23、p59 图 87 |
| FIFO 四线 CMOS SPI | `SDOA/B/C/D` **每条**先送 `0xAA`，占 8 个 DCLK。每字首拍四线均为 `D19`。次拍按物理 A/B/C/D 分别送 `D16/D17/D18/D19`，末拍送 `D0/D1/D2/D3` | `8 + 6N` 个 DCLK | p51–52、表 22/23、p59 图 88 |
| FIFO 单线 LVDS | `DA±` 先送固定 `0xAA`，之后是 `N` 个 MSB-first、未扩展的 20 位字，所有样本后跟一个画出的 24 位 CRC 字段 | `8 + 20N + 24` 个串行位，占 `4 + 10N + 12` 个逻辑 DDR 边沿对 | p59–60 图 89，DDR 基本定义 p45–47 |

图 88 的四线位序以物理引脚标注为准。如本包 PHY 字 `[3:0]` 接物理 `A/B/C/D`，这个 PHY 字在非全同的 payload beat 上等于对应逻辑半字节的位反序。举例，正样本 `0x135A9` 的 SPI 符号扩展字是 `0x0135A9`，第 2 个 payload beat 的逻辑半字节为 `1`，物理 A/B/C/D 依次为 `1/0/0/0`。[图 87、88](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=59)

结果 SPI 的 `DCLK` 来自主机，复用 `CLK+`。`CLK−` 复用为低有效 `DCS`，`DCO±` 在 SPI 模式下禁用。p51 规定 DCLK 下降沿移出，p7 给出最小周期 20 ns。ADI 员工 DomSloan 确认 Mode 3 和首个时钟边沿移出首位，上升沿为主机采样边沿。DCS 下降沿进入选中状态，首位由下一次 DCLK 下降沿推出。LVDS 使用主机供给的差分 `CLK±` 与双边沿数据，默认 `DCO±` 是延迟回传的时钟。FIFO 读在捕获完成后开启，读时钟与 CNV 不要求固定相位。图 89 画出 READ_DONE 在末字送入接口时变化，末字段仍须完整读出。[p7](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7) · [p45–47](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=45) · [p51](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=51) · [ADI 员工确认 Q3](https://ez.analog.com/data_converters/precision_adcs/f/q-a/603310/fifo-full-signal/597558)

紧凑复核表

| 边界 | 已确认 | 仍需绑定 |
|---|---|---|
| 图 87/88 SPI | 图中两种总时钟数已含头和全部符号扩展样本。ADI 评估固件 `6835d5c` `ad4080_iio.c` 实际读 `3N+1` 或 `3N+4` 字节，跳过 1/4 字节后按大端 24 位解包 | SPI 图示记录外的后续输出行为。p51 的 CRC 泛述与图示和固件读长之间的适用范围仍需原厂确认 |
| 图 89 LVDS | 一个 24 位尾字段位于 watermark 的全部 20 位样本之后 | 计算所覆盖的首尾位、是否含固定头、是否补位、生成多项式、`init`、`xorout`、`refin/refout` 及 CRC 字自身的上线路序 |
| ADI 低层驱动/HDL | no-OS `68f9869` 的 `ad4080_read_data` 将调用者传入的 `len` 用于数据 SPI 传输。AXI AD408x 的公开 HDL 说明展示 LVDS 解串、20 位结果拼字及固定字对齐 | 这些接口没有给出 FIFO CRC24 算法或参考码 |

固件 `ad4080_read_fifo_data` 在 1227–1261 行给出长度，在 1418–1466 行给出样本位置及大端解包。这样可把 Rev. B 图示的无尾 SPI 读长用作参考模式。p51 中 `data result record` 的措辞并未定义逐样本 CRC。配置口 p41 的 CRC-8 算法适用于寄存器传输，结果口用独立 CRC。另一则 EngineerZone 帖子有用户称单线实测 CRC 校验通过，回复中未公开可复核的原始尾位或算法参数，保留为待确认线索。[ADI 参考固件固定版本](https://raw.githubusercontent.com/analogdevicesinc/precision-converters-firmware/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c) · [ADI no-OS 固定版本](https://github.com/analogdevicesinc/no-OS/blob/68f9869649621441de4a1949caeef0be3f3e2692/drivers/adc/ad4080/ad4080.c#L103-L112) · [AXI AD408x](https://developer.analog.com/docs/hdl/latest/library/axi_ad408x/index.html#internal-interface-description) · [用户台架帖](https://ez.analog.com/data_converters/precision_adcs/f/q-a/606806/ad4080---intermittent-adc_cnv_err-at-startup-and-cnv-oscillator-disable/606699)

p51 对固定测试字的描述较宽，p84 表 48 的寄存器位定义明确写 `LVDS Only`。固定字 `0xAC5D6` 可用于 LVDS 链路的位对齐。连续 LVDS 的 `ad4080_ddr_edge_words` 静态位选符合 p45 和图 74/75，同一槽先下降沿后上升沿，双 lane 的 DA/DB 分别发奇/偶位。FIFO LVDS 读限定单 lane，独立的头与尾由 record framer 处理。[Rev. B 表 48](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84) · [连续 LVDS 图 74/75](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=46)

## 独立可复算向量

运行 `python3 io/evidence/record-source-vectors.py`。SPI 黄金数据由 Python 的有符号三字节大端转换得到，四线再按图 88 转置为物理 A/B/C/D。表内 SPI4 一位十六进制对应一个 DCLK 的四线电平，从左至右是时间顺序。LVDS 列按线位从早到晚拼接。尾为任意已排线的注入值，借此验证 24 位尾的传递。

| 输入 20 位二补码序列 | SPI1 `SDOB` | SPI4 物理 `A/B/C/D` | LVDS `DA` 含给定尾 |
|---|---|---|---|
| `135A9` | `AA0135A9` | `F0F0F0F0 08CA59` | `AA135A9 38BC42` |
| `E62B4` | `AAFE62B4` | `F0F0F0F0 F764D2` | `AAE62B4 D36587` |
| `135A9 E62B4` | `AA0135A9FE62B4` | `F0F0F0F0 08CA59 F764D2` | `AA135A9E62B4 A5176C` |
| `E62B4 135A9` | `AAFE62B40135A9` | `F0F0F0F0 F764D2 08CA59` | `AAE62B4135A9 2E904D` |

N=1 时三模式分别为 32 DCLK、14 DCLK、52 DDR 边沿位。N=2 时为 56 DCLK、20 DCLK、72 DDR 边沿位。检查完整记录尾标记，默认 SPI 在末字最后一拍结束，LVDS 在注入尾最后一拍结束。单字正负、双字首尾反转和变化的外部尾可以定位符号扩展、顺序、四线位转置或末位截断。模拟用例通过后仍需实物或原厂参考向量确认真实 CRC 值。

独立 Verilator 5.30 smoke 对 `ad4080_fifo_record_framer.sv` 快照 `b53600e2725c62bd4bd4a09ddf434f75acec107ac9be57d3ba017570ac9e4218` 逐 beat 核对了以上 4 组向量 × 3 种模式，12 组通过。检查了首尾标记、数据宽度、字段种类和 `done`。默认 SPI 在无 CRC 服务时完整发出样本。LVDS 在尾未到达时等待，接受外部尾后完成。另测一条 LVDS 缺绑定的拒绝路径，未发出头字段。该结果验证逻辑 beat，物理 DCLK/DCO/PAD 时序需另行集成验证。

## 台架采集条件

1. 在同一配置、同一器件版本下，用立即触发和小 watermark `N=1,2,3` 分别采集。记录 `0x15/0x16/0x1C–0x1E` 的实际配置和 GPIO 状态。选择两种符号、不同最低位的原始样本。固定字模式可用于单独的 LVDS 链路对齐，进行 FIFO CRC 抓取时恢复正常转换。保留每个接收字的实际值。
2. SPI 抓 `DCS`、`DCLK` 和全部 `SDOA..D`，同步保留 `CNV`、`FIFO_FULL`、`FIFO_READ_DONE`。低速开始，在下降沿后的有效窗口解码，并同时画出上升沿采样点。先完整抓图示读长，另做受控延长 DCLK 的独立抓包，分辨是否出现与数据相关的附加位、稳定闲置电平或高阻。四线保持物理 A/B/C/D 命名。
3. LVDS 抓 `CLK±`、`DCO±`、`DA±` 的差分极性及两边沿原始顺序，横跨 `0xAA`、`20N` 和全部 24 尾位。N 为奇数时 payload 非整字节，可检验以 20 位串入和补齐处理的差别。保留不同长度及互换首尾的原始位流，然后用原厂参数或已知模型进行逐条回算。
4. 固定 `0xAA` 前缀对 LFSR 的影响可以折算为另一初值。仅增加固定头的捕获数量，难以分辨覆盖头和改变 `init` 两种解释。最终签收需要原厂算法、受控制的不同头或独立已知初始化状态。成功采到尾字段只证明其存在，算法还需参数与向量闭合。
