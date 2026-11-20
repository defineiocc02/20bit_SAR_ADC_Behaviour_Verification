# AD4080 参考数据接口与边沿字合同

## 原件与本次范围

参考原件为 Analog Devices《AD4080》Rev. B，共 95 页。本地 PDF SHA-256 为 `f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1`。本件的槽索引、输出全宽、闲置逻辑槽清零是自研适配 API 约定。Pad 和封装规划见 [IO_PLAN.md](IO_PLAN.md)。[ADI Rev. B 手册](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=1)

本页的 RTL `ad4080_ddr_edge_words` 提供连续转换、未编码的 LVDS 结果或固定测试模式进入 DDR 宏前的并行位排列。源 20 位字由上游结果产生和提交。DUT 无时钟、复位、寄存器或协议状态，且无 valid/tag 端口。FIFO 记录见独立的 [组帧合同](FIFO_RECORD.md)。实际发射、输出使能、DCO、配置 SPI、Manchester 和寄存器映射继续由各自的控制器或 PHY 实现。

## 可综合端口合同

| 端口 | 方向 | 含义 |
| --- | --- | --- |
| `sample_i[19:0]` | 入 | 本笔转换结果的 20 位原码位模式。D19 可为二补码符号位，适配过程逐位透明 |
| `dual_lane_i` | 入 | 0 单 lane，1 双 lane。属于本笔帧的已提交模式 |
| `intf_check_i` | 入 | 1 优先选固定字 `20'hAC5D6`，其发生条件由外围控制，仅用于 LVDS |
| `da_fall_o[9:0]`、`da_rise_o[9:0]` | 出 | DA 的下降沿/上升沿装载字。每个向量的 `[0]` 为最早槽 |
| `db_fall_o[9:0]`、`db_rise_o[9:0]` | 出 | DB 的下降沿/上升沿装载字。单 lane 全零，双 lane 的 `[9:5]` 清零 |
| `slot_count_o[3:0]` | 出 | 单 lane 为 10，双 lane 为 5。输出向量的有效槽为 `0..slot_count_o-1` |

同一槽按下降沿后随上升沿定义。用差分正端 CLK+ 的逻辑极性解释边沿，echo 下 DCO+ 保持对应极性并带延时。合法输入为确定的 0/1 位。上游需先持稳 `sample_i`、模式和对应帧身份，再由接收端在同一边界采纳。向量可在组合传播中变化。已接受帧的模式冻结、停钟、复位隔离和错误退出由外围控制。双 lane 的高槽以及单 lane DB 的逻辑零作为确定性装载值，PHY 仍需单独控制 OE。

| lane 模式 | 槽 `k` | DA 下降 | DA 随后上升 | DB 下降 | DB 随后上升 |
| --- | --- | --- | --- | --- | --- |
| 单 | 0…9 | `D[19−2k]` | `D[18−2k]` | 0 | 0 |
| 双 | 0…4 | `D[19−4k]` | `D[17−4k]` | `D[18−4k]` | `D[16−4k]` |

单 lane 第一槽 DA 为 D19/D18，最后槽为 D1/D0。双 lane 第一槽下降沿 A/B 为 D19/D18，随后的上升沿 A/B 为 D17/D16。最后槽下降沿为 D3/D2，最后上升沿为 D1/D0。双 lane 的 DA 在两个边沿均发奇数位，DB 均发偶数位。图 75、77 支持这种配对。图 76 的单 lane 图题与该图的 DA/DB 双轨和位值标注不一致。单 lane 模式依据第 45 页正文与图 74。[Rev. B 第 45–47 页，图 74–77](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=45)

## 商用品 LVDS 时钟与数据

