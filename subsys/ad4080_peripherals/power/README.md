# AD4080 裸片电源、休眠和复位

本工程将常供电数字逻辑、配置保留态、FIFO 阵列与有效态归入 **VDD11** 电源域，将数据 PHY、GPIO 与配置读回归入独立 **IOVDD** 电源域。模拟运行依赖 **VDD33** 和 **REFIN**，低压 ADC 核心也使用 VDD11。左上 LDO1 暂分配给 VDD11，左下 LDO2 暂分配给 IOVDD。分配依据是封装器件两条轨的负载量级和本工程窗口尺寸，两处裸片结构的实际网络身份待连接证据确认。

供电图和 RTL 使用同一组域及接点。 [电源架构图](power_architecture.svg) · [外围管理 RTL](ad4080_pwr_supervisor.sv)

下文页号采用数据手册印刷页号。 [ADI AD4080 数据手册 Rev. B](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=1)

## 1. 供电及引脚

| 电源或信号 | 封装接点 | 封装正常工作条件 | 裸片电源分配与接口 |
| --- | --- | --- | --- |
| VDDLDO | B3 | 双内供工程使用 1.5–2.7 V，首轮输入以 1.8 V 作预算 | 共喂两路 LDO 的输入。全外供时 B3 开路，双 LDO 自动关闭 |
| VDD11 | A1/A2/A3 | 1.1 V 标称，外供 1.045–1.155 V | ADC 低压核心、常供电控制、功能 IP 与结果存储的主轨。内供时三脚在器件侧低阻短接，电源仅供器件内部负载 |
| IOVDD | A6 | 内供 1.1 V 标称，外供名义 1.1–1.2 V，规格 1.045–1.26 V | SPI 数据、LVDS、GPIO、配置读回。内供时只供器件内部负载 |
| VDD33 | F3/G3 | 外供 3.135–3.465 V | 模拟电源、参考监测及配置 SPI 输入钳位。进入待机或睡眠后可撤除 |
| REFIN | F4/G4 | 3.000 V 标称，保证性能的范围 2.995–3.005 V | 参考输入，像模拟信号一样处理压降、动态源阻抗、噪声及独立回流 |
| GND / REFGND / IOGND | 见 p.10–12，IOGND 为 B6 | 封装板级要求共同的低阻地平面 | 裸片保留独立的回流分支、井和衬底隔离，在封装及板级按电流回路受控连接 |

VDDLDO 的 p.5–6 Table 1 和 p.23 内供概述写 1.4–2.7 V。p.4 总规格条件采用 1.5–2.7 V，p.10 B3 也给出 1.5–2.7 V，并分列 VDD11 LDO 启动条件 **大于 1.4 V**、IOVDD LDO **大于 1.5 V**。双内供把电源容差、封装压降和启动瞬态算入 B3，设计低端留出超过 1.5 V 的余量。p.5 给出输出典型 1.1 V（25 °C、VDDLDO = 1.8 V）及典型 LDO 启动 10 µs。上电控制等待实际轨稳定。 [规格，p.5–6](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=5) · [VDDLDO 引脚，p.10](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=10)

- **内供**。VDDLDO 接电源，封装 A1/A2/A3 用器件侧粗线或铺铜短接。VDD11 与 IOVDD 保持各自网络，且两轨的封装引脚均禁止并接其他电源或外部负载。
- **全外供**。VDDLDO 悬空，A1/A2/A3 接 1.1 V 外部电源，A6 接外部 I/O 电源，VDD33 仍外供。工程以冷启动绑带表示供电方式。 [内外供接线，p.23–24](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=23)
- **配置与数据**。默认 CMOS CNV 和配置 CS/SCLK/SDI 使用 VDD11 域的输入规范，CS/SCLK/SDI 的 pad 钳位接 VDD33。LVDS CNV/CLK、数据 SPI 的 DCS/DCLK、GPIOx 与数据串行输出涉及 IOVDD。默认配置 SDO 从 GPIO0（G7）返回。域、电压容限与方向须分别落实到 pad 选择和 UPF 接点。 [数字接口规格，p.5](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=5) · [引脚定义，p.10–12](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=10)

