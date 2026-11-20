# DATA INTERFACE、CLK 与 Pad 规划

## 照片定位和目标走廊

IPBrain 原图顶边下方标有 `DATA INTERFACE`，左中部、贴近 ADC 左肩的位置标有 `Clock`，四边可见 Pad。原图中 `DATA INTERFACE` 估读范围是 `(180,94)–(811,129)` px，图像原点为左上。目标工程采用左下为原点的 1500 µm × 1500 µm 坐标。下表以 [plan.json](../floorplan/plan.json) 的硬边界放置功能区。照片可支持区位和邻接关系，Pad 槽编号、供电网归属、RDL/球映射和单元宽度尚待原生资料。[裸片原图的归属与摘要](../evidence/inputs.md)

| 对象 | 规划矩形 `(xmin,ymin,xmax,ymax)`，µm | 接口边界 |
| --- | --- | --- |
| Pad 环 | die `(0,0,1500,1500)` 与 core `(120,120,1380,1380)` 之间，四边各 120 µm | 预排 Pad/ESD/PG/封装逃线。实际装配看目标工艺合法槽位、供电 tap 间距和封装 |
| `DIO` | `(237,1330,1090,1372)` | 853 × 42 µm 软/硬接口窗口，DDR 边沿字、帧装载、DA/DB/DCO PHY 一侧。结果/模式交接留在此处 |
| `IO_EAST` | `(1090,1360,1380,1376)` | 与 DIO 右端相接的候选高速通道。进入东侧 Pad 之前查清 top metal、拐角和电源截面 |
| `CLK` | `(260,742,475,828)` | CNV 接收后的低抖动支路与转换时序入口，近 `Q` 和 RDAC。数据 CLK 的高速分发就近归 DIO 子树 |
| 数字交接 `CLK_RST` | x=145，y=945…982 | `CLK→IP` 转换事件、数字控制时钟、复位。细化每根网的相位、频率、源端、目标和起停条件 |
| 数字交接 `RESULT` | x=1095，y=925…978 | 结果字、帧状态和有效交付 `CTRL/DIO`。由数字/控制方冻结有效、反压、标签和 CDC |
| 数字交接 `CFG_TEST` | y=1100，x=520…675 | 配置提交、测试和状态快照。先使用外围控制或预留路径，具体验证由各域所有者负责 |

北侧 TX 短接北侧 Pad，东侧 CLK RX 沿 `IO_EAST` 到 DIO。将 `CLK±` 和 DCO/DA/DB 的高频共模回流留在北、东北一侧。`CNV±` 自西侧进入左中 `CLK` 区，再就近进入采样控制。将触发器之后进入数字域的逻辑事件从采样孔径支路分开。在 `CLK→CLK_RST` 和 `RESULT→DIO` 段保护安静路径。`RESULT` 从 x=1095 一带绕行 SRAM/OTP 边缘的可走通道由路由员核定，真实宏边界、电源栅格和路由需求可能改变占位。`RESIDUE`、`RA_ADC2`、`REF_FEED` 保持为模拟专用走廊。顶层不得共用这些走廊跑 DCO、SDO、SCLK 或扫描高速线。

## 包脚与裸片组序

48 个有效电气功能球及 G6 DNC 的参考映射在 [package_pins.csv](package_pins.csv) 中逐球展开。关键网络见下表。球号来自 AD4080 49 球 CSP_BGA 的 top view 图 2，括号标出单端结果 SPI 复用。[Rev. B 图 2 和表 6](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=10) · [复用表 22–23](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=51)

| 组 | 正端、负端或单端脚位 | 域与模式 |
| --- | --- | --- |
| 转换触发 | A4 CNV+，A5 CNV− | LVDS 时 IOVDD 输入。CMOS CNV+ 进 VDD11 域容限接收，CNV− 接 IOGND |
| 数据 CLK/DCS | B7 CLK+ (DCLK)，A7 CLK− (DCS) | LVDS CLK 输入对，或 IOVDD 单端结果钟和选通 |
| 回送 DCO | B5 DCO+，B4 DCO− | IOVDD LVDS 输出。self clock 或结果 SPI 时关闭 |
| 数据 DA | C7 DA+ (SDOA)，D7 DA− (SDOB) | IOVDD LVDS 主 lane。单 lane 结果 SPI 用 D7 |
| 数据 DB | C6 DB+ (SDOC)，D6 DB− (SDOD) | IOVDD LVDS 第二 lane，或四 lane 结果 SPI 两根单端输出 |
| 配置 SPI | G1 CS，E7 SCLK，F7 SDI，G7 GPIO0/SDO | 三个输入为 VDD11 域。默认 SDO 为 GPIO0，输出为 IOVDD |
| 状态与事件 | E6 GPIO1，F1 GPIO2，F2 GPIO3 | IOVDD 双向。`FIFO_FULL`、`FIFO_READ_DONE`、`ALERT`、`EXT_EVENT`、`FILTER_SYNC` 须通过 GPIO 功能选择 |
| 数字供电 | A6 IOVDD，B6 IOGND，A1–A3 VDD11，B3 VDDLDO | IOVDD/IOGND 承载 Pad 电流及 VDD11↔IOVDD 交接。独立供电模式见下一节 |
| 模拟、参考和热回路 | D1/E1 IN+/−，D2/E2 AUXIN+/−，G2 CMO，F4/G4 REFIN，F3/G3 VDD33 | GND 多球，REFGND 七球，详细编号查逐球表 |

