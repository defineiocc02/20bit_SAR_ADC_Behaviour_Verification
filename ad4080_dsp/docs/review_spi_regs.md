# AD4080 SPI 接口与配置寄存器 RTL 审查报告

- 审查对象：`ad4080_rtl/rtl/spi_slave.sv`、`ad4080_rtl/rtl/ad4080_regs.sv`；为确认信号来源/去向，交叉查阅 `ad4080_top.sv`、`gpio_ctrl.sv`、`event_detect.sv`、`ad4080_dsp_chain.sv`、`sinc1_decim.sv`、`sinc5_decim.sv`、`sinc5_comp.sv`。
- 手册依据：AD4080 Data Sheet Rev. B（纯文本 `/tmp/ad4080.txt`，原始 PDF `/Users/zhaoyi/Zotero/storage/E6WK8CIC/ad4080.pdf`）。文中页码均为页脚 `Rev. B | xx of 95`，括号内同时给出 `/tmp/ad4080.txt` 行号便于复核；图表结论以 PDF 渲染页为准。
- 方式：手册逐条比对 + 位宽/帧时序推演。**未修改任何 RTL 文件**，只给结论与可落地改法。
- 等级定义：**P0** = 会导致功能错误（数据错、寄存器写不进/读不对）；**P1** = 协议或寄存器映射不兼容（符合手册的主机无法正常驱动）；**P2** = 健壮性/可维护性/锦上添花。
- `[待确认]` 表示手册未写明或手册自身矛盾，需上游确认；未标注的结论均可由所引原文直接支撑。

---

## 0. 结论表

| 编号 | 手册要求 | RTL 现状 | 差异等级 | 需要的代码改动（一句话） |
|---|---|---|---|---|
| S-01 | SPI Mode 3：CPOL=1/CPHA=1，SCLK 空闲高，数据下降沿更新、上升沿采样（p.34） | 空闲高、`sck_rise` 采样 SDI、`sck_fall` 更新 SDO（`spi_slave.sv:46-48,88,106`） | 符合 | 无需改动 |
| S-02 | 指令相 16 bit（1 方向位 + 15 地址），数据相 ≥1 字节且默认 streaming 可连续多字节（p.35-36） | `bitcnt` 到 24 即停，固定收/发 1 字节，后续位全部丢弃（`spi_slave.sv:96-103`） | **P0** | 改为「字节计数器 + 地址指针」状态机，支持一帧 N 字节 |
| S-03 | 方向位：1=读，0=写（p.35） | `cmd<=instr_nxt[15]`，`we_r<=~cmd`（`spi_slave.sv:93,101`） | 符合 | 保持 |
| S-04 | `SHORT_INSTRUCTION=1` 时地址缩为 7 bit，指令相 8 bit（p.35、p.76） | 固定 16 bit 指令，无该位 | 缺失（P1） | 指令长度参数化，按 `0x01[3]` 选择 8/16 |
| S-05 | `ADDR_ASCENSION` 默认 0（递减），1 递增；0x0000↔0x7FFF 回绕（p.36-37） | 无地址计数器，地址只用指令里那一个 | 缺失（P1） | 增加 `addr_ptr` 与回绕逻辑 |
| S-06 | 多字节字节序：默认 MSB 先、LSB 后；递增模式 LSB 先（p.38 Fig.58/59） | 未实现多字节，字节序无从谈起 | **P0** | 与 S-02 同改，按 `addr_asc` 选首字节 |
| S-07 | `STRICT_REGISTER_ACCESS` 复位=1；多字节部分访问报错并丢弃（p.37、p.81） | 逐字节独立写，无 strict 概念、无部分访问检测 | 缺失（P1） | 加字节计数校验与 `REGISTER_PARTIAL_ACCESS_ERR` |
| S-08 | `SINGLE_INST` 复位=0（streaming 为默认）（p.35、p.76） | 无该位；实际行为是「单指令 + 单字节」的混合体 | 缺失（P1） | 实现 `0x01[7]` 并区分两种寻址模式 |
| S-09 | 写数据相 SDO 应驱动 0（p.35） | `sdo_oe=cmd`，写操作时 SDO 高阻（`spi_slave.sv:110`） | 部分（P2） | 写时 `oe=1, sdo=0` |
| S-10 | `tSCK` 最小 20 ns（≤50 MHz）（p.7 Table 2） | 注释要求 `clk ≥ 8×SCLK`（`spi_slave.sv:11`） | 符合 | 保持，建议加参数断言 |
| C-01 | CRC-8：x^8+x^2+x+1，种子 0xA5，覆盖指令相+数据相（p.40-41） | 完全没有 CRC 引擎 | 缺失（P1） | 加 bit-serial CRC-8，按 SCLK 边沿喂入 |
| C-02 | `CRC_ENABLE=01` 且 `CRC_ENABLEB=10` 同时写入才使能；其余组合关闭（p.40、p.81） | 无 `0x10` 寄存器 | 缺失（P1） | 实现 `0x10` 并按互补对判定 |
| C-03 | 校验失败：置 `0x11[3]` CRC_ERR（W1C，写 0x08 清除）；写操作作废本次及后续数据（p.40） | 无错误状态、无屏蔽 | 缺失（P1） | 加 `crc_err` 粘滞位 + 写使能门控 |
| C-04 | CRC 使能后每寄存器数据后追加 8 bit CRC；读时器件也回 CRC（p.41、p.42 Fig.66） | 帧长固定 24 bit，CRC 字节被当作越界位丢弃 | 缺失（P1） | 帧长 +8，增加 CRC 收发相 |
| C-05 | 事务内时钟数错误应置 `CLOCK_COUNT_ERR`（p.82） | 无检测，CS 提前拉高静默丢弃 | 缺失（P2） | 帧尾比对 `bitcnt` 是否落在 8 bit 边界 |
| G-01 | `0x14` 位序：bit7 FIFO_FULL / bit6 FIFO_READ_DONE / bit5 HI / bit4 LO / bit3 POR_ANA / bit2 ADC_CNV_ERR / bit1 ROM_CRC_ERR / bit0 POR_FLAG（p.83） | 顶层把 `{fifo_full,fifo_empty,fifo_ovf,fifo_unf,clip_low,clip_high,captured,chain_valid}` 直接当状态字节（`ad4080_top.sv:123`） | **P0** | 按表重排，接入真实状态源 |
| G-02 | `0x14` 的 bit5/4/3/2/0 为 W1C，bit7/6/1 只读（p.83） | `0x14` 无写分支，只读直通（`ad4080_regs.sv:105`） | 缺失（P1） | 实现 W1C 与只读位保护 |
| G-03 | `0x14` 复位值 0x09（POR_ANA_FLAG + POR_FLAG = 1）（p.83、p.74） | 复位值由顶层信号决定，不是 0x09 | 错误（P1） | 寄存器内部 `status_r <= 8'h09` |
| G-04 | `SEND_STATUS=1` 时指令相由 SDO 回传 16 bit（长指令）/8 bit（短指令）状态，MSB 先（p.39-40） | 未实现；指令相 `oe_r=0`，SDO 高阻 | 缺失（P1） | 加状态字并串移位器 |
| G-05 | `0x1C`：[7] INT_EVENT_EN、[6] HI_ROUTE、[5] LO_ROUTE、[4] ADC_CNV_ERR_ROUTE、[3:2] 保留、[1:0] FIFO_MODE（p.90） | 只存 bit7 与 [1:0]，[6:4] 写被丢弃、读恒 0（`ad4080_regs.sv:84,111`） | 错误（P1） | 补齐 3 个 route 位并读出 |
| G-06 | GPIO_x_SEL 为 **4 bit**，编码 0000…1001（1010 禁用）（p.88-89） | 只有 3 bit，bit3 被丢弃；1001 混叠成 0001（`ad4080_regs.sv:82-83,109-110`、`gpio_ctrl.sv:19,27-28`） | 部分（P1） | 扩到 4 bit 并按手册功能表重排 |
| G-07 | `0x19` 复位 0x01（GPIO0 输出使能，SDO 默认从 GPIO0 出）（p.87） | 复位 0x00（`ad4080_regs.sv:75`） | 部分（P2） | 改复位值 |
| M-01 | `FIFO_WATERMARK` 复位 0x4000（p.91、p.74） | 复位 0（`ad4080_regs.sv:68`） | 错误（P2） | 改 `15'h4000` |
| M-02 | 写 <1 钳到 1，>16384 钳到 16384（p.91） | 无钳位 | 缺失（P1） | 写入时按 16-bit 原始值钳位 |
| M-03 | HI/LO_THRESHOLD 为 12-bit **二进制补码**，LSB=1.46484 mV（p.92） | `hi_r/lo_r` 与输出端口都是无符号；下游按无符号比较（`ad4080_regs.sv:56`、`event_detect.sv:33,46,50`） | **P0** | 全链路改 `signed` 并按补码比较 |
| M-04 | HYSTERESIS 为 11-bit **无符号**，LSB=1.46484 mV（p.91） | 11 bit 无符号，读回零扩展正确（`ad4080_regs.sv:55,88,115`） | 符合 | 保持 |
| M-05 | OFFSET 12-bit 补码、GAIN 10-bit 复位 0x200（p.92-93） | `offset_code` 为 signed，GAIN 复位 0x200（`ad4080_regs.sv:64,31`） | 符合 | 建议内部 `offset_r` 也声明 signed |
| M-06 | `0x29`：[6:3] SINC_DEC_RATE、[1:0] FILTER_SEL，复位 0（p.94） | 字段位置与读回正确（`ad4080_regs.sv:97,124`） | 符合 | 保持 |
| M-07 | SINC_DEC_RATE 需解码为 N=2^(code+1) 并驱动滤波器（p.94、p.66） | `dec_rate` 在顶层**未连接**：`dsp_chain` 的端口叫 `dec_sel`，实例化时漏接（`ad4080_top.sv:88-97`、`ad4080_dsp_chain.sv:34`） | **P0** | 补 `.dec_sel(dec_rate)` |
| M-08 | 非法 SINC_DEC_RATE 码落到该滤波器最大抽取率（p.94） | 滤波器内部已钳位（`sinc1_decim.sv:41-47`、`sinc5_decim.sv:46-52`） | 符合 | 保持（接线后才生效） |
| M-09 | 改 FILTER_SEL 后必须由 FILTER_SYNC 复位滤波器（p.64、p.94） | 写 `0x29` 不产生任何同步脉冲 | 缺失（P1） | 写 `0x29` 产生单拍 `filter_sync` |
| M-10 | `0x16` bit0=LVDS_CNV_EN、bit1 保留；lane 选择在 `0x15[2]`；`0x16` 复位 0x00（p.84-85） | 把 bit1 当 `LANE_SEL`，且复位成 0x03（`ad4080_regs.sv:73-74,80,106`） | 错误（P1） | 删掉 bit1 解释，lane 移到 `0x15`，复位改 0 |
| M-11 | `0x00-0x11` 为接口配置/ID 区，`0x15/0x17` 也存在（p.73-82） | 全部缺失，读回 0；SW_RESET/ADDR_ASCENSION/SINGLE_INST/SHORT_INSTRUCTION/CRC/STRICT/SEND_STATUS 均不可配 | 缺失（P1） | 至少实现只读 ID 与 `0x00/0x01/0x10/0x11` |

