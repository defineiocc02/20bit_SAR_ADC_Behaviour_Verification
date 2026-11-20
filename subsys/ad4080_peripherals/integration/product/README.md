# AD4080 外围单存储产品绑定

`ad4080_peripheral_product` 接收转换团队输出的 20 位二补码与真实转换身份。固化版本的 `ad4080_top` 负责配置 SPI、系统校正、滤波、阈值和记录尺寸。外围接入上电/OTP/MBIST、唯一结果阵列、采后记录组帧和连续数据输出。

## 源码与存储所有权

- [DSP 版本](../../evidence/dsp-revision.txt) 统一所有入口。`bind_dsp.py` 从指定仓库固定 Git 对象执行 `git archive`，按 SHA-256 提取所需 DSP 模块及 PF1 系数头。每次绑定在新的 ASCII 临时目录中生成。
- 临时复制的 `result_fifo` 移出数据阵列，保留空满计数和指针。原 `fifo_capture` 保留四种捕获模式和冻结快照，真实单口写受理推动写进度。原 `ad4080_top` 传递存储接缝。其它 DSP 源保持原摘要。
- `u_services.u_result_store` 是唯一结果阵列，含四个 4096×20 银行。功能读必须预留 CDC 容量，且真实单口读地址受理才退休原游标。`u_record` 通过一个 8×22 队列运送采后字。连续数据跨域另用两个 52 位触发器槽，分别保存 20 位数值和 32 位转换身份。
- 旁路、SINC1、SINC5 和 PF1 使用同一个校正模块。SINC5/PF1 共享一个 CIC；PF1 追加公开曲线拟合的定点 FIR 和二倍抽取。厂商原抽头、器件级噪声和失真留待器件证据。

## 边沿、供电与结果会话

| 接缝 | 承担的行为 |
| --- | --- |
| CNV 和 ADC 完成数据 | `cnv_tick_core_i` 是核心域的译码上升沿，须与同步后 GPIO 对齐。源给出结果及 `sample_tag_core_i`，静默资格须覆盖转换和数值流水在途。默认启用拍点模式。显式给 `CORE_CLOCK_HZ` 后才可用核心时钟量化 25 ns 最短间隔。 |
| 连续数据 | 在 `clk_io_i` 下从同源 CNV 边界取得 `cnv_tick_io_i` 与 `cnv_tag_io_i`。旁路按原转换身份配对，滤波按已经公布的数值身份保持或更新。A/B/C 和滤波选择作为一份原 CSR 快照应用。SYNC、取数故障或退出连续方式会清空旧数据和预约。 |
| 采后单 lane LVDS | 捕获完成后用 `record_command_core_i` 请求记录。数据以高位先出的 FALL/RISE 逻辑对输出。CRC 服务给外部格式资格，并在最后一个 payload pair 消费沿提交 24 位余数。记录起发后，缺拍或阻塞会退出，CRC 尾末位交付后才报核心域完成。 |
| 采后 SPI | `0x15=0x41/45` 依次选单线 B/四线 A–D。通过 Mode 3 结果端口 `result_dcs_ni` 和 `result_dclk_i` 输出固定头和符号扩展至 24 位的数据。主机在真正写满后可立即选中。首个合法 DCLK 下降沿发出头首位并向核心授权同一 SRAM 的真实读，至第九沿给首个载荷位组。诊断 ready 暴露载荷邮箱，主机使用 FIFO_FULL。外供 IOVDD 的待机可读已保存记录。 |
| 物理状态和 CSR | GPIO FIFO_FULL 在最后一次捕获写上升。读相 FULL 撤销和 READ_DONE 置位取真实首/末 TX 字装入事件。LVDS 使用位对域本地沿，SPI 使用实际 DCLK 事件同步到服务域。原 `0x14` SPI 读取同一状态的双级取样。READ_DONE 出现时最后样本仍有余位，LVDS 还有 CRC 尾。 |
| 顶层供电控制 | 配置 SPI 的 `0x02`、`0x00` 和 `0x18` 送往同一个 AON 仲裁，确实接收/拒绝后才更新对应状态。软初始化中仅实际供电合格的 `0x11` 只读轮询进入原 SPI。LDO、VOD、终端及 CNV 输入选择是实际宏请求。REFIN 已合格后掉电的事件进入原 `0x14[3]` sticky，可实际 SPI W1C；从未有合格 REFIN 的冷启动禁止固定字。 |
| 掉压或中止 | 未读完成记录在 IOVDD 掉电时保留。任何真实读已发生的半记录遇掉电或发送错误，明确作废并清除原 FIFO 元数据。重新布防和撤销能在 I/O 停钟时清掉旧的 GPIO 记录状态。 |
| MBIST | 未完成记录和活动读锁挡住破坏性接手。测试结束需要清除原记录/队列指针再回报重建完成。后续正常记录复用同一物理地址空间。 |

数字验证输入在引脚译码之后，验证输出到逻辑位对、单端配置/结果 SPI 逻辑位与各 OE 信号。ODDR、DCO/PAD 关系、差分接收和输出负载、目标 SRAM 宏和真实 PVT 时序仍需物理绑定。采后 LVDS 试验用了独立工程余数，真实器件 CRC24 参数依芯片或原厂证据。配置 `0x17` 原值可读回，非法 VOD 编码向宏发出的请求回落到 `010`。

## 复跑入口

从外围子系统根目录执行。给 `DSP_UPSTREAM_REPO` 包含上述固定提交的 Git 工作树。配置 `VERILATOR`、`YOSYS` 可覆盖本机工具路径。