下列裸片组序用于占位和路线分配。每组的方向是物理查看方向。具体槽编号、节距和供电 Pad 数留待宏和封装共同冻结。组内差分 P/N 毗邻，隔离 tap、夹具和电源槽放在对之间或组间。

| Pad 环与顺序视角 | 目标段 | 组内顺序草案 | 设计检查 |
| --- | --- | --- | --- |
| 北边西→东 | x=237…1090 | `IOGND/钳位` · `DA+ DA−` · `DCO+ DCO−` · `DB+ DB−` · `IOVDD/IOGND` | DCO 置于数据对之间，便于同参考面和两侧同类组逃线。DB 预留第二组 TX |
| 东边北→南 | y=1240…1380 | `IOGND` · `CLK+ CLK−` · `IOVDD/IOGND` | 差分 CLK 或结果 DCLK/DCS 复用。顶端在 `IO_EAST` 可达范围预留引出，转角间距需由宏验证 |
| 东边北→南 | y=750…1240 | `CFG_CS` · `CFG_SCLK` · `CFG_SDI` · `GPIO0/CFG_SDO` · `IOGND` · `GPIO1…3` | 低速配置和状态置于 GND 分隔的独立组，尽量缩短至控制/配置边界 |
| 西边北→南 | y=735…900 | `地/隔离` · `CNV+ CNV−` · `地/隔离` | CNV 进 `CLK` 左侧，保留转换触发抖动预算。物理 Pad 组下界高于 `Q` 邻界 |
| 西边北→南 | y=310…730 | `安静地` · `IN+ IN−` · `AUXIN+ AUXIN−` · `安静地` | 信号近采样，走线不跨 CNV 高速接收供电回路。模拟 owner 冻结 ESD 电容和校准注入 |
| 南边西→东 | x=145…1354 | `REFGND` · `REFIN` · `CMO/安静地` · `VDD33/GND` | 参考端回路单列，重复电源和地 Pad 的数量由电流密度、隔离和 RDL 决定 |
| 北西角及其邻段 | LDO1 邻近 | `VDDLDO/GND` · `VDD11/GND` · `IOVDD/IOGND` 供电接入候选 | 与北侧 TX 经 PG 网络连接。四角及长边的钳位、IO fill/corner、地回流和备用槽随工艺复核 |

计划保留单/双 lane、两种 CNV 连接和回送时钟所需的 Pad 位置。自研调试优先项为双 lane 200 MHz echo，前提是选到匹配的两组数据 TX、DCO TX、可靠端接及可逃线封装。40 MSPS 吞吐预算还需上游数据交付、帧位点、主机端建保持和板级链路共同成立。AD4080 商用品默认单 lane echo、CMOS CNV。目标芯片模式和兼容程度须在 pin freeze 时签署。[交流时序表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7) · [图 74–77](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=46) · [接口默认值表 48–49](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84)

## 工作电压、供电和隔离