> 统计：**P0 = 5 条**（S-02、S-06、G-01、M-03、M-07），**P1 = 17 条**，**P2 = 4 条**，**符合 = 7 条**。

---

## 1. SPI 接口协议

### 1.1 S-01 SPI 模式（符合）

**手册依据（p.34，`/tmp/ad4080.txt:1662-1667`）**

> "The configuration interface timing convention implemented in this design is consistent with SPI Mode 3, clock polarity (CPOL) = 1, clock phrase (CPHA) = 1. As such, the serial clock (SCLK) is expected to idle high, and the state of the data pins, SDI and SDO, are updated on the falling (leading) edge of the clock, such that these pins can be sampled on the subsequent rising (trailing) edge."

**RTL 依据（`spi_slave.sv:35-48, 88, 106`）**

```systemverilog
wire cs_fall  = cs_s1 & ~cs_s0;
wire sck_rise = sck_s1 & ~sck_p;   // 上升沿采样 SDI
wire sck_fall = ~sck_s1 & sck_p;   // 下降沿更新 SDO
```

**结论**：CPOL/CPHA 语义正确；`sdo` 在 `sck_fall` 装载、主机在后续 `sck_rise` 采样，与 Mode 3 一致。

**修改建议**：无需改动。仅建议把 `clk ≥ 8×SCLK` 从注释升级为断言：

```systemverilog
// 仿真期检查：SCLK 高/低电平至少覆盖 3 个 clk，保证 2FF 同步+边沿检测可靠
property p_sck_min_pulse;
    @(posedge clk) disable iff (!rst_n)
    $rose(sclk) |-> ##[1:3] $stable(sclk);
endproperty
```

### 1.2 S-02 帧结构：固定 1 字节，多字节帧被截断（P0）

**手册依据（p.35，`/tmp/ad4080.txt:1732-1742`）**

> "Each instruction phase is immediately followed by an associated data phase... The minimum size of the data payload is defined as a single byte; however, it can include multiple bytes depending on the depth of the register addressed and the interface configuration settings for the SINGLE_INST and STRICT_REGISTER_ACCESS bits."

**手册依据（p.35，`/tmp/ad4080.txt:1792-1804`）**

> "The configuration interface memory controller defaults to streaming mode upon power up (SINGLE_INST = 0). In streaming mode, multiple, contiguous registers are accessed in a single SPI frame, starting at the address specified in the instruction phase... For each byte transferred during the subsequent data phase, the internal address counter is automatically updated according to the setting of the ADDR_ASCENSION bit."

**RTL 依据（`spi_slave.sv:88-104`）**

```systemverilog
if (sck_rise) begin
    if (bitcnt < 6'd16) begin
        instr  <= instr_nxt;
        bitcnt <= bitcnt + 1'b1;
        ...
    end else if (bitcnt < 6'd24) begin
        shin   <= shin_nxt;
        bitcnt <= bitcnt + 1'b1;
        if (bitcnt == 6'd23) begin
            wdata <= shin_nxt;
            we_r  <= ~cmd;
        end
    end
end
```

`bitcnt == 24` 之后没有任何分支，第 25 个 SCLK 起 SDI 被忽略、SDO 停在上一位。

**差异分析**

1. 数据相只处理 **1 字节**。所有 2 字节寄存器（`0x1D/0x1E`、`0x1F/0x20`、`0x21/0x22`、`0x23/0x24`、`0x25/0x26`、`0x27/0x28`）无法用一帧写全。
2. 这是实际功能错误，不只是"少个特性"：按手册默认配置，主机会在一帧里发「指令 + MSB + LSB」。RTL 在第 24 个上升沿把**第一个数据字节**（MSB）写进高字节寄存器，第 2 个字节被丢弃，低字节保持旧值。以 WATERMARK 默认 0x4000 为例，写 1000 会变成 0x7F00（高字节被覆盖、低字节仍为 0），FIFO 触发点完全错。
3. 默认 `SINGLE_INST=0`（streaming），连续多字节是**默认**行为，不是可选扩展。

**修改建议**

把 `bitcnt` 语义改为「帧内位计数 + 字节边界」，并增加地址指针。最小改动骨架：

```systemverilog
// 新增端口（由 ad4080_regs 提供配置）
input  logic        cfg_single_inst,  // 0x01[7], 复位 0
input  logic        cfg_short_inst,   // 0x01[3], 复位 0
input  logic        cfg_addr_asc,     // 0x00[5], 复位 0

localparam int      AW_MAX = 15;

logic [5:0]  bitcnt;      // 帧内位计数
logic [5:0]  iw;          // 指令位数：8 或 16
logic [15:0] addr_ptr;    // 当前字节地址
logic [2:0]  byte_bit;    // 数据相字节内位计数 0..7
logic [7:0]  shin;

assign iw = cfg_short_inst ? 6'd8 : 6'd16;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        ...
    end else if (cs_fall) begin
        bitcnt   <= '0;
        byte_bit <= '0;
        addr_ptr <= '0;
    end else if (!cs_s1) begin
        if (sck_rise) begin
            // ---- 指令相 ----
            if (bitcnt < iw) begin
                instr  <= {instr[14:0], sdi};
                bitcnt <= bitcnt + 1'b1;
                if (bitcnt == iw - 6'd1) begin
                    cmd      <= cfg_short_inst ? instr_nxt[7]  : instr_nxt[15];
                    addr_ptr <= cfg_short_inst ? {8'h00, instr_nxt[6:0]}
                                               : instr_nxt[14:0];
                end
            end
            // ---- 数据相：每 8 个上升沿构成 1 字节 ----
            else begin
                shin     <= {shin[6:0], sdi};
                byte_bit <= byte_bit + 1'b1;
                bitcnt   <= bitcnt + 1'b1;
                if (byte_bit == 3'd7) begin
                    wdata    <= {shin[6:0], sdi};   // 当前字节
                    we       <= ~cmd;
                    byte_bit <= 3'd0;
                    if (!cfg_single_inst)           // streaming：地址自动步进
                        addr_ptr <= cfg_addr_asc ? addr_ptr + 16'd1
                                                 : addr_ptr - 16'd1;
                end
            end
        end
        // 读：每个字节起始的下降沿装载新的 rdata（见 S-06）
    end
end
```

注意：`addr` 输出应改为 `addr_ptr` 的寄存副本，读数据相在每个字节第一个 `sck_fall` 重新装载 `shout`，否则第 2 字节仍输出第 1 字节内容。

### 1.3 S-03 方向位（符合）

**手册依据（p.35，`/tmp/ad4080.txt:1724-1726`）**

> "...starts with a single command bit indicating the operation type (Logic 1 for read, and Logic 0 for write) that is then followed by the start address for the operation."

**RTL 依据（`spi_slave.sv:93,101`）**

```systemverilog
cmd  <= instr_nxt[15];
...
we_r <= ~cmd;
```

**结论**：SDI 先收 MSB，`instr_nxt[15]` 正是首比特，方向解析正确。读时 `sdo_oe=cmd`（`spi_slave.sv:110`），写时不驱动，方向语义正确。

### 1.4 S-04 短指令模式缺失（P1）

**手册依据（p.35，`/tmp/ad4080.txt:1726-1731`）**

> "By default, the address is 15 bits long, but the data interface has an optional short instruction mode that reduces it to 7 bits. The short instruction mode is enabled by setting the SHORT_INSTRUCTION bit = 1 in the Interface Configuration B register (Address 0x01)."

**手册依据（p.76 Table 33）**

> "[3] SHORT_INSTRUCTION Set the Instruction Phase Address to 7 or 15 bits. 0: 15-Bit Addressing. 1: 7-Bit Addressing."

**RTL 依据**：`spi_slave.sv:89-95` 硬编码 `bitcnt < 16`、`addr <= instr_nxt[14:0]`，无 8 bit 模式。

**差异**：主机若按短指令模式发 8+8 bit 帧，RTL 会把数据字节的前 8 位当地址低 8 位、把后续位当数据，解析完全错位。

**修改建议**：见 1.2 的 `iw`/`cfg_short_inst`；并补 `0x01` 寄存器（见 M-11）。

