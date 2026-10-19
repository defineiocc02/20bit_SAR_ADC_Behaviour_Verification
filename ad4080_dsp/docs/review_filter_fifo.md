# AD4080 数字滤波器 / FIFO / 事件检测 RTL 审查报告

- 审查对象：`ad4080_rtl/rtl/` 下 `sinc1_decim.sv`、`sinc5_decim.sv`、`sinc5_comp.sv`、`fifo_capture.sv`、`result_fifo.sv`、`event_detect.sv`、`cnv_ctrl.sv`，以及与之相关的集成文件 `ad4080_dsp_chain.sv`、`ad4080_regs.sv`、`gpio_ctrl.sv`、`ad4080_top.sv`。
- 手册依据：AD4080 Data Sheet Rev. B（`/tmp/ad4080.txt`，原始 PDF `/Users/zhaoyi/Zotero/storage/E6WK8CIC/ad4080.pdf`）。文中页码均为手册页脚 "Rev. B | xx of 95"。
- 方式：逐条比对 + 位宽/时序推演。**未修改任何 RTL 文件**，本报告只给结论与可落地改法。
- 说明：`[待确认]` 表示手册未明确或需上游确认；未标注的结论均可由所引原文直接支撑。

---

## 0. 结论表

| 编号 | 手册要求（页码） | RTL 现状 | 差异等级 | 改法摘要 |
|---|---|---|---|---|
| F-01 | FILTER_SEL 四种路径，允许抽取率：00 不滤波 / 01 SINC1 2..1024 / 10 SINC5 2..256 / 11 SINC5+COMP 4..512（p.66 Table 29） | 无任何 FILTER_SEL×抽取率合法性检查；`filter_sel` 只做 4 选 1 数据 MUX（`ad4080_dsp_chain.sv:78-85`） | **P0** | 增加 `dec_n` 解码与按 `filter_sel` 的钳位表 |
| F-02 | SINC_DEC_RATE 编码 N=2^(code+1)，1000→512（仅 SINC1），1001→1024（仅 SINC1）（p.94 Table 62） | `dec_rate` 仅寄存，全设计无任何解码逻辑（`ad4080_regs.sv:97,132`） | **P0** | 实现 `N = 1<<(code+1)` 并驱动滤波器 |
| F-03 | 非法值/非法组合"将滤波器置为最大抽取率"（p.94） | 无钳位 | P1 | 按 FILTER_SEL 分别钳到 1024/256/512 |
| F-04 | 改 FILTER_SEL 后必须由 FILTER_SYNC 复位滤波器（p.64, p.94） | 写 0x29 不产生任何复位/同步事件 | P1 | 寄存器写 0x29 产生 `filter_sync_pulse` |
| F-05 | 写滤波配置寄存器 Bits[7:0] 本身即复位滤波器（p.67） | 同上，无动作 | P1 | 同上，复用同一脉冲 |
| F-06 | SINC1 建立 = 2 + D + 1 个 CNV（p.69） | 仅文件头注释；逻辑上第 D 个 `valid_in` 后 1 拍出结果，无 settling 计数/`settled` 指示 | P1 | 增加 CNV 计数与 `settled` 输出 |
| F-07 | SINC5 建立 = 2 + 5D + 4 个 CNV（p.69） | 仅注释，无计数 | P1 | 同上 |
| F-08 | SINC5+COMP 建立 = 2 + 35D + 10 个 CNV（p.69） | 连注释都没有 | P1 | 同上 |
| F-09 | SINC5 为 5 级 CIC，需 IN_W + 5·log2(D) 位；D=256 → 60 bit | `ACC_W = IN_W + 5*LOG2R`，D=256 → 60 bit，**正确** | 无差异 | 保持；但需固定为最大值 60 以支持运行时改 D |
| F-10 | —（实现质量） | 舍入常数 `(1 << (SHIFT-1))` 为 32-bit int；SHIFT≥33（D≥128）时移位量 ≥32 → 结果为 0 | P1 | 改为 `(ACC_W'(1) << (SHIFT-1))` |
| F-11 | SINC5+COMP 总抽取 = N×2（p.94, p.66） | `sinc5_comp` 把 `DECIM` 原样传给内部 `sinc5_decim`，且 FIR 每 `s5_valid` 出一次 → 总抽取只有 D，不是 2D | **P0** | 内部 SINC5 用 `DECIM/2`，FIR 每 2 个 s5 输出做一次 |
| F-12 | 补偿滤波器系数手册未给（p.70 仅响应曲线） | 默认 `C0=1.0,C1=C2=0` 直通；文件头已声明不臆造 | P1 `[待确认]` | 需 ADI 系数或从响应拟合 |
| F-13 | ADC 结果为 20-bit **二进制补码**（p.20 Table 7；p.5 Table 1） | `sinc5_comp` 用 `$signed({1'b0, s5_data})` 当无符号，负半轴全部错误并饱和 | **P0** | 改 `$signed(s5_data)` |
| F-14 | FIFO 模式下填满一条记录需 D×WATERMARK + settling 个 CNV（p.69） | 无该约束的建模/校验 | P2 | 文档化 + TB 断言 |
| F-15 | FILTER_SYNC **低有效**，"held low 时滤波器保持复位"（p.53, p.68） | `filter_sync` 按高有效使用（`sinc1_decim.sv:50`、`sinc5_decim.sv:60,78,95`） | P1 | 顶层反相，或改成 `filter_sync_n` |
| Q-01 | 四模式语义（p.57 Table 27） | mode 00/01/10 基本对，11 见下；顶层注释自认"mode 11 按 10 处理"（`ad4080_top.sv:13`），但 `fifo_capture` 实际实现了 overwrite | P1 | 统一注释与实现，补齐 11 的细节 |
| Q-02 | 写 FIFO_MODE 即 arm（p.62） | `arm` 是顶层外部端口，**写 0x1C 完全不 arm**（`ad4080_top.sv:39,105`；`ad4080_regs.sv:84`） | **P0** | 0x1C 写产生 `fifo_mode_we` 单拍脉冲接 `arm` |
| Q-03 | 重 arm 流程：写 00 → 再写目标模式（p.60, p.63） | 无模式变化检测，任何 `arm` 电平都重 arm | P1 | 仅在 `fifo_mode_we` 时置 arm，且 00 清 armed |
| Q-04 | mode 00 同时复位并重新 arm 事件触发（p.57） | mode 00 只关 `cap_en`，`armed/event_seen/captured` 不变 | P1 | mode==00 时清内部状态 |
| Q-05 | mode 11 的 WATERMARK 必须是 4 的倍数（p.57, p.63, p.91） | 不检查、不报错 | P1 | 写 0x1D/0x1E 时钳位到 4 的倍数或置错误位 |
| Q-06 | mode 11 满后把事件位置自动写回 FIFO_WATERMARK（p.63） | 无回写路径 | P1 | `fifo_capture` 输出 `event_pos`，`ad4080_regs` 可写 |
| Q-07 | mode 11 连续覆盖最旧（p.63） | `result_fifo` 的 `overwrite_en` 实现正确（`result_fifo.sv:151,189`） | 无差异 | 保持 |
| Q-08 | mode 11 读回全部 16384（p.63） | `rd_target = DEPTH`，正确（`fifo_capture.sv:51`） | 无差异 | 保持 |
| Q-09 | FIFO_FULL 在"WATERMARK 个结果装入"时置位（p.57, p.83） | `full` 来自 `count == 16384`（`result_fifo.sv:147`）；mode 01/10 下 WATERMARK<16384 时**永不置位** | **P0** | `full` 改为 `wm_reached`（mode 01/10） |
| Q-10 | FIFO_FULL 读出第一个结果（搬入输出移位寄存器）即清（p.53） | 只有排空（count→0）才清 | P1 | 加 `full_clr`，首个 `rd_fire` 清除 |
| Q-11 | FIFO_READ_DONE = 无新数据/读完一条记录（p.56, p.83） | `done_r` 在读完 `rd_target` 后置位，语义基本对；但未接进状态字节 | P1 | 见 R-01 |
| Q-12 | 写入 WATERMARK 个样本需 WATERMARK + 3 个 CNV（p.58）；arm 后先 idle、再 2 个 CNV 才 writing（p.60） | 无 3 拍/2 拍启动延迟，`valid_in` 第一拍即写 | P1 | 加 3 拍启动状态机（若需 cycle-accurate） |
| Q-13 | WATERMARK 写 <1 钳到 1，>16384 钳到 16384（p.91） | 只做了 0→1（`fifo_capture.sv:49`），**未钳上限**；>16384 时永远到不了 watermark | P1 | `wm_eff = (wm<1)?1:(wm>16384)?16384:wm` |
| Q-14 | FIFO_WATERMARK 复位值 0x4000=16384（p.91） | 复位 0（`ad4080_regs.sv:68`） | P2 | 改 `15'h4000` |
| Q-15 | 单口 FIFO，读写不可同拍（p.56） | `result_fifo` 明确允许同拍读写（`result_fifo.sv:34-36`） | P2 | 文档化偏差，或加仲裁 |
| E-01 | HI/LO_THRESHOLD 为 **二进制补码**，HYSTERESIS 为 **无符号**，LSB 均 1.46484 mV（p.91 Table 57, p.92 Table 58） | `d12` 与阈值都按无符号比较（`event_detect.sv:33,46,50`） | **P0** | 补码有符号比较 |
| E-02 | 20-bit ADC 结果为二进制补码（p.20 Table 7, p.5 Table 1） | `d12 = data_in[19:8]` 当无符号 | **P0** | `$signed(data_in[19:8])`。**注意：不是 offset-binary，不要异或 0x800** |
| E-03 | 阈值 LSB 1.46484 mV = 256 × 20-bit LSB(5.72 µV)（p.20, p.91-92） | 取 `[19:8]` 的位对齐正确 | 无差异 | 保持 |
| E-04 | HI 在 ≥HI_THRESHOLD 置位、<HI−HYST 清除；LO 反之（p.56 Fig.84） | 方向正确 | 无差异 | 保持 |
| E-05 | HI_STATUS / LO_STATUS 各自独立 W1C（p.55, p.83） | 单一 `clear` 同时清两路 | P1 | 拆成 `hi_clr` / `lo_clr` |
| E-06 | ALERT **低有效**，且是**电平型**：阈值不再越界即回高（p.55, p.53） | `alert = hi_flag \| lo_flag`，高有效且粘滞 | **P0** | 独立电平比较，输出 `alert_n` |
| E-07 | HI_ROUTE/LO_ROUTE 可分别屏蔽到 ALERT/状态/FIFO（p.54, p.90） | 无 route 输入；屏蔽位也没存（`ad4080_regs.sv:84` 只存 bit7 和 [1:0]） | P1 | 增加掩码与 0x1C bit6/5 存储 |
| E-08 | INT_EVENT_EN=0 走外部 EXT_EVENT，=1 走内部阈值（p.54 Table 26） | `event` 是顶层裸输入，无内外选择 | P1 | 内部/外部二选一 |
| E-09 | 事件到状态位/ALERT 有 2 个 CNV 延迟（p.55） | 0 拍（同拍置位） | P2 | 加 2 级 CNV 域延迟 |
| E-10 | 阈值检测对象是所选滤波器的输出（p.54） | `event_detect` **未被实例化**，`ad4080_top` 无阈值检测 | **P0** | 接入 `chain_y/chain_valid` |
| E-11 | —（集成） | `event_detect`、`gpio_ctrl`、`cnv_ctrl` 均为孤立模块，顶层未实例化 | **P0** | 顶层接线 |
| E-12 | GPIO_x_SEL 为 4 bit，编码 0000..1001（p.53 Table 25, p.89） | `gpio_sel` 只有 3 bit，编码/顺序完全不同（`gpio_ctrl.sv:27-28`） | P1 | 扩到 4 bit 并按表重排；ALERT/FILTER_RDY 反相 |
| R-01 | DEVICE_STATUS 0x14：bit7 FIFO_FULL、bit6 FIFO_READ_DONE、bit5 HI_STATUS、bit4 LO_STATUS、bit3 POR_ANA、bit2 ADC_CNV_ERR、bit1 ROM_CRC_ERR、bit0 POR_FLAG（p.83 Table 47）；复位 0x09 | `status_in` 塞的是 `{fifo_full, fifo_empty, fifo_ovf, fifo_unf, clip_low, clip_high, captured, chain_valid}`，与手册**逐位不符**（`ad4080_top.sv:123`） | **P0** | 按表重排；`read_done`/`hi`/`lo` 接进去 |
| R-02 | 0x14 的 HI/LO/POR/CNV_ERR 为 W1C（p.83） | 无 W1C 逻辑，`status_in` 只读 | P1 | 寄存器内实现 W1C |

