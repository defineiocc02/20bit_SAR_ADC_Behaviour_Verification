# AD4080 Rev. B 产品数字端复验

## 逐项对拍

从交付提交 `bca954b` 出发，按原件印刷页逐项核对。功能矩阵的每个“逻辑已测”条目给出独立回归项和实测锚点。产品用同一冻结 DSP 经真实配置 SPI、GPIO、结果 SPI 及单口存储检查可观察行为。按相应操作名查[功能对拍矩阵](../../evidence/manual-functional-coverage.csv)与[同版全量回执](../../evidence/peripheral-review.json)。

| 手册要求 | 基线复现 | 修订后判据 |
| --- | --- | --- |
| 表 31，37 个已定义地址 | 补齐整张表的正式产品读回 | 硬 POR 后按降序、软复位后按保留的升序分别核对 37 字节。六个多字节实体按真实线序访问。0x19 按低四位验复位后的方向，上半字节随外部引脚状态。 |
| p.28，软复位保留已完成 FIFO | 存入两笔后，软件复位被拒绝 | ADC 空闲、记录冻结且 DCS 未开始时受理软复位。0x11 从 0x80 恢复至 0x00，0x1C 回 0x00，从同一 SRAM 完整读回 `53A71、53A72`。 |
| p.80 表 44，0x0F[2] | 写入 0x04 被拒绝，读回仍为零 | 普通及 CRC 流式读可见 0x04，越权位不破坏旧值，清位和软复位后为 0。非零 LOOP_COUNT 另受 p.80 不支持说明约束。 |
| 表 49，0x16[0] 仅用于 LVDS | 切结果 SPI 后物理选择请求仍为 1 | SPI1/SPI4 的请求均为 0，0x16 仍读回 01。只写 0x15 回 LVDS，选择请求恢复为 1。 |
| 图 91，转换可与采后读并行 | 连续 CNV 下单线 SPI 第九个 DCLK 的首载荷错误 | CNV 每 60 ns 持续输入，DCLK 周期 20 ns，单线 56 拍与四线 20 拍逐位通过，均只写入并读出原先的两个字。 |
| 表 11，低功耗下的 GPIO 驱动 | 内供待机后仍见 GPIO1–3 驱动 | 内供的待机和睡眠均为 OE=0000，外供均为 OE=1110，唤醒恢复已编程的方向。输入 Pad 的电气隔离由实物验证。 |
| 表 25、图 98，直接 LVDS 的 FILTER_SYNC 低电平保持 | 第二个低电平 CNV 锁故障 | 三个低电平 CNV 持续保持复位并忽略旧码，释放后新码 8、16 得到第一笔均值 12。 |
| 表 61，公布的三个名义增益码 | 原 SHIFT=14 速率夹具增益被放大，适合验证数据通路 | 单独用 SHIFT=15 从 RTL 回读 0x001/200/3FF。正输入 +200000 得到 196881/200000/203119，负输入对称。三档的核心校正单元均为两周期，手册给出的芯片单位增益低延迟不由该单元证明。 |

[ADI Rev. B 原件](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) · [寄存器逐地址读回](evidence/register-reset.csv) · [逐模块手册对照](../../evidence/MANUAL_TRACE.md)

## 速率与基本使用

复验日期为 2026-11-20。起点是发布提交 `7afa115a5b1a6eea2806cb52f83783f17a5bcb4e` 的干净检出，原有 37 个用例全部通过。短保持反例在相同产品顶层复现，随后修复 DSP 的 SDI 捕获。发布验证使用[冻结 DSP 对象](../../evidence/dsp-revision.txt)与各构建输出的文件摘要。产品顶层用四组 4096×20 存储银行接通实际配置、采集、滤波、事件与界面。输入来自外部已译码 CNV、20 位补码及同一转换标签。

## 本轮发现与修订

| 对照项目 | 原件判定 | 复验办法与结果 |
| --- | --- | --- |
| 配置端口采样 | p.7 表 2 给出 20 ns SCLK，低与高各至少占周期 45%，SDI 上升沿建立 1 ns、保持 0 ns | 400 MHz 核心，50 MHz SCLK，两组低/高时间 9/11 与 11/9 ns。在每个 SCLK 上升沿后 1 ps 反转 SDI。旧实现延后取引脚值，首个 0xA6 回读为 0xFF。修订后在 SCLK 本身锁住 SDI，然后随同步的时钟事件在核心域判决。每种 DCLK 接口档合计 8 次暂存写读与 2 次器件 ID 读取均通过。 |
| 40 MS/s 连续单线 | p.48 示例与 p.49–50 表 20、21 | 配置 A/B=0x40/0x50 时为单位增益，首位距同源译码 CNV 为 46.25 ns。A/B=0x40/0x70 且启用增益校正时为 51.25 ns。两档均为 400 MHz LVDS CLK+，各对 128 笔的 20 位、CNV 标签、首/末槽和缺字诊断逐位比对。 |
| 40 MS/s 连续双线 | p.48 双 lane 200 MHz 样例，表 20、21 | 配置 A/B=0x44/0x20 并启用增益校正，首位为 52.50 ns。128 笔逐位比较，DA/DB 的相对位序与标签均受判断。 |
| PF1 文档回执 | p.64 图 96、p.68–69 和 p.94 表 62 | 产品 README 的旧码 1024/1020/984 已更正为真实 744/1481/480。PF1 N=2、总倍率 4 实际首个 RDY 于 E152 发布，E153/E157/E161 写入并读回连续 46 位对。建立拍数沿用本实现的总倍率解释，见原件审查。 |