**上电**。内供先加 VDDLDO 并等两个 1.1 V 输出稳定。全外供先建立 IOVDD 和 VDD11。随后建立 VDD33，再放行数字输入，随后施加 IN+/IN− 与 REFIN。模拟输入可以在 VDD33 建立时随之施加，REFIN 必须等 VDD33 达到 3.3 V ± 5% 才启用。配置 SPI 主控在 VDD33 ≤ IOVDD − 0.3 V 时保持 CS、SCLK、SDI 为低或高阻，避免通过钳位向 VDD33 回灌。裸片参考设计的配置写许可进一步等到 VDD33 处于工作范围。**完全下电**用相反次序，先撤输入驱动与参考、再撤数字驱动及 VDD33，最后撤 VDDLDO 或两条外供低压轨。 [上下电序列，p.24–25](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=24)

## 2. 两个手册 POR 监测器

| 监测项 | 手册行为 | 本工程接点和影响 |
| --- | --- | --- |
| VDD11 数字 POR | VDD11 低于预设 0.93 V 时数字保持复位，恢复后配置按默认值初始化。数字 POR 也影响模拟复位 | `por_d_ok_async_i` 进入常供域和 FIFO 硬复位树。硬 POR 清 FIFO 有效态与指针，阵列内容由有效态屏蔽 |
| REFIN 模拟 POR | VDD33 为监测器供电，名义阈值 2.7 V。手册还给出低于 2.6 V 停止输出结果，恢复时将 REFIN 拉至 2.87 V | `ref_ok_async_i` 联合 `vdd33_ok_async_i` 停止模拟转换。REFIN 或 VDD33 单独移除时，VDD11 持续供电即可保持配置和已存 FIFO |

Fig. 40 用带反相标记的信号和门画出复位树。按“故障为 1”的表达，数字保持来自 VDD11 POR 或软件复位，模拟保持来自数字保持、参考或模拟电源失效、或所选低功耗模式。0.93 V 和 2.7 V 是复位判据，工作电压须满足上一节窗口。模拟宏需单列门限 PVT、迟滞、毛刺过滤及小于 0.93 V 的复位电气保证。工程另用 VDD33 和 IOVDD 真实电源有效信号实现 pad/域保护。 [POR 电路，p.24](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=24) · [DEVICE_STATUS，p.83](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=83)

硬 POR 和软件复位后至少等 **300 µs** 才进行常规访问。初始化状态由配置 IP 通过 INTERFACE_STATUS_A（0x11）的 NOT_READY_ERR 表达。DEVICE_STATUS（0x14）分别提供粘滞的 POR_FLAG、POR_ANA_FLAG，写 1 清除。`por_ana_event_aon_o` 把 VDD33 有效时的新 REFIN 故障送给 CSR。建议故障事件和 W1C 同沿时优先记录新故障，持续低压只记一次。硬 POR 和软件复位的位初值仍由 CSR 负责。上游配置 SPI 负责软件复位的两次写入序列，先向 0x00 写 0x81，然后清一个或两个 SW_RESET 位。已在途的转换必须先完成。软件复位重置其他配置并保留 **ADDR_ASCENSION 与 FIFO 内容**。 [软件复位，p.27–28](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=27) · [INTERFACE_CONFIG_A，p.75](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=75)

## 3. 低功耗与唤醒