### 1.5 S-05 ADDR_ASCENSION 与地址回绕缺失（P1）

**手册依据（p.36 Table 15，`/tmp/ad4080.txt:1810-1818`）**

> "0 (Default) Decrement Address. Multibyte registers are accessed by addressing the most significant byte address.
> 1 Increment Address. Multibyte registers are accessed by addressing the least significant byte address."

**手册依据（p.37，`/tmp/ad4080.txt:1870-1892`）**

> "...resulting in the address pointer decrementing by one for each data byte transmitted... until the counter reaches Address 0x0000. If additional bytes are received, the pointer automatically rolls over to the maximum address value, 0x7FFF...
> ...the address pointer incrementing by one... until the maximum address value, 0x7FFF, is reached, after which the pointer rolls over to 0x0000."

**RTL 依据**：无 `addr_ptr`，`addr` 在 16 个上升沿后固定（`spi_slave.sv:94`）。

**修改建议**：

```systemverilog
// 递减：0x0000 再减 → 0x7FFF；递增：0x7FFF 再加 → 0x0000
wire [14:0] addr_lo = addr_ptr[14:0];
wire [14:0] addr_nxt = cfg_addr_asc
                     ? (addr_lo == 15'h7FFF ? 15'h0000 : addr_lo + 15'd1)
                     : (addr_lo == 15'h0000 ? 15'h7FFF : addr_lo - 15'd1);
```

### 1.6 S-06 多字节字节序（P0）——手册正文与图表矛盾，需按图表实现

**手册依据（p.38 正文，`/tmp/ad4080.txt:1950-1955`）**

> "As indicated in Figure 58, the address counter, by default, automatically decrements (ADDR_ASCENSION = 0) such that the most significant byte of the multibyte register is accessed first, followed by the remaining byte(s) in that register in ascending order. Conversely, if ADDR_ASCENSION = 1, the least significant byte of the multibyte register is accessed first followed by most significant byte."

**手册依据（p.36 Table 15 与 p.38 Fig.58/59）**

> Table 15：ADDR_ASCENSION=0 → "Multibyte registers are accessed by addressing the most significant byte address"。
> Fig.58（ADDR_ASCENSION=0，STRICT=1）：指令地址 = `MSBYTE ADDRESS`；数据相 WRITE = `MSBYTE DATA` → `LSBYTE DATA`（地址 `ADDR` → `ADDR−1`）。
> Fig.59（ADDR_ASCENSION=1，STRICT=1）：指令地址 = `LSBYTE ADDRESS`；数据相 = `LSBYTE DATA` → `MSBYTE DATA`（地址 `ADDR` → `ADDR+1`）。

**手册矛盾点 `[待确认]`（p.38，`/tmp/ad4080.txt:1956-1965`）**

> "In the case that the address counter automatically decrements (ADDR_ASCENSION = 0), the starting address is assigned to the register address for the least significant byte of that multibyte register, and conversely, if configured to increment automatically, the starting address must be set to the register address for the most significant byte."

这段与 Table 15、Fig.58/59 **相反**。此外 p.36（`/tmp/ad4080.txt:1823-1828`）还有一句「the most significant byte of the multibyte register resides in the least significant address of the register segment」，同样与 Fig.57（`MSB DATA` 画在 `MSB ADDR`）相反。**Table 15 + Fig.57/58/59 三者互相一致，且与「MSB 字节放在高地址」的常规约定自洽，判断 p.36/p.38 两处正文为手册笔误**。本报告以图表为准，但该处必须由上游确认。

**RTL 依据**：`spi_slave.sv:96-103` 只处理 1 字节，`ad4080_regs.sv:85-96` 把每个字节当作独立地址的独立寄存器（0x1D 写低字节、0x1E 写高字节），没有「一帧两字节、字节序随 ADDR_ASCENSION 变化」的概念。

**差异**

- 手册默认模式（ASC=0）下，WATERMARK 的合法帧是：指令地址 `0x1E`，数据 `MSB` 再 `LSB`。
- 当前 RTL 收到这帧时：`0x1E` 写入 `wm_r[14:8] <= MSB`，第二个字节丢弃 → 低字节不更新。
- 若主机改用「两帧分别写 0x1D、0x1E」，在真实器件上又违反 `STRICT_REGISTER_ACCESS=1`（每次只写半个寄存器），会置 `REGISTER_PARTIAL_ACCESS_ERR` 并丢弃。即：**两种主机写法在当前 RTL 与真实器件之间无法同时兼容**。

**修改建议（与 S-02 合并实现）**

```systemverilog
// 指令相锁存的是"起始字节地址"，数据相按 addr_asc 决定字节序
// ASC=0：起始=高字节地址，先 MSB 后 LSB
// ASC=1：起始=低字节地址，先 LSB 后 MSB
// 对 2 字节寄存器，寄存器文件按 addr_ptr 逐字节写入即可，
// 不需要在 spi_slave 里区分 MSB/LSB —— 字节序由"起始地址+指针方向"天然决定。
```

对应寄存器文件侧保留「按地址逐字节写」的实现即可（`ad4080_regs.sv:85-96` 的结构是对的），前提是 SPI 把两个字节都送进来、且地址指针方向正确。

### 1.7 S-07 STRICT_REGISTER_ACCESS 缺失（P1）

**手册依据（p.37，`/tmp/ad4080.txt:1905-1917`）**

> "The function of the STRICT_REGISTER_ACCESS bit is to indicate to the interface controller that all bytes of a multibyte register must be accessed in the current frame for valid communication to have occurred. In the event a multibyte register is only partially accessed, an interface fault is generated in the Interface Status A register... and the partial content update is discarded... The access restriction function is enabled by default (STRICT_REGISTER_ACCESS = 1)."

**RTL 依据**：`ad4080_regs.sv:79-100` 每个 `we` 直接写字段，没有「先暂存、整寄存器提交」的两级结构，也没有部分访问判定。

**差异**：RTL 实际等价于 `STRICT_REGISTER_ACCESS=0`（逐字节自由写），与器件默认值相反。

**修改建议**

1. 对每个多字节寄存器增加 `byte_cnt` 校验：`0x1D/0x1E`、`0x1F/0x20`、`0x21/0x22`、`0x23/0x24`、`0x25/0x26`、`0x27/0x28` 必须 2 字节。
2. 增加影子寄存器，整组到齐后一次性提交：

```systemverilog
logic [7:0] wm_shadow_lo, wm_shadow_hi;
logic       wm_lo_seen, wm_hi_seen;

// 在 spi_slave 输出 "frame_end" 时判定
if (strict_en && frame_end && is_multibyte(addr_ptr)
    && !(wm_lo_seen && wm_hi_seen)) begin
    partial_err <= 1'b1;           // → 0x11[1]
    wm_lo_seen  <= 1'b0;
    wm_hi_seen  <= 1'b0;           // 丢弃影子内容
end
```

3. `0x11` 需实现（当前完全缺失，见 M-11），否则错误无处上报。

### 1.8 S-08 SINGLE_INST 默认 streaming 缺失（P1）

**手册依据（p.76 Table 33）**

> "[7] SINGLE_INST ... 0: Streaming mode is enabled. The address increments or decrements as successive data bytes are received. 1: Single instruction mode is enabled."

**RTL 依据**：无 `0x01` 寄存器；`spi_slave.sv` 每帧只接受一条指令、一个字节，不属于手册定义的任何一种模式。

**修改建议**：实现 `0x01[7]`；streaming 时地址自动步进（S-05），single 时每 8 bit 数据后重新进入指令相。后者需要 SPI 状态机而非单纯计数器，建议与 S-02 一起重写为 4 状态 FSM：`IDLE → INSTR → DATA → (CRC) → INSTR|IDLE`。

### 1.9 S-09 写数据相 SDO 未驱动 0（P2）

**手册依据（p.35，`/tmp/ad4080.txt:1768-1771`）**

> "Note that during the data phase of a write operation, the SDO output is driven to Logic 0 when the product is not reporting the latest CRC checksum to ensure a valid data state is presented to the host controllers SDI pin."

**RTL 依据（`spi_slave.sv:110`）**

```systemverilog
oe_r  <= cmd;   // 只有读操作驱动 SDO
```

**差异**：写操作时 `sdo_oe=0`，SDO 高阻。若主机把 SDO/SDI 短接做回环自检，或依赖写期间 SDO 为 0，行为不符。

**修改建议**：

```systemverilog
// 写数据相（含 CRC 相）驱动 0；读相驱动数据；指令相按 SEND_STATUS 决定
oe_r <= cmd || (bitcnt >= iw);      // 指令相之外始终驱动
sdo_r <= cmd ? shout[7] : 1'b0;
```

### 1.10 S-10 时序约束（符合）

**手册依据（p.7 Table 2，`/tmp/ad4080.txt:376-384`）**

> "SCLK Period tSCK 20 ns ... SDI Valid Setup Time Before SCLK Rising Edge tSSDI 1 ns ... SDI Valid Hold Time After SCLK Rising Edge tHSDI 0 ns"

**RTL 依据（`spi_slave.sv:11`）**

```systemverilog
// 结构：SCLK/CS 由 clk 过采样（要求 clk >= 8x SCLK），不把 SCLK 当第二时钟域。
```

**说明**：50 MHz SCLK + 8× 过采样 → `clk ≥ 400 MHz`。功能模型可接受，但要注意 `sck_rise` 经 2FF 同步后才采样 SDI，相对真实 SCLK 上升沿滞后约 2–3 个 `clk`。`tHSDI` 最小值为 0 ns（主机可在上升沿后立即换数据），滞后采样存在采到下一比特的风险。建议在 `sck_rise` 检测当拍用**打拍前的 SDI** 或把 SDI 也做 3 级延迟对齐，并在 TB 中按 `tHSDI=0` 做最坏角验证。此条不改变「符合」判定，属实现风险提示。