> 统计：**P0 = 9 组**（F-01+F-02、F-11、F-13、Q-02、Q-09、E-01+E-02、E-06、E-10+E-11、R-01），**P1 = 17 条**，**P2 = 6 条**（明细见第 5 节）。

---

## 1. 数字滤波器

### 1.1 四种滤波配置与允许的抽取率（F-01/F-02/F-03）

**手册依据（p.66 Table 29）**

> "Table 29. Digital Filters Decimation Options According to FILTER_SEL Bits
> 0b00 No filtering (default) — No decimation
> 0b01 SINC1 filter — 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024
> 0b10 SINC5 filter — 2, 4, 8, 16, 32, 64, 128, 256
> 0b11 SINC5 + compensation filter — 4, 8, 16, 32, 64, 128, 256, 512"

**手册依据（p.94 Table 62）**

> "[6:3] SINC_DEC_RATE Decimation Factor. These bits set the Sinc Decimation Factor N. The filter compensation block incurs an additional 2× decimation. The total decimation for a selected filter is sinc1 = N, sinc5 = N, or sinc5 + compensation = N × 2.
> 0000: N = 2. … 0111: N = 256. 1000: N = 512 (sinc1 only). 1001: N = 1024 (sinc1 only).
> For a selected filter, setting invalid values, outside of those specified here, will set the filter at the maximum decimation rate."

**RTL 现状**

- `ad4080_regs.sv:97`：`16'h0029: begin fsel_r <= wdata[1:0]; dec_r <= wdata[6:3]; end` —— 只寄存。
- `ad4080_regs.sv:132`：`assign dec_rate = dec_r;` —— 无消费者。
- `ad4080_dsp_chain.sv:22-23,60-76`：三条滤波器支路都用同一个 **elaboration 期常量** `DECIM`。
- `ad4080_top.sv:14` 自己写明："运行时抽取率 dec_rate 只寄存，链路的 DECIM 仍是编译期参数。"

**差异**：编码 N=2^(code+1) 在 RTL 中完全不存在；FILTER_SEL 与抽取率的交叉合法性也不存在。这是"配置能写进去但完全不生效"的功能级缺陷。

**可落地改法**（放进 `ad4080_regs.sv` 或新的 `filter_cfg_decode.sv`）

```systemverilog
logic [3:0] dec_code;
logic [1:0] fsel;
logic [10:0] n_dec;      // N，2..1024
logic [10:0] r_total;    // 实际总抽取率

always_comb begin
    n_dec = 11'(1) << (dec_code + 4'd1);   // 0000->2 ... 1001->1024
    unique case (fsel)
        2'b00:   r_total = 11'd1;                              // bypass
        2'b01:   r_total = (n_dec > 11'd1024) ? 11'd1024 : n_dec;
        2'b10:   r_total = (n_dec > 11'd256)  ? 11'd256  : n_dec;
        2'b11:   r_total = (n_dec > 11'd256)  ? 11'd512  : (n_dec << 1);
        default: r_total = 11'd1;
    endcase
end
```

