# AD4080 对标芯片的转换 IP 与产品外围

**功能组交一笔已经完成去扰和权重重构的 20 位转换码。后端和外围组接住这笔码，负责系统零点/增益、可选滤波、触发记录、配置和对外读出，以及这些模块的存储、供电和测试。** 两边在生产顶层的 `out_valid/out_code[19:0]/out_tag[31:0]` 及 `out_sat/out_err` 交接。功能组在同一项目的生产 RTL 目录维护转换核，这里登记交界、面积与尚需完成的产品集成。[原生产顶层](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/rtl/v21/adc_digital_ip_v21.sv) · [逐功能责任和证据表](interfaces/function_ownership.csv) · [本轮完整审查、修复与复测](evidence/acceptance.md)

[可直接查看的整芯片布局](figures/floorplan.png) · [后端交付报告排版源](report/handoff.tex) · [分项面积账](floorplan/area_by_scope.csv) · [可打开的 GDS](floorplan/chip.gds) · [DEF](floorplan/chip.def) · [抽象 LEF](floorplan/planning.lef)

**从信号和实测结果查手册。** [逐模块对照表](evidence/MANUAL_TRACE.md)列出每条信号的手册页、实现、观察点和判据。[引脚时序图](figures/signal-timing.svg)来自产品实际逐沿输出；[逐拍数据](integration/product/evidence/pin_edges.csv)可独立复核。系统校正先加 OFFSET、再乘 GAIN；图 96 的定向输入在现用 RTL 实测为 1032。[校准回执](calibration/rtl_verification.json) · [可打印审查 PDF](report/manual-trace.pdf)

## 工作树入口

从仓库根目录进入 `subsys/ad4080_peripherals/`。相邻目录 `../ad4080_dsp/` 提供[已有后处理源码](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/tree/7408edf/subsys/ad4080_dsp/rtl)。DSP 的主要任务是把收到的 20 位码做用户校正和可选滤波，例如 40 MS/s 选择 SINC1 四倍抽取后得到 10 MS/s。它的参考顶层还提供事件和捕获控制。外围包只存自己的装配、存储和 I/O 边界，冻结 DSP 源由 Git 对象导入独立临时目录。`rtl/v21/` 和 ADC 的生产 filelist 继续由转换组维护。

- [系统 OFFSET/GAIN 校准](calibration/README.md)提供可运行的主机程序。可接两个已标定的直流电平，也可用 ADCToolbox 拟合已知正弦。原 DSP 执行逐样本校正；[产品入口](integration/product/README.md)用配置 SPI 写系数，并从同一份结果 SRAM 读回校正码。
- `integration/`、`transport/`、`io/` 收拢外围装配、跨时钟搬运和读出协议。`memory/`、`rtl/`、`otp/`、`power/` 是存储和电源服务。`interfaces/` 记录所有权，并含复用现有 DSP 的真实测试接口。
- `verify/` 是本子系统的回归入口，`floorplan/`、`budget/`、`figures/` 是版图及面积入口。桌面交付包另含可直接阅读的 `report/handoff.pdf` 和带原图对照的 `figures/floorplan.pdf`。原图身份见[资料说明](evidence/inputs.md)。

## 数字滤波器如何配置

`0x29` 是实际的八位滤波配置寄存器。上电复位为 `0x00`，输出直通。位 [1:0] 选择路径，位 [6:3] 填基础抽取率 N 的编码，N = 2^(编码 + 1)。三条滤波路径均由配置 SPI 选通。现有实现中普通五阶和补偿五阶共享同一个 CIC。补偿 FIR 的定点系数由公开带宽和频响数据设计。[寄存器表 62](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=94) · [带宽表 28](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=64) · [数字后处理 RTL](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/353f745b872a66aaac29fa0e858a13c30b5e8f07/subsys/ad4080_dsp/rtl/ad4080_dsp_chain.sv)

| 输入 40 MS/s，基础 N=4 | 写入 0x29 | 输出速率 | 手册 −3 dB 带宽 |
| --- | --- | ---: | ---: |
| SINC1 平均 | `0x09` | 10 MS/s | 4.56 MHz |
| SINC5 | `0x0A` | 10 MS/s | 2.10 MHz |
| SINC5 加补偿，再二抽一 | `0x0B` | 5 MS/s | 2.024 MHz |

在默认长指令、关闭配置 CRC 的工程环境中，发送 `00 29 0B` 即写入最后一行。直接 LVDS 模式可以由写 0x29 或 GPIO FILTER_SYNC 复位滤波器；使用 FIFO 时，每条记录都要用低有效的 GPIO FILTER_SYNC 对齐首个 CNV。首个有效输出还需等该档建立，等待从同步沿起算。详细测量按原输入采样频率归一，区分数据手册表值、读图误差与真实 RTL。[同步图与模式说明](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=67) · [厂商目标数据](interfaces/dsp_review/manual_filter_targets.csv)

## ISSCC 原型给功能组的面积参照