| 对象 | 商用品范围 | 目标芯片审查 |
| --- | --- | --- |
| `VDD11` / `IOVDD` | 标称均 1.1 V。外供 VDD11 1.045–1.155 V，IOVDD 1.045–1.26 V。VDDLDO 外供时使用双 LDO，包脚说明给两个 LDO 应用 1.5–2.7 V 的范围 | 用实际工艺电源模型选择目标额定值。标称相同但工作区间和关断状态不同，确认 level shifter、隔离、上电时序与防反灌。AD4080 的 LDO 模式与外供 1.1 V 模式须互斥 |
| CMOS CNV、CS、SCLK、SDI | VDD11 域，表 1 输入工作容限 0–2.5 V，VIH 下限 0.92 V。Table 3 的绝对最大 +2.75 V 约束输入应力 | 使用带目标供电、输入容限和掉电条件的 Pad 库。2.5 V 主机对新芯片允许的等级、ESD 钳位和 Pin-High/VDD-Off 要实测。3.3 V 主机要经过外部或适当工艺容限转换 |
| GPIO、DCS、DCLK | IOVDD 域，表 1 输入工作容限 0–1.26 V，VIH 下限 0.92 V | DCLK/DCS 的 SPI 模式逻辑电平由表 1 限定。A7/B7 同球在 LVDS 输入模式的绝对最大值不能用作 SPI 工作值。配置 SDO 也是 IOVDD 电平 |
| CLK/CNV 差分输入 | 表 1 默认 `VICM=0.7–1.4 V`、`VIDIFF=0.1–0.6 V`。CLK 对及差分 CNV 对按包脚说明跨接 100 Ω | 目标 RX 模型确认全 PVT 灵敏度、CMRR、抖动、占空比及共模范围。若片内终端可选，避免与外接 100 Ω 重复端接 |
| DCO、DA、DB 差分输出 | IOVDD 驱动，表 1 的直流/摆幅条件使用 100 Ω 负载。`LVDS_VOD` 选择三个档位 | 依据拟采用的 TX 和负载取得实测 VOD/VOCM、过冲和输出边沿，启动/复位/关电检查安全 OE、电气高阻或钳位 |
| VDD33、REFIN、REFGND | 商用器件的参考输入工作点常用 3.0 V，VDD33 为 3.135–3.465 V。手册 PCB 共享完整地平面，并令参考去耦回流紧贴参考回路 | 芯片内部参考电流环、模拟输入、DCO/serializer 回流在物理上分区，骨干电源/地和 substrate tie 遵循目标 PDK。Chip、package、PCB 各自验回流，不用一条割裂的地平面跨差分走线 |

第 51 页的 VOD 名义档位采用其各档近似文字，电气验收优先按表 1 明确定义的测量和范围，二者的数字口径需在宏手册确认。Rev. B 表 5 的 HBM/FICDM 是商用品器件额定值。目标 Pad cell 的钳位拓扑、导通、供电隔离、闩锁、ESD 测试等级和低输入电容均需工艺校验。手册的封装内去耦只适用于其商用封装；新封装/PCB 的去耦和共阻抗需独立预算。[表 1，供电和 I/O 电平](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=5) · [表 3、5–6，额定值、ESD 与球位](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=9) · [表 50，LVDS 档位](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=85) · [PCB 参考和地平面，第 72 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=72)

## 时钟面、数据交接与物理验证

| 域或交接 | 装配要求 | 验收数据 |
| --- | --- | --- |
| `CNV_pad → CLK → Q` | 采样触发的低抖动路径，先经过合规 RX，再进定相缓冲。孔径时刻直接受该支路影响。内部转换/粗细时序及有效采样点由 ADC owner 给出 | CNV pad-to-aperture 路径、PVT 变动、周期/积分抖动、同采样周期过渡余量，并回填模拟 SNR 与转换阈值预算 |
| `CLK_pad → DIO RX/serializer` | CLK± 差分 RX 与 DDR 时钟树。Echo 的 DCO 由同源专用缓冲/转发宏驱动。结果 SPI 使用同脚另一套 `DCLK/DCS` 单端逻辑 | 时钟输入波形、正负沿偏斜、周期高低脉宽、DCO 相移、两沿 STA I/O delay、输入输出宏 arc、PHY setup/hold |
| `DCO/DA/DB → 主机` | P/N 同组相邻、相同层和相同参考面，按封装+板基线匹配各 lane 到 DCO。留终端与可测点。Host self-clock 校准独立确定取样窗 | `tDCO/tDCOD/tCLKD` 对应的目标时序测试。记录 DCO/data 相位直方图、BER、接收端余量、均衡/端接和 PVT，兼看首位帧对齐 |
| ADC 结果→高速装载 | 连续模式由系统对 CNV 与数据 CLK 建立定相或明确 CDC。若相位关系缺乏硬保证，需完整字的握手或异步 FIFO，跨域同时传出样本身份和有效 | 固定 CNV/CLK 相位约束及多周期证据，或带重复/丢样/乱序检查的 CDC 验证。读取套用一一对应的 frame ID，正常连续流考核满速、背压和停钟恢复 |
| 结果存储→DIO | FIFO burst 的写域 CNV 和读域 CLK/DCLK 可独立。单端口写/读互斥的所有权要握手转移。IO 域用 framer 接现有单 LVDS pair 或 20 位 SPI 样本，完成同步头、计数以及已绑定 CRC 尾 | 指针、帧头、第一结果及最后样本与 `FIFO_FULL/READ_DONE` 的同一事务关系。复位时约束存储访问、transport epoch 和外部 CRC 会话的作废顺序。[FIFO 记录合同](FIFO_RECORD.md) |
| `SCLK → CTRL → 各运行域` | CS 成帧，配置字经 shadow+commit 原子切换。稳定的单比特状态可做同步器，窄脉冲用握手/toggle，宽字依随握手或快照同步 | 异步时钟组清单、数据保持协议、复位顺序、flush、丢钟及并发提交的 CDC/RDC 案例。SPI 写成功回执与各域配置完成用不同状态 |
| 环境与供电复位 | Pad 输出使能在 POR/reset/brownout 下钳住。使用跨域共同的隔离策略或异步置位、各域同步释放方案。停钟域保持复位/隔离直到时钟和电源有效 | Pad safe-state 真值表、复位/关电/再启路径仿真、power intent、供电切换和 RX/TX 互斥波形 |