要点：
1. `n_dec` 用 11 位（1024 需要 11 位）；`dec_code+1` 最大 10，`11'(1)<<10 = 1024` 正确。
2. FILTER_SEL=11 时，编码 0111（N=256）对应总抽取 512，正好是允许上限；编码 1000/1001 对 SINC5/COMP 非法，按 p.94 钳到最大（256/512）。
3. **真正的架构改动**：`sinc1_decim`/`sinc5_decim` 的 `DECIM` 必须从 `parameter` 改为运行时端口（或在每个合法档位例化后 MUX）。位宽不能再依赖 `$clog2(DECIM)`，必须取该滤波器的最坏情况常量：

```systemverilog
// SINC1: 最大 D=1024
localparam int S1_ACC_W = IN_W + 10;      // 20+10 = 30
// SINC5: 最大 D=256
localparam int S5_ACC_W = IN_W + 5*8;     // 20+40 = 60
```

计数器改为与 `r_total-1` 比较，累加器宽度保持上述常量不变（见 1.4 的位宽论证）。

---

### 1.2 建立时间 / group delay（F-06/F-07/F-08）

**手册依据（p.69）**

> "Settling CNV PulsesSINC1 = 2 + D + 1"
> "Settling CNV PulsesSINC5 = 2 + 5 × D + 4"
> "Settling CNV PulsesSINC5 + COMP = 2 + 35×D+10"
> "Where D equals the decimation rate 2, 4, 8 …"
> "Filter Settling Time = Settling CNV Pulses_FILTERTYPE × tCONV"

**RTL 现状**

- `sinc1_decim.sv:6` 注释 `settling CNV pulses = 2 + D + 1`；`sinc5_decim.sv:6` 注释 `2 + 5*D + 4`。**只有注释，没有逻辑。**
- 实际行为：第 D 个 `valid_in` 那一拍之后 1 拍给出 `valid_out`（`sinc1_decim.sv:62-66`）；SINC5 同样在第 D 个 `valid_in` 后（`dec_en`）出结果（`sinc5_decim.sv:64-70,104-111`）。
- `sinc5_comp.sv` 的文件头（:1-14）完全没有 35D+10 的记载。
- 顶层没有任何"首个有效结果之前的数据无效"的标记；`cnv_ctrl` 未实例化，`valid_in` 是外部端口，2 个 CNV 的 ADC 流水延迟没有建模。

**差异**：如果 `valid_in` 已经代表"该次转换结果已可用"，那么滤波器本身只需 D 个样本；但手册的 2+D+1 是以 **CNV 上升沿**为基准的绝对时间。RTL 既没有把 `valid_in` 与 CNV 对齐，也没有 `settled` 输出，因此：
- 无法保证第一个输出对应"已建立"的结果；
- 无法实现 p.69 的 `Total Required CNVs = D×WATERMARK + Settling CNV Pulses`。

**改法**：给每条滤波支路加 CNV 计数与 `settled` 指示。

```systemverilog
// 以 CNV（valid_in）为基准
localparam int SETTLE_S1 = 2 + 1;      // D 另算：2 + D + 1
logic [15:0] cnv_cnt;
logic        settled;

always_ff @(posedge clk or negedge rst_n)
  if (!rst_n || filter_sync_n) begin
      cnv_cnt <= '0;
      settled <= 1'b0;
  end else if (valid_in) begin
      if (cnv_cnt != 16'hFFFF) cnv_cnt <= cnv_cnt + 1'b1;
      if (cnv_cnt >= (16'(r_total) + 16'(2 + 1 - 1))) settled <= 1'b1;
  end

assign valid_out_gated = valid_out && settled;
```

SINC5 的阈值换成 `5*r_total + 4 + 2 - 1`，COMP 换成 `35*r_total + 10 + 2 - 1`（`r_total` 为总抽取率）。

> 若本设计定位为"行为级参考模型、不追求 cycle-accurate"，至少应把 `settled` 作为端口留出并在 TB 中断言，而不是只写在注释里。

---

### 1.3 SINC1 实现核查

`SINC1(D)` 的传递函数是长度 D 的矩形窗，抽取点上的块平均与滑动平均等价，`sinc1_decim.sv:9-14` 的说明正确。

逐项核查：

| 项 | 结论 |
|---|---|
| 累加位宽 `ACC_W = 20 + ceil(log2 D)`（:34） | D=1024 时 30 bit；最大累加值 1024×(2^20−1) < 2^30，**不溢出** |
| 舍入 `(sum + D/2) >> log2D`（:41,63） | `sum` 声明为 `[ACC_W:0]`（31 bit），加 D/2 后仍不溢出；`sum[LOG2D +: IN_W]` 取位正确 |
| `acc` 清零时机（:65） | 最后一个样本只参与 `sum` 的组合加法，不写入 `acc`，正确 |
| 补码输入 | 模 2^ACC_W 的加法 + 截取低 20 位，对补码输入是**正确**的（与有符号算术等价位模式） |
| D=1 分支（:57-61） | FILTER_SEL=00 是 bypass，不会走 D=1；保留无害 |
| `filter_sync` 极性（:50） | **高有效**，与手册"active low"相反（见 1.6） |
| 运行时 D | **不支持**（参数） |

结论：算法本身无溢出、无舍入错误；问题集中在"不可运行时配置"和"无 settling"。

---

### 1.4 SINC5 的 CIC 位宽增长（F-09/F-10）

**手册依据**：SINC5 为 5 级 CIC，允许 D=2..256（p.66 Table 29）。5 级积分器对满量程直流输入的增益为 D^5，需要 `IN_W + 5·log2(D)` 位。

**RTL 现状**

```systemverilog
// sinc5_decim.sv:33-35
localparam int LOG2R = $clog2(DECIM);
localparam int ACC_W = IN_W + 5*LOG2R;
localparam int SHIFT = 5*LOG2R;
```

- D=256 → `LOG2R=8` → `ACC_W=20+40=60`，与"20 + 5·8 = 60"一致，**位宽正确，不会溢出**。
- 注释（:13-14）也明确写了 "R=256 时 20+40=60 bit"，与实际代码一致。
- 积分器/梳状器/`comb_in` 全部使用 `ACC_W`（:37-42），没有混用窄位宽。

**但存在一个实现 bug**：

```systemverilog
// sinc5_decim.sv:109
data_out <= (comb_nxt[4] + (1 << (SHIFT-1))) >> SHIFT;
```

`1` 是 32-bit 有符号整型常量，`SHIFT-1 = 5·log2R − 1`。当 D ≥ 128 时 `SHIFT ≥ 35`，`1 << 34` 及更大移位在 32 位上下文中结果为 **0**（部分仿真器给 X）。于是 D=128/256 时舍入项消失，输出系统性偏小半个 LSB（量化偏差，不溢出）。

**改法**

```systemverilog
localparam logic [ACC_W-1:0] ROUND = ACC_W'(1) << (SHIFT-1);   // 或 {{(ACC_W-SHIFT){1'b0}}, 1'b1, {(SHIFT-1){1'b0}}}
...
data_out <= (comb_nxt[4] + ROUND) >> SHIFT;
```

> 另外：若将来支持运行时 D，`ACC_W` 必须固定为 60（最大 D=256），不能再用 `$clog2(DECIM)`。

---

### 1.5 SINC5 + 补偿滤波器（F-11/F-12/F-13）

**手册依据（p.94 Table 62）**

> "The filter compensation block incurs an additional 2× decimation. The total decimation for a selected filter is … sinc5 + compensation = N × 2."

**手册依据（p.66 Table 29）**：SINC5+COMP 允许总抽取率 4, 8, …, 512。

**RTL 现状与差异**

```systemverilog
// sinc5_comp.sv:36-40
sinc5_decim #(.IN_W(IN_W), .DECIM(DECIM)) u_sinc5 (...);
```

1. **总抽取率错**：内部 SINC5 直接用 `DECIM`，FIR 又在**每个** `s5_valid` 上输出一次（:62-69）。因此总抽取率 = `DECIM`，不是 `2×N`。
   - 若调用者传 N（2..256），总抽取率少一半；
   - 若调用者传 2N（4..512），内部 SINC5 就在 2N 上工作，而 SINC5 本身只允许 ≤256，且频响不对。
2. **补码被当无符号**：