| 模式 | 模拟与保持 | 内供 IOVDD | 全外供 IOVDD | 首次转换 |
| --- | --- | --- | --- | --- |
| 正常 `00` | 按电源和初始化就绪条件运行 | 内供 IO LDO 使能 | 外部 IO 源供电 | 完成初始化和模拟就绪后按系统仲裁允许 |
| 待机 `10` | ADC 模拟保持复位，VDD11 保持。VDD33 在位时参考缓冲和 CMO 缓冲启用 | IO LDO 关闭，所有 GPIO、LVDS 与 SPI 数据禁用。配置仅受限写 0x00/0x02，配置读回禁用 | 合格的 IOVDD 保留 GPIO 与 SPI 数据，SPI 数据可读已有 FIFO。VDD33 在工作窗口时完整配置可用。LVDS 禁用 | 接受退出命令后按 100 µs 守卫，再等附加恢复 |
| 睡眠 `11` | ADC 模拟保持复位，VDD11 保持。参考及 CMO 缓冲关闭 | 与待机相同 | 与待机相同 | 接受退出命令后按 180 µs 守卫，再等附加恢复 |

先停止新的 CNV 和数据接口时钟事务，完成在途事务后申请切换。允许正常与任一低功耗模式之间切换，待机与睡眠之间经正常模式，`01` 保留。应用使用 `OPERATING_MODES`，PWR_CTRL（0x18）直接禁用 LDO 的位维持上电默认。处于待机或睡眠且 VDD33 已撤除时，应先恢复 VDD33 再下达退出命令。此期间外供 IOVDD 的 SPI 数据及 FIFO 读仍可用，配置 SPI 输入保持钳位保护。 [模式与 I/O，p.26–27](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=26) · [PWR_CTRL，p.86](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=86)

p.26–27 Table 11 将 100/180 µs 列为从退出命令到正常模式的最大唤醒时间，Fig. 44 另外标出在下一次转换前的 `t_recover`。该图在本版未给出 `t_recover` 数值。工程 RTL 采用完整守卫并等待新的附加恢复完成与模拟就绪。未收到完成信号时，仍封住 CNV。模拟负责人需在 VDD33 撤除和保持两种条件下分别给出 REFIN/参考缓冲/CMO 建立、转换时钟入口条件及 PVT 最大恢复时间。 [Fig. 44，p.27](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=27)

## 4. 负载和热的一阶预算

以下电流为封装整器件数据，单位 mA，单元格为典型 / 最大。Table 1 主规格条件包括 40 MSPS、REFIN 3.0 V、环境 −40 至 85 °C。电流栏说明 `LVDS_CNV_EN=0`，静态时转换器和接口空闲、FIFO 禁用，待机与睡眠的电流行使用 `LVDS_SELF_CLK_MODE` 禁用。逐 LDO 或宏的峰值与波形须在目标实现提取。 [规格条件，p.4](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=4) · [供电电流，p.6](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=6)

| 负载条件 | VDD33 | VDD11 外供 | IOVDD 外供 | VDDLDO 双内供 | 外供封装耗散 mW |
| --- | ---: | ---: | ---: | ---: | ---: |
| 静态空闲 | 5.4 / 7.0 | 15.3 / 23 | 5.2 / 6.0 | 21.5 / 26.5 | 40.4 / 58.4 |
| 动态，DC 输入 | 15.7 / 18 | 23.2 / 32 | 6.0 / 7.5 | 29.8 / 35.5 | 83.9 / 108.8 |
| 动态，−0.5 dBFS 正弦 | 14.2 / 16 | 23.2 / 32 | 6.3 / 7.5 | 30.2 / 35.5 | 79.3 / 101.9 |
| 待机 | 1.4 / 1.9 | 1.2 / 5.5 | 0.047 / 0.300 | 1.9 / 5.0 | 6.0 / 13.3 |
| 睡眠 | 0.6 / 0.9 | TBD / TBD | 0.050 / 0.300 | 1.5 / 4.5 | 3.4 / 9.8 |