作者在讲稿第 17 页画出低段和高段扰动、SAR 判决、DEM 和 RDAC 的连线。第 29–31 页展开 18 片取 8 片、水平和垂直选元以及桥接交换。论文第 182 页说明 DAC 权重的系数在片外求得，校正在片上执行。我们把片组调度、DEM 选择、dither 生成和数字扣回、权重装载与逐笔重构全部交给已有转换 IP。其控制的电容、开关、桥接与电荷注入发生在模拟宏。两组分别交数字状态和实际开关负载。[作者讲稿第 17、29–31 页](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/references/ISSCC2024_SLIDES.pdf) · [ISSCC 论文](https://doi.org/10.1109/ISSCC49657.2024.10454329)

| 数字 | 范围与口径 |
| --- | --- |
| **约 0.060 mm²** | 讲稿第 40 页在裸片上单独框出了 `Digital`，论文图 9.8.7 可交叉辨认。用横、纵两根原标尺分别换算，框约 124 × 487 µm。作者标注的范围止于这个整体框。DEM、dither、调度或重构各占多少，公开原件没有分别给出。 |
| **0.77 / 0.90 / 2.25 mm²** | 0.77 是讲稿的转换核心，0.90 包含参考缓冲，2.25 是论文原型整片。讲稿第 41 页按核心和系统列功耗，不能用功耗表来分摊版图面积。 |
| **0.250 / 0.120 / 0.135 mm²** | 依次为本项目给功能组的毛分区、综合标准单元目标、CTS/ECO 后单元上限。0.250 中先扣 0.025 禁放，按 0.225 净行的 60% 得 0.135。0.250 约是论文红框图估毛面积的 4.1 倍，工程实现范围、网表与工艺库都需单独核对。现版实际占地待功能组提交同版报告。 |

[原图量测方法与面积门](budget/README.md) · [量测回执](evidence/isscc_digital_area.json) · [原生产清单](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/flow/build/filelists/files_adc_v21.f)

## 外围怎么分工

建议总后端负责合并和验收，按现有六人计划协调。下表的“外围 RTL”可由总后端兼任。“数字接口与验证”是原计划岗位，到岗前由总后端维护台架入口、片外校准岗交对照数据。原片上功能岗提供带 tag、valid 和 flags 的 20 位结果及边界时序。余差放大器岗提供保持窗口，片上基准岗提供参考有效与恢复条件。

| 工作 | 主责与协作 | 拿到什么，交出什么 | 本轮进展与签收条件 |
| --- | --- | --- | --- |
| 上电、OTP 与寄存器配置 | 总后端／外围 RTL。模拟总责和基准岗给电源有效条件，接口岗给 SPI 命令 | 将电源状态、复位和配置写入变成“允许转换／允许读记录”。OTP 全表校验后一次发布。 | 已核实正常与低功耗、软件复位、参考掉压和 W1C 的逻辑行为。数字权限可在仿真中验收。LDO、非易失宏的实际镜像及 PVT 恢复由模拟和宏方签收。[电源](power/README.md) · [控制面复测](interfaces/control_review/README.md) |
| 用户校正与滤波 | 外围 RTL 对接现有 DSP，片外校准岗提供独立预期码 | 输入为已合码的样本。统一 offset-binary／补码边界，用用户 OFFSET/GAIN 修通道误差，选择直通或 SINC 抽取。 | 先跑真实配置写入和固定样本。分别验码值、饱和、配置切换、首个可用样本。补偿系数和所有倍率的器件时序需实物绑定。[数据面复测](interfaces/dsp_review/README.md) |
| 事件、FIFO 与 SRAM 测试 | 总后端兼存储/DFT，对接方用同一个记录控制器 | 阈值或 GPIO 触发后保存所选结果，冻结记录，读回结束再布防。MBIST 须先排空功能读写、取得授权，再独占存储。 | 检查事件前后顺序、早事件、水位、暂停、未完记录时的模式切换和重建。物理编译宏仍由存储供应方交付。[存储与 MBIST](memory/README.md) · [三域装配修正](integration/REVIEW.md) |
| 配置 SPI、结果读出与 FPGA | 数字接口与验证岗负责数字帧、独立接收和联调。混合信号总责负责芯片 PHY | 配置 SPI 写寄存器；结果 SPI 或 LVDS 读数据。主机把配置、复位时刻、记录长度、捕获的原始位流一起留档。 | 已打通 SPI1/4 的固件读取前缀和单 LVDS 的工程绑定整帧。商用 CRC24 参数、真实 DCLK/DCO 和 CNV 首发相位，要拿评估板或原厂资料核对。[结果帧合同](io/FIFO_RECORD.md) · [整记录回执](integration/evidence/readback_link.txt) |
| 面积与整芯片交付 | 总后端汇总。功能组和宏供应方分别交网表与抽象 | 按真实功能配置用一份最终网表计面积，结果记录只绑定一份 16K×20 存储。 | 已核几何与物理门。最后用真实宏 LEF/Liberty 验两处窄口、时钟、供电及跨域，确认完整 DSP 能装入 CTRL。[物理与面积台账](floorplan/area_by_scope.csv) |

## 一条记录如何运作

1. **上电与设置。** 等核心电源、参考和所需 I/O 域合格，装载并校验本批 OTP/配置。接口岗经配置 SPI 写校正、滤波、阈值和水位，读回关键值，再发同步与布防。配置期间有尚未完成的记录时先等它结束或显式清除。
2. **采样与记录。** 功能组每交一笔码，外围按该笔状态完成码制转换和用户校正。滤波时，事件比较看被选中的滤波结果。验收时把首个合格结果与 CNV、样本序号一起对齐。触发后由同一记录控制器写 SRAM。
3. **读回与放行。** 手册图 99 给出 SINC1 两倍抽取、水位 3 的例子。产品用实际配置 SPI 布防，SRAM 在第 6、8、10 个 CNV 沿写入 200、600、1000；第 10 沿 FULL 置位。从同一四银行读回的 LVDS 工程 CRC 记录连续占 46 对，首末样本装载和末 CRC 位分别核对。[实际产品记录](integration/product/README.md)
4. **异常恢复。** 掉电、记录被中止或 MBIST 接管时先封住新的读写和对外发布。区分要保留的记录与已经失效的记录，等宏重新就绪、记录元数据重建后再开下一笔。接口岗核查复电后的首个完整字，校准岗独立比对码序。

**推进顺序。** 先用同一条三字记录调通数据面与控制面。随后跑满深度、跨时钟、掉电、复位、自测的反例。已打通原 DSP 到唯一四银行结果存储的整记录回路，校准也通过了 SPI 写入和读回。下一步把目标工艺的宏与芯片接口接入同版网表，复验面积、首次出码和实物帧。先期台架保留原码，FPGA 做接收/缓存，主机保留离线滤波参考，便于定位差异。[完整外围审查与复现](evidence/acceptance.md)

## 外围具体预留多少

| 工程窗口 | 毛面积 |
| --- | ---: |
| CTRL，结果处理、寄存器、触发与管理逻辑共用 | 0.026400 mm² |
| DIO，高速数据与接口边界 | 0.035826 mm² |
| FIFO，结果存储宏及其窗口 | 0.121000 mm² |
| **以上三项小计** | **0.183226 mm²** |
| OTP 预留 0.097290，加用途待定的小 SRAM 0.005850 | 0.103140 mm² |
| **外围五窗小计** | **0.286366 mm²** |

以上按单一归属计入工程 floorplan，转换功能 IP 的 0.250 mm² 单列。共享时钟、LDO、电容、参考、模拟转换区、Pad 和公共通道还有自己的面积行。GDS 的 **10/0 层为功能组转换 IP，11/0 层为产品处理与控制 CTRL**，可在 KLayout 中分别开关。[物理分区归属](floorplan/block_binding.csv) · [完整面积账](floorplan/area_by_scope.csv) · [布局和供电通道](floorplan/README.md)

面积上应首先复核 **CTRL 的 0.026400 mm²**。现在 SINC5 与补偿模式共用一条五阶 CIC；PF1 采用 35 个对称抽头，18 个独立乘项。该数字是功能结构。若暂按 60% 利用率试算，26,400 µm² 毛窗给出 15,840 µm² 单元预算，实际还要扣行禁放。在取得相同功能配置的工艺库映射与布局报告之前，这个预算不能作为已装入的面积回执。[分区预算](floorplan/area_by_scope.csv) · [实际产品结构](integration/product/README.md)

## 交接与复核

- **功能组交**同版网表、已映射面积、内部硬宏及 halo、引脚分组、时钟及例外路径。模拟组交判决、开关、保持和 ADC2 返回窗口。P2/P6 保留 522 根 RDAC、31 根双 SADC 接点。余差电压沿 RDAC → RA → ADC2 在模拟区内传递。[逐端口核对](interfaces/README.md)
- **外围团队交**处理与控制的目标配置及网表、SRAM/OTP/PHY 的真实 LEF 与 Liberty、从配置写入到一整帧对外数据的系统用例。当前单路 400 MHz 与双路 200 MHz 均有 40 MS/s 稳态 RTL 回归。空弹性队列需约 3–4 个 I/O 周期起发，最短 CNV 至首位延迟须另验。[吞吐与首位预算](transport/README.md) · [各工作包的实测](evidence/acceptance.md)
- 有同一提交的源码检出时，新工作树内直接运行 `python3 -B verify/check_ownership.py` 复核三个系统的引用边界。如需复测原文，可将 `ADC_FUNCTION_REPO` 设为含两份原件的仓库路径，再运行 `python3 evidence/isscc_digital_area.py "$ADC_FUNCTION_REPO/references/ISSCC2024_SLIDES.pdf" "$ADC_FUNCTION_REPO/references/ISSCC2024_PAPER.pdf" --output /tmp/isscc-digital-area.json`（需 PyMuPDF）。[静态实例检查回执](evidence/rtl_ownership.json)
- 从解压根运行 `python3 -B floorplan/build.py` 可再生分区面积账、GDS 与 DEF。公开源码用 `python3 -B verify/check_delivery.py` 核对完整性；桌面包可加 `--all-assets` 同时核对三份本地附件。[独立几何回读](evidence/floorplan-check.json) · [资料与提交身份](evidence/inputs.md)