| 名称 | 外部方向与边沿 | 40 MSPS 连续读出条件 |
| --- | --- | --- |
| `CNV±` | 输入。CNV+−CNV− 的上升沿取样并启动转换，默认 CMOS CNV+ 且 CNV− 接 IOGND | 25 ns 周期。双边沿均靠近 CLK± 的上升沿，表 2 给出 `tCCA ≤ 535 ps`；CNV 高低脉宽各至少 `tCLK` |
| `CLK±` | 输入。主机持续提供数据读出时钟 | 单 lane 400 MHz、10 周期每转换。双 lane 200 MHz、5 周期每转换。芯片的 CLK± 上限为 400 MHz |
| `DCO±` | 输出。echo 模式是同相、缓冲的 CLK±，`tDCO=1–2 ns` | 数据相对对应 DCO 边沿的 `tDCOD=0.02–1 ns`。接收采样相位由主机眼图/输入时序定 |
| `DA±/DB±` | 输出。已启用 lane 每个边沿发一位 | 单 lane DA 800 Mbit/s。双 lane 各 400 Mbit/s。两种配置理论结果总载荷均为 800 Mbit/s |
| self clock | DCO 发射器关闭且 DCO 可不接。主机用自己的 CLK 副本与延时校准采收 | `tCLKD=1–2.1 ns` 是商用器件的 CLK 到 Dx 延迟。封装外飞行时差与收端相位需实测 |

单 lane 默认 echo。`0x15[2]` 选 lane 数，`0x15[4] INTF_CHK_EN` 使正常结果不可读，改发固定码。脚位和寄存器数据优先遵循表 48 的 LVDS only 限定。第 33 / 51 页功能描述较泛。固定码用来验证 P/N、lane、位序和眼图相位，实际 ADC 字的帧对齐另用已知输入和 `CNV→D19` 度量。`0x16[3]` 选择 self clock，`0x16[0]` 可在 LVDS 数据接口下选择差分 CNV。`0x16[2]` 的 Manchester 只在双 lane 连续转换、`FILTER_SEL=0` 时成立。隔离示例在耦合电容之前保留 100 Ω 端接。Manchester 启用时不得将本实现的无编码边沿字直接视为串化帧。[交流时序表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7) · [图 74–78](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=46) · [寄存器表 48–50](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84)

### 帧开始和末位时刻

第 48–50 页区分内部结果可用、读时钟装载相位和电平在 Pad/主机可采样的时间。`tMSB` 最大 22.4 ns（开启增益校正），默认增益 `0x200` 时最大 18 ns。内部可用预算是 `tCYC + tMSB`。40 MSPS 参考首位相位按落在 CLK 下降沿的手册例子计算，寄存器字段编码查表 20，时钟计数不能直接写入字段。

| 40 MSPS 模式 | `tCLK` | 手册所用钟计数 | `0x16[7:4]` | CNV 到首发相位 |
| --- | ---: | ---: | --- | ---: |
| 单 lane、校正开启 | 2.5 ns | 10 | `0b0111` | 51.25 ns |
| 单 lane、增益默认 | 2.5 ns | 8 | `0b0101` | 46.25 ns |
| 双 lane、校正开启 | 5 ns | 5 | `0b0010` | 52.50 ns |
| 双 lane、增益默认 | 5 ns | 5 | `0b0010` | 52.50 ns（据公式重算，需台架复核） |

这里使用 `tCYC + (计数 + 0.5) × tCLK`。第 48 页最后一例将 `400 MHz/2.5 ns` 与 `18 ns/5 ns` 混列，双 lane 40 MSPS 应使用 200 MHz/5 ns。表 21 单 lane 20 MSPS 的 5.98 对应列出 6，与第 48 页向下取整口径有出入。该速率由主机用实际结果复核，不把公式变成自动写寄存器的逻辑。图 74/75 的理想 DDR 首末发边沿相差 `(20/L−1) × tCLK/2`，40 MSPS 下单 lane 23.75 ns、双 lane 22.50 ns。整帧占用 25 ns。第 50 页通式使用 `tCLK` 且未标双沿折半，因此在计算本适配的槽和末位时采用可逐沿数出的图 74/75。主机最终可采到末位的时间增加物理传播、相位及建立保持项。[交流时序表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7) · [图 74–77](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=46) · [首位延迟实例与表 20–21、图 79–81](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=48)

## 两套 SPI 和 FIFO 读出