- 外供名义电压下，DC 动态估算 `3.3×15.7 + 1.1×23.2 + 1.1×6.0 = 83.93 mW`。正弦动态约 79.31 mW。表列的外供 DC 最大值与 `3.465×18 + 1.155×32 + 1.26×7.5 = 108.78 mW` 一致。
- 双内供的低压负载功率由 VDDLDO 输入承担。以 1.8 V 和 3.3 V 重算，DC 动态约 `1.8×29.8 + 3.3×15.7 = 105.45 mW`，正弦约 101.22 mW。静态、待机、睡眠分别约 56.52、8.04、4.68 mW。典型 DC 双内供比外供低压轨功率多约 21.52 mW，估计式为 `1.8×29.8 − 1.1×(23.2+6.0)`。分摊到单独窗口的耗散需使用各宏实际电流、输出电压和静态电流。
- 把 DC 最大电流和最高电压同时代入，得到双内供一阶预留 `2.7×35.5 + 3.465×18 = 158.22 mW`。其口径是独立上限的组合预算。REFIN 与外部驱动器、电源转换损耗另记。
- 只按 p.9 Table 4 的 BC-49-8 封装 `θJA = 66.8 °C/W` 作对照，105.45 / 158.22 mW 分别升温约 **7.04 / 10.57 °C**。该值使用 JEDEC 2S2P、16 个热过孔和自然对流模型。裸片实现需另核实际封装热阻和 LDO、SRAM、LVDS 局部温升。 [Table 4，p.9](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=9)

## 5. 裸片 PDN 与去耦分工

本轮规划以外供对照的 VDD11 **32 mA** 和 IOVDD **7.5 mA** 最大负载定初步支路优先级。左上 LDO1 窗口为 85 × 245 µm（20,825 µm²），给 VDD11 的模拟低压与数字/SRAM 负载分别预留供回支路。左下 LDO2 窗口为 93 × 68 µm（6,324 µm²），走独立 IOVDD 供回通道，先核对左侧逃线，再沿较外侧 pad 电源环或独立合格 PG 层，经左边和顶边到数据接口。左内沿 x=121–146 µm 的 25 µm 预算留给粗量化器 Q 的支路，IO 干线及回流单独占用资源。用目标 pad LEF、pad 和过孔 IMAX、布局后 IR/EM 复核两条供回路径。后端还应落实 LDO2 靠采样区的衬底防护、供回网连续及与 RDAC、REF、余差敏感走廊的隔离。两个窗口来源为规划，输出管、补偿、布局、负载范围及身份由宏方电路和版图核定。

| 封装内 X6S 去耦 | 标称值 | 回流 |
| --- | ---: | --- |
| VDD33 | 470 nF | GND |
| VDDLDO | 220 nF | GND |
| VDD11 | 4 × 470 nF = 1.88 µF | GND |
| IOVDD | 220 nF | IOGND |

以上封装内器件容差是 ±10%。参考另有封装内 X6S 储能 CREF **9.4 µF ±20%**。p.72 的板级无需另加上述电源旁路结论依赖集成这些器件的 BC-49-8 封装。裸片规划的 CAP 窗口和分布式片上 decap 按 PDK 有效电容密度、偏压、漏电和面积落实局部瞬态。目标封装基板与外部近端电容承担微法级储能，由芯片、封装和板级联合提取有效 C/ESR/ESL，扫描负载瞬态、反谐振和 LDO 环稳。封装对照须保留供回低阻连接和参考独立回流，公共地平面中避免让大电流 IO 或 LDO 回流穿过参考/采样局部回路。 [参考及电源去耦，p.22–23](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=22) · [布局建议，p.72](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=72)

