# 输入资料与证据范围

| 输入 | 本次使用的证据 | 可复核身份 |
| --- | --- | --- |
| AD4080 Rev. B，95 页 | 图 1 的功能边界，表 1/2/6 的电源和引脚，图 38–44 的双 LDO、POR 与低功耗，图 74–95 的输出和 FIFO，公开寄存器 | 当次阅读 PDF 的 SHA-256 为 `f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1`。[ADI 手册](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) |
| 用户已有的 IPBrain 裸片彩色标注图 | 原朝向、相对邻接及四个像素框；独立分辨照片标记、手册功能和项目分配 | 原图 1126×1125 px，SHA-256 `689cd44fc8afd8f23ced86f6f1beaad5b8e5687fdea67176ee2866a6bdfcc25c`。[原图读数与规划说明](../floorplan/README.md) |
| ISSCC 2024 论文 9.8 与讲稿第 17、19–22、29–32、40–41 页 | 论文原型 40 nm、2.25 mm²；讲稿区分 0.77 mm² 核心和含参考缓冲 0.90 mm²。第 17、29–31 页给出扰动与 DEM 机制，第 40 页两根尺寸箭头用于独立换算 Digital 整框约 0.060 mm²。作者没有把这个框细分到 DEM 或 dither 单项。 | [论文 DOI](https://doi.org/10.1109/ISSCC49657.2024.10454329) · [讲稿文件](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/references/ISSCC2024_SLIDES.pdf) |
| 原项目《AD4080 项目可行性报告》“版图与封装工程”及原分区汇报 | 沿用 1.5×1.5 mm、40 nm MPW 的设计起点。64 Pad 原型是待封装方确认的算例；商品 49 球 BGA 采用手册球图。 | 论文和已有项目资料只用于设置规划假设。可提供实际 die 边长、封装/IO LEF 后重新定标。 |
| ADI 数字接口的公开参考固件 | 参考固件的单线读长为 1 + 3N 字节，四线为 4 + 3N 字节。它在这个位置结束读取，不能据此推断继续送钟的输出或 LVDS 的 CRC 算法。 | [ADI 评估固件固定提交](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L1227) · [逐图裁定](../io/evidence/record-source-check.md) |
| ADCToolbox、已有 DSP 与配置寄存器 | 固定版本的正弦最小二乘估幅和直流，结合两个已标定的参考量，再由已有 DSP 的整数链验证。产品寄存器码另用实测映射校核。 | [固定源码与取舍](../calibration/SOURCES.md) · [Rev. B 第 71 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=71) |
| ADI no-OS 与 HDL | 未改的官方 C 驱动运行在我们的配置 SPI 上。固定 HDL 的接收、拼字和符号扩展接入本项目实时发送逻辑。采用理想采样原语。 | [配置实跑及版本](../interfaces/control_review/README.md) · [收端版本与实跑](../io/adi_receiver/README.md) |
| 功能 IP 只读 Git 快照 | 从顶层声明核对模数边界和启动要求；旧 28HPC+ 面积仅作不同基线的历史对照。当前 IP 无同版目标工艺物理面积回执。 | `7408edf8db9a41eebdd756378b69d2775f107dec`。[顶层源码](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv) · [面积证据归属说明](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/audit/ALL_13_MODULES_FINAL.md) |

商业裸片的 SRAM/OTP 宏 LEF、bitcell 密度、OTP 镜像、标准单元库和网表未取得。面积门是给本项目团队的预算。可交付的实测是图像框选、工程几何回读和本包外围参考 RTL 测试。