```sh
export DSP_UPSTREAM_REPO="$(git rev-parse --show-toplevel)"
export YOSYS=yosys
bash integration/product/run_product.sh --all-pins
CAL_PYTHON=/path/to/adctoolbox-venv/bin/python bash integration/product/run_product.sh --calibration
```

| 入口 | 终止标记 |
| --- | --- |
| 默认 | `AD4080_PRODUCT_BINDING PASS` |
| `--all-pins` | `AD4080_PRODUCT_ALL_PINS PASS` |
| `--pin-edges` | `AD4080_PRODUCT_PIN_EDGES PASS` |
| `--filters` | `AD4080_PRODUCT_FILTERS PASS` |
| `--result-spi` | `AD4080_PRODUCT_RESULT_SPI PASS` |
| `--control-pins` | `AD4080_PRODUCT_CONTROL_PINS PASS` |
| `--full-record` | `AD4080_PRODUCT_FULL_RECORD PASS` |
| `--calibration` | `PRODUCT_ADCTOOLBOX_PLAN PASS` 及 `AD4080_PRODUCT_CALIBRATION PASS` |

设置 `YOSYS` 时，每轮要求真实 JSON、实际顶层与 `check -assert` 完成标记。检查器读取实际参数、DSP 层级、记录和连续路径，并逐实例统计四银行与运输队列。仿真配置核心为 100 MHz、典型 CNV 间隔 60 ns。SPI 常规配置含 7 位短地址与产品编号 `0x0050`。四线写数据在无 CRC 回传时驱动 SDO 为 0，片选解除后释放。[AD4080 Rev. B p.35，Write Access](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=35)

`--all-pins` 在同一具名工作目录使用一份 SHIFT=14 二进制，分别冷启动常规、滤波、结果 SPI、AON 控制和完整记录。常规收据为 `simulation.log` 和 `pin_edges.csv`，其余收据为 `simulation_<组名>.log`。实际采后 SPI 刺激使用 400 MHz 服务时钟与 50 MHz DCLK。校准为另外一次 SHIFT=15 构建。

## 可复查的记录

| 用例 | 整帧或实际判据 |
| --- | --- |
| Fig.83 | 输入向上、向下越过阈值后两次 CNV 到 GPIO ALERT。原 SPI 回读 0x14 sticky 再 W1C。另用实际 20 ns 间隔查 ADC_CNV_ERR。 |
| Fig.99 / Fig.89 | GPIO SYNC，SINC1 N2，RDY 低 E5/E7/E9，实际带数写 E6/E8/E10。三字 200、600、1000，46 个逐拍逻辑双沿对 `AA_000C8_00258_003E8_CED817`。单字 W1 在同一次首末装载更新 FULL/DONE，26 对 `AA_00123_578475`。 |
| SINC5 / PF1 | 真 SPI 0x29 写 `0x02` 或 `0x03`。SINC5 D2 输出 74、164、114，PF1 D4 输出 1024、1020、984。两种独立原码组均在 GPIO RDY 和真实物理写时核对，读回完整三个式样字。 |
| 阈值触发记录 | 真实 `OFFSET=-64`、`GAIN=0x300`、SINC1 N4、水位 3 及模式 `10`。195 未触发，1105 触发后不进入记录，随后三字 1885、2535、−3315 以 `AA_0075D_009E7_FF30D_C69FFD` 输出。 |
| 空、未完、满及环形 | 空与未完拒绝读取。模式 01 接受恰 16384 次物理写，末地址 `0x3FFF`，额外码不入记录。已满模式 11 试验查物理地址 `0x3FFF→0x0000`，回读前缀 `03FFF_04000`。完整四模式定向另跑。 |
| 电源与自测恢复 | IOVDD 掉压时保留未读记录、作废已取半记录。独占 MBIST 后实际中止原因 3，清元数据并重新捕获到零地址。 |

试验 CRC 对记录 AA 头和真实载荷逐位计算，初值 `0xB704CE`、多项式 `0x864CFB`；独立定值由 GF(2) 长除求得。主台架的 `PIN_EDGE` 逐 I/O 槽保留空拍，`FILTER_EDGE` 记录原码刺激后的有效状态、真实单口写数据和地址；连续与结果 SPI 有各自的时戳输出。

## 产品级 OFFSET/GAIN

`--calibration` 调用现有 [主机校准程序](../../calibration/README.md) 和 ADCToolbox 估计 2048 笔独立正弦训练。按该 CLI 实际输出的字节，经真实 SPI 事务提交并读回 `OFFSET=0xFB0`（−80）和 `GAIN=0x15D`。此路径选工程 `K=1, SHIFT=15`，普通图示回归选 `SHIFT=14`。校正先加 OFFSET，再按增益对偏移后的码计算增量，完整施加后做 20 位限幅。

| 留出参考码 | 中性实测 | 实际校准后 |
| ---: | ---: | ---: |
| −300000 | −301420 | −300000 |
| 0 | 80 | 0 |
| 250000 | 251330 | 250000 |

两轮真 CNV 采样用同一物理存储及 46 对读回链。中性帧为 `AA_B6694_00050_3D5C2_4AB21A`，校准后为 `AA_B6C20_00000_3D090_C5A11E`。主机计划基于这批训练条件和显式工程映射，器件实物比例仍需测量。

常规和校准回执见 [verification.txt](evidence/verification.txt) 与 [calibration.txt](evidence/calibration.txt)，原源及临时绑定层摘要见 [upstream.txt](evidence/upstream.txt)，主机实算字节见 [calibration_plan.json](evidence/calibration_plan.json)。[sources.sha256](evidence/sources.sha256) 与 [calibration_sources.sha256](evidence/calibration_sources.sha256) 在 runner 打印的每轮新 build 目录验证，包含生成的 `bound_dsp/` 文件和校准计划。