```systemverilog
// sinc5_comp.sv:47-49
acc = $signed({1'b0, s5_data}) * C0
    + $signed({1'b0, x1})     * C1
    + $signed({1'b0, x2})     * C2
    + (1 << 14);
```

`s5_data` 是 SINC5 的输出。ADC 结果是 20-bit **二进制补码**（p.20 Table 7：`FS−1 LSB = 0x7FFFF`，`−FS = 0x80000`；p.5 Table 1："Data Format … Twos complement"）。SINC5 的模运算会把补码原样传下来，所以 `s5_data` 是补码。`{1'b0, s5_data}` 把负数变成 2^19..2^20 的大正数，乘以 `C0=32768` 后必然落进饱和分支（:65-67），**负半轴输出全错**。

**改法**

```systemverilog
localparam int N_SINC = DECIM / 2;          // 手册：总抽取 = N×2

sinc5_decim #(.IN_W(IN_W), .DECIM(N_SINC)) u_sinc5 (
    .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
    .valid_in(valid_in), .data_in(data_in),
    .data_out(s5_data), .valid_out(s5_valid));

// 2 抽取 + 3-tap FIR
logic        phase;
logic [IN_W-1:0] x0, x1, x2;
logic            fir_valid;

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n || filter_sync) begin
        phase <= 1'b0; x1 <= '0; x2 <= '0;
        fir_valid <= 1'b0; data_out <= '0;
    end else begin
        fir_valid <= 1'b0;
        if (s5_valid) begin
            phase <= ~phase;
            x2 <= x1;
            x1 <= s5_data;
            if (phase) begin                       // 每 2 个 s5 输出取 1 个
                x0 = s5_data;
                acc = $signed(x0) * $signed(C0)
                    + $signed(x1) * $signed(C1)
                    + $signed(x2) * $signed(C2)
                    + 20'sd16384;
                // acc 位宽需 >= 20+16+2 = 38，建议 40 bit signed
                fir_valid <= 1'b1;
                // y = acc >>> 15，再饱和到 20-bit 补码
            end
        end
    end
end
```

同时把饱和改成有符号边界：

```systemverilog
localparam logic signed [IN_W-1:0] MAX20 = 20'sh7FFFF;
localparam logic signed [IN_W-1:0] MIN20 = 20'sh80000;
if      ($signed(y) > MAX20) data_out <= MAX20;
else if ($signed(y) < MIN20) data_out <= MIN20;
else                         data_out <= y[IN_W-1:0];
```

**系数（F-12）**：手册 p.70 只给 Figure 105-107 的幅频响应，**没有给系数**。默认 `C0=1.0, C1=C2=0` 只能算直通，不能声称实现了"补偿"。需向 ADI 索取或用响应曲线拟合后回填，标 `[待确认]`。

---

### 1.6 FILTER_SYNC 与滤波器复位（F-04/F-05/F-15）

**手册依据（p.67 "Filter Reset Conditions"）**

> "Direct LVDS: the AD4080 resets the filter by the following two methods:
> ► By configuring the filter, by issuing a write to the filter configuration register, Bits[7:0] (Address 0x29).
> ► By asserting a GPIO that's configured for FILTER_SYNC operation.
> With FIFO: When the FIFO is enabled, the user must use a GPIO configured as FILTER_SYNC to reset the filter for each FIFO acquisition."

**手册依据（p.53 Table 25）**

> "1000b FILTER_SYNC … When held low, this input holds the digital filter in reset."

**手册依据（p.68）**

> "This reset must be given on the first CNV rising edge, where the FILTER_SYNC signal must be brought low at least 15ns prior to the CNV edge and then released at least 5ns before the next rising edge."

**RTL 现状**

- `sinc1_decim.sv:50`、`sinc5_decim.sv:60,78,95`：`if (filter_sync) … acc <= 0` —— **高有效**。
- 写 0x29 不产生任何复位（`ad4080_regs.sv:97` 只写寄存器）。
- FIFO 采集不触发 filter_sync（`ad4080_top.sv:103-112` 的 `arm` 与 `filter_sync` 是两路独立输入）。

**差异**：
1. 极性相反 —— 实际芯片里 GPIO 常态为高，等于把滤波器一直按在复位里。
2. 缺"写配置即复位"这一条通路。
3. 缺"每次 FIFO 采集前必须复位滤波器"的联动。

**改法**

```systemverilog
// ad4080_regs.sv：0x29 写产生单拍同步脉冲
output logic filter_cfg_we;
always_ff @(posedge clk or negedge rst_n)
  if (!rst_n)                 filter_cfg_we <= 1'b0;
  else                        filter_cfg_we <= we && (addr == 16'h0029);

// top：低有效合并
assign filter_sync_n = ~(filter_sync_pin | filter_cfg_we | fifo_arm_pulse);
```

滤波器内部条件改为 `if (!rst_n || !filter_sync_n)`，或直接把端口改名为 `filter_sync_n`。15ns/5ns 的建立保持属于板级/引脚时序，数字模型可只在 TB 中约束。

---

## 2. 结果 FIFO

### 2.1 四种模式（Q-01/Q-04/Q-07/Q-08）

**手册依据（p.57 Table 27）**

> "00 FIFO disabled — FIFO is not used. **This value also resets and rearms the event trigger.**
> 01 Immediate trigger mode — data capture is initiated immediately after receipt of the first valid converter result and continues until [N = WATERMARK] results are loaded.
> 10 Event trigger capture, read latest WATERMARK — capture initiated by the user-selected event method … data captures stop once [N = WATERMARK] results are captured. Only result data after the event is of interest.
> 11 Event trigger capture mode, read all FIFO — The data capture immediately initiates after the receipt of the first valid converter result. … the user can read [N = WATERMARK] results after the event and (16,384 − [N = WATERMARK]) before the event. Only WATERMARK values that are multiples of four are valid."

**RTL 现状逐模式核对**（`fifo_capture.sv:53-61`）

| 模式 | 手册 | RTL | 结论 |
|---|---|---|---|
| 00 | 关闭；复位并重新 arm 事件触发 | `cap_en=0`，但 `armed/event_seen` 不清 | 部分符合，缺复位语义 |
| 01 | arm 后立即写，写满 WATERMARK 停 | `cap_en=armed`，`wr_cnt<wm_eff` 才写（:63-64,99-105） | 基本符合 |
| 10 | 事件后写 WATERMARK 个 | `cap_en=armed && event_seen` | 符合 |
| 11 | arm 后即连续写、满则覆盖最旧；事件后再写 WATERMARK 个后停；读全部 16384 | `cap_en=armed`、`overwrite=1`、`wr_en=armed&&valid_in`；事件后 `post_cnt` 计到 `wm_eff` 再停（:89-97）；`rd_target=DEPTH` | **基本符合**，但缺 4 倍数校验、事件位置回写、FIFO_FULL 定义 |

补充：`ad4080_top.sv:13` 的注释说"mode 11 按 10 处理"，但 `fifo_capture` 实际已经实现了 overwrite 路径。**注释与实现不一致**，会误导后续维护，应更正。

`post_cnt` 的计数边界是正确的：写第 WATERMARK 个后置 `captured` 并撤销 `armed`，正好 WATERMARK 个。

---

### 2.2 arm / rearm（Q-02/Q-03）

**手册依据（p.62）**

> "As in all cases of arming the FIFO, the first rising edge after a FIFO_MODE write command arms the FIFO for data capture; however, no data is written to the FIFO until an event of the selected method occurs."

**手册依据（p.60 / p.63）**

> "a rearming event for immediate mode capture involves disabling the FIFO by writing 00 to FIFO_MODE then re-enabling by writing 01 to the FIFO mode to arm the FIFO for a new capture."
> "The FIFO is firstly disabled by writing 00 to the FIFO_MODE bits before, and then rearmed by again enabling the required capture mode."

**RTL 现状**

```systemverilog
// ad4080_top.sv:39
input  logic arm,
// ad4080_top.sv:105
.arm(arm),
```

`arm` 是顶层裸端口，与 SPI 写 0x1C **没有任何连接**；`ad4080_regs.sv` 也没有 `fifo_mode_we` 输出。因此**通过配置接口根本无法启动一次采集**。这是 P0。