| 接口 | 信号和边沿 | 用途和负荷 |
| --- | --- | --- |
| 配置 SPI | `CS_n`、`SCLK`、`SDI`、默认 GPIO0 上的 `SDO`。Mode 3，SCLK 闲置高，下降沿更新数据，上升沿采收。`tSCK ≥ 20 ns`，读端 `tDSDO ≤ 14.5 ns` | 主机读写配置、状态和寄存器。默认 16 位指令，另有 8 位。配置 CRC 可启用，使用独立 CRC-8 |
| 结果 SPI | `DCS_n`、`DCLK`、`SDOA..D`。切 SPI 时 CLK±/DA±/DB± 复用为 CMOS，DCO 关闭。DCLK 下降沿移出，`tDCK ≥ 20 ns`、`tDDSDO ≤ 9.6 ns` | FIFO 突发读。单 lane 固定走 `SDOB`，四 lane 各一位每周期，裸接口吞吐分别 50/200 Mbit/s。第 51 页的四 lane “200 MHz”是聚合数据率口径，不可设置 200 MHz DCLK |

两套 SPI 的 50 MHz 指手册允许的最高时钟频率。主机实际可用时钟需单独计算读出边沿预算。例如配置 SDO 的 14.5 ns 最大延迟已超过 50 MHz、50% 占空下的一次半周期，需满足接收建立、板传播及相位条件。

表 22–23 的命名关系是 `CLK+→DCLK`、`CLK−→DCS`、`DA+→SDOA`、`DA−→SDOB`、`DB+→SDOC`、`DB−→SDOD`。四 lane 的别名 `SDO3..0` 按 A/B/C/D 顺序。图 88 给出的实际每 24 位记录先在四线上发符号 D19 拷贝，随后 A/B/C/D 按每个 nibble 内从低到高位编号（D16、D17、D18、D19，最后 D0、D1、D2、D3）。单 lane 图 87 在 SDOB 上从符号扩展到低位顺序发出。AD4080 表 6 还提示四 lane 的 SDOD 在片选不活跃时不会进入高阻，做共享总线要解决争用。

图 87/88 给出 `8+24N` / `8+6N` 个 DCLK。首段为固定 `0xAA`，四 lane 各线同为 8 clocks。SPI 的每个样本从 20 位符号扩展到 24 位。图 89 的单 lane LVDS 显式标出同一头、`20×N` 位载荷和一个 24 位 CRC 尾。`ad4080_fifo_record_framer` 复用现有 transport 的 LVDS pair，默认 SPI 采用 ADI 驱动的前缀长度。明确的尾部约定和未知 CRC 项见 [组帧合同](FIFO_RECORD.md)。

图 82–85 定义 GPIO 输出警报/阈值、输入外部事件与 FIFO 触发关系。图 86 显示立即触发例中最后写入在 `WATERMARK+3` 个 CNV 时钟处。图 87–89 的 FIFO 读在进数结束后另开读时钟，不施加连续 LVDS 的 CNV/CLK `tCCA` 条件。图 90/91 记录了由配置 SPI 置 `FIFO_MODE=01` 并由 CNV 推进的启动，以及 `00→01` 重新武装。目标 `FIFO_FULL`、`FIFO_READ_DONE` 和事件行为需由结果存储/控制者确认并与读出 CDC 合作。[配置 SPI 第 33–44 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=33) · [结果 SPI 表 22–23](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=51) · [事件与 FIFO 图 82–91](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=54)

## 验收项

| ID | 可执行验收 |
| --- | --- |
| `REQ-IO-01` | 单 lane 逐下降/上升沿重组得到原样本，槽数 10，DB 两向量皆零 |
| `REQ-IO-02` | 双 lane 逐下降/上升沿、每沿 DA 后 DB 重组得到原样本，槽数 5，高槽皆零 |
| `REQ-IO-03` | `intf_check_i=1` 时两模式均重组得到 `20'hAC5D6`，下次解除后恢复透明映射 |
| `REQ-IO-04` | 通用综合检出零寄存器、零锁存器；在生成的通用逻辑上以独立解码关系证明合法二态输入 |

本件为自研规划合同，正式 Pad/PHY 接口冻结与签核留待 [IO_PLAN.md](IO_PLAN.md) 所列责任人与数据。版本标记 `v1.0`，内容散列与实际证据随交付清单。