---

## 2. CRC-8

### 2.1 C-01 完全没有 CRC 引擎（P1）

**手册依据（p.41，`/tmp/ad4080.txt:2138-2146`）**

> "The following CRC-8 polynomial is implemented in the AD4080 to compute the checksum for each register transaction: x8 + x2 + x + 1.
> Each serial transaction is processed through this polynomial to generate the checksum on a per register basis. The data and seed values used for each checksum calculation are a function of the access command (read/write); ADDR_ASCENSION, STRICT_REGISTER_ACCESS, and SINGLE_INST settings; and the location of the register data in the data stream."

**手册依据（p.41，`/tmp/ad4080.txt:2166-2169`）**

> "In single instruction mode (SINGLE_INST = 1), the polynomial is computed for each register using the default seed value of 0xA5, the instruction phase data, and depending on the access command, the desired register or padding data."

**种子与覆盖范围（p.41 Table 19，`/tmp/ad4080.txt:2183-2193`）**

| 命令 | 主机侧数据源 | 种子 | 器件侧数据源 | 种子 |
|---|---|---|---|---|
| Write | Instruction and data | 0xA5 | Register data / Current start address | 0xA5 / 当前起始地址 |
| Read | Instruction and padding data | 0xA5 | Instruction and register content | 0xA5 / 当前起始地址 |

即：**首次 CRC 覆盖「16 bit 指令（含方向位）+ 数据字节」，种子 0xA5；streaming 模式下后续寄存器的 CRC 以「当前起始地址」为种子**。

**RTL 依据（`spi_slave.sv:9`）**

```systemverilog
//   - CRC-8 / streaming / short-instruction / status-on-SDO are NOT implemented.
```

**修改建议：bit-serial CRC-8（LSB-first 实现，多项式 0x07）**

```systemverilog
module spi_crc8 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       init,      // 每个寄存器事务开始：CS 下降沿或新寄存器起始
    input  logic [7:0] seed,      // 0xA5 或 当前起始地址
    input  logic       bit_in,
    input  logic       bit_en,    // 每个 SCLK 采样/更新点
    output logic [7:0] crc
);
    wire fb = crc[7] ^ bit_in;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)     crc <= 8'hA5;
        else if (init)  crc <= seed;
        else if (bit_en) crc <= {crc[6:0], 1'b0} ^ (fb ? 8'h07 : 8'h00);
    end
endmodule
```

喂数规则（需与 S-02/S-06 的字节序一致）：

- 写：`bit_en` 有效于指令相每个 `sck_rise`（SDI 指令位）+ 数据相每个 `sck_rise`（SDI 数据位）；CRC 字节本身不计入。
- 读：`bit_en` 有效于指令相每个 `sck_rise` + 数据相每个 `sck_rise` 的 SDI padding 位（主机侧）；器件侧则应喂「指令 + 读出的寄存器内容」，即用 SDO 移位寄存器的当前位。
- CRC 位序（是否每字节 MSB 先、指令位是否含方向位）手册只给「Instruction and data」的粒度，**位序未明写**，标 `[待确认]`；按串行流 MSB-first 实现。

**streaming 后续种子的位宽问题 `[待确认]`**：种子是 8 bit，而地址 15 bit，手册只说 "seeded with the starting address"，未说明取低 8 位还是取模。建议暂按 `addr[7:0]` 实现并在报告中标注。

### 2.2 C-02 CRC 使能需互补写入（P1）

**手册依据（p.40，`/tmp/ad4080.txt:2103-2113`）**

> "The configuration CRC function is disabled by default and can be enabled through two complementary bit fields, CRC_ENABLE and CRC_ENABLEB... To enable the CRC function, set the CRC_ENABLE bits to 1 and the CRC_ENABLEB bits to 10. Each of the complementary CRC bit fields is 2-bit wide, and any combination other than that specified results in the function remaining disabled."

**手册依据（p.81 Table 45，`/tmp/ad4080.txt:3991-4007`）**

> "[7:6] CRC_ENABLE ... 0: CRC Disabled. 1: CRC Enabled.
> [1:0] CRC_ENABLE Inverted CRC Enable. These bits must be written to with the inverted value of the CRC_ENABLE."

**RTL 依据**：`ad4080_regs.sv` 没有 `0x10` 分支（`case` 中从 0x0016 跳到 0x0019），读回默认落到 `8'h00`（`ad4080_regs.sv:125`）。

**修改建议**

```systemverilog
logic [1:0] crc_en_r, crc_enb_r;

16'h0010: begin
    crc_en_r  <= wdata[7:6];
    crc_enb_r <= wdata[1:0];
    // 0x10[5]=STRICT, [4]=SEND_STATUS 也在此实现
end

assign crc_en = (crc_en_r == 2'b01) && (crc_enb_r == 2'b10);
```

默认复位 `crc_en_r=2'b00, crc_enb_r=2'b11`（对应 `0x10` 复位值 0x23）。使能 CRC + 保持严格模式 + 关闭状态回传时，主机应写 `0x62`（`[7:6]=01, [5]=1, [4]=0, [1:0]=10`）。

### 2.3 C-03 校验失败处理缺失（P1）

**手册依据（p.40，`/tmp/ad4080.txt:2084-2099`）**

> "A mismatch in the checksum values computed by the host and the AD4080 interface results in setting the CRC_ERR flag (CRC_ERR = 1) in the Interface Status A register... During a write access, a CRC error invalidates the most recent register data as well as any subsequent register data writes if in streaming mode (SINGLE_INST = 0), which prevents loading any potentially corrupted data into the configuration memory... The error flag is cleared by writing code 0x08 to the Interface Status A register to set the CRC_ERR bit to a Logic 1."

**RTL 依据**：无 `0x11` 寄存器、无错误粘滞位、写使能不受任何校验门控。

**修改建议**

```systemverilog
// 0x11 INTERFACE_STATUS_A
logic [7:0] if_status;

// CRC 比较（在 CRC 字节最后一个上升沿）
if (crc_en && crc_byte_end && (crc_calc != crc_rx))
    if_status[3] <= 1'b1;           // CRC_ERR

// 写门控：CRC 错时本帧剩余数据全部作废
wire wr_ok = ~crc_err_in_frame;
assign wdata_gated = wr_ok ? wdata : 8'h00;
assign we_gated    = wr_ok & we;

// W1C：写 0x08 清除
if (we && addr == 16'h0011 && wdata[3])
    if_status[3] <= 1'b0;
```

同时 `0x11[1]`（部分访问）、`[0]`（非法地址）、`[2]`（写只读）、`[4]`（时钟数错）、`[7]`（未就绪）都应实现，否则 CRC 故障无法被主机观测。

### 2.4 C-04 CRC 使能后的帧长与收发位置（P1）

**手册依据（p.42 Fig.66，PDF p.42）**

> 长指令模式 + Status + CRC：
> WRITE `SDI = [W][ADDR 14:0][DATA 7:0][CRC 7:0]`，`SDO = [STATUS 15:0][PADDING]`；
> READ `SDI = [R][ADDR 14:0][PADDING 7:0][CRC 7:0]`，`SDO = [STATUS 15:0][DATA 7:0][CRC 7:0]`。

**RTL 依据**：`bitcnt` 上限 24（`spi_slave.sv:96`），第 25 位起忽略；CRC 字节会被丢弃，且读操作不会回 CRC。

**修改建议**：把帧长改为 `iw + 8*N + (crc_en ? 8 : 0)`，并增加 `crc_phase` 标志；读相在数据字节之后把 `crc_calc` 串出。

### 2.5 C-05 时钟数错误检测缺失（P2）

**手册依据（p.82 Table 46）**

> "[4] CLOCK_COUNT_ERR Clock Count Error. This bit is set when an incorrect number of clocks is detected in a transaction."

**RTL 依据**：CS 提前拉高时 `cs_s1` 分支只清 `sdo/oe`（`spi_slave.sv:83-86`），不检查 `bitcnt` 是否停在合法边界。

**修改建议**

```systemverilog
// CS 上升沿：合法帧的 bitcnt 必须满足
// bitcnt == iw + 8*N (+8 if crc_en)，且数据相已收满整字节
if (cs_rise && (bitcnt < iw || ((bitcnt - iw) % 8) != 0))
    if_status[4] <= 1'b1;
```

### 2.6 `spi_slave.sv` 在 CRC 维度缺什么（对第 2 点的直接回答）

| 缺失项 | 现状 | 需要新增 |
|---|---|---|
| CRC-8 引擎 | 无（`spi_slave.sv:9` 自认） | `spi_crc8` 子模块，按 SCLK 边沿逐位更新 |
| CRC 种子装载 | 无 | CS 下降沿装 `0xA5`；streaming 后续装 `addr[7:0]` `[待确认]` |
| CRC 覆盖范围 | 无 | 指令相 16 bit + 数据相每字节（写：SDI；读：SDO/padding） |
| CRC 字节收发 | 无，`bitcnt` 上限 24 | 帧长 `iw + 8N + 8`，写收 8 bit 比对、读发 8 bit |
| 失败处理 | 无 | `CRC_ERR` 置位 + 本帧后续写屏蔽 + 丢弃 |
| 时钟数校验 | 无 | CS 上升沿检查 `bitcnt` 落在字节边界 |

**结论**：CRC 不是"改几行"能补上的，需要在 S-02 的帧状态机上增加一个 `CRC` 状态和一条并行 CRC 通路；建议与多字节改造一起做，否则会二次返工。


---

## 3. SEND_STATUS：指令相 SDO 回传状态（G-04，P1）

**手册依据（p.39，`/tmp/ad4080.txt:1978-2001`）**