另外 `arm` 是电平（`fifo_capture.sv:76`），若上位机把它拉高，`armed` 会每个时钟都被重新置 1，`captured` 永远无法保持。

**改法**

```systemverilog
// ad4080_regs.sv
output logic fifo_mode_we;
always_ff @(posedge clk or negedge rst_n)
  if (!rst_n) fifo_mode_we <= 1'b0;
  else        fifo_mode_we <= we && (addr == 16'h001C);

// top
fifo_capture u_capture (
    .arm(fifo_mode_we),      // 写 0x1C 即 arm
    ...
);
```

`fifo_capture` 内部把 `if (arm)` 改成边沿触发（或直接依赖单拍脉冲）：

```systemverilog
if (arm && (mode != 2'b00)) begin ... end
else if (mode == 2'b00) begin
    armed <= 1'b0; event_seen <= 1'b0; captured <= 1'b0; done_r <= 1'b0;
    wr_cnt <= '0; post_cnt <= '0; rd_cnt <= '0;
end
```

"写同值是否重新 arm"：手册 p.62 说"a FIFO_MODE write command arms"，字面上任何写都 arm；但 p.60/p.63 又要求先写 00。**两处表述不一致，标 `[待确认]`**。建议实现为"任何一次 0x1C 写都产生 arm 脉冲"，这样两种读法都能工作（写 00 时 `mode==00` 分支会清状态，下一次写目标模式再 arm）。

---

### 2.3 WATERMARK 语义（Q-05/Q-06/Q-13/Q-14）

**手册依据（p.91 Table 56）**

> "This is the watermark value. If the user writes a value <1, it is clipped at 1. If >16,384, clipped at 16,384."
> "… In event trigger capture mode, read all FIFO, this value must be set as a multiple of four only."
> Reset: `0x4000` (= 16384)

**手册依据（p.63）**

> "Only multiples of four are valid values to set the FIFO_WATERMARK register when using this mode."
> "In this mode, once the FIFO is filled, the position in the FIFO memory at which the event occurred gets automatically stored in the FIFO_WATERMARK register."
> "the first result after the event triggered is located at 16384 − (FIFO_WATERMARK − 1)"

**RTL 现状**

```systemverilog
// fifo_capture.sv:49
assign wm_eff = (watermark == 15'd0) ? 15'd1 : watermark;
```

- 下限 1：**正确**。
- 上限 16384：**缺失**。`watermark` 是 15 bit（最大 32767），若写 20000，`wr_cnt` 永远到不了，`captured` 永不置位，且 FIFO 会先满并拒绝写入。
- 4 的倍数：不检查。
- 事件位置回写 0x1D/0x1E：无通路。
- 寄存器复位值：`wm_r <= 15'd0`（`ad4080_regs.sv:68`），手册是 `0x4000`。

**改法**

```systemverilog
// 钳位
logic [14:0] wm_clip;
assign wm_clip = (watermark < 15'd1)    ? 15'd1    :
                 (watermark > 15'd16384)? 15'd16384: watermark;

// mode 11 强制 4 的倍数（向下取整，或置错误位）
assign wm_eff = (mode == 2'b11) ? (wm_clip & 15'h3FFC) : wm_clip;
```

```systemverilog
// ad4080_regs.sv: 复位值
wm_r <= 15'h4000;
```

事件位置回写需要 `fifo_capture` 增加一个输出，例如 `event_pos`，在 `captured` 时把 `wr_ptr` 快照送出，再由寄存器块写入 `wm_r`（同时需要一条内部写通路）。手册给的读回关系是"事件后第一个结果位于 `16384 − (WM − 1)`"，实现时要保证回写的值与这个索引口径一致 `[待确认]`（手册没有给出回写值的精确定义，只说明"事件发生的位置"）。

---

### 2.4 FIFO_FULL / FIFO_READ_DONE（Q-09/Q-10/Q-11）

**手册依据（p.57）**

> "When N = WATERMARK is reached, that is, when the conversion result corresponding to the specified count in the FIFO_WATERMARK register is loaded into the data FIFO, memory is set as full, and the FIFO_FULL bit gets asserted in device status register."

**手册依据（p.83 Table 47）**

> "7 FIFO_FULL FIFO Full Status Flag. 0: FIFO Not Full. 1: FIFO Full."
> "6 FIFO_READ_DONE FIFO Read Done Flag. 0: FIFO Read Not Done. 1: FIFO Read Done."

**手册依据（p.53 Table 25）**

> "The FIFO full indicator is set when the conversion result corresponding to the specified count in the FIFO watermark registers … is loaded into the data FIFO. **The FIFO full status bit is cleared by reading data from the FIFO, and it is cleared when the first conversion result is moved from the FIFO to the data interface output shift register.**"

**RTL 现状**

```systemverilog
// result_fifo.sv:147
assign full = (count == V_DEPTH);   // 物理满：16384
```

`fifo_capture.full` 直接接这个 `full`（:124,32）。后果：
- mode 01/10 且 WATERMARK = 1000 时，`FIFO_FULL` **永远为 0**，主机按手册轮询会死等。
- 只有 WATERMARK=16384 时两者才重合。
- FIFO_FULL 的清除也不对：RTL 要等 count 回 0，手册要求"第一个结果被搬出"即清。

**改法**

在 `fifo_capture` 内产生水线到达信号，并作为状态位源：

```systemverilog
logic wm_reached;
always_ff @(posedge clk or negedge rst_n)
  if (!rst_n)                 wm_reached <= 1'b0;
  else if (arm)               wm_reached <= 1'b0;
  else if (wr_en && (overwrite ? (event_seen && post_cnt == wm_eff-1)
                               : (wr_cnt == wm_eff-1)))
                              wm_reached <= 1'b1;
  else if (rd_fire_first)     wm_reached <= 1'b0;   // 首个读即清

assign fifo_full_status = wm_reached;
```

`FIFO_READ_DONE`：现有 `done_r` 在读完 `rd_target` 后置位，语义正确；问题是它没有进入 `DEVICE_STATUS` 的 bit6（见 R-01），而且只有 `arm` 能清。手册说"when the FIFO is first enabled"清——`arm` 等价于 enable，可接受。

**mode 11 的 FIFO_FULL**：手册没有单独说明读全部模式下 FULL 是在"物理满 16384"还是"事件后 WATERMARK 到"。现有实现是物理满，暂标 `[待确认]`，需在验证计划中明确。

---

### 2.5 写入时序与容量（Q-12/Q-15）

**手册依据（p.58）**

> "Internal timing dictates that FIFO_WATERMARK + three conversion clocks are required to write FIFO_WATERMARK sample results into the FIFO memory."

**手册依据（p.60）**

> "Upon receipt of the update to the general configuration register (Address 0x1C), the FIFO controller advances to an idle state on the next rising edge of CNV. The FIFO then advances to the writing state after two further CNV clock edges and begins filling the FIFO until WATERMARK results are loaded."

**RTL 现状**：`cap_en = armed` 后，第一个 `valid_in` 立即写入（`fifo_capture.sv:63`）。没有 idle→writing 的 3 拍状态机。对于行为模型可接受，但对于需要与手册时序图逐拍对齐的验证环境是 P1。

**单口限制（p.56）**

> "As a single port memory, simultaneous data interface read and ADC conversion result write operations are not permitted to the FIFO."

`result_fifo.sv:34-36` 明确写了"写与读可同拍发生（各自独立判 full/empty）"，并实现了双口行为。这是**有意为之的偏差**，不构成数据错误，但应在交付说明中保留登记（P2）。

---

### 2.6 `result_fifo` 覆盖逻辑核查（Q-07）

```systemverilog
// result_fifo.sv:151,189
assign wr_fire = wr_en && (!full || overwrite_en);
if (wr_fire && full && overwrite_en && !rd_fire) rd_ptr <= rd_ptr + ONE;
```

