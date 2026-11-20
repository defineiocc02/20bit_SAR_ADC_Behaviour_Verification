# AD4080 FIFO CRC24 证据复查

核验日期为 2026-11-20。本次针对 AD4080 FIFO **端口原始位流**。已核资料支持 `0xAA`、样本排线与图示读长。公开资料和本机相关文件中尚未找到足以复算的商业芯片尾位及配套消息，或可独立确定 CRC24 的完整参数。LVDS 保留外部提供尾位的绑定边界，SPI 保留图示读长和追加线位观测。[项目原文逐图裁定](../evidence/record-source-check.md)

## 原厂源码和指南

| 核验对象 | 固定版本 | 观察到的数据路径及证据边界 |
|---|---|---|
| AD4080 数据手册 | Rev. B，95 页，本机原件 SHA-256 `f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1` | p51 描述独立的结果 CRC24。图 87/88 给 SPI `8+24N` 或 `8+6N` DCLK，图 89 给 LVDS 的 `0xAA`、`20N` 和单个 24 位尾。图示定位字段，未披露生成多项式、init、xorout、反射、覆盖细节及尾字段数值。p41 的 CRC-8 单独属于配置口。[数据手册 p51](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=51) · [图 87–89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=59) |
| ADI Linux | `3556cbcd5b00cc28f4946da5496cb78ff68578b2` | `drivers/iio/adc/ad4080.c` 通过 SPI regmap 配置，使用 IIO backend 获取采样缓冲；AD4080 通道 `realbits=20`、`storagebits=32`。驱动以固定码对齐后端。可见路径没有保留可读取的 FIFO 头/CRC24 线位。[驱动接口与缓冲](https://github.com/analogdevicesinc/linux/blob/3556cbcd5b00cc28f4946da5496cb78ff68578b2/drivers/iio/adc/ad4080.c#L858-L905) · [样本通道格式](https://github.com/analogdevicesinc/linux/blob/3556cbcd5b00cc28f4946da5496cb78ff68578b2/drivers/iio/adc/ad4080.c#L527-L536) |
| ADI FPGA HDL | `f518dd124fc005c6f9cd80b7947b10a638c37b37` | `axi_ad408x.v` 实例化 `ad408x_phy.v`，后者经输入解串、`ad_pack` 生成单/双 lane 采样字，固定 `0xAC5D6` 用于对齐。检查了输入、打包、同步和 `adc_valid` 出口。该路径公开的是采样字与有效标记，未见 FIFO CRC24 校验路径或商业 FIFO 金样本。[顶层连接](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/library/axi_ad408x/axi_ad408x.v#L274-L310) · [PHY 解串和打包](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/library/axi_ad408x/ad408x_phy.v#L329-L419) |
| ADI no-OS | `d6aff3cd77c31f8e09de9dc22720738d73fcfda1` | `ad4080.c` 的 FIFO 读交给 SPI 并传入调用者的长度。`iio_ad4080.c` 在单线模式按 `1+3N` 字节读 AD4080，首字节跳过 `0xAA`，样本按 BE24 还原。代码保留的传输边界为完整样本末尾。[传输入口](https://github.com/analogdevicesinc/no-OS/blob/d6aff3cd77c31f8e09de9dc22720738d73fcfda1/drivers/adc/ad4080/ad4080.c#L130-L140) · [读取和解包](https://github.com/analogdevicesinc/no-OS/blob/d6aff3cd77c31f8e09de9dc22720738d73fcfda1/drivers/adc/ad4080/iio_ad4080.c#L130-L175) · [分配和读长](https://github.com/analogdevicesinc/no-OS/blob/d6aff3cd77c31f8e09de9dc22720738d73fcfda1/drivers/adc/ad4080/iio_ad4080.c#L1432-L1462) |
| ADI Precision Converters Firmware | `6835d5c30d25d37499de5f210256828193d5c78e` | `projects/ad4080_iio/app/ad4080_iio.c` 支持单线 `3N+1` 字节和四线 `3N+4` 字节，按 1/4 字节跳头并逐字解 BE24。行 1227–1287 启动传输，1418–1466 转换输出。该参考程序未读入额外 CRC 字节，SPI 超过已发时钟的物理输出仍为待测项。[固定版原始文件](https://raw.githubusercontent.com/analogdevicesinc/precision-converters-firmware/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c) |
| UG-2214、UG-2305、UG-2361 | Rev. C、Rev. A、Rev. A | FMCZ 指南使用 LVDS 及 Kuiper Linux/LIBIIO 的 ACE 数据集。ARDZ 指南使用 CMOS SPI FIFO，再经参考固件把结果交给 ACE。ADMX6001 指南的 CSV 是 IIO `voltage0` 样本和固定 `0xAC5D6` 的对齐检查，所示时间轴是样本序号。未见保留完整 FIFO 头、结果与 CRC 尾的线记录。[UG-2214 p13](https://www.analog.com/media/en/technical-documentation/user-guides/eval-ad4080-ug-2214.pdf#page=13) · [UG-2305 p12](https://www.analog.com/media/en/technical-documentation/user-guides/eval-ad4080ardz-ug-2305.pdf#page=12) · [UG-2361 p14–15](https://www.analog.com/media/en/technical-documentation/user-guides/eval-admx6001.pdf#page=14) |
| EngineerZone 公开答复 | 2026-03-13、2026-11-09 | ADI 员工确认 Data SPI Mode 3 及首 DCLK 发首位。有用户称其 AD4080ARDZ 单线实测检查了 CRC，公开帖未给原始尾或可复算算法。针对 ADC 状态的答复将 CRC 列为读回完整性线索，未定义计算参数。以上均未形成可绑定的 CRC 数据集。[ADI 已验证回复](https://ez.analog.com/data_converters/precision_adcs/f/q-a/603310/fifo-full-signal/597558) · [台架运行声明](https://ez.analog.com/data_converters/precision_adcs/f/q-a/606806/ad4080---intermittent-adc_cnv_err-at-startup-and-cnv-oscillator-disable/606699) · [ADI 对 CRC 用途的回复](https://ez.analog.com/data_converters/precision_adcs/f/q-a/606806/ad4080---intermittent-adc_cnv_err-at-startup-and-cnv-oscillator-disable/606688) |

`0xAC5D6` 是接口对齐用的 20 位测试字。它的数值不包含 FIFO 的 `0xAA` 字段或 CRC24，这三者分别由不同证据支持。[Rev. B p50、p84](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84)

## 本机资料范围

只读盘点下述 AD4080 关联目录的常见采集格式、波形/抓包文件名以及文档中的 FIFO CRC、逻辑分析仪、原始读回等标记。进一步读取了表头和文件字段，用于辨识板卡档案的性质。

- `项目库产物文件夹/ad4080-test-board` 及 `PCB_design_resources_20261013/04_history/outputs/ad4080-test-board`。`fpga-interface.csv` 是连接器引脚/网络表，`reference-layout.json`、`conversion-report.json` 是板级转换和布局，参考目录中的 CSV 为 BOM/贴装。
- `项目库/AD4080_架构分析与研究路线_20261118`、`项目库/10_项目区/2026_20bit10Msar` 及 `Desktop/v21_数字IP_算法与状态图_20261118`。与 AD4080 相关的可见资料为架构说明、规划、论文/讲稿与算法图。
- `项目库产物文件夹/custom-adc-test-board-20261006-a5/review/eval-board-sources/ad4080ardz`。可见材料为官方设计文件、原理图及板级加工/装配资料。对参考 ZIP 只读列目录，其中符合抓包扩展名筛选的 CSV 为 BOM 和短路/加工报告。

在上述范围内未得到含 `DCS/DCLK/SDO` 或 `DA/DCO` 的实物 FIFO 完整记录，也没有可复算的 ADI CRC24 金样本。台架和原功能 IP 保持只读任务边界。完整交接记录需要原始线位、捕获条件和可核对来源。首轮采集工况与歧义边界见 [离线复核说明](README.md#最少首轮采集工况)。
