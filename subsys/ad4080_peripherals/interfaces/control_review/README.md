# 配置 SPI、寄存器与电源控制交接

报告 ID `CTRL-7408EDF-GPIO-WM`

独立 DSP 的冻结基线是 `7408edf8db9a41eebdd756378b69d2775f107dec`。**默认顶层拒绝配置写入**。按公开依据绑定写相 SDO、字节序与地址后，配置 SPI 可读写已实现的寄存器，也能通过 0x11 观察已定义的协议错误。0x02 模式控制、参考故障再置位和软复位送往服务壳，在独立包内由[薄桥](ad4080_csr_power_bridge.sv)接通并通过真实 SPI 引脚回放。原始 `ad4080_top` 的对应服务连线仍待其所有者接入。

证据使用本地 95 页 AD4080 Rev. B，SHA-256 为 `f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1`。交叉核对 ADI no-OS 提交 `0e6ed1dea135ef541ad6323fc9fcad116ffce90b` 的 AD4080 驱动。DSP 基线源码仅由本地 Git 对象归档至独立 scratch。验证使用同一交付包的 AON 监督器，证据层级为开源 RTL 仿真。[ADI Rev. B](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf) · [ADI 驱动头文件](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h) · [冻结 DSP 顶层](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/subsys/ad4080_dsp/rtl/ad4080_top.sv)

## 1. 手册表 31–62 的实现范围

| 手册表及功能 | 冻结基线与本包的处理 |
| --- | --- |
| 31–33，总表、0x00/0x01 指令控制 | 长指令首位是读写，默认 15 位地址。表 33 和第 35 页给出短指令 7 位地址，可显式绑定。第 35 页说明写数据相 SDO 驱动 0，因此测试绑定 `P_WRITE_SDO_DRIVE=1`。第 27 页与 ADI `ad4080_soft_reset` 都要求两次写，首步置两个复位位，次步清一个或两个。ADDR_ASCENSION 在合格软复位后保留。 |
| 34，0x02 DEVICE_CONFIG | 表 11 及表 34 一致给出 `00` 正常、`10` 待机、`11` 睡眠。新桥拒绝 `01` 与非零保留位。原 bank 对非零模式拒绝并始终读回 0。 |
| 35–42，器件 ID 与 Scratch | 表 36 字段和 ADI no-OS 的 AD4080 产品 ID 低字节为 0x50，基线未绑定时拒绝读取。第 77 页寄存器标题给出 0x00，与表 36 字段不同，本次用表项与驱动共同值作验证绑定。 |
| 43–45，流模式与 CRC/严格访问 | 表 44 的 0x0F[2] 已实测普通及 CRC 流式读写、保留位拒绝和复位。0x0E 非零循环按 p.80 的不支持条款拒绝。正常 ASC/DESC、严格实体和后续 CRC 种子有独立回放。 |
| 46–47，0x11 接口状态与 0x14 设备状态 | 本次真实 SPI 触发部分写、越址、只读写、未就绪及 W1C。原 0x14 有数字/模拟 POR 初值和事件位，转换周期错误及 ROM CRC/ECC 位固定为 0。模拟掉参考事件由新桥接入。 |
| 48–51，数据接口与 LDO 开关 | 0x15–0x17 原实现只返回固定字段并拒绝写入。0x18 PWR_CTRL 不在原地址表。数据串行端、LDO 选择和供电隔离循各自接口合约。 |
| 52–55，GPIO 与事件使能 | 真实 SPI 可选择 HI/LO/ALERT 和低有效 FILTER_SYNC。表 53/54 指定 0xA 勿用，0xB–0xF 未列出功能。新桥在 0x1A/0x1B 对两字段同时校验，已列出但尚未绑定的 FILTER_RDY 仍由冻结 bank 拒绝。 |
| 56–59，水位、迟滞和两个阈值 | 严格模式一次提交完整实体。普通模式的水位逐字节夹限带来驱动兼容性反例。阈值及迟滞以 GPIO、状态及 W1C 定向用例验收。 |
| 60–62，OFFSET、GAIN、FILTER_CONFIG | OFFSET 低地址 0x25、高地址 0x26。GAIN 0x27/0x28，复位 unity 0x0200。逐码映射缺正式绑定时，非 unity 写入被拒绝。滤波数值与吞吐由独立数据链验证提供。 |