- 满 + 覆盖 + 无读：`wr_ptr+1`、`rd_ptr+1`，`count` 保持 16384，最旧被丢弃 —— **正确**。
- 满 + 覆盖 + 同拍读：`wr_ptr+1`、`rd_ptr+1`（只加一次，由 `rd_fire` 分支执行），`count` 保持 —— **正确**，且不会多丢一个。
- 未满 + 覆盖：正常追加，不丢 —— **正确**。
- 覆盖模式下 `overflow` 被抑制（:201 的 `!overwrite_en`）—— 符合"覆盖最旧不报错"的语义。

这一块实现是对的，保留。

---

## 3. 事件检测

### 3.1 码制：这是最严重的一处（E-01/E-02/E-03）

**手册依据（p.20 Table 7）**

> "Table 7. Input Voltage to Output Code Mapping
> Description | Analog Input Voltage Difference | **Digital Output Code (Twos Complement, Hex)**
> FS − 1 LSB | +VREFIN × (1 − 1/2^19) | 0x7FFFF
> Midscale | 0 | 0x00000
> −FS | −VREFIN | 0x80000"

**手册依据（p.5 Table 1）**

> "LOW VOLTAGE DIFFERENTIAL SIGNALING (LVDS) … Data Format Serial LVDS data output **Twos complement**"
> "DIGITAL OUTPUTS (SDOx) … Data Format … **Twos complement**"

**手册依据（p.92 Table 58 / p.91 Table 57）**

> "[11:0] HI_THRESHOLD … **Twos complement** data format where LSB = 1.46484 mV. 0x800 represents −2048 × LSB, and 0x7FF represents +2047 × LSB."
> "[11:0] LO_THRESHOLD … **Twos complement** data format where LSB = 1.46484 mV."
> "[10:0] HYSTERESIS … **Unsigned** data format where LSB = 1.46484 mV."

**位对齐**：20-bit LSB = 5.72 µV（p.20："resulting in an LSB size of 5.72 μV"）；阈值 LSB = 1.46484 mV。满量程 2×VREFIN = 6 V 时，6 V / 2^20 = 5.72 µV、6 V / 2^12 = 1.4648 mV，两者恰好 **256:1**。因此阈值对应 20-bit 结果的高 12 位 `data_in[19:8]`。RTL 的取位（`event_detect.sv:33`）**正确**。

**RTL 现状**

```systemverilog
// event_detect.sv:30-35
logic [11:0] d12;
assign d12 = data_in[19:8];                                  // 无符号
assign hi_clr_th = $signed({1'b0, hi_thresh}) - $signed({2'b0, hysteresis});  // hi_thresh 被当成无符号正数
assign lo_clr_th = $signed({1'b0, lo_thresh}) + $signed({2'b0, hysteresis});

// :46-51
if (d12 >= hi_thresh) ...                                    // 无符号比较
if (d12 <= lo_thresh) ...
```

后果：
1. `HI_THRESHOLD = 0x800`（−2048）本意是"低门限之外"，RTL 却当成 +2048，结果几乎不触发。
2. `HI_THRESHOLD = 0x7FF`（+2047）与 `d12` 无符号比较时，`d12` 的负半轴（0x800..0xFFF）全部 `>= 0x7FF`，会被当成"高于高阈值"而误触发。
3. `LO_THRESHOLD = 0x7FF` 时同理。
4. 迟滞的加减法也因为阈值被当成无符号正数而失去意义。

**关于"offset-binary"的说明（重要）**

任务书提出的做法是 `data[19:8] ^ 12'h800` 再比较。**这与 Rev.B 手册冲突**：手册在 p.20 Table 7 与 p.5 Table 1 两处明确写的是 binary **two's complement**，不是 offset binary。若对补码再加 0x800，等价于把所有码平移 2048 LSB（约 11.7 mV），会让阈值判定整体偏移半个量程。

因此本报告的改法是**按补码**：`d12 = $signed(data_in[19:8])`。如果上游有额外证据表明实际数据通路在某处做了 offset-binary 转换（例如 `lvds_framer`/`spi_slave` 的输出格式与内核不同），需要补充说明后再决定是否异或——这一点标 **`[待确认]`**，但**不能**在无证据的情况下直接 XOR。

**改法**

```systemverilog
logic signed [11:0] d12;
logic signed [11:0] hi_th_s, lo_th_s;
logic signed [12:0] hyst_s, hi_clr_th, lo_clr_th;

assign d12     = $signed(data_in[19:8]);      // 20-bit 补码的高 12 位
assign hi_th_s = $signed(hi_thresh);          // 12-bit 补码
assign lo_th_s = $signed(lo_thresh);
assign hyst_s  = $signed({1'b0, hysteresis}); // 11-bit 无符号 -> 12-bit 正数 -> 13-bit

assign hi_clr_th = {hi_th_s[11], hi_th_s} - hyst_s;   // 13-bit 有符号，范围 -4095..+2047
assign lo_clr_th = {lo_th_s[11], lo_th_s} + hyst_s;   // 13-bit 有符号，范围 -2048..+4094

// 比较
if (d12 >= hi_th_s)                          hi_flag <= 1'b1;
else if (d12 < hi_clr_th)                    hi_flag <= 1'b0;

if (d12 <= lo_th_s)                          lo_flag <= 1'b1;
else if (d12 > lo_clr_th)                    lo_flag <= 1'b0;
```

注意 `{hi_th_s[11], hi_th_s}` 是把 12 位补码**符号扩展**到 13 位；原来 `$signed({1'b0, hi_thresh})` 是零扩展，对负阈值是错的。

---

### 3.2 迟滞方向（E-04）

**手册依据（p.56）**

> "A single hysteresis setting is configured, that is then applied to both the HI_THRESHOLD and LO_THRESHOLD bits. The high and low detection flags remain set until the hysteresis thresholds are crossed."

RTL 的方向：HI 清除门限 = `HI − HYST`，LO 清除门限 = `LO + HYST`（`event_detect.sv:34-35,47,51`），与 Figure 84 的"回差带"一致，**方向正确**。修好码制后这段逻辑可以直接用。

---

### 3.3 标志清除（E-05）

**手册依据（p.55）**

> "Each of these two bits are independently cleared when a 1 is written to these bits. Power cycling or device reset also result in the bits clearing."

**手册依据（p.83）**

> "5 HI_STATUS High Threshold Detection Status Flag. **Writing 1 to this bit clears it.** … R/W1C"
> "4 LO_STATUS … **Writing 1 to this bit clears it.** … R/W1C"

**RTL 现状**：`event_detect.sv:41` 只有一个 `clear` 输入，同时清 `hi_flag` 和 `lo_flag`。无法只清一路。

**改法**

```systemverilog
input logic hi_clr, lo_clr;

...
end else begin
    if (hi_clr) hi_flag <= 1'b0;
    if (lo_clr) lo_flag <= 1'b0;
    if (!hi_clr && sample_valid) begin ... end
    if (!lo_clr && sample_valid) begin ... end
end
```

寄存器侧（`ad4080_regs.sv`）在 `we && addr==16'h0014` 时按位产生 W1C 脉冲：

```systemverilog
if (we && addr == 16'h0014) begin
    if (wdata[5]) hi_clr_pulse <= 1'b1;
    if (wdata[4]) lo_clr_pulse <= 1'b1;
end
```

另外 `event_detect.sv:41` 的 `|| !enable` 会在 `INT_EVENT_EN=0` 时清标志。手册只说"只有置 1 后才用 ADC 结果做检测"，**没有说关闭时清标志**，建议改为只在 `enable` 时更新、关闭时保持，标 `[待确认]`。

---

### 3.4 ALERT 极性与类型（E-06）

**手册依据（p.55）**

> "A GPIO configured in this mode is **normally high, with a logic low indicating that an event has occurred**. As indicated in Figure 84, this GPIO **remains low only while the threshold level is crossed, and it returns to logic high as soon as the threshold bound is no longer crossed**, and the timing in Figure 83 is satisfied."
> "When a CNV+ rising edge occurs where the analog input no longer crosses the set threshold, ALERT de-asserts two conversion cycles later. **Any HI_DTCT or LO_DTCT already set is not cleared at this point.** These signals are only cleared by writing 1 to the relevant bits."