| 主责 | 发给总后端的冻结输入 | 后端验收 |
| --- | --- | --- |
| LDO / 模拟宏负责人 | 目标 PDK 3.3 V 器件选项、LDO1/2 pin/极性、反灌阻断、Vin/Vout PVT、dropout、Iq、峰值及稳态负载、电流限值、软启、输出 C/ESR 允许窗。分别给 REFIN 监测、POR、PG、ready、掉压阈值、迟滞和空载/满载瞬态 | 从 LDO 输出实际 minimum 电压扣掉封装、die PG 压降、负载阶跃 droop，仍满足目标宏电源条件。量化 pad 及本地 SRAM/逻辑波形，核对芯片/封装联合环稳与供电噪声 |
| PDK / 标准单元 / IO | 目标 corner Liberty/LEF、金属与过孔极限、3.3 V 容限、防回灌 pad、上电失效安全的 LS/隔离/钳位和 AON PG 单元、异步复位同步单元、ESD/井/衬底规则、RC/DRC/LVS deck | 按 VDD11 常供、IOVDD 可失效、VDD33 可撤除建电源状态表。输出开关路径的控制来自仍活的电源域。每一跨域 pin 录明驱动电压、断电方向、钳位值、同步或电平转换单元 |
| FIFO / SRAM / 数字装配 | SRAM 写、读、保持 Vmin，standby pin、读写/唤醒电流谱、宏 VDD/VSS PG pin 及端口时序。配置、指针/有效位、读回与 clock domain 合同，补 BIST 与 OTP 服务期间切换互锁 | 主轨常供方案下共同保留阵列及元数据。若采用宏的低压保持态，分别验证从读写进入保持和返回读写的时序。隔离与 PHY 复位先于电源请求撤除，恢复先等 PG、目的域释放及读握手，不清 FIFO 有效记录 |
| 封装 / 后端 PG / 验证 | 裸片 pad/bump map、GND/REF/IO 回流、封装 RLC/热模型，PG strap 及多过孔资源，LDO 启动/转换/多 lane/SRAM/BIST 各场景波形 | 做 DC IR、瞬态 droop 和 PG 启停，按 Iavg、Irms、Ipeak 分查金属及过孔 EM。检查指定位点压降、地弹、串扰、跨域 back-power 和故障撤销。复位树做 recovery/removal、RDC/CDC、供电感知验证与物理 DRC/LVS/PEX |

例如 VDD11 从标称 1.100 V 到手册外供工作下端 1.045 V 总差额为 55 mV。预算时应先分给电源源端容差、LDO 压降/变化、封装、芯片金属和瞬态。芯片功能宏允差与局部 ΔV 需用目标 PDK 数据，使用 `Z_target = ΔV_allow/ΔI` 设定幅频目标。照片、窗口面积、手册整机电流和本页负载估算支撑规划，IR/EM 和热/噪声验收待目标实现。

## 6. 外围 RTL 端口合同与时序

所有后缀 `_aon_` 的命令、完成与许可在 `clk_aon_i` 消费。此时钟位于 VDD11 且保持连续，频率参数 `AON_MAX_CLK_HZ` 用实物最大频率约束。用 64 位中间量求 `ceil(频率 × 微秒 / 1_000_000)`，参考实例按 10 MHz 计算，300/100/180 µs 对应 3000/1000/1800 个周期。意外绑定为 0 时初始化一直保持，配置、转换、IO 事务继续关闭。接受模式命令和软件复位使用组合同沿回复。掉压的异步复位和隔离路径由电源有效域执行，释放经过各目的域的两个有效沿。