Rev. B 第 71 页把 OFFSET 写成 0x24/0x25。第 37、74、92 页与 ADI `AD4080_REG_OFFSET` 都指向 **0x25/0x26**，且 0x24 明确为 LO_THRESHOLD 高字节。严格下降地址从 0x26 发高、低字节。严格上升地址从 0x25 发低、高字节。测试中 LO 和 OFFSET 回读不同值，排除地址串写。第 71、93 页提供非 unity 的端点与增量，完整定点映射的工程绑定另行验收。[Rev. B 第 37–38 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=37) · [表 59–61](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=92) · [no-OS OFFSET/GAIN 地址](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L47-L52)

## 2. 冻结顶层真实引脚回放

[测试台](control_review_tb.sv)并排驱动原参数实例和控制参数已绑定的实例。两者均使用 Mode 3、长指令和真实 SDO 回读。测试绑定为 `P_MULTIBYTE_ORDER=0`、`P_OFFSET_LSB_ADDR=0x25`、`P_PRODUCT_ID_L=0x50`、`P_SHORT_ADDR_BITS=7`、`P_WRITE_SDO_DRIVE=1`。GAIN 和补偿模型保留原来源守卫。

| 项目 | 实测观测与裁定 |
| --- | --- |
| 默认写入 | 0x0A 写 0x5A。默认实例工程拒绝原因 **1**，读回 **0x00**，0x11 未附加厂家错误。显式绑定实例的写相 SDO 有效且为 0，读回 **0x5A**。 |
| 实体端序与拒绝 | 严格下降顺序写入并读回 OFFSET **0x0FED**，独立 LO_THRESHOLD **0x0345**。缺第二字节时值保留，0x11 为 **0x12**。写 0x02 清部分访问后仍有钟数错误 0x10，再单独清除。下降寻址误从 0x25 起步再置原因 5 与部分访问状态。切到上升地址后写入和读回 **0x037A**。 |
| no-OS 水位助手 | no-OS 的普通写助手先独立写低字节再写高字节。从默认水位 **0x4000** 用该顺序写 **0x01AA** 时，中间值 0x40AA 被截回 0x4000，低字节丢失，最终读回 **0x0100**。用严格 ASC 实体一次提交则读回 **0x01AA**。 |
| 非 unity | SPI 写 GAIN **0x0201**，得到原因 **1**，读回保留 **0x0200**，0x11 为 0。工程拒绝端可见。处理链没有接受该写值。 |
| 协议故障 | 越址、只读写及未就绪分别为原因 **3/4/8**。累计 0x11 为 **0x85**，长指令附带状态 **0x0025**。W1C 可选择性清成 0x81，再清为 0。工程原因需另行采集。 |
| 事件与再置位 | 配置高/低阈值 +16/−16、迟滞 2 后，两个事件累积状态 **0x30**。清 HI 后 **0x10**，继续高仍为 0x10，回窗再越上限到 **0x30**。W1C 清粘滞位，HI_DTCT 与 ALERT 继续反映检测电平。源码对粘滞位同沿清除优先，独立的触发脉冲仍由是否新越阈决定。 |
| HI_DTCT GPIO 差异 | 先超过 HI、再回到迟滞释放边界内。GPIO 的 HI_DTCT 已落低、ALERT 已释放、HI_STATUS 仍为 1，且未执行 W1C。与第 55 页 Fig. 83 所画 HI_DTCT 保持和 ALERT 释放的分叉不同。第 56 页 Fig. 84 用检测信号描述迟滞，需板级测量锁定对外语义。 |
| FILTER_RDY 与 GPIO | 选择 GPIO1 的功能 3 时原因 **2**，选择寄存器保留 **0x40**。FILTER_SYNC 作低电平保持，并强制输入脚释放 OE。另向 GPIO3 写保留编码 **0xA**，原 bank 接受并读回 0xA6，GPIO3 OE=0 且没有拒绝。表 54 要求勿用该选项。 |
| 复位与服务缺口 | 0x02 写待机 0x02 后读回 0，工程拒绝原因 2。0x14 的模拟、数字 POR 可独立 W1C，原顶层无独立参考监测事件输入。首次或第二次软复位写时 ADC 非空闲均被拒绝。合法两次写只产生一次内部 bank 脉冲，回读 POR=0x09 且 ADDR_ASC 保留。原顶层将该脉冲接到未使用信号。 |

