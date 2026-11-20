# AD4080 外围单存储产品绑定

`ad4080_peripheral_product` 由固定 `ad4080_top`、已有外围服务壳和已有单 lane 整记录链组成。输入是转换组交付的 20 位二补码结果，配置经原 SPI、CSR 和数值链。输出是完整采后记录的逻辑 DDR 位对。

## 绑定源码和存储所有权

- `bind_dsp.py` 从受保护仓固定 Git 对象 `7408edf8db9a41eebdd756378b69d2775f107dec` 执行 `git archive`，只取 12 个必要 DSP 源文件。每个入口文件检查完整 SHA-256。生成器只在新的 ASCII 临时目录写出绑定，每次构建使用这一个栈。
- 临时副本中修改的模块为 `result_fifo`、`fifo_capture`、`ad4080_top`。`result_fifo` 移出数组和实际读写，保留计数、读写指针和空满运算。`fifo_capture` 的触发、冻结快照、记录模式和长度来自原状态机，写进度改由实际物理写受理推动。顶层只传递新增接缝。CSR、SPI、数值链、事件和 GPIO 的九个源码逐字节保持原 SHA。
- `ad4080_peripheral_services.u_result_store` 是唯一的结果阵列。实写从 DSP 进入同一个单口仲裁，记录读只有在传输队列留出容量且 SRAM 同沿受理时才推进原 FIFO 读游标。原 `fifo_capture` 提供下一地址，包含环形快照地址选择。I/O 叶子有一个默认 8×22 整字 CDC 队列。

## 控制与退场

| 事务 | 连接和优先级 |
| --- | --- |
| 采集与停流 | 上游只在 `conversion_allow_core_o` 允许时提交样本。`sample_source_idle_core_i` 需覆盖转换和已选数值链的在途尾部。记录未完由原 `capture_incomplete` 送到 AON，和空总线分开判断。若在途样本实际拿不到写口，当前记录作废并锁存 `capture_write_loss_core_o`，防止跳字后被误作完整记录。 |
| 记录授权 | 核心提供 `record_command_core_i`，以 `record_ready_core_o` 受理检查。无完成记录给拒绝脉冲。成功时冻结原长度并锁住配置活动，经稳定字数和请求位交给 I/O。格式和 CRC 未绑定时拒绝，未读记录可重新请求。 |
| 结果归属 | 原 DSP 的 FIFO 读完状态反映最后一个 SRAM 地址已受理。`record_done_core_o` 反映 I/O CRC 尾末槽已交付。生产主机完成对账使用后者。地址顺序、字数和实际能读到的内容都由同一记录配对。 |
| 掉压或中止 | 未读的完成记录在 IOVDD 掉电时保留。已接受至少一次 SRAM 读的半记录若遇掉电或运行中错误，明确作废，调用原 FIFO 元数据 clear；物理阵列不逐字清零。恢复后需要新采集，避免拼接上一条记录的剩余字。 |
| MBIST | 未完成记录和活动读锁挡住破坏性接手。独占发生时屏蔽旧记录的可读资格。确认旧会话已作废后，注册清除原记录/队列指针，下一步才回送重建完成，随后新记录从零地址重新填入。 |

SPI 配置的 CSR 软复位按原模块许可执行，AON 供电/初始化管理请求单独保留。二者在完整芯片的统一命令提交和设备状态寄存器桥中合并验收。`format_bound_io_i`、`crc_service_ready_io_i` 由真实 I/O 协议适配提供，`crc_start_io_o` 只随合法组帧起点出现。`io_record_epoch_o` 撤销时 CRC 端需清除旧会话。

## 可重复验证

从交付子系统根目录运行。`DSP_UPSTREAM_REPO` 指向可读取固定提交的保护仓。本入口从 `integration/files.f`、`integration/readback_files.f` 和本目录 `rtl.f` 生成一个完整清单。Verilator、Yosys 路径可由环境变量覆盖。

```sh
DSP_UPSTREAM_REPO="$(git rev-parse --show-toplevel)" \
YOSYS=yosys bash integration/product/run_product.sh
```

脚本在每轮独立 `/tmp/ad4080-product.*` 树留存 `upstream.log`、源哈希、编译与仿真日志、`synthesized.json` 和 `structure.log`。带 `YOSYS` 的运行要求实际 `check -assert`、指定顶层和 JSON。结构检查验证原 SPI、DSP、SINC1、事件、捕获和队列控制叶各一份，`u_services.u_result_store` 下四个 4096×20 阵列，外加 `u_record` 的一个 8×22 队列。编译绑定的工程参数和仿真对齐，采用降序多字节、`OFFSET` 低地址 `0x25`、非单位增益 `K=1`、`SHIFT=14`。四线配置写在无 CRC 返回的数据相驱动 SDO 为 0，片选结束后释放。器件未给定的补偿系数默认未绑定。根目录原三域和读回脚本仍可独立运行。[AD4080 Rev. B p.35，Write Access](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=35)