| 端口 | 方向及所属域 | 契约 |
| --- | --- | --- |
| `clk_aon_i`, `clk_core_i`, `clk_io_i` | 入，各自时钟 | AON 连续。Core 和数据控制时钟允许停。后者用接口内部预置时钟保证首个外部 SCLK/DCLK 事务前完成释放 |
| `external_io_i` | 入，冷启动绑带 | 1 表示两条低压轨全外供，硬 POR 内设定并固定至本次下电 |
| `por_d_ok_async_i`, `vdd33_ok_async_i`, `ref_ok_async_i`, `iovdd_ok_async_i`, `ana_ready_async_i` | 入，异步高有效 | 依次表示核心 POR 释放、VDD33 合规及 pad 可接收、REFIN 合格、IOVDD 合格、模拟建立。来自模拟及供电域的失效有效信号在活电侧默认钳 0。VDD11 工作窗口的 run/PVT 裕量由供电和时钟准入落实 |
| `mode_req_valid_aon_i`, `mode_req_aon_i[1:0]`, `clients_idle_aon_i`, `sw_reset_req_aon_i` | 入，AON | 原子请求一个 AON 周期。所有客户空闲后才提交，由配置桥接解码和重试 |
| `mode_req_accept_aon_o`, `sw_reset_accept_aon_o`, `operating_mode_aon_o[1:0]`, `wake_busy_aon_o` | 出，AON | 请求和受理同沿消费。唤醒阶段模式先显示 `00`，用 busy 区分 |
| `cfg_hw_reset_no` 与 `fifo_reset_no` | 出，AON 与 Core 同步释放 | 核心 POR 才清配置及结果保持状态。FIFO reset 接有效态与指针，配置用软件事件选择性重置 |
| `core_reset_no`, `io_reset_no`, `ana_reset_no` | 出，Core / IO / AON 同步释放 | Core 允许运行非保持数据通路。IO reset 清 PHY 事务。模拟接收独立复位，具备在唤醒中先建立的路径 |
| `cfg_init_done_aon_o`, `cfg_control_write_allow_aon_o`, `cfg_full_access_allow_aon_o` | 出，AON | 分别是数字初始化完成、合格 VDD33 下受限的模式/软件复位写、IOVDD 恢复且允许配置读及一般写。配置保持与接口可读独立 |
| `por_ana_event_aon_o` | 出，AON 高有效单周期事件 | VDD33 有效时捕获新的 REFIN 故障。停钟时异步保持，恢复时经同步后发送。下游按事件锁存为 0x14 bit3，配置域异步时使用可确认的事件传输 |
| `fifo_read_allow_aon_o`, `lvds_allow_aon_o`, `conversion_allow_aon_o` | 出，AON | 已存 FIFO SPI 读、LVDS 数据模式、新 CNV 分开许可。各使用方再叠加实际 FIFO 有效性、PHY 模式与 OTP/BIST/运行互斥 |
| `wake_recover_req_aon_o`, `wake_recover_done_aon_i` | 出 / 入，AON | 守卫完成且本轮握手见到 done 为 0 才发出 req。完成者只回应新 req，保持 done 到 req 撤回，模拟另须报告 ready |
| `isolate_io_o`, `iovdd_ldo_en_o`, `ref_cmo_en_o` | 出，常供控制侧 | IO 隔离高有效。内供 IO LDO 拉低需前一 AON 间隔已发钳位请求。REF/CMO 在 VDD33 合规且模式允许时工作 |

状态路径为正常 → 待机或睡眠 → 唤醒计时 → 附加恢复 → 正常。核心 POR 优先，其次是已受理的软件复位，其次才是模式请求。低功耗只接受回正常的模式命令。虽然退出时模式码先显示 `00`，恢复中仍拒绝模式写，防止用待机的短时限替换睡眠。软件复位在低功耗可起动退出流程，在恢复途中则重新执行 300 µs 初始化，再从原低功耗模式的完整时限起算。参考掉电立即封住模拟路径，在唤醒过程中清计时并撤回恢复请求。I/O 掉电异步封住读回与数据路径，核心供电良好时配置受限写通路和 FIFO 状态可继续保持。数字/接口域复位释放各自等待时钟。数据接口从隔离恢复时，顶层保证读端协议与 FIFO 记录边界。掉压中止正在传输的字应按协议丢弃或重发。

RTL 里的许可用于控制和握手，跨域绑定时用接收侧同步和专用 ICG/pad 单元。AON 或对端电源失效时的默认状态由实际 POR、PG 端单元和活电侧钳位建立。`ref_ok_async_i` 下降门限及恢复到 2.87 V 的判据由模拟宏实现，`ana_ready_async_i` 必须反映本次建立。目标宏和库需给出实际最小可捕获脉宽、迟滞、时钟可用性和恢复时延。带时钟的恢复路径仍需 RDC/CDC、复位树、power-intent 和模拟宏验证。

## 7. 复核裁定与未绑定项

复核对象为 2026-11-20 的外围电源交付及原功能仓 `main@7408edf8db9a41eebdd756378b69d2775f107dec`。原仓通过 Git 只读核查。测试输入、结果和源文件哈希见 [实测记录](verification.txt)。