ADI no-OS 的水位 helper 使用两个独立字节操作，运行它前需核查 strict 配置并读回完整水位。该软件还通过 `ad4080_update_bits` 依次置位和清除两个 SW_RESET 位，模式接口使用 0x02 的两位字段。[ADI no-OS 两次软复位写](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.c#L222-L236) · [ADI no-OS 模式写](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.c#L389-L406) · [ADI no-OS 水位写](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.c#L979-L996) · [Rev. B 第 53–56 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=53)

### 水位的原子写入与 arm 守卫

主机独占配置序列，确认 `STRICT_REGISTER_ACCESS` 已启用且 `FIFO_MODE=00`。ASC 从 0x1D 发低、高字节，DESC 从 0x1E 发高、低字节。完整的两字节写和读各占一个 CS 帧，使用已绑定的 SPI 帧格式。比较整个 16 位回读后才写 0x1C 的模式位，保留同寄存器其他字段并复核模式。写 0x1C 后读回失败时先按状态未知处理，待接口空闲并查询实际模式后再处理。[Rev. B 第 37–38 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=37) · [FIFO 水位 Table 56](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=91)

```python
def set_fifo_watermark_then_arm(spi, watermark, mode):
    if not 1 <= watermark <= 16384 or mode not in (1, 2, 3):
        raise ValueError("watermark or FIFO mode")
    if mode == 3 and watermark % 4:
        raise ValueError("FIFO mode 11 requires a multiple of four")
    with spi.exclusive_configuration():
        if not spi.read8(0x10) & 0x20:
            raise RuntimeError("strict entity access required")
        asc = bool(spi.read8(0x00) & 0x20)
        general = spi.read8(0x1C)
        if general & 0x03:
            raise RuntimeError("FIFO must be idle before programming")
        addr, order = (0x1D, "little") if asc else (0x1E, "big")
        payload = watermark.to_bytes(2, order)
        spi.write_entity(addr, payload)   # 两字节处于同一 CS 帧
        if spi.read_entity(addr, 2) != payload:
            raise RuntimeError("FIFO watermark readback mismatch")
        target = (general & 0xFC) | mode
        spi.write8(0x1C, target)
        if spi.read8(0x1C) != target:
            raise RuntimeError("FIFO arm state unknown")
```

`spi` 由主机接入工程提供，独占区应阻止其他写入者和帧格式切换。读接口须报告总线不可用、错误帧和短读。引脚回放中的等效流程实际检查低字节错误 **0x0100**、高字节错误 **0x02AA**、mode 11 粒度错误均未发非零 0x1C 写。ASC 和 DESC 原子访问均读回 **0x01AA**，随后实际写入并读回 `FIFO_MODE=01`。

## 3. 独立 AON 桥接与实测

[桥接测试台](power_bridge_tb.sv)从真实 SPI 引脚进入冻结 `spi_slave`。桥位于它的寄存器实体端与冻结 `ad4080_regs` 之间，并连接包内[电源监督器](../../power/ad4080_pwr_supervisor.sv)。执行、服务和返回使用同一个 100 MHz AON 测试时钟。外供模式下保持 IOVDD，监督器提供配置输入许可和 SDO 输出使能前的全访问许可。

| 接点 | 消费规则与实测 |
| --- | --- |
| `0x14`、`por_ana_event_i` | 原 bank 的其它位和写入路径继续使用。桥覆盖 0x14 bit3 与长状态 bit7。真实 REFIN 输入掉低后，原 bank 仍为 **0xA0**，SPI 读 **0xA8**，长状态 **0x2880**，保留原 FIFO/HI 位测试夹具和 Scratch。让 REFIN 事件与实际 SPI W1C 同沿到达，实测碰撞 **1** 次，事件优先。后续普通 W1C 可清。 |
| `0x02`、`mode_req/accept` | 该写入由桥转交服务。只有合规字段才发请求。服务拒绝时工程诊断给值非法 9 或不准入 8，读回来自监督器的已受理模式。引脚完成 **00→10→00→11→00**，并观察到模式先显示 00、转换许可仍关闭的恢复窗。保留码 01 和未排空请求均被阻断。 |
| `0x00`、`sw_reset_req/accept` | 桥镜像基线的一位待完成状态，仅完整首帧可进入。第二步由实际服务资格同沿裁决，并用 AON accept 门控原 bank 的 idle 判据。服务和原 bank 同一上升沿提交。两次写之间客户变忙时两方均未复位，Scratch 保留。重试后只受理一次，实际初始化后读回 Scratch 0、POR 0x09、ADDR_ASC=1。 |
| `0x1A/0x1B`、GPIO mux | 只拦截这两个地址的完整写入。0xA 禁用与 0xB–0xF 未定义选择均给桥诊断原因 2，原 bank 不接收该事务。**24** 个引脚用例遍历两个地址、两 nibble 位置和六个编码，每次两个字段均保持前值。编码 0x3 透传原 bank，仍按 FILTER_RDY 未绑定拒绝。 |

GPIO 的选择分配与 0xA 处理取自 Rev. B Table 53/54。[Rev. B 第 88–89 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=88)

桥的端口以 AON 同步事务为合同。`capture_incomplete_i`、`capture_io_active_i` 必须与原 bank 对应状态同源。`clients_idle_i` 必须代表转换、结果通道及数据接口已排空，配置 SPI 当前帧不计为客户忙。组合 `accept` 必须由实际电源监督器驱动。执行域不同时，先以可靠握手将完整事务移入同一裁决域。复位端镜像状态依赖冻结 bank 的阶段语义，更新 bank 时须重跑同沿一致性断言。

## 4. 关闭条件

| 待关闭项 | 释放所需证据 |
| --- | --- |
| 产品顶层与配置拒绝 | 从项目 SPI 管脚，经 pad 和 SPI 前端连接选定 bank 与新桥，并将 `cfg_reject` 接到工程故障采集。用同版参数回放初始化和读写，保留读回。 |
| 水位 | 产品主机部署严格原子事务和完整读回守卫。仍需普通模式双字节兼容时，由 CSR 所有者明确双字节写与夹限规则并补实测。 |
| 增益、滤波与 GPIO | 交付经确认的定点增益映射、GAIN=0 策略和补偿系数。FILTER_RDY 由实际 CNV、所选抽取及串行读取时序共同验收。将独立桥的 GPIO 整实体拒绝接入产品配置错误采集。 |
| 事件与状态 | 确定检测电平、粘滞状态、ALERT 和 FIFO 事件的责任。核查目标定义的 W1C 与越阈同沿、持续越阈后的再置位、回窗及实际 GPIO 延迟。 |
| 电源与复位 | 核对真实 REFIN/核心 POR、内供 IOVDD 下受限写及禁读、初始化期 0x11 提前读的规则和 pad 隔离。联归须覆盖未完成捕获、读取中及保留完成记录。 |
| 其它可见故障 | 由时序所有者绑定 ADC_CNV_ERR、非易失存储所有者绑定 ROM_CRC_ERR，完成真故障注入和状态追踪。0x18、串行数据 PHY 和流式 CRC 依各自验证启用。 |

## 5. 复跑

在交付根运行。需要可读的冻结提交、Python 3、Verilator、Clang C++20 与 Make。`COUNTEREXAMPLE` 行表示该基线问题已复放。

```sh
python3 -B interfaces/control_review/run.py \
  --repo /path/to/20bit_SAR_ADC_Behaviour_Verification \
  --scratch /path/to/ascii/scratch \
  --verilator /path/to/verilator
```

默认两套测试，可选 `--suite baseline` 或 `--suite bridge`。每次在 scratch 建唯一次目录，存储 `*.compile.log` 和 `*.simulate.log`。冻结提交或依赖缺失、构建或测试中止、完成标记缺失时退出非零。成功时输出 `CONTROL_REVIEW_COMPLETE` 和 `POWER_BRIDGE_COMPLETE`。[本轮执行记录](validation.txt)

## 6. 运行 ADI no-OS 的实际配置流程

报告 ID `CTRL-7408EDF-NOOS`。本测试把 ADI 固定提交的 `ad4080.c`、`ad4080.h` 和 `no_os_util.c` 原样编译，与 C++ 平台适配、独立 AON 薄桥和 **冻结的 SPI/CSR RTL** 链接。C 里的 `ad4080_init()` 以及各读改写 API 实际驱动长指令、Mode 3、无 CRC 的配置端。返回字节从 RTL 的 SDO/OE 取得，配置模型中寄存器状态由 RTL 保存。平台对无 CRC 读取的数据相发送零占位，并保留指令和写数据。在仿真中，工程拒绝脉冲映射为 `-EIO`。产品主机须以实际拒绝采集或完整回读判断写入结果。[ADI no-OS 固定源](https://github.com/analogdevicesinc/no-OS/tree/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080) · [完整 SPI 帧记录](noos_validation.txt)

| 实际 API 流程 | 观测 |
| --- | --- |
| `ad4080_init`、真实 ID 与初始化 | 读取 CHIP_TYPE **0x07**、产品 ID **0x0050**，执行官方两次软复位并等待真实 AON ready，设置 ASC、长指令和普通字节访问。表 36 字段及 ADI AD4080 产品 ID 一致支持低字节 0x50。第 77 页标题复位值的冲突已登记 |
| 固定数据接口的读改写 | 该 init 对 `0x15/16/17` 重写 **0x40/0x00/0x20**。测试启用 `P_ACK_FIXED_DATA_DEFAULT`，只接纳与当前固定值相同的完整单字节写。请求改变数据格式到 SPI 被拒绝，结果口能力探测返回 `-ENOTSUP` |
| 模式与滤波 | 原 API 写待机、读回 2，退出后读回 0，并经 AON 恢复窗再放行转换。原 `ad4080_update_bits` 将 SINC1、16 倍抽取写入 0x29，读回 **0x19**。无补偿来源的 SINC5+ 请求拒绝，读回保持 0x19 |
| GPIO 定义冲突 | 固定 AD408x 公用头将 0xA 命名为 `AD4080_GPIO_CNV_INHIBIT_INPUT`，AD4080 Rev. B 表 53/54 标为勿用。AD4080 配方以器件表为准。实际 `ad4080_set_gpio_output_func` 请求 0xA，桥报原因 2，0x1A 保持原值 |
| 水位红/绿 | 未改厂商 helper 从 **0x4000** 写 **0x01AA** 后读 **0x0100**。兼容配方从 0x4000 写 0x01AA 完整读回后实际 arm，另覆盖 0x0001、0x0100、0x4000、ASC/DESC。已 arm 和模式 11 粒度错误分别在主机预检时拒绝 |
| 保留严格协议 | 官方 API 使能 strict。其普通单字节 helper 在严格模式触发原因 5。完整 DESC 实体帧仍可从 0x1E 写高/低字节、复读 **0x01AA** |

测试中的固定数据配置只含公开默认字段。`P_ACK_FIXED_DATA_DEFAULT` 默认关闭，产品启用时应以实际选用的数据配置为依据。当前夹具的电源、结果和捕获输入用于验证配置控制，数据 PHY、LVDS 电气、实际流量与水位联动须由产品绑定后联验。[Rev. B 表 35–36](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=77) · [Rev. B 表 48–50](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84) · [Rev. B 表 53–54](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=88) · [ADI AD408x GPIO 枚举](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L244-L257)

### 使用原厂 API 的普通水位配方

在独占配置、确认在途读取已结束且记录可覆盖后，检查 `FIFO_MODE=00`、`STRICT=0`。使用原 `ad4080_write` 把水位高字节 0x1E 预置为 **0x01**。随后调用原 `ad4080_set_fifo_watermark`，它仍先写低字节再写目标高字节。这样两次实际写之间的值处于 0x0100–0x01FF，避开冻结 bank 逐笔对零和超量程值的夹限。用原 `ad4080_get_fifo_watermark` 比较完整值，匹配后才写并读回 FIFO 模式。该流程需锁住其他配置写和 arm。产品可直接编译 [C 配方](noos_host_recipe.c) 的 `project_ad4080_set_fifo_checked`，省略 `AD4080_NOOS_SIM` 即只保留配方。需要一帧原子提交的主机继续使用本指南的严格实体方式。[ADI 官方 helper](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.c#L979-L1026)

### 构建与复跑

厂商 C 和头文件由脚本校验 SHA-256 后临时解包，发布包保留本地封装、入口和测试记录。进交付根执行。用支持 Clang C++20 的 Verilator，以及位于保护仓外的独立 scratch。

```sh
noos_scratch="$(mktemp -d)"
curl --fail --location --retry 3 \
  'https://codeload.github.com/analogdevicesinc/no-OS/tar.gz/0e6ed1dea135ef541ad6323fc9fcad116ffce90b' \
  --output "$noos_scratch/adi-noos.tar.gz"
python3 -B interfaces/control_review/run_noos.py \
  --repo /path/to/20bit_SAR_ADC_Behaviour_Verification \
  --adi-archive "$noos_scratch/adi-noos.tar.gz" \
  --scratch "$noos_scratch" --verilator /path/to/verilator
```

输出应含 `NOOS_HOST_COMPLETE` 和 `NOOS_SPI_RTL_COMPLETE`。各 `SPI R/W` 行记录发送字节、SDO 响应与实际拒绝原因。归档缺失、源文件不符、编译失败或少专项记录时退出非零。[本次编译与帧记录](noos_validation.txt)


## 7. 配置 SPI GPIO0 引脚逐沿检查

`cfg_spi_pin_edges_tb.sv` 用真实 `ad4080_top` 和实际 GPIO 复用。主机只驱动 CS、SCLK、SDI，并从默认选择的 **GPIO0 SDO/OE** 取样。P_WRITE_SDO_DRIVE=1（普通写数据相驱动零）、7 位地址能力、严格端序、OFFSET 与 ID 只在本台架按已知配置绑定。对应 Rev. B p33–35 Fig.53/54 和 p43–45 Fig.67–73。真实产品的参数、权限和 pad 逻辑由产品入口另行验收。[配置接口原件](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=34) · [读写引脚时序](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=43)

### 模型与边界

- Table 2 的配置接口 `tSCK=20 ns`，每个高/低相至少周期的 45%。夹具执行时钟 **2.5 ns**，测试周期 **20/24/32 ns**，低/高相分别为 **9/11、12/12、17.6/14.4 ns**。每种周期扫 5 个相位、各做一次写和一次读，代表有 `clk≥8×SCLK` 的数字过采样配置。
- 每个已驱动的上一位在新降沿 **+0.7 ns** 核对保持。新一位在降沿 **+8 ns** 和随后的实际采样上升沿核对 GPIO0 SDO/OE。未驱动时 CSV 的 SDO 值应结合 OE 掩码读取。CS 为高时继续摆动 SCLK，核对 OE 释放，收帧后另在 CS↑ **+8 ns** 核对模型的数字退场。
- Fig.67/70 的 `tCSSCK` 和 `tSCKCS` 箭头涉及首末采样上升沿。Table 2 给 0 ns 最小边界。测试分别执行 **CS↓同首采样↑**（关闭 SEND_STATUS）、**CS↑同末采样↑**，以及 **CS↓同首降沿**的附带状态读取。此 RTL 的 SDI 从原始输入采样，夹具保持 SDI 到下一降沿。Table 2 的真实 `tSSDI=1 ns`、`tHSDI=0 ns`、`tHSDO=0.7 ns`、`tDSDO=14.5 ns` 及 pad 三态传播要在目标实现上闭合。2.5 ns 内部时钟、数字同时推进和模拟相位扫描无法验双同步器的亚稳态，也不构成 50 MHz 外部 pad 的 AC 签核。[Rev. B Table 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7)

### 红绿结果

| 定向场景 | 引脚或提交期望 | 冻结 `8294988` | 修复候选 |
| --- | --- | --- | --- |
| CS↑与末个 SCLK↑同拍，写 SCRATCH 0xC7 | 数据相 SDO 0，OE 持续；写受理，无原因脉冲，后读 C7 | 写相 OE 正常，但报钟数原因 7，回读仍 9D | 写受理，读回 **C7**，原因 0 |
| CS↓与首个 SCLK↑同拍，写 SCRATCH 0x69 | 接收首指令位，8 个数据位 OE 有效，随后读 69 | 数据相 OE 掩码 7F（期望 FF），原因 7，读回旧 A5 | 8 位数据相 OE=FF，读回 **69**，原因 0 |
| CS↓与首个 SCLK↓同拍，读取 SEND_STATUS | 16 位附带状态 00C0，再读 Scratch A5 | 状态 0000，仍读 A5 | 引脚输出 **00C0 A5** |
| 普通字节发 4 位后结束 | 原 Scratch 保留，原因 7 | 9D 保留 | **9D**，原因 7 |
| 严格双字节只发首字节后结束 | 原 OFFSET 保留，原因 5 | 0134 保留 | **0134**，原因 5 |
| 启用 SEND_STATUS 与 CRC，长帧 | 写回 00C0/00/56，读回 00C0/C6/5D。错误校验只报 CRC | 已通过 | 同样通过。错 CRC 原因 6，读回仍 **C6** |
| 短地址、连续多实体、同 CS 下一条单指令 | GPIO0 逐位回读等于目标，普通写数据相为 0 | 已通过 | 同样通过。短流读 **00 0200 0134**，单指令多命令读 **9D 07** |

该轮边沿修复位于 `spi_slave.sv`。末位 SCLK↑ 与 CS↑ 来自同一采样拍时先采样并释放 OE，下一拍按更新后的状态收帧或记错。首位 SCLK↑ 与 CS↓ 同拍时收首指令位并推进初始 CRC。首降沿与 CS↓ 同拍时预装附带状态。最终冻结提交另通过配置 CRC 首实体和后续流实体、写中止重试及短指令回绕，以及软初始化 0x11 状态读。初始化忙态从 0x80 回到 0x00，非法访问维持粘滞，初始化完成后可用 W1C 清除。

### 复跑与证据

从外围子系统目录执行。命令按 `evidence/dsp-revision.txt` 从 Git 对象提取冻结 DSP，输出每个实际编译输入的摘要。复查历史时可用 `--revision` 指定完整提交，`--spi-candidate` 可覆盖该实验的单个 SPI 叶子。

```sh
python3 -B interfaces/control_review/run_pin_edges.py \
  --repo /path/to/peripherals-worktree --scratch /path/to/external/scratch \
  --lvds-profile --crc-stream --init-poll \
  --verilator /path/to/verilator
```

冻结 DSP 的联合回归通过 **166 帧、17,329 次检查、零失败**。逐帧 CSV 的 `2ad7b06_frozen_combined` 是当前结果，`fall_*` 与 `rise_*` 按先到的位在高位打包。`expect_oe` 为逐位有效掩码，CS 收尾释放的目标 OE=0。采用同一冻结提交的真 SPI/AON 薄桥及原版 no-OS C 回执已复跑。产品顶层另加入 50 MHz 时钟采样沿后 1 ps 改值的用例。[逐帧 CSV](pin_edges_validation.csv) · [配置引脚回执](pin_edges_validation.txt) · [控制面回执](validation.txt) · [原版 no-OS 回执](noos_validation.txt) · [产品速率回执](../../integration/product/evidence/at-speed.txt)


### 连续 LVDS 配置的引脚回读

同一台架使用 `--lvds-profile` 启用新版 DSP 的 `P_CONTINUOUS_LVDS_BOUND`。该组额外 **25 帧**仍只由 GPIO0 配置 SPI 对拍。夹具用可控 `data_config_ready_i` 验证在途配置守卫及已受理 A/B。实际 LVDS 时钟、lane 对齐和 Pad 电气由产品台架及后端验收。

| 事务 | 期望及实测 |
| --- | --- |
| 单 lane 写测试码和 self | 0x15/0x16 回读 **0x50/0x58**。0x16 高字段为码 5，Table 20 单 lane 的时钟序号 8 |
| 切 lane 与非法码 | 码 5 时直接切双 lane 拒绝（原因 9）。单 lane 码 9 合法，码 0xA 拒绝。双 lane 档的码 5 也拒绝，原 B 保留 |
| 先设合法公用档后切双 lane | 0x15/0x16 回读及真实配置端口为 **0x54/0x28**。0x16 高字段码 2 是双 lane 时钟序号 5 |
| 保留位 | 0x15 缺 bit6 或请求未绑定的数据 SPI、0x16 请求未绑定 Manchester，均报原因 2，A/B 保持 |
| `data_config_ready_i=0` | 试图改变 A 或 B 均报原因 8，A/B 仍为 **0x54/0x28** |

跑新版统一提交时传入 `--revision <新完整 SHA> --lvds-profile`。工作树联调期间可改用 `--dsp-candidate-dir <DSP RTL 目录> --lvds-profile`，同一 scratch 一次性复制全部 DUT 叶子并列出真实编译摘要。[Rev. B p49 Table 20](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=49) · [寄存器表 48/49](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84)

新版 bank 对 GPIO 0xA 自身在两个地址、两个 nibble 均原子拒绝。原 `ad4080_regs_rejection_tb` 通过 48 项检查。控制台架真实 SPI 对 **0xA7／0x7A** 均得到原因 2，0x1B 完整读回 **0x86**，桥的 24 个独立负例仍通过。[控制回执](validation.txt)