> "The AD4080 can be configured to autonomously transmit status data through the SDO line every time while the SPI controller is sending the SPI instruction phase data over the SDI. This feature is controlled through the SEND_STATUS bit in the Interface Configuration C register (Address 0x10), and it is disabled by default... The status data that is sent is taken from the Interface Status A register and from the device status register, but the content is different depending on the setting of the SHORT_INSTRUCTION bit... the status data is sent MSB first."

**长指令模式位分配（p.39 Table 16，`/tmp/ad4080.txt:2002-2052`）**

| SDO 位 | 名称 | 来源 |
|---|---|---|
| 15, 14 | 0 | 固定 0 |
| 13 | FIFO_FULL | 0x14[7] |
| 12 | FIFO_READ_DONE | 0x14[6] |
| 11 | HI_STATUS | 0x14[5] |
| 10 | LO_STATUS | 0x14[4] |
| 9 | ADC_CNV_ERR | 0x14[2] |
| 8 | ROM_CRC_ERR | 0x14[1] |
| 7 | POR_ANA_FLAG | 0x14[3] |
| 6 | POR_FLAG | 0x14[0] |
| 5 | NOT_READY_ERR | 0x11[7] |
| 4 | CLOCK_COUNT_ERR | 0x11[4] |
| 3 | CRC_ERR | 0x11[3] |
| 2 | WR_TO_RD_ONLY_REG_ERR | 0x11[2] |
| 1 | REGISTER_PARTIAL_ACCESS_ERR | 0x11[1] |
| 0 | ADDRESS_INVALID_ERR | 0x11[0] |

注意：**不是简单拼接**。`0x14` 的 bit3/bit0 被挪到 SDO 的 bit7/bit6，`0x14` 的 bit2/bit1 挪到 bit9/bit8；`0x11[6:5]` 保留位在 SDO 中只占 1 位（bit4=0）。

**短指令模式（p.40 Table 17，`/tmp/ad4080.txt:2053-2072`）**

> `SDO[7]=0, [6]=POR_FLAG(0x14[0]), [5]=NOT_READY_ERR(0x11[7]), [4]=CLOCK_COUNT_ERR, [3]=CRC_ERR, [2]=WR_TO_RD_ONLY, [1]=PARTIAL, [0]=ADDRESS_INVALID`

**RTL 依据**：`spi_slave.sv:83-86` 在 CS 高时 `oe_r<=0`；指令相无任何状态装载；`ad4080_regs.sv` 无 `0x10`/`0x11`。

**修改建议**

```systemverilog
function automatic logic [15:0] status_word16(
    input logic [7:0] dev,      // 0x14 当前值
    input logic [7:0] ifa       // 0x11 当前值
);
    status_word16 = {2'b00,
                     dev[7], dev[6], dev[5], dev[4], dev[2], dev[1], dev[3], dev[0],
                     ifa[7], 1'b0, ifa[4], ifa[3], ifa[2], ifa[1], ifa[0]};
endfunction

function automatic logic [7:0] status_word8(
    input logic [7:0] dev,
    input logic [7:0] ifa
);
    status_word8 = {1'b0, dev[0], ifa[7], ifa[4], ifa[3], ifa[2], ifa[1], ifa[0]};
endfunction

// CS 下降沿并行装载；每个 SCLK 下降沿左移
if (cs_fall)
    st_sh <= send_status
           ? (cfg_short_inst ? {8'h00, status_word8(dev_status, if_status)}
                             : status_word16(dev_status, if_status))
           : 24'h0;

if (sck_fall && bitcnt < iw && send_status) begin
    sdo_r <= st_sh[15];         // 长指令模式从 bit15 开始
    st_sh <= {st_sh[14:0], 1'b0};
    oe_r  <= 1'b1;
end
```

短指令模式要按 8 bit 对齐（从 `st_sh[7]` 开始），否则状态会错位 8 位。

---

## 4. 0x14 DEVICE_STATUS（G-01/G-02/G-03）

### 4.1 G-01 位序完全错误（P0）

**手册依据（p.83 Table 47，`/tmp/ad4080.txt:4044-4079`）**

> "Address: 0x14, Reset: 0x09, Name: DEVICE_STATUS
> [7] FIFO_FULL ... [6] FIFO_READ_DONE ... [5] HI_STATUS ... [4] LO_STATUS ... [3] POR_ANA_FLAG ... [2] ADC_CNV_ERR ... [1] ROM_CRC_ERR ... [0] POR_FLAG"

**RTL 依据（`ad4080_top.sv:123-124`）**

```systemverilog
assign status_in = {fifo_full, fifo_empty, fifo_ovf, fifo_unf,
                    clip_low, clip_high, captured, chain_valid};
assign status    = status_in;
```

`ad4080_regs.sv:105` 又把 `status_in` 原样返回：

```systemverilog
16'h0014: rdata = status_in;
```

**逐位对照**

| bit | 手册要求 | 当前 RTL 实际含义 | 结果 |
|---|---|---|---|
| 7 | FIFO_FULL | `fifo_full` | 巧合正确 |
| 6 | FIFO_READ_DONE | `fifo_empty` | **错**（空标志 vs 读完标志） |
| 5 | HI_STATUS | `fifo_ovf` | **错** |
| 4 | LO_STATUS | `fifo_unf` | **错** |
| 3 | POR_ANA_FLAG | `clip_low` | **错** |
| 2 | ADC_CNV_ERR | `clip_high` | **错** |
| 1 | ROM_CRC_ERR | `captured` | **错** |
| 0 | POR_FLAG | `chain_valid` | **错** |

**影响**：主机读 0x14 得到的是完全无关的内部数据；`FIFO_READ_DONE` 永远读不到（读到的是 empty），POR 标志读不到，事件状态读不到。任何依赖状态字节的上层流程都会误判。

**修改建议**

```systemverilog
// ad4080_top.sv
assign status_in = {
    fifo_full,          // [7]
    read_done,          // [6]  顶层 fifo_capture 的 read_done
    hi_dtct,            // [5]
    lo_dtct,            // [4]
    por_ana_flag,       // [3]
    adc_cnv_err,        // [2]
    rom_crc_err,        // [1]
    por_flag            // [0]
};
```

其中 `hi_dtct/lo_dtct` 需由 `event_detect` 产生（当前该模块未实例化，见 `docs/review_filter_fifo.md` E-10/E-11），`adc_cnv_err` 来自 `cnv_ctrl`，`derived` 标志来自 POR 逻辑。

### 4.2 G-02 W1C 与只读属性缺失（P1）

**手册依据（p.83 Table 47）**

> "[5] HI_STATUS ... Writing 1 to this bit clears it. R/W1C
> [4] LO_STATUS ... R/W1C
> [3] POR_ANA_FLAG ... R/W1C
> [2] ADC_CNV_ERR ... R/W1C
> [1] ROM_CRC_ERR ... R
> [0] POR_FLAG ... R/W1C"

**RTL 依据**：`ad4080_regs.sv:79-100` 的 `case` 中没有 `16'h0014`，写 0x14 不产生任何动作；读始终返回 `status_in`。

**修改建议**

```systemverilog
logic [7:0] dev_status;
wire  [7:0] st_set  = {fifo_full, fifo_read_done, hi_flag, lo_flag,
                       1'b0, adc_cnv_err, 1'b0, 1'b0};   // bit3/bit0 由 POR 事件置位
wire        wr_st   = we && (addr == 16'h0014);
wire  [7:0] w1c_hit = wr_st ? (wdata & 8'b0011_1101) : 8'b0000_0000;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) dev_status <= 8'h09;
    else begin
        dev_status <= (dev_status | st_set) & ~w1c_hit;
        dev_status[7] <= fifo_full;         // RO
        dev_status[6] <= fifo_read_done;    // RO
        dev_status[1] <= rom_crc_err;       // RO
    end
end
```

`por_flag` / `por_ana_flag` 的置位源应是上电/软复位事件，而不是常量；上面的 `st_set[3]/st_set[0]` 需接 POR 脉冲。

### 4.3 G-03 复位值不是 0x09（P1）

**手册依据（p.83、p.74 Table 31）**

> "Address: 0x14, Reset: 0x09"
> 0x09 = `POR_ANA_FLAG(bit3)=1` + `POR_FLAG(bit0)=1`。

**RTL 依据**：`ad4080_regs.sv` 没有 0x14 的复位逻辑，`status_in` 由组合信号驱动；上电后读到的是 `fifo_empty=1` 等随机组合，不可能等于 0x09。

**修改建议**：按 4.2 的 `dev_status <= 8'h09` 实现；POR 事件到来时重新置 bit3/bit0。

---

## 5. 0x1C GENERAL_CONFIG（G-05，P1）

**手册依据（p.90 Table 55，`/tmp/ad4080.txt:4282-4308`）**

> "[7] INT_EVENT_EN Internal Event Detection Enable... 0: disabled. 1: enabled.
> [6] HI_ROUTE ... 0: Mask High Detection. 1: Route High Detection to Alert Pin, Status Register, and FIFO.
> [5] LO_ROUTE ... 1: Route Low Detection to Alert Pin, Status Register, and FIFO.
> [4] ADC_CNV_ERR_ROUTE ... 1: Route ADC Conversion Error to Alert Pin and Status Register.
> [3:2] RESERVED ... [1:0] FIFO_MODE"
> Reset: 0x00.

**RTL 依据（`ad4080_regs.sv:84,111`）**

```systemverilog
16'h001C: begin even_r <= wdata[7]; fmode_r <= wdata[1:0]; end
...
16'h001C: rdata = {even_r, 5'b0, fmode_r};
```

**差异**