**手册依据（p.53 Table 25）**

> "0110b ALERT Status Alert (Active Low) Output."

**RTL 现状**

```systemverilog
// event_detect.sv:55
assign alert = hi_flag | lo_flag;    // 高有效 + 粘滞
```

两处错误：
1. **极性反了**：应为低有效。
2. **类型错了**：ALERT 是电平信号（跟随当前是否越界），不是粘滞状态位的 OR。RTL 里 ALERT 会一直保持到 W1C，和手册的"threshold bound 不再被跨越就回高"完全不同。

**改法**：把"粘滞状态"和"电平告警"拆成两套信号。

```systemverilog
// 粘滞状态（进 DEVICE_STATUS）
logic hi_flag, lo_flag;

// 电平告警（进 GPIO / FIFO 触发）
logic hi_level, lo_level;
assign hi_level = (d12 > hi_clr_th);   // 仍在上回差带之上
assign lo_level = (d12 < lo_clr_th);

assign alert_n = ~(hi_level | lo_level);   // 低有效
```

> 上式的精确边界（用 `>` 还是 `>=`、用设定门限还是回差门限）手册 Figure 84 只给了示意，标 `[待确认]`，需在验证中用波形对齐。关键点是 **ALERT 必须是电平型、低有效**。

---

### 3.5 路由与内外事件选择（E-07/E-08）

**手册依据（p.90 Table 55）**

> "7 INT_EVENT_EN … 0: Internal event detection is disabled. 1: Internal event detection is enabled."
> "6 HI_ROUTE … 0: Mask High Detection. 1: Route High Detection to Alert Pin, Status Register, and FIFO."
> "5 LO_ROUTE … 0: Mask Low Detection. 1: Route Low Detection to Alert Pin, Status Register, and FIFO."

**手册依据（p.54 Table 26）**

> "INT_EVENT_EN = 0 → External event: GPIO_x_SEL = 4b1001 … Event triggers when a logic high is detected on the selected GPIO input."
> "INT_EVENT_EN = 1 → Internal event: ADC Results threshold detection is enabled."

**手册依据（p.54）**

> "To use the ALERT signal to trigger the FIFO, the HI_ROUTE and/or the LO_ROUTE bits must be configured as required, and the INT_EVENT_EN bit must be set to 1."

**RTL 现状**

- `ad4080_regs.sv:84`：`16'h001C: begin even_r <= wdata[7]; fmode_r <= wdata[1:0]; end` —— 只存 bit7 和 [1:0]，**bit6/5 (HI_ROUTE/LO_ROUTE) 丢失**，读回（:111）也是 0。
- `ad4080_top.sv:38`：`event` 是顶层裸输入，没有内部/外部选择，也没有掩码。
- `event_detect` 无 route 端口。

**改法**

```systemverilog
// regs
logic hi_route, lo_route;
16'h001C: begin
    even_r   <= wdata[7];
    hi_route <= wdata[6];
    lo_route <= wdata[5];
    fmode_r  <= wdata[1:0];
end

// 事件源选择
assign event_int  = (hi_route && hi_level) || (lo_route && lo_level);
assign event_sel  = even_r ? event_int : ext_event_pin;   // EXT_EVENT，高有效
assign fifo_event = event_sel;
```

同样的 `hi_route/lo_route` 也要门控 `HI_STATUS/LO_STATUS` 与 ALERT，否则屏蔽位形同虚设。

---

### 3.6 集成缺口（E-10/E-11）—— 这是 P0

`event_detect.sv`、`gpio_ctrl.sv`、`cnv_ctrl.sv` 三个模块**在整个顶层层级中都没有被实例化**。

用 `ad4080_top.sv` 核对，它例化的只有 `u_regs`、`u_spi`、`u_chain`、`u_capture`、`u_lvds`（:67,76,88,103,116）。`event` 是一个外部输入端口，`status_in` 里没有 `hi/lo` 状态，`gpio_*` 端口完全不存在。

因此：
- 内部阈值事件检测**功能上不存在**；
- `HI_STATUS/LO_STATUS` 永远为 0；
- `INT_EVENT_EN/HI_ROUTE/LO_ROUTE` 写了也没用；
- GPIO 路由整块缺失。

**改法**：在顶层接入，并让检测对象是**所选滤波器的输出**（p.54："where the digital filter is enabled, the output of the selected filter refers to the ADC data result that is checked by the threshold detection"）：

```systemverilog
event_detect u_evt (
    .clk(clk), .rst_n(rst_n),
    .enable(int_event_en),
    .hi_clr(hi_status_w1c), .lo_clr(lo_status_w1c),
    .sample_valid(chain_valid),      // 滤波器/旁路后的有效
    .data_in(chain_y),               // 滤波器输出，20-bit 补码
    .hi_thresh(event_hi), .lo_thresh(event_lo), .hysteresis(event_hyst),
    .hi_flag(hi_status), .lo_flag(lo_status),
    .alert_n(alert_n)
);
```

`cnv_ctrl` 也建议接入，把 `cnv_pulse` 作为 `valid_in` 的时基来源，这样 2-D 的 `valid_in` 延迟才能和建立时间对上 `[待确认]`（当前 `valid_in` 是外部给的，语义未定义）。

---

### 3.7 GPIO 路由（E-12）

**手册依据（p.53 Table 25 / p.89-90 GPIO_CONFIG_B/C）**

> "0000b Configuration SPI SDO; 0001b FIFO full; 0010b FIFO read done; 0011b Filter result ready (Active Low); 0100b HI_DTCT; 0101b LO_DTCT; 0110b ALERT (Active Low); 0111b GPIO data; 1000b FILTER_SYNC (Active Low); 1001b EXT_EVENT."

**RTL 现状（`gpio_ctrl.sv:27-28`）**

```systemverilog
if (gpio_sel[i] == 3'd0) gpio_o[i] = gpio_reg[i];
else                     gpio_o[i] = funcs[gpio_sel[i]];
```

问题：
1. `gpio_sel` 只有 **3 bit**，手册需要 4 bit（编码到 1001）。
2. 编码含义完全不同：RTL 的 `0` 是寄存器数据（手册是 SPI SDO，`0111` 才是 GPIO data）；`funcs` 的顺序是 `[0]HI,[1]LO,[2]ALERT,[3]FILTER_RDY,[4]FIFO_FULL,[5]EMPTY,[6]OVF,[7]CAPTURED`，与手册无一致关系。
3. ALERT（0110）和 FILTER_RESULT_RDY（0011）是**低有效**，RTL 直接透传，没有反相。

**改法**

```systemverilog
input logic [3:0][3:0] gpio_sel;   // 4 bit

always_comb begin
  for (int i = 0; i < 4; i++) begin
    unique case (gpio_sel[i])
      4'd0: gpio_o[i] = spi_sdo;
      4'd1: gpio_o[i] = fifo_full_status;
      4'd2: gpio_o[i] = fifo_read_done;
      4'd3: gpio_o[i] = ~filter_result_rdy;  // 低有效
      4'd4: gpio_o[i] = hi_dtct;
      4'd5: gpio_o[i] = lo_dtct;
      4'd6: gpio_o[i] = alert_n;             // 低有效
      4'd7: gpio_o[i] = gpio_reg[i];
      4'd8: gpio_o[i] = 1'b0;                // FILTER_SYNC 是输入，不是输出
      4'd9: gpio_o[i] = 1'b0;                // EXT_EVENT 是输入
      default: gpio_o[i] = 1'b0;
    endcase
    gpio_oe[i] = gpio_dir[i] && !(gpio_sel[i] == 4'd8 || gpio_sel[i] == 4'd9);
  end
end
```

---

### 3.8 事件延迟（E-09）

**手册依据（p.55）**

> "A latency of two conversion clock cycles exists from the first CNV+ edge where the analog input crosses a threshold to a detected event that is flagged in the device status register and to any GPIO configured to route ALERT."

RTL 在同拍 `sample_valid` 上比较并置位（`event_detect.sv:44`），没有 2 个 CNV 的延迟。P2（行为模型可接受，逐拍比对不可接受）。