连续 LVDS 需在封装脚量 `tCCA`。约束中对实际 RX 与转发时钟使用正确波形、正负沿、时钟不确定度与最小/最大外部延迟。签核前将 tRX、时钟分配、宏、Pad、封装、板和主机 RX 的抖动偏斜预算贯通。高频走线与 `REFIN`、`CMO`、采样输入、RDAC、余差的并行耦合由 post-route 提取回注。满速并行 IO 最差开关向量用于定位供电纹波、基准扰动、采样时钟调制和 FFT 伪影。未确定金属层、PDN、工艺角及寄生模型时，几何走廊只给出布线优先级。[交流时序表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7) · [图 74–80](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=46) · [布局指引，第 72 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=72)

## DFT、实测和 Pin Freeze

1. 实验室用配置 SPI 读器件标识和回读配置，独立连接结果 SPI 与 LVDS。先验上电/关电各脚安全态、DCS 和 DIO 模式切换争用、复位下 DCO/DA/DB OE。用示波器和差分端接测 CLK、DCO 与 lane 的极性、眼图、相位，依次验证 1 lane 和 2 lane 的 `0xAC5D6` 固定字。DB 空闲逻辑向量和物理 OE 都记录。
2. 清除测试模式后输入已知转换数据序列，核对首个 D19、最后 D0、跨帧不重叠、转换与输出帧身份。沿主机取样相位扫错率和稳定窗，比较 echo 与 self。温压角、RDL/封装、板终端、输入跃变、模式切换、停钟恢复都应有对应采样记录。
3. 结果 SPI/FIFO 台架用小 watermark 和异步读时钟，记录实际头、符号扩展、尾 CRC 覆盖及 `FIFO_READ_DONE`。Pad 数字扫描、SRAM MBIST、OTP 编程访问、测试时钟旁路和 ATE 归 DFT/宏 owner。预留测试访问路径，现有 GPIO0 保证配置 SDO 的可启动访问。本工作包的固定字是端口联通性工具，结构覆盖率需专用 ATPG/MBIST 测定。[Rev. B 图 86–91](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=58) · [固定检查字，表 48](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84)

| 冻结项 | 责任人和所需证据 | 本阶段 |
| --- | --- | --- |
| PHY 和 IO 模式集合 | 系统/FPGA、模拟接口设计。确认单/双 lane、echo/self、CNV 类型、结果 SPI 是否复用，运行和测试的 mode/OE 互斥 | 布局占位已定义，模式定案待签 |
| 实际 Pad 槽与 RDL | Pad/封装与版图。签 Pad 顺序、方向、间距、ball/RDL 对应、各域 ESD/corner/PG 填充、探针和测试可访问性。交接带坐标的 Pad/Bump map、封装模型、pin permutation 和未连接表 | 商用包脚已核对，目标实际槽和封装待签 |
| 滑移与差分匹配 | PHY/STA/SI/PI。目标 PDK、接收/发射宏 LEF、Liberty/时钟弧、SPICE、IBIS 或 S 参数，填入端接、打线寄生、抖动、峰值电流，再闭合两沿 timing/BER、回流与耦合 | 几何走廊已规划，真实宏、PDK、封装模型和签核数据待补 |
| 域/复位/供电 | ADC、数字/DFT、物理。冻结内部 `CLK_RST/RESULT/CFG_TEST` 角色、时钟 ID、复位、电源/隔离、PG 与 CTS 输入和两沿约束 | 交接组与职责已记录，CDC/RDC/STA 待集成 |
| DFT 和引脚移交 | DFT、封装、测试工程。测试 strap/pad mux、扫描约束、时钟、ATE 模型和故障向量验收，共签引脚冻结与变更记录 | INTF_CHK 的组合位序已做逻辑验收，DFT/ATE 待设计 |

## 可用工具和证据范围

本地实跑证据由 Verilator 和 YoWASP Yosys 产生，见 [验证说明](README.md) 与 [证据](evidence)。这组工具能验证边沿字、确定性行为和通用逻辑综合。正式 PHY 需要目标工艺和宏团队提供双沿时序弧、Pad/ESD 及掉电模型、真实可放置物理视图、参考/供电耦合约束、封装 RDL/IBIS/S 参数、板端负载和主机接收限制。商业 EDA、目标库映射、全路径 STA/DRC/LVS、全吞吐、ESD、EM/IR、PEX 与板级测量在此工作包均为 `NOT_RUN`。