- bit6/bit5/bit4 写操作被**静默丢弃**，读回恒 0。
- 因此 `HI_ROUTE/LO_ROUTE` 永远为 0，按 p.56 原文「By default ... masking the threshold level detection from generating any event alert」，事件永远不会路由到状态位/ALERT/FIFO —— 即使 `INT_EVENT_EN=1` 也无效。
- 变量名 `event_en` 只对应 `INT_EVENT_EN`，语义命名可保留，但必须补 route 位。
- `FIFO_MODE` 写入后不 arm FIFO（顶层注释自认，`ad4080_top.sv:13`；详见 `review_filter_fifo.md` Q-02），这是另一个 P0，本报告不重复计分。

**修改建议**

```systemverilog
output logic int_event_en, hi_route, lo_route, adc_cnv_err_route;

16'h001C: begin
    int_event_en      <= wdata[7];
    hi_route          <= wdata[6];
    lo_route          <= wdata[5];
    adc_cnv_err_route <= wdata[4];
    fmode_r           <= wdata[1:0];
end

16'h001C: rdata = {int_event_en, hi_route, lo_route,
                   adc_cnv_err_route, 2'b00, fmode_r};
```

route 位在下游的用法（与 p.55 一致）：

```systemverilog
wire hi_status_set = hi_dtct && hi_route;
wire lo_status_set = lo_dtct && lo_route;
wire alert_n       = ~((hi_dtct && hi_route) | (lo_dtct && lo_route));
```

---

## 6. GPIO 配置（G-06/G-07）

### 6.1 G-06 SEL 位宽 3 bit 不够，且编码顺序不对（P1）

**手册依据（p.88 Table 53，`/tmp/ad4080.txt:4214-4238`）**

> "[7:4] GPIO_1_SEL ... [3:0] GPIO_0_SEL
> 0000: Configuration SPI SDO Data.
> 0001: FIFO Full Flag.
> 0010: FIFO Read Done Flag.
> 0011: Filter Result Ready (Active Low).
> 0100: High Threshold Detect.
> 0101: Low Threshold Detect.
> 0110: Status Alert (Active Low).
> 0111: GPIO Data.
> 1000: Filter Synchronization Input (Active Low).
> 1001: External Event Trigger Input for FIFO.
> 1010: Do not use this setting."

`0x1B` 的 `GPIO_3_SEL/GPIO_2_SEL` 同表（p.89）。

**RTL 依据（`ad4080_regs.sv:82-83,109-110`）**

```systemverilog
16'h001A: begin gsel_r[0] <= wdata[2:0]; gsel_r[1] <= wdata[6:4]; end
16'h001B: begin gsel_r[2] <= wdata[2:0]; gsel_r[3] <= wdata[6:4]; end
...
16'h001A: rdata = {1'b0, gsel_r[1], 1'b0, gsel_r[0]};
```

`gpio_ctrl.sv:19,27-28`：

```systemverilog
input  logic [3:0][2:0]  gpio_sel,
...
if (gpio_sel[i] == 3'd0) gpio_o[i] = gpio_reg[i];
else                     gpio_o[i] = funcs[gpio_sel[i]];
```

**差异**

1. 每个 SEL 只有 3 bit，bit3 在写入时被丢弃、读回恒 0 → `1000`（FILTER_SYNC）和 `1001`（EXT_EVENT）**无法选择**。
2. `1001` 被截成 `001`，在 `gpio_ctrl` 里落到 `funcs[1]`（当前定义为 LO_DTCT），即**选择外部事件触发却输出低阈值标志**，是错误的功能映射，不只是"少两个选项"。
3. `gpio_ctrl` 的 `funcs[]` 顺序与手册编码完全无关（`gpio_ctrl.sv:9-11` 自定义 `[0]HI,[1]LO,[2]ALERT,...`），即使位宽够了也要按手册重排。
4. 手册要求 `0000` = SPI SDO、`0111` = GPIO Data；当前 `sel==0` 输出的是 `gpio_reg[i]`（寄存器数据），与手册相反。

**修改建议**

```systemverilog
// ad4080_regs.sv
output logic [3:0][3:0] gpio_sel;
logic  [3:0][3:0] gsel_r;

16'h001A: begin gsel_r[0] <= wdata[3:0]; gsel_r[1] <= wdata[7:4]; end
16'h001B: begin gsel_r[2] <= wdata[3:0]; gsel_r[3] <= wdata[7:4]; end

16'h001A: rdata = {gsel_r[1], gsel_r[0]};
16'h001B: rdata = {gsel_r[3], gsel_r[2]};
```

```systemverilog
// gpio_ctrl.sv：按手册编码逐项映射
input  logic [3:0][3:0] gpio_sel,
input  logic            sdo_i,
input  logic            fifo_full, fifo_read_done, filter_rdy,
input  logic            hi_dtct, lo_dtct, alert,
input  logic            ext_event,

always_comb begin
    for (int i = 0; i < 4; i++) begin
        gpio_oe[i] = gpio_dir[i];
        case (gpio_sel[i])
            4'd0: gpio_o[i] = sdo_i;            // 0000 SPI SDO
            4'd1: gpio_o[i] = fifo_full;        // 0001
            4'd2: gpio_o[i] = fifo_read_done;   // 0010
            4'd3: gpio_o[i] = ~filter_rdy;      // 0011 active low
            4'd4: gpio_o[i] = hi_dtct;          // 0100
            4'd5: gpio_o[i] = lo_dtct;          // 0101
            4'd6: gpio_o[i] = ~alert;           // 0110 active low
            4'd7: gpio_o[i] = gpio_reg[i];      // 0111
            4'd8: begin                          // 1000 FILTER_SYNC 输入
                gpio_o[i]  = 1'b0;
                gpio_oe[i] = 1'b0;
            end
            4'd9: begin                          // 1001 EXT_EVENT 输入
                gpio_o[i]  = 1'b0;
                gpio_oe[i] = 1'b0;
            end
            default: gpio_o[i] = 1'b0;           // 1010 do not use
        endcase
    end
end
```

同时 `GPO_x_EN`（`0x19[3:0]`）为 1 时才允许输出；SEL=1000/1001 是输入，必须强制 `gpio_oe=0`，与 `GPO_x_EN` 无关。

### 6.2 G-07 0x19 复位值（P2）

**手册依据（p.87 Table 52，`/tmp/ad4080.txt:4177`）**

> "Address: 0x19, Reset: 0x01 ... [0] GPO_0_EN ... 0x1 R/W"

**手册依据（p.34，`/tmp/ad4080.txt:1659-1661`）**

> "By default, at power-up or after a software reset, the configuration interface SDO function is enabled and assigned to the GPIO0 pin."

**RTL 依据（`ad4080_regs.sv:75`）**：`gdir_r <= 4'd0;` → 读回 0x00。

**修改建议**：`gdir_r <= 4'b0001;`。

---

## 7. 多字节数值寄存器（M-01…M-05）

### 7.1 M-01 FIFO_WATERMARK 复位值（P2）

**手册依据（p.91，`/tmp/ad4080.txt:4315-4323`）**

> "Address: 0x1D and Address: 0x1E, Reset: 0x4000, Name: FIFO_WATERMARK
> [15] RESERVED ... [14:0] FIFO_WATERMARK ... 0x4000 R/W"

**RTL 依据（`ad4080_regs.sv:68`）**：`wm_r <= 15'd0;`

**修改建议**

```systemverilog
wm_r <= 15'h4000;
// 或分字节：wm_lo_r <= 8'h00; wm_hi_r <= 8'h40;
```

### 7.2 M-02 WATERMARK 钳位（P1）

**手册依据（p.91，`/tmp/ad4080.txt:4316`）**

> "This is the watermark value. If the user writes a value <1, it is clipped at 1. If >16,384, clipped at 16,384."

**RTL 依据（`ad4080_regs.sv:85-86`）**

```systemverilog
16'h001D: wm_r[7:0]   <= wdata;
16'h001E: wm_r[14:8]  <= wdata[6:0];
```

无任何范围处理。

**修改建议**（注意 16384 = 0x4000 需要 15 bit 的 bit14，写 >0x4000 时钳位）

```systemverilog
logic [7:0] wm_lo, wm_hi;      // 0x1D / 0x1E 原始字节
wire [15:0] wm_raw = {1'b0, wm_hi[6:0], wm_lo};   // {bit15, [14:8], [7:0]}

assign fifo_watermark = (wm_raw < 16'd1)     ? 15'd1     :
                        (wm_raw > 16'd16384) ? 15'd16384 : wm_raw[14:0];
```

若要求读回值也反映钳位结果，应在写入 `0x1E`（默认帧的最后一字节）时把钳位后的值回写 `wm_lo/wm_hi`。

### 7.3 M-03 HI/LO_THRESHOLD 补码被当无符号（P0）

**手册依据（p.92 Table 58/59，`/tmp/ad4080.txt:4344-4345, 4353-4354`）**

> "[11:0] HI_THRESHOLD High Threshold Value. Twos complement data format where LSB = 1.46484 mV. 0x800 represents −2048 × LSB, and 0x7FF represents +2047 × LSB."
> "[11:0] LO_THRESHOLD ... Twos complement data format ..."

**手册依据（p.20 Table 7，`/tmp/ad4080.txt:818-830`）**

> "Digital Output Code (Twos Complement, Hex) ... 0x7FFFF / 0x00000 / 0x80000"

**RTL 依据（`ad4080_regs.sv:56,89-94`）**

```systemverilog
logic [11:0] hi_r, lo_r;
...
16'h0021: hi_r[7:0]   <= wdata;
16'h0022: hi_r[11:8]  <= wdata[3:0];
```

**RTL 依据（`event_detect.sv:33-35,46,50`）**