速率验证的 CNV 周期为 25 ns，码源在下一笔 CNV 到来后的核心拍提供上一笔完成样本。这是可复现的数字宏接口合同。具体时间起点见 [速率回执](evidence/at-speed.txt)；原件依据见 [p.7 表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7)、[p.48 时序示例](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=48)、[p.49 表 20](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=49)、[p.50 表 21](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=50)。

首位延迟按 `25 ns + (有效时钟计数 + 0.5) × LVDS 时钟周期` 对拍。表 20 将寄存器 B 的高半字节 5、7、2 分别解成 8、10、5 拍。p.48 最后一段的标称频率、除数和末式乘数彼此不一致，本轮采用前面给出一致算式的三个示例，并用图 79、80 及表 20、21 交叉核对。配置 SPI 回归复现了最短保持窗口，物理 Pad 的 1 ns 建立及 0 ns 保持仍按目标库做约束与量测。[Rev. B p.48–50](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=48)

## 使用顺序

1. 在实际板或验证环境提供核心、AON、I/O 时钟及电源和参考条件，完成本工程的 OTP 适配器读、验收与初始化握手。冷机固定字依赖合格参考历史。用配置 SPI 读产品 ID 0x0050。配置关闭期间先处理 0x11 初始化状态。电源唤醒遵循 AON 恢复握手。[p.24 供电](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=24) · [p.27 模式恢复](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=27) · [配置回执](../../interfaces/control_review/pin_edges_validation.txt)
2. **连续 LVDS**。先停采样并排空上个记录，写 0x1C=0x00 禁用 FIFO，0x29=0x00 直通，按上表写 0x15/0x16。待 `continuous_config_ready_core_o` 和新预约资格生效后，交付同一 CNV 的核心和 I/O 事件，结果与标签同拍。检查每笔首位、末位及 `continuous_frame_missing_io_o`，发生缺字后重新确定返回时序。目标 50 MHz 配置和 25 ns CNV 使用本次复验的 400 MHz 核心模型；实际核心时钟必须与 `CORE_CLOCK_HZ` 参数一致。[p.48 输出时序](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=48)
3. **采后 FIFO**。停采样并清掉旧记录，配置水位与 0x29，再布防 0x1C。三字立即捕获示例用严格访问、降序整实体从 0x1E 写高低字节 00/03，写 0x29=0x01 选 SINC1 N=2，GPIO 配成同步输入与 RDY/FULL，写 0x1C=0x01 后用 GPIO 对齐本笔采样。主机等 FULL 再选 DCS 并按位序读。SPI1 和 SPI4 两字前缀分别为 56 与 20 个 DCLK 上升采样沿。连续与 FIFO 两条用途须用对应输出资格。[p.56–59 捕获和记录](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=56) · [p.68 图 99](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=68) · [产品回执](evidence/verification.txt)

在实例化时显式提供手册布局的 `SPI_MULTIBYTE_ORDER=0`、`OFFSET_LSB_ADDRESS=37` 与 `SPI_WRITE_SDO_DRIVE=1`。产品台架在速率档采用 `P_CNV_TIMING_BOUND=1`、`CORE_CLOCK_HZ=400000000`。增益校正演示绑定 `GAIN_BOUND=1`、`GAIN_K=1`、`GAIN_SHIFT=14`，作为工程转换关系；单位增益用码 0x200。寄存器写入、上游实际时钟和转换标签须成组交付。[实例与端口](ad4080_peripheral_product.sv)

## 自包含数字验收

从含固定 DSP Git 对象的仓库运行。Verilator 版本采用 5.030，Yosys 用于检查实际展开的存储、时钟和 CDC 结构。完整复验在单/双 lane 状态分别冷启动，速率测试与默认五组、系统校准使用独立的新构建目录。

~~~bash
export DSP_UPSTREAM_REPO="$(git rev-parse --show-toplevel)"
cd "$DSP_UPSTREAM_REPO/subsys/ad4080_peripherals"
export YOSYS=yosys
bash integration/product/run_product.sh --at-speed
bash integration/product/run_product.sh --all-pins
CAL_PYTHON=/path/to/adctoolbox-venv/bin/python bash integration/product/run_product.sh --calibration
~~~

结果要求实测出现 `AD4080_PRODUCT_AT_SPEED PASS`、`AD4080_PRODUCT_ALL_PINS PASS`、`AD4080_PRODUCT_CALIBRATION PASS` 及各档 `PRODUCT_STRUCTURE PASS`。统一验收还独立核对图 83、89、99 的逐沿读写、26 档定点滤波频响、校准算术与 ROM/CRC/时钟边界。[统一回执](../../evidence/peripheral-review.json) · [逐模块手册对照](../../evidence/MANUAL_TRACE.md)

## 成品签核状态

本轮放行条件覆盖数字 RTL 在指定时钟、复位、转换返回、状态和工程 CRC 合同下的集成与使用。目标芯片签核仍需实际转换宏和 SRAM/OTP 宏、SCLK/SDI Pad 建立保持、DCO/ODDR 的物理延迟与相位、CDC/STA/UPF，以及商业器件 CRC24 参数或有效现场样本。开放证据及具体责任方在[待验清单](../../evidence/peripheral_open_items.csv)。供项目集成和仿真的数字接口按本页命令验收，面向真实芯片的放行以该清单闭环为准。