| 复核项 | 裁定与可复测证据 |
| --- | --- |
| AON 参数为零 | 旧 RTL 向量观察到 `init/cfg/CNV/iso = 1/1/1/0`，失败退出 134。修订后同激励为 `0/0/0/1`，退出 0。正频率分别以 10,000,001 Hz 和 4,294,967,295 Hz 验证向上取整和 64 位中间量 |
| 参考故障粘滞 | 原 CSR 的 `por_analog_r` 在硬或软件复位置位，0x14 bit3 写 1 清除。它缺参考故障输入，清位后保持 0 直到下次数字复位。新增事件输出在 AON 停钟时捕获 RTL 模型 6 ns 故障，恢复第 3 沿产生事件。单比特 CSR 模型验证清位、重新置位、同沿新故障优先，以及持续故障无重发。原 CSR 的真实解码和事件绑定见下项 |
| 睡眠经待机绕过 180 µs 守卫 | 状态机拒绝直接 Sleep→Standby，退出进入恢复后也拒绝模式改写。对抗向量在 1000 和 1799 周期观察到转换仍禁止。重复软件复位重新执行 3000 + 1800 周期，旧 done 被拒 |
| 停钟、复位释放与保留 | 参考掉压可在无 AON 沿时封住新转换。IO/Core 停钟期间硬断言，恢复各自等待目的时钟。在低功耗外供 IOVDD 下模型仍可读已存样本，软件复位保持 FIFO 与 ADDR_ASCENSION |

**产品 SPI 与真实 CSR 未绑定。** 原 `subsys/ad4080_dsp/rtl/ad4080_regs.sv` 的 0x02 读回固定 `00`，对 `10/11` 写入按未支持拒绝。0x14 bit3 也缺参考事件输入。原 `ad4080_top.sv` 将 `sw_reset_pulse` 接到未使用信号。完成产品绑定时，由 CSR 所有者将 0x02 写入和 0x00 软件复位序列交给 AON 同沿事务，0x11 反映初始化和合规访问语义，0x14 bit3 锁存同步后的事件。验证需从真实 SPI 执行 W1C、仅掉 REFIN、读回 bit3 重新置 1，同时检查 bit0、配置和 FIFO 保持。另由真实 SPI 发 0x02 睡眠及退出，观测模式读回、上述守卫与 FIFO 访问。 [原寄存器实现](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_regs.sv#L158) · [原 DSP 顶层](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_top.sv#L119)

本机 arm64 Verilator 5.30 的 DUT `-Wall` lint、8 组定向仿真和 3 个参数专项通过。Yosys 0.69 对本外围 RTL 运行结构检查，报告 0 问题。测试用逻辑 PG 和最小配置、样本模型观察接点。物理 POR、时钟、PG、隔离和最小脉宽进入目标工艺验证。 [验证记录](verification.txt)

在本目录用已安装的 Verilator 和支持 C++20 的编译器复跑。用临时 ASCII 路径保留彼此独立的构建产物。

```sh
pwr_build=$(mktemp -d /tmp/ad4080-pwrrtl.XXXXXX)
cp ad4080_pwr_reset_sync.sv ad4080_pwr_supervisor.sv tb_ad4080_pwr_supervisor.sv "$pwr_build/"
(
  cd "$pwr_build" || exit 1
  verilator --lint-only --timing -Wall --top-module ad4080_pwr_supervisor \
    ad4080_pwr_reset_sync.sv ad4080_pwr_supervisor.sv || exit 1
  for rate in 10000000 0 10000001 4294967295; do
    verilator --binary --timing --assert -Wall --compiler clang -CFLAGS '-std=c++20' -j 2 \
      --top-module tb_ad4080_pwr_supervisor -GTEST_AON_MAX_CLK_HZ="$rate" --Mdir "obj_$rate" \
      ad4080_pwr_reset_sync.sv ad4080_pwr_supervisor.sv tb_ad4080_pwr_supervisor.sv || exit 1
    "./obj_$rate/Vtb_ad4080_pwr_supervisor" || exit 1
  done
)
```