| 实测 | 观察 |
| --- | --- |
| 四线配置 SPI | 对暂存寄存器 `0x0A` 正常逐位写 `0xA5`。每个写数据位在主机采样点均为 `sdo_oe=1`、`sdo=0`，`CS` 后释放并正确读回。 |
| 真 SPI 到完整记录 | 实际写入 `OFFSET=-64`、`GAIN=0x300`、`SINC1 N=4`、HI 阈值码 4、水位 3 和捕获模式 `10`。196 未触发，1106 触发但未被写为事件后第一字。真实存储写三次、读三次，I/O 端重组 `AA_0075E_009E8_FF30E_71F3EC`，依次为头、1886、2536、−3314 和试验 CRC。 |
| 空与未完成 | 零读请求、AON 模式/软复位/BIST 被挡住，实际 SPI 软复位第一段也被原 CSR 拒绝。格式不合格时未耗任何读地址。 |
| 物理满与冲刷 | 配置旁路、水位 16384 和模式 `01`。接受 16384 次物理写，末址 `0x3FFF`，额外样本未入记录。未读时 IOVDD 掉电保留；再在实际载荷发送时掉电，当前记录和搬运会话退场，复电后无旧记录。 |
| 实际 MBIST 接手 | 观察同一单口的测试请求。中止原因 3 后执行真实 FIFO 元数据清除，交回正常访问并从地址零捕获新记录。 |
| 模式 `11` 满史冻结 | 四银行满后实际环写，记录事件址 `0x3FFB`。收齐四个事件后字，真实首两读址 `0x3FFF → 0x0000`，I/O 载荷前缀 `03FFF_04000`，随即主动截断并作废。 |

正向记录为 46 个逻辑双沿位对。余数 `0x71F3EC` 来自 68 位头和载荷前缀、初始值 `0xB704CE` 及测试多项式 `0x864CFB`。固定黄金使用独立 GF(2) 长除，CRC 服务逐位取实际交付的头与载荷。产品 CRC、真实 Pad/ODDR/DCO 与 CNV 时序、原 `FILTER_RESULT_RDY` 的器件级时限、目标编译 SRAM 宏及全片 STA 依交接证据闭合。模式 `11` 的回归边界为已填满且环写的历史及跨环首两字。

## 产品级 OFFSET/GAIN 验证

`--calibration` 调用现有 [主机校准入口](../../calibration/README.md)，使用其 2,048 笔已知正弦训练。ADCToolbox 估计后，runner 只把 CLI 实际返回的字节组为两个 SPI 实体。测试台在同一个产品顶层选择 `K_GAIN=1, SHIFT=15`，随后用原配置 SPI 写入并完整回读 `OFFSET=0xFB0`（−80）和 `GAIN=0x15D`。不带选项的产品回归采用 `SHIFT=14`，继续比较原 SINC1 的 46 位对黄金。

```sh
DSP_UPSTREAM_REPO="$(git rev-parse --show-toplevel)" \
CAL_PYTHON=/path/to/adctoolbox-venv/bin/python \
YOSYS=yosys bash integration/product/run_product.sh --calibration
```

三个已知参考码均在本轮两点和正弦训练集合之外。合成入口以 `x=1.005t+80` 提供原始码，同一三笔分别在中性设置和实际校准设置下写存、读回和拼帧。每轮 46 个双沿位对，以完整独立黄金检查各字、帧界和试验 CRC。

| 留出参考码 | 实际中性输出 | 实际校准后输出 |
| ---: | ---: | ---: |
| −300000 | −301420 | −300001 |
| 0 | 80 | 0 |
| 250000 | 251330 | 250000 |

两种运行的结束行分别为 `AD4080_PRODUCT_BINDING PASS` 和 `AD4080_PRODUCT_CALIBRATION PASS`。校准在测试台采纳字节前查 `PRODUCT_ADCTOOLBOX_PLAN PASS`。两者均验相应工程移位和唯一 SRAM 层级。

常规与校准工具回执见 [verification.txt](evidence/verification.txt) 和 [calibration.txt](evidence/calibration.txt)，原源与绑定层摘要见 [upstream.txt](evidence/upstream.txt)，实算主机计划见 [calibration_plan.json](evidence/calibration_plan.json)。两种运行分别记录 [sources.sha256](evidence/sources.sha256) 和 [calibration_sources.sha256](evidence/calibration_sources.sha256)。清单里的 `bound_dsp/` 和生成的训练计划在 runner 打印的本轮临时目录内校验。