```systemverilog
// 在 CNV 域打两拍
logic [1:0] evt_pipe;
always_ff @(posedge clk or negedge rst_n)
  if (!rst_n) evt_pipe <= '0;
  else if (cnv_pulse) evt_pipe <= {evt_pipe[0], cmp_result};

assign hi_flag_set = evt_pipe[1];
```

> 这里假设 `sample_valid` 就是 CNV 域节拍；若 `valid_in` 已含 ADC 的固定延迟，则只需补 2 拍中的剩余部分。标 `[待确认]`。

---

## 4. 其它相关差异（状态寄存器）

### 4.1 DEVICE_STATUS 位映射（R-01）—— P0

**手册依据（p.83 Table 47）**：bit7 FIFO_FULL / bit6 FIFO_READ_DONE / bit5 HI_STATUS / bit4 LO_STATUS / bit3 POR_ANA_FLAG / bit2 ADC_CNV_ERR / bit1 ROM_CRC_ERR / bit0 POR_FLAG；复位 `0x09`。

**RTL 现状（`ad4080_top.sv:123-124`）**

```systemverilog
assign status_in = {fifo_full, fifo_empty, fifo_ovf, fifo_unf,
                    clip_low, clip_high, captured, chain_valid};
```

逐位对照：

| bit | 手册 | RTL |
|---|---|---|
| 7 | FIFO_FULL | fifo_full（且定义还是错的，见 Q-09） |
| 6 | FIFO_READ_DONE | fifo_empty |
| 5 | HI_STATUS | fifo_ovf |
| 4 | LO_STATUS | fifo_unf |
| 3 | POR_ANA_FLAG | clip_low |
| 2 | ADC_CNV_ERR | clip_high |
| 1 | ROM_CRC_ERR | captured |
| 0 | POR_FLAG | chain_valid |

完全错位。主机读 0x14 得到的每一位都不是手册定义的含义。

**改法**

```systemverilog
assign status_in = {
    fifo_full_status,   // [7]
    fifo_read_done,     // [6]
    hi_status,          // [5]
    lo_status,          // [4]
    por_ana_flag,       // [3] 复位 1
    adc_cnv_err,        // [2]
    rom_crc_err,        // [1] 复位 0（本模型无 ROM，可恒 0）
    por_flag            // [0] 复位 1
};
```

复位值 `0x09` 需要寄存器块在 `!rst_n` 时给 bit3/bit0 置 1（当前 `status_in` 是组合输入，没有复位语义）。

---

### 4.2 其余寄存器差异

| 地址 | 手册 | RTL | 等级 |
|---|---|---|---|
| 0x1C [6:5] | HI_ROUTE / LO_ROUTE | 未实现（`ad4080_regs.sv:84`） | P1 |
| 0x1C [4] | ADC_CNV_ERR_ROUTE | 未实现 | P2 |
| 0x1D/0x1E | 复位 0x4000 | 复位 0（:68） | P2 |
| 0x14 | W1C（bit5/4/3/2/0） | 只读，无 W1C | P1 |
| 0x19-0x1B | GPIO0 默认输出 SDO | `gdir_r <= 0`（全输入） | P2 |

---

## 5. 优先级汇总

### P0（功能错误，必须改；未改前不具备交付条件）

1. **F-01/F-02** 运行时抽取率完全未实现（`dec_rate` 无消费者、`DECIM` 是编译期参数）。FILTER_SEL=00/01/10/11 与 2..1024 的组合在 RTL 中不存在。
2. **F-11** SINC5+COMP 总抽取率少一半（内部 SINC5 用 `DECIM` 而非 `DECIM/2`，且 FIR 未做 2 抽取）。
3. **F-13** `sinc5_comp` 把补码当无符号（`$signed({1'b0, s5_data})`），负半轴输出全错。
4. **Q-02** 写 FIFO_MODE 不 arm（`arm` 是外部端口，未接寄存器写）。
5. **Q-09** FIFO_FULL 用物理满而非 WATERMARK 到达，mode 01/10 下永不置位。
6. **E-01/E-02** 事件阈值无符号比较（20-bit 结果是补码、阈值是补码），负阈值失效、负半轴误触发。
7. **E-06** ALERT 高有效且粘滞，手册要求低有效、电平型。
8. **E-10/E-11** `event_detect` 未实例化，内部事件检测功能不存在。
9. **R-01** DEVICE_STATUS 8 位映射全错。

### P1（明显偏差，影响可用性/验证一致性）

10. F-03 非法抽取率未钳到最大值。
11. F-04/F-05 改配置不自动复位滤波器。
12. F-06/F-07/F-08 无 settling 计数/`settled` 指示（SINC5+COMP 的 35D+10 连注释都没有）。
13. F-10 `1 << (SHIFT-1)` 在 D≥128 时溢出 32 位，舍入失效。
14. F-15 FILTER_SYNC 极性反（高有效 vs 低有效）。
15. Q-03 缺少 00→目标 的 rearm 语义；`arm` 是电平不是脉冲。
16. Q-04 mode 00 不清 `armed/event_seen/captured`。
17. Q-05 mode 11 未校验 WATERMARK 为 4 的倍数。
18. Q-06 mode 11 事件位置未回写 FIFO_WATERMARK。
19. Q-10 FIFO_FULL 未在首个读时清除。
20. Q-12 无 WATERMARK+3 CNV 的启动延迟。
21. Q-13 WATERMARK 未钳到 16384。
22. E-05 HI/LO 标志不能独立 W1C。
23. E-07 HI_ROUTE/LO_ROUTE 未实现（掩码失效）。
24. E-08 INT_EVENT_EN / EXT_EVENT 内外选择未实现。
25. E-12 GPIO_x_SEL 位宽与编码不符，低有效信号未反相。
26. R-02 0x14 无 W1C 逻辑。

### P2（记录/文档级）

27. F-12 补偿系数缺失（`[待确认]`，需 ADI 数据）。
28. F-14 `D×WATERMARK + settling` 未建模/未断言。
29. Q-14 FIFO_WATERMARK 复位值 0x4000 vs 0。
30. Q-15 单口 FIFO 同拍读写（有意偏差，保留登记）。
31. E-09 事件 2 个 CNV 延迟未建模。
32. E-03 之外：`cnv_ctrl` 未接入，CNV→`valid_in` 的语义未定义 `[待确认]`。

---

## 6. 待确认清单

| # | 问题 | 需要的输入 |
|---|---|---|
| 1 | 20-bit 结果在**本 RTL 数据通路内**是否始终为补码？任务书提到的 offset-binary 与手册 p.20 Table 7 / p.5 Table 1 冲突。 | 确认 `lvds_framer`/`spi_slave`/`sys_error_corr` 的数值口径；若确有 offset-binary 转换，需给出位置 |
| 2 | 写同值到 FIFO_MODE 是否重新 arm？p.62 与 p.60/p.63 表述不一致 | ADI FAE 或实测 |
| 3 | mode 11 的 FIFO_FULL 定义（物理满 vs 事件后 WATERMARK 到） | ADI FAE |
| 4 | mode 11 回写到 FIFO_WATERMARK 的"事件位置"精确口径（与 `16384−(WM−1)` 的关系） | ADI FAE |
| 5 | ALERT 电平的精确边界（`>` vs `>=`，用设定门限还是回差门限） | Figure 84 原图/实测 |
| 6 | SINC5+COMP 的补偿系数 | ADI 提供；手册只给响应曲线 |
| 7 | `INT_EVENT_EN=0` 时已置位的 HI/LO 标志是否保持 | ADI FAE |

---

## 7. 审查方法与限制

- 手册 PDF 文本由 `pypdf` 抽取，公式段落存在换行/上标丢失（例如 `2^20` 被抽成 `220`），本报告在引用时已结合上下文还原并标注原句。
- 所有 RTL 结论均基于源码静态阅读与位宽/时序推演，**未运行仿真/综合**（环境中无 Verilator/VCS）。
- 本报告**未修改任何 RTL 文件**。