```systemverilog
assign d12 = data_in[19:8];
assign hi_clr_th = $signed({1'b0, hi_thresh}) - $signed({2'b0, hysteresis});
...
if (d12 >= hi_thresh)            hi_flag <= 1'b1;
...
if (d12 <= lo_thresh)            lo_flag <= 1'b1;
```

**差异**

1. 寄存器与端口全部是无符号，`0x800` 被解释为 +2048 而不是 −2048。
2. 下游比较把 `d12` 与阈值都当无符号，负阈值（0x800–0xFFF）永远无法触发，负半轴输入（20-bit 结果为负，取 `[19:8]` 后仍在 0x800–0xFFF 区间）比较结果完全错误。
3. 位对齐本身是对的：`1.46484 mV / 5.72 µV ≈ 256`，取 20-bit 结果的 `[19:8]` 正好是 12-bit 补码，**不需要额外移位**。

**修改建议**

```systemverilog
// ad4080_regs.sv
output logic signed [11:0] event_hi, event_lo;
logic signed [11:0] hi_r, lo_r;

// event_detect.sv
input  logic signed [11:0] hi_thresh, lo_thresh,
input  logic        [10:0] hysteresis,     // 无符号

logic signed [11:0] d12;
assign d12 = $signed(data_in[19:8]);

assign hi_clr_th = hi_thresh - $signed({1'b0, hysteresis});
assign lo_clr_th = lo_thresh + $signed({1'b0, hysteresis});

if (d12 >= hi_thresh) hi_flag <= 1'b1;
else if (d12 <  hi_clr_th) hi_flag <= 1'b0;

if (d12 <= lo_thresh) lo_flag <= 1'b1;
else if (d12 >  lo_clr_th) lo_flag <= 1'b0;
```

`HYSTERESIS` 保持无符号（p.91 明确 "Unsigned data format"），只是参与有符号运算前零扩展。

### 7.4 M-04 HYSTERESIS（符合）

**手册依据（p.91 Table 57，`/tmp/ad4080.txt:4330-4331`）**

> "[10:0] HYSTERESIS Hysteresis Value. Unsigned data format where LSB = 1.46484 mV. 0x000 represents 0 × LSB, and 0x7FF represents 2047 × LSB."

**RTL 依据（`ad4080_regs.sv:55,87-88,115`）**：11 bit、无符号、读回 `{5'b0, hyst_r[10:8]}`，与手册一致。

> 注意：任务书把 HYSTERESIS 也归入"二进制补码"，与 p.91 原文 "Unsigned data format" 不符。**HYSTERESIS 是无符号量**，不应改成 signed；只有 HI/LO_THRESHOLD 和 OFFSET 是补码。若误改，0x7FF 会变成 −1，回差方向反了。

### 7.5 M-05 OFFSET / GAIN（符合，一处建议）

**手册依据（p.92-93，`/tmp/ad4080.txt:4357-4379`）**

> "OFFSET ... Twos complement data format where LSB = 0.00572 mV. 0x800 represents −2048 × LSB, and 0x7FF represents +2047 × LSB."
> "GAIN ... Reset: 0x0200 ... GAIN = 0x200 disables the gain correction function."

**RTL 依据（`ad4080_regs.sv:31,50,63-64,93-96,120-123`）**

```systemverilog
output logic signed [11:0] offset_code,
logic [9:0]  gain_r;
...
offset_r <= 12'sd0;
gain_r   <= 10'h200;
```

**结论**：位宽、复位值、端口符号都正确。唯一建议：`offset_r` 内部声明为 `logic signed [11:0]`（当前是 `logic [11:0]`），避免后续在寄存器文件内做算术时被当无符号；当前只做位切片和直通，所以没有实际错误。

---

## 8. FILTER_CONFIG 0x29（M-06…M-09）

### 8.1 M-06 字段布局（符合）

**手册依据（p.94 Table 62，`/tmp/ad4080.txt:4389-4414`）**

> "[7] RESERVED ... [6:3] SINC_DEC_RATE ... [2] RESERVED ... [1:0] FILTER_SEL ... Reset: 0x00"

**RTL 依据（`ad4080_regs.sv:97,124`）**

```systemverilog
16'h0029: begin fsel_r <= wdata[1:0]; dec_r <= wdata[6:3]; end
...
16'h0029: rdata = {1'b0, dec_r, 1'b0, fsel_r};
```

**结论**：字段位置、保留位读 0 都正确。

### 8.2 M-07 dec_rate 根本没有接入滤波器（P0）

**手册依据（p.94，`/tmp/ad4080.txt:4391-4393`）**

> "These bits set the Sinc Decimation Factor N. The filter compensation block incurs an additional 2× decimation. The total decimation for a selected filter is sinc1 = N, sinc5 = N, or sinc5 + compensation = N × 2."

**RTL 依据（`ad4080_top.sv:88-97`）**

```systemverilog
ad4080_dsp_chain #(.IN_W(IN_W), .DECIM(DECIM), .USE_FIFO(1'b0)) u_chain (
    .clk(clk), .rst_n(rst_n), .valid_in(valid_in), .x(x),
    .offset_code(offset_code), .gain_code(gain_code),
    .filter_sel(filter_sel), .filter_sync(filter_sync),
    .rd_en(rd_en),
    ...
);
```

`ad4080_dsp_chain.sv:34` 的端口是：

```systemverilog
input  logic [3:0] dec_sel,      // SINC_DEC_RATE
```

顶层既没有 `.dec_sel(...)`，也没有叫 `dec_rate` 的端口 → **该输入悬空**。`ad4080_top.sv:14` 的注释「运行时抽取率 dec_rate 只寄存，链路的 DECIM 仍是编译期参数」与代码一致，但这不是可接受的行为：写 `0x29` 对数据通路零影响，主机以为改了带宽/抽取率，实际不变。

**修改建议**

```systemverilog
ad4080_dsp_chain #(.IN_W(IN_W), .DECIM(DECIM), .USE_FIFO(1'b0)) u_chain (
    ...
    .dec_sel(dec_rate),          // ← 补上这一行
    ...
);
```

并统一命名（`dec_sel` ↔ `dec_rate` 二选一），避免下次再漏。

### 8.3 M-08 非法码钳位（符合）

**手册依据（p.94，`/tmp/ad4080.txt:4395-4396`）**

> "For a selected filter, setting invalid values, outside of those specified here, will set the filter at the maximum decimation rate."

**RTL 依据**

```systemverilog
// sinc1_decim.sv:41-47
if (s > 4'd9) begin d = 11'd1024; l = 5'd10; end
else            d = 11'd1 << (s + 4'd1);

// sinc5_decim.sv:46-52
if (s > 4'd7) begin d = 11'd256; l = 4'd8; end
else            d = 11'd1 << (s + 4'd1);
```

**结论**：钳位策略与 Table 29/62 的允许范围一致（SINC1 → 1024，SINC5 → 256）。`sinc5_comp` 内部先做 N 再 2× 抽取（`sinc5_comp.sv:86-95` 的 `phase` 交替），总抽取 = 2N，最大 512，也与 Table 29 一致。**前提是先把 M-07 的连线补上**，否则这些逻辑收不到 `dec_sel`。

### 8.4 M-09 改 FILTER_SEL 需 FILTER_SYNC（P1）

**手册依据（p.64，`/tmp/ad4080.txt:3248-3251`）**

> "To ensure the first filter result produces the correct data, when a user makes a change to the filter selection, a reset must be issued via the GPIO pin configured for filter synchronization (FILTER_SYNC)."

**手册依据（p.94，`/tmp/ad4080.txt:4408-4409`）**

> "To ensure the first filter result produces the correct data, when a user makes a change to the filter selection, a reset must be issued via the GPIO pin configured for filter synchronization (FILTER_SYNC)."

**RTL 依据（`ad4080_regs.sv:97`）**：写 `0x29` 只更新 `fsel_r/dec_r`，不产生任何单拍同步信号；`filter_sync` 是顶层外部输入（`ad4080_top.sv:37`）。

**修改建议**

```systemverilog
output logic filter_sync_pulse;

// 写 0x29 任意位都产生一拍（p.67：写滤波配置寄存器 Bits[7:0] 本身即复位滤波器）
always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) filter_sync_pulse <= 1'b0;
    else        filter_sync_pulse <= we && (addr == 16'h0029);
```

顶层：`assign filter_sync_eff = filter_sync_pin | filter_sync_pulse;`（注意手册 FILTER_SYNC 为低有效，见 `review_filter_fifo.md` F-15）。

---

## 9. 其他寄存器映射问题

### 9.1 M-10 0x16 位定义与复位值错误（P1）

**手册依据（p.85 Table 49，`/tmp/ad4080.txt:4114-4135`）**

> "Address: 0x16, Reset: 0x00, Name: ADC_DATA_INTF_CONFIG_B
> [7:4] LVDS_CNV_CLK_CNT ... [3] LVDS_SELF_CLK_MODE ... [2] LVDS_MNC_EN ... [1] RESERVED ... [0] LVDS_CNV_EN"

**手册依据（p.84 Table 48，`/tmp/ad4080.txt:4099-4101`）**

> "[2] SPI_LVDS_LANES LVDS/SPI Lane Control ... 0: One Lane Active. 1: Multiple Lanes Active (Two for LVDS and Four for the SPI Data Interface)."

**RTL 依据（`ad4080_regs.sv:5,73-74,80,106`）**

```systemverilog
//   0x16  ADC_DATA_INTF_CONFIG_B  [0] LVDS_CNV_EN, [1] LANE_SEL (1/2)
...
lvds_r   <= 1'b1;   // default LVDS
lane_r   <= 1'b1;   // default 1 lane
...
16'h0016: begin lvds_r <= wdata[0]; lane_r <= wdata[1]; end
...
16'h0016: rdata = {6'b0, lane_r, lvds_r};
```

**差异**

1. `0x16[1]` 在手册中是 **RESERVED**，不是 `LANE_SEL`。lane 控制位是 `0x15[2] SPI_LVDS_LANES`，而 `0x15` 整个寄存器在 RTL 中不存在。
2. `0x16` 复位值是 0x00，RTL 复位成 `{lane=1, lvds=1}` = 0x03。手册明确 `LVDS_CNV_EN` 默认 0（CNV 为 CMOS 模式），RTL 默认却是 LVDS 模式。

**修改建议**

```systemverilog
// 0x15 至少实现 lane 位（其余位按需）
logic lane_sel, data_intf_mode;
16'h0015: begin
    lane_sel       <= wdata[2];   // SPI_LVDS_LANES
    data_intf_mode <= wdata[0];   // DATA_INTF_MODE
end
16'h0015: rdata = {6'b0, lane_sel, 1'b0, data_intf_mode};

// 0x16：bit1 保留
16'h0016: lvds_cnv_en <= wdata[0];

// 复位
lvds_cnv_en <= 1'b0;
lane_sel    <= 1'b0;
```

### 9.2 M-11 接口配置/ID 寄存器整体缺失（P1）

**手册依据（p.73 Table 30，`/tmp/ad4080.txt:3606-3616`）**

> "0x00 to 0x11 Configuration interface and Product ID
> 0x14 Device status
> 0x15 to 0x17 Interface configuration"

**RTL 依据（`ad4080_regs.sv:79-99`）**：写 `case` 从 `0x0016` 开始，读 `case` 从 `0x0014` 开始，`default` 返回 `8'h00`（`ad4080_regs.sv:125`）。

**缺失清单**

| 地址 | 作用 | 缺失后果 |
|---|---|---|
| 0x00 | SW_RESET、ADDR_ASCENSION、SDO_ENABLE | 无法软复位；无法切地址方向 |
| 0x01 | SINGLE_INST、SHORT_INSTRUCTION | 无法切指令/寻址模式 |
| 0x03/04/05/06 | CHIP_TYPE=0x07、PRODUCT_ID=0x0050、CHIP_GRADE=0x02 | 主机枚举读回 0，通常直接判失败 |
| 0x0A | SCRATCH_PAD | 寄存器读写自检失败 |
| 0x0B/0C/0D | SPI_REVISION=0x83、VID=0x0456 | 同上 |
| 0x10 | CRC_ENABLE/STRICT/SEND_STATUS | 本报告 2/3 章全部功能不可配 |
| 0x11 | INTERFACE_STATUS_A | 所有接口错误无处上报 |
| 0x15/0x17 | 数据接口配置 | lane/测试图案不可配 |

**修改建议**：至少补齐只读 ID 与最小可写集合：

```systemverilog
case (addr)
    16'h0003: rdata = 8'h07;   // CHIP_TYPE
    16'h0004: rdata = 8'h50;   // PRODUCT_ID_L
    16'h0005: rdata = 8'h00;   // PRODUCT_ID_H
    16'h0006: rdata = 8'h02;   // CHIP_GRADE
    16'h000B: rdata = 8'h83;   // SPI_REVISION
    16'h000C: rdata = 8'h56;   // VENDOR_L
    16'h000D: rdata = 8'h04;   // VENDOR_H
    16'h000A: rdata = scratch_r;
    ...
endcase
```

`0x00` 的软复位语义（p.27-28：先写 0x81 置 `SW_RESET/SW_RESETX`，再写 0x00 触发，初始化 300 µs，期间 `NOT_READY_ERR=1`）必须成对实现，不能只认单次写。

---

## 10. 优先级排序

### P0 —— 会导致功能错误（5 条）

1. **S-02 / S-06 多字节帧被截断**：数据相固定 1 字节，`0x1D-0x28` 的所有 2 字节寄存器只能写进一半；且字节序/起始地址规则未实现。这是最严重的功能错误，主机按手册写的帧会得到错误寄存器值。
2. **G-01 0x14 状态位序全错**：顶层把 FIFO 内部信号按完全无关的顺序塞进状态字节，`FIFO_READ_DONE`、`HI/LO_STATUS`、`POR_*` 全部读不到。
3. **M-03 阈值补码被当无符号**：负阈值永不触发，负半轴输入的事件检测失效。
4. **M-07 `dec_rate` 未接入 DSP 链**：写 `0x29` 对抽取率零影响，滤波器配置形同虚设。

> 说明：S-02 与 S-06 是同一处代码改造的两个验收面（帧长 + 字节序），修复时应一起完成；若按"独立缺陷"计数则为 5 条。

### P1 —— 协议/映射不兼容（17 条）

| 类别 | 条目 |
|---|---|
| 寻址/帧模式 | S-04 短指令、S-05 ADDR_ASCENSION、S-07 STRICT、S-08 SINGLE_INST |
| CRC | C-01 引擎、C-02 互补使能、C-03 错误处理、C-04 帧长/收发位置 |
| 状态回传 | G-02 0x14 W1C、G-03 复位 0x09、G-04 SEND_STATUS |
| 通用/GPIO | G-05 0x1C route 位、G-06 GPIO SEL 4 bit |
| 数值寄存器 | M-02 WATERMARK 钳位 |
| 滤波器 | M-09 FILTER_SYNC 脉冲 |
| 其它映射 | M-10 0x16 位定义/复位、M-11 0x00-0x11/0x15/0x17 缺失 |

### P2 —— 健壮性/一致性（4 条）

| 条目 | 内容 |
|---|---|
| S-09 | 写数据相 SDO 未驱动 0 |
| C-05 | CLOCK_COUNT_ERR 未检测 |
| G-07 | 0x19 复位值应为 0x01 |
| M-01 | WATERMARK 复位值应为 0x4000 |

---

## 11. 与其他审查报告的交叉说明 / 待确认清单

### 11.1 交叉说明（避免两份报告结论冲突）

1. **`review_filter_fifo.md` F-02 称「全设计无任何解码逻辑」不准确**。解码实际存在于 `sinc1_decim.sv:41-47` 与 `sinc5_decim.sv:46-52`（含非法码钳位），`sinc5_comp.sv:86-95` 也实现了额外 2× 抽取。真实缺陷是 **`ad4080_top.sv:88-97` 漏接 `.dec_sel(dec_rate)`**（本报告 M-07）。结论（写 0x29 无效）一致，但根因与修法不同：不需要重写解码，只需补连线并统一端口名。
2. **`review_filter_fifo.md` F-11 称 SINC5+COMP 总抽取只有 D 不成立**。`sinc5_comp` 的 `phase` 交替逻辑每两个 `s5_valid` 才输出一次，总抽取确为 2N，与 p.94 一致。该条应从 P0 降级/撤销。
3. **`review_filter_fifo.md` E-12（GPIO SEL 3 bit）与本报告 G-06 是同一缺陷**。本报告额外指出 `1001` 被截成 `0001` 会造成「选 EXT_EVENT 却输出 FIFO_FULL」的错误映射，因此建议维持 P1、并在依赖外部事件触发 FIFO 的场景上升为 P0。
4. **`review_filter_fifo.md` R-01（状态位序）与本报告 G-01 是同一缺陷**，本报告补充了 W1C/只读位/复位 0x09 的完整改法（G-02/G-03）。

### 11.2 `[待确认]` 清单

| 编号 | 问题 | 建议处理 |
|---|---|---|
| Q1 | p.38 正文「ASC=0 时起始地址为 LSB 地址」与 Table 15、Fig.58/59 相反 | 以 Table 15 + 图表为准；请 ADI FAE 确认 |
| Q2 | CRC 位序（每字节 MSB-first？是否含方向位？）手册未明写 | 先按串行流 MSB-first 实现，TB 用已知向量对齐 |
| Q3 | streaming 后续 CRC 种子「当前起始地址」是 15 bit，种子只有 8 bit | 暂按 `addr[7:0]`，需确认 |
| Q4 | 写 `0x14` 的只读位（bit7/6/1）是否触发 `WR_TO_RD_ONLY_REG_ERR` | 手册只写「写只读寄存器」，未说寄存器内单个只读位；暂按忽略处理 |
| Q5 | 非法 `SINC_DEC_RATE` 对 FILTER_SEL=11 的「最大抽取率」是 512（总）还是 256（N） | 按 Table 29 的 512 总抽取实现（N=256 再 ×2） |
| Q6 | `0x1D/0x1E` 的钳位是写入时生效还是读出时生效 | 建议写入时钳位并回写影子寄存器；需确认读回值语义 |

---

## 12. 结论

`spi_slave.sv` 是一个**只支持「16 bit 指令 + 单字节数据」的最小可用子集**，CPOL/CPHA、方向位、指令解析本身正确；但它不具备手册默认的 streaming/多字节能力，也没有 CRC、短指令、状态回传、地址计数器，因此**只能驱动单字节寄存器（0x14/0x16/0x18/0x19/0x1C/0x29），无法正确访问任何 2 字节寄存器**。

`ad4080_regs.sv` 的字段位置大多正确（0x19/0x1C/0x29/0x14 地址译码、0x1F-0x28 的位宽与复位值基本对），主要问题集中在：
1. 状态字节**打包顺序由顶层随意拼接**（G-01），是所有问题的最高优先级；
2. **控制类寄存器整体缺失**（0x00/0x01/0x10/0x11，M-11），导致报告 2/3 章的协议特性无开关；
3. **3 处位宽/位义错误**：0x1C 丢 route 位（G-05）、GPIO SEL 3 bit（G-06）、0x16 bit1 误当 LANE_SEL（M-10）。

建议按第 10 节的 P0 → P1 顺序修复；P0 四项修完后，至少可以保证「按手册默认配置、单帧读写任意单/双字节寄存器」的功能正确性。
