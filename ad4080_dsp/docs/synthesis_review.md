# AD4080 数字 RTL 可综合性 / 时序风险审查

| 项目 | 内容 |
|:---|:---|
| 审查对象 | `ad4080_rtl/rtl/*.sv`（15 个文件） |
| 快照时间 | **2026-10-19 13:40**（文件 mtime 见附录 A） |
| 审查类型 | 静态代码审查（可综合性 / CDC / 时序 / 面积 / DFT） |
| 审查方式 | **未运行综合、未运行仿真**（本机无 `iverilog`/`verilator`/`vcs`/`dc_shell`），所有延迟/面积为手算模型 |
| 是否改 RTL | 否。本文只出报告，建议以代码片段给出 |

> **重要：本次审查期间 RTL 正在被其他成员并发修改。**
> `sys_error_corr.sv`（13:30）、`sinc1_decim.sv`（13:31）、`sinc5_decim.sv`（13:31）、`sinc5_comp.sv`（13:33）、`ad4080_dsp_chain.sv`（13:33）、`event_detect.sv`（13:33）、`gpio_ctrl.sv`（13:34）、`ad4080_regs.sv`（13:40）、`fifo_capture.sv`（13:40）在审查过程中被改写。
> 本报告**以 13:40 快照为准**，并在第 0 节列出"已修复 / 新引入"的差异。若 RTL 继续变动，请以最新代码复核行号。

> 本文所有数字均带假设前提。凡标 `[假设]` 者为我方工程取值，不是工艺厂数据，落地前必须用实际库/综合报告复核。

---

## 0. 相对上一版的差异（避免重复报告已修问题）

| 项 | 上一版 | 13:40 快照 | 结论 |
|:---|:---|:---|:---|
| `sys_error_corr` 乘法位宽 | `prod_s1*K_GAIN` 在 32-bit 上下文求值（需 40 bit），溢出回绕 | 改为 `assign prodk_s2 = prod_s1 * K_GAIN_S;`（左值 41 bit → 求值宽度 41） | ✅ **已修复** |
| `K_GAIN` | 参数 511 vs 注释 512 自相矛盾 | 统一为 **512**，`PRODK_W=41`，`K_GAIN_S` 10 bit | ✅ **已修复**（仍建议回 p.71 确认端点口径） |
| `fifo_capture` watermark 越界 | 15-bit 可达 32767 > DEPTH，`captured` 永不置位 | `ad4080_regs.sv:111-112` 增加 `wm_clamped`（0→1，>16384→16384） | ✅ **已在寄存器侧修复**（`fifo_capture` 本身仍无钳位，见 P1-9） |
| 运行时抽取率 | `dec_rate` 寄存但无人使用 | `sinc1/sinc5/sinc5_comp` 全部改为 `dec_sel` 运行时输入，`dsp_chain` 透传 | ✅ **已实现**（但引入新问题，见 P1-1/P1-2） |
| `event_detect` 阈值对齐 | 用无符号 `data[19:8]` 直接比补码阈值，负阈值永不成立 | `d12 = $signed(data_in[19:8] ^ 12'h800)` 做 offset-binary→补码 | ✅ **已修复** |
| `gpio_ctrl` SEL 宽度 | 3 bit，`funcs[0]` 不可达 | 改为 4 bit，按 Table 52/53/54 全编码 | ✅ **已修复** |
| **顶层接口** | 与子模块一致 | **`ad4080_regs` / `ad4080_dsp_chain` 端口已改，`ad4080_top` 未同步** | ❌ **新引入的 P0**（见 P0-1） |
| **`fifo_capture` 读门控** | `rd_en` 直连 FIFO | 新增 `rd_fire`（`captured` 门控）但**只用于计数，未接 FIFO** | ❌ **新引入的 P0**（见 P0-3） |
| **CIC5 面积** | `ACC_W = IN_W + 5*log2D`（D=4 时 30 bit） | 固定 `ACC_W = 61`（按 D=256 最坏情况） | ⚠️ **面积翻倍**（D=4 时 30→61，见 P2-2） |

---

## 一页结论

### P0 — 阻断项（功能错 / 不可综合 / 不可流片）

| # | 问题 | 位置 | 后果 |
|:--:|:---|:---|:---|
| **P0-1** | **顶层与子模块端口不一致，当前工作树无法 elaborate**：<br>① `u_regs` 连 `.dec_rate(...)`，但 `ad4080_regs` 的端口已改名为 `dec_sel`；<br>② `u_regs` 连 `.status_in(status_in)`，但 `ad4080_regs` 已把 `status` 改成 **output**，无 `status_in` 端口；<br>③ `u_chain` 传 `#(.DECIM(DECIM))`，但 `ad4080_dsp_chain` 已删除 `DECIM` 参数；<br>④ `u_chain` 未连 `.dec_sel`（新必填输入） | `ad4080_top.sv:67-74, 88-97` 对照 `ad4080_regs.sv:55,86,251`、`ad4080_dsp_chain.sv:23-34` | 编译/elaborate 直接报错；即使注释掉错误端口，`dec_sel` 悬空 → 抽取率不可控。**当前 RTL 不可用** |
| **P0-2** | 4 个模块**全工程无例化**（`cnv_ctrl`、`event_detect`、`gpio_ctrl`、`cal_two_point`），只在各自文件里存在（`cal_two_point` 另在 TB 里） | 全工程 grep；`ad4080_top.sv` 端口表也没有 `cnv_p/cnv_n`、`gpio_*`、`alert` | 数据手册声明的 CNV 输入、阈值/迟滞事件、GPIO 路由、两点校准在顶层全部不可用；`ad4080_regs` 新增的 `hi_clr/lo_clr/event_*/gpio_*/lvds_*` 输出**无人接收**，综合时会被整片裁掉 |
| **P0-3** | `fifo_capture` 的 `rd_fire`（带 `captured` 门控）只驱动 `rd_cnt`，**FIFO 的 `rd_en` 仍接原始 `rd_en`** | `fifo_capture.sv:80`（生成 `rd_fire`）vs `:139`（`.rd_en(rd_en)`） | ① 注释承诺的"`captured=1` 之前忽略 `rd_en`"**不成立**，FIFO 真的被弹出；② mode 10/11 的**事件前数据被提前读走**，触发捕获失去意义；③ 无效读把 `underflow` 置成粘滞 1；④ `rd_cnt` 只统计 `captured` 之后的读，`read_done` 与真实读次数**永久错位** |
| **P0-4** | 16384×20 的 `mem` 是普通 `reg` 数组。`(* ram_style = "block" *)` 只对 FPGA 生效；DC/Genus **不会**映射到工艺 SRAM 宏 | `result_fifo.sv:131, 157-161, 166-176` | ASIC 流程展成 **327,680 个触发器 + 16K:1 20-bit 读 mux**（≈0.36 mm²，是 SRAM 方案的 7 倍），综合/布线不可收敛，且无 BIST/bypass。**不可流片** |

### P1 — 时序 / 功能 / 可靠性风险

| # | 问题 | 位置 | 量级 / 后果 |
|:--:|:---|:---|:---|
| P1-1 | `sinc5_decim` 输出归一化用**实时** `l_map`，而累加用**块边界锁存**的 `d_cur`；`l_cur` 算了却不用（死寄存器） | `sinc5_decim.sv:58`（`shift_amt=5*l_map`）vs `:130-131, 138-139`（`l_cur` 锁存） | ① 复位后第一块：`d_cur=2` 但 `shift_amt` 可能 =40 → 首个输出≈0；② 每次改 `dec_sel`，块边界那一个输出被错误缩放 `2^(5Δ)` |
| P1-2 | CIC5 积分器/梳状器 5 级 **61-bit** 串行加/减链 | `sinc5_decim.sv:70-76, 109-115` | 5×0.78 ≈ **3.9 ns**，**500 MHz（2 ns）不收敛**；D=4 时也不收敛（位宽已固定 61） |
| P1-3 | `sys_error_corr` 第 2 级：31×10 乘法 → 41-bit 加 → 移位 → 24-bit 加 → 2 个比较，全在一拍 | `sys_error_corr.sv:73-74, 106-109` | 2.4–2.8 ns（位宽修好后路径反而更长），500 MHz 不收敛 |
| P1-4 | `sinc5_comp` 3 个 20×17 乘法 + 2 级加 + 饱和，全组合 | `sinc5_comp.sv:67-71, 99-107` | 2.2–2.6 ns（真实系数下）；默认 `C1=C2=0` 会被折叠，掩盖问题 |
| P1-5 | `sinc5_comp` 系数用 `C1[15:0]` 零扩展成 17-bit 无符号 → **负系数无法表示**（-1000 变成 +64536） | `sinc5_comp.sv:61-64` | 一旦填真实补偿系数（通常含负抽头），滤波器系数完全错 |
| P1-6 | 复位策略混用：`ad4080_regs`/`spi`/`corr`/`sinc*`/`fifo_capture`/`lvds` 用异步复位，`result_fifo` 用同步复位；顶层无 reset synchronizer | `ad4080_regs.sv:115` vs `result_fifo.sv:157/166/181/196` | 释放沿偏斜 0–1 拍；异步释放可能亚稳态；复位扇出 ≥2,500 |
| P1-7 | `sinc5_decim:101`、`sinc5_comp:74` 把 `filter_sync` 与 `rst_n` 写在同一个 `if` 条件里 | 同上 | 部分工具把 `filter_sync` 综合成异步复位端，RTL 仿真与网表行为不一致 |
| P1-8 | 除 `cs_n/sclk/cnv_*` 外，`event/arm/rd_en/filter_sync/bit_en/valid_in` 均无同步器；`gpio_i` 也无 | `ad4080_top.sv:33-45`；`fifo_capture.sv:92,101`；`gpio_ctrl.sv:61-65` | 若来自焊盘：偶发漏触发 / 亚稳态，实验室极难复现 |
| P1-9 | `fifo_capture` 自身不做 watermark 钳位，依赖 `ad4080_regs` 的 `wm_clamped`；且 mode 11 的 `rd_target` 恒为 16384 | `fifo_capture.sv:57-59, 67, 126` | 单独复用该模块时 `captured` 仍会挂死；mode 11 若 FIFO 未写满 16384，`read_done` 永不置位 |
| P1-10 | SPI 过采样倍数无文档约束：数学下限 4×，工程值 8×；`cs_fall` 与 `sck_rise` 同拍时上升沿被吞 | `spi_slave.sv:36-48, 75-88` | `t_CSSCLK ≥ 3·T_clk`，否则首比特丢失、整帧 24 bit 错位 |

### P2 — 面积 / 可维护性

| # | 问题 | 位置 | 可省 |
|:--:|:---|:---|:---|
| P2-1 | 2 个 CIC5 + 1 个 SINC1 常驻（`sinc5_comp` 内部又例化一份 CIC5） | `ad4080_dsp_chain.sv:66-82`；`sinc5_comp.sv:45-49` | **964 FF + 5 加 + 5 减**（约占 DSP 链 40%） |
| P2-2 | CIC5 位宽固定 61（按 D=256 最坏情况），D=4 时也按 61 存；且未做 Hogenauer 裁剪 | `sinc5_decim.sv:35, 61-65` | 裁剪后 5 级 29/37/45/53/61（合 225 vs 305）→ **每个 CIC5 每个数组省 240 FF** |
| P2-3 | `l_cur` 是死寄存器（4 FF + 潜在 lint 告警） | `sinc5_decim.sv:131, 139` | 4 FF（但应按 P1-1 改为**使用**它） |
| P2-4 | `sys_error_corr` 注释写 2 级流水，实际 `valid_in→valid_out` 是 **3 拍** | `sys_error_corr.sv:25, 105-125` | 接口契约不一致 |
| P2-5 | `cal_two_point` 的 `error` 恒 0（无置位路径） | `cal_two_point.sv:127` | 错误指示失效 |
| P2-6 | `sinc5_decim` 的 `norm` 对负的梳状瞬态按无符号右移 → 回绕成大正数，输出不饱和 | `sinc5_decim.sv:117-123, 136` | 抽取切换/启动后 1–2 个样本可能满幅跳变，建议饱和 |
| P2-7 | 无 scan 结构、无 MBIST、无 SRAM bypass、无 test clock mux | 全工程 | DFT 不可插入 |
| P2-8 | `sinc1_decim` 复位后第一块固定按 D=2 处理（`d_cur` 复位值 2） | `sinc1_decim.sv:72-73` | 首个输出抽取率错（有 `filter_sync` 则无此问题） |

**一句话结论**：算法层的两个老 bug（增益乘法位宽、事件阈值符号）本轮已修好；但**接口重构只改了子模块、没改顶层，当前工作树直接编不过（P0-1）**，`fifo_capture` 新增的读门控没接到 FIFO 上（P0-3），CIC5 固定在 61 bit 后 5 级串行链在 500 MHz 下差一倍时序（P1-2）。建议先让顶层 elaborate 通过 → 修 `rd_fire` → 再做 CIC5 位宽裁剪 + 级间流水 → 最后做 CIC5 共享。

---

## 1. 组合环与锁存器

### 1.1 结论

| 检查项 | 结果 | 依据 |
|:---|:---|:---|
| 组合环 | **0 处** | `rdata→spi→we→regs→rdata` 中间隔触发器，是时序环；`gpio_ctrl` 内 `fsync_i = fsync_i \| ...` 在 `always_comb` 开头已初始化（`:51-54`），属累加 OR，非环 |
| 推断锁存器 | **0 处** | 见 1.2 |
| 多驱动 | **0 处** | 每个 `logic` 只出现在一个 `always_ff`/`assign`；`hi_flag`/`hi_pulse` 在 `event_detect` 同一块内多处赋值，优先级明确 |

### 1.2 逐条核对

| 模块 | 检查点 | 结论 | 依据 |
|:---|:---|:---|:---|
| `ad4080_regs` | `rdata` 22 路 `case` | 全分支 + `default: 8'h00` → **无 latch** | `ad4080_regs.sv:213-242` |
| `ad4080_regs` | 写 `case` | 有 `default: ;` → **无 latch**（写的是 flop，本来也不产生 latch） | `:159-207` |
| `gpio_ctrl` | `for(i=0;i<4;i++)` 内 3 类分支 | `gpio_o`/`gpio_oe` 在循环前已整体清零，4 次迭代全部赋值 → **无 latch** | `:50-88` |
| `ad4080_dsp_chain` | `case(filter_sel)` | 有 `default` → **无 latch** | `:87-94` |
| `fifo_capture` | `case(mode)` | 有 `default` → **无 latch** | `:69-77` |
| `sinc5_decim` | `integ_nxt[0:4]` | 5 个元素逐个赋值 | `:70-76` |
| `sinc5_decim` | `comb_nxt[0:4]` / `comb_in[0:5]` | 循环覆盖 `comb_in[1..5]`，`comb_in[0]` 单独赋值 → 6 个全覆盖 | `:109-115` |
| `sinc1_decim` | `sum`/`avg` | 单条 `assign` | `:65-66` |
| `sinc5_comp` | `acc` | 单条赋值，三乘积相加 | `:67-71` |
| `event_detect` | 全部 `assign` | — | `:50-61` |

### 1.3 三处"综合风格"风险（不是 latch，但会让 RTL 与网表不一致）

**A（P1-7）`rst_n || filter_sync` 同条件** —— `sinc5_decim.sv:101`、`sinc5_comp.sv:74`：

```systemverilog
if (!rst_n || filter_sync) begin ... end
```
仿真语义是同步清；综合工具可能把 `rst_n & ~filter_sync` 接到异步复位端。`filter_sync` 还要背 900+ 个复位端扇出。**建议拆嵌套 `if`**：

```systemverilog
if (!rst_n)                     begin ... end
else if (filter_sync)           begin ... end
else if (valid_in)              begin ... end
```

**B（P2）`initial $fatal`** —— `result_fifo.sv:117-126`。DC/Genus 支持 elaboration 期 `$fatal`，部分 lint/形式工具会直接报错，Vivado 综合忽略。建议包 `` `ifndef SYNTHESIS ``。

**C（P2）`always_comb` 内整体赋值 unpacked array** —— `sinc5_decim.sv:70-76, 109-115`。SV 合法，个别老版本综合器支持不完整；建议改 `for` 循环 + 显式下标。

---

## 2. 多驱动与跨时钟域

### 2.1 多驱动

逐条核对：`y`/`valid_out`/`clip_*`（`sys_error_corr.sv:94-132` 单块）、`mem`（`result_fifo.sv:157-161` 单块）、`wr_ptr`/`rd_ptr`（`:181-191` 单块）、`status`（`ad4080_regs.sv:244-245` 单 `assign`）——**无多驱动**。

`fifo_capture` 的 `armed`/`captured`/`wr_cnt` 在同一 `always_ff` 的不同分支各出现一次，优先级由 `if/else` 决定，正常。

### 2.2 SPI 过采样倍数推算（P1-10）

现有结构（`spi_slave.sv:34-48`）：

```
sclk ──► sck_s0 ──► sck_s1 ──► sck_p
          (1)        (2)        (3)
sck_rise = sck_s1 & ~sck_p        // 对已同步信号做沿检测
```

| 约束 | 推导 | 倍数要求 |
|:---|:---|:---|
| 不混叠（每个 SCLK 半周期至少采到一次高、一次低） | 半周期 ≥ 1 clk，且相邻两沿不能落进同一拍 | **≥ 2×**（理论上限 f_sclk = f_clk/2） |
| 沿检测脉冲不被漏采 | `sck_rise` 只宽 1 clk，要求 SCLK 高 ≥ 1 clk 且低 ≥ 1 clk | **≥ 4×** |
| 3 级触发器延迟 | 从真实沿到 `sck_rise` 有效 = 2–3 clk；24 个沿不能错位，要求单半周期 ≥ 2 clk 余量 | **≥ 4×** |
| CS 提前量 | `cs_fall` 在真实下降后 2–3 clk 才有效；且 `cs_fall` 在 `if` 链里优先级最高（`:75`），同拍时 `sck_rise` 被吞 | `t_CSSCLK ≥ 3·T_clk + t_su` |
| 工程取值 | 上面三项叠加 + 抖动余量 | **8×**（与 `spi_slave.sv:11` 注释一致） |

**结论**：`f_clk ≥ 8 × f_sclk`。

| f_clk | f_sclk 上限 | `t_CSSCLK` 需 ≥ |
|:---|:---|:---|
| 100 MHz | 12.5 MHz | 30 ns |
| 200 MHz | 25 MHz | 15 ns |
| 500 MHz | 62.5 MHz | 6 ns |

MTBF 粗估 `[估算]`：28 nm、`f_clk=100 MHz`、`f_sclk=12.5 MHz`、2 级同步、`τ≈30 ps`、建立窗口 50 ps → MTBF ≥ `10^9` 小时，可接受。**但 `t_CSSCLK` 必须写进 SPI 时序规格**：首比特丢失会让 24 bit 整帧错位（`bitcnt` 少计 1，`wdata` 变成 `{shin[6:0], sdi}` 的错位值），典型现场故障。

### 2.3 CS 异步下降沿清计数器

`cs_fall` 本身是**已同步**信号（`cs_s1 & ~cs_s0`），清 `bitcnt`（`:75-82`）不会产生亚稳态，**这一点安全**。真实风险两条：

1. **优先级吞沿**：`if (cs_fall) ... else if (cs_s1) ... else if (sck_rise)`。CS 刚拉低那拍若正好采到第一个 SCLK 上升沿，该沿被丢弃 → 少收 1 bit。靠 `t_CSSCLK` 保证。
2. **`cs_s1` 高电平分支不清 `bitcnt`**（`:83-86`）：CS 抖动产生 < 2 clk 的窄脉冲时 `cs_fall` 采不到，`bitcnt` 保持残值。建议在 CS 高电平期间持续清：

```systemverilog
end else if (cs_s1) begin
    bitcnt <= '0;
    sdo_r  <= 1'b0;
    oe_r   <= 1'b0;
end
```

### 2.4 CNV 控制（`cnv_ctrl.sv`）

- 2FF + 1 级沿检测（`:25-35`），`cnv_pulse` 宽 1 clk 且由触发器输出译码，**无毛刺**，可当初使能用（不要当时钟）。
- 倍数：`f_clk ≥ 4 × f_CNV`（不丢沿）。AD4080 20 MSPS 下 `f_clk ≥ 80 MHz` 即可。
- `raw = lvds_en ? (cnv_p ^ cnv_n) : cnv_p`（`:23`）：XOR 在同步器**之前**，两路异步输入的到达偏斜会在 `raw` 上产生窄脉冲。建议先各自 2FF 再 XOR（多 1 clk 延迟）：

```systemverilog
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin p0<=0;p1<=0;n0<=0;n1<=0; end
    else begin p0<=cnv_p; p1<=p0; n0<=cnv_n; n1<=n0; end
end
wire raw_s = lvds_en ? (p1 ^ n1) : p1;
```

### 2.5 未同步的输入（P1-8）

| 信号 | 首个消费者 | 位置 |
|:---|:---|:---|
| `event` | `event_seen` | `fifo_capture.sv:101` |
| `arm` | `armed` | `fifo_capture.sv:92` |
| `rd_en` | `rd_fire` / `result_fifo` | `fifo_capture.sv:80`；`result_fifo.sv:171,187,202` |
| `filter_sync` | `sinc1`/`sinc5`/`sinc5_comp` | `sinc1_decim.sv:76`；`sinc5_decim.sv:83,101,126`；`sinc5_comp.sv:74` |
| `bit_en` | `lvds_framer` | `lvds_framer.sv:48` |
| `gpio_i[3:0]` | `fsync_i`/`ext_evt_i` | `gpio_ctrl.sv:61-65` |
| `valid_in`/`x` | `sys_error_corr` | `sys_error_corr.sv:83-87` |

`valid_in`/`x` 若由同一 `clk` 域的 ADC 数字核产生，可不同步（`[假设]`：这是设计意图）。其余 5 个若来自引脚/另一时钟域，**必须 2FF**。`event` 尤其危险：亚稳态被 `event_seen` 采成 0/1，表现为"偶发漏触发"。

---

## 3. RAM 推断（16384 × 20）

### 3.1 两种流程分别会得到什么

| 流程 | 结果 | 说明 |
|:---|:---|:---|
| **FPGA（Vivado/Quartus）** | 读地址已打拍、写同步 → 推成 **Block RAM**，`(* ram_style="block" *)` 生效 | 16384 深 × 20 bit：`RAMB36`(1024×36) 需 **16 块**；`RAMB18`(1024×18) 需 **32 块**。若判成分布式 → `327680/64 = 5120` 个 LUT6，约半片中型 FPGA |
| **ASIC（DC/Genus）** | `ram_style` 是**综合属性，不是 ASIC 指令**，工具直接忽略。`reg` 数组 + 同步读展成 **327,680 个 DFF** + **16K:1 × 20 bit** 读 mux 树 | 综合时间爆炸、面积爆炸、布线不可收敛。不是"推断失败"，是**根本不会去推断 SRAM** |

> ASIC 的 SRAM 宏是工艺厂编译生成的硬宏（`.lib/.lef/.gds`），综合器只能例化。想变成 SRAM 只有两条路：① 手工例化宏 + wrapper；② 用厂商 Memory Compiler 流程配合工具的内存推断开关（DC `-mem` / Genus `-mapper` + memory lib），工程上极少用。

### 3.2 为什么必须换宏

1. **面积**：DFF 方案是宏方案的 **6–8 倍**（见 3.3）。
2. **读延迟**：16K:1 读 mux 即使 4:1 树也要 7 级（`log4(16384)`），叠加在本该 1 拍的 SRAM 读延迟上；宏内部已有灵敏放大 + 列 mux，低一个量级。
3. **功耗**：327k DFF 每拍全翻转，动态功耗是宏的数十倍；宏支持 CS 使能，未选中阵列不激活。
4. **可制造性**：宏带冗余列/行修复；DFF 堆出的 40 KiB 阵列没有冗余，良率直接受限于存储阵列。
5. **DFT**：宏可接 MBIST；327k 个 scan flop 的向量与覆盖率不可接受。

### 3.3 面积估算

| 方案 | 计算 | 面积 |
|:---|:---|:---|
| 阵列本体（6T） | 327,680 bit × 0.12 µm²/bit `[假设：28nm HD 6T]` | **39,300 µm² ≈ 0.039 mm²** |
| 完整 SDP 宏（含译码/灵敏放大/时序控制，×1.35） | 39,300 × 1.35 | **≈ 0.053 mm²** |
| 只能用 32-bit 宽宏（20 bit 非标准宽度，浪费 37.5%） | 16384 × 32 × 0.12 × 1.35 | **≈ 0.085 mm²** |
| DFF 实现（1.1 µm²/FF 含 mux 开销 `[假设]`） | 327,680 × 1.1 | **≈ 0.36 mm²**（不含 mux 树与拥塞） |

**结论**：327,680 bit、6T、SDP 宏约 **0.05–0.085 mm²**，DFF 方案 **0.36 mm²**，差 4–7 倍。这个数字必须在架构评审时定下来，不能留给综合去"试"。

### 3.4 换宏要补的接口（当前完全没有）

```systemverilog
input  logic test_mode;     // 1 = BIST/scan 接管
input  logic ram_bypass;    // 1 = 读口强制 0，供 scan 捕获
// 宏侧还需要：CEN / GWEN / WEN[19:0] / A[13:0] / D[19:0] / Q[19:0] / BIST 环
```

- 写侧：`wr_fire` → `CEN=0, GWEN=0, WEN=全1`；地址 `wr_ptr[13:0]`。
- 读侧：`rd_fire` → `CEN=0, GWEN=1`；地址 `rd_ptr[13:0]`；`Q` 下一拍有效——**与现有 `result_fifo.sv:166-176` 时序完全一致**，功能逻辑不用改。
- `ram_bypass` 必须在 scan 模式下把 `rd_data` 强制为 0，否则 SRAM 的 `Q` 是不可控的 scan 输出来源。

### 3.5 复位：`mem` 不该复位 —— 当前实现是对的

`result_fifo.sv:157-161` 写块**无复位**，`:166` 读块只复位 `rd_data`/`rd_valid`，不复位 `mem`。**正确**：复位 327,680 个 bit 需要同等数量的复位端，面积和复位树都不可接受；FIFO 正确性由指针保证（`empty` 复位后为 1，首读被挡）。

两个流程的差异必须知道：

| 流程 | 上电初值 | 影响 |
|:---|:---|:---|
| FPGA BRAM | 默认全 0（可 INIT） | 读空 FIFO 得到 0，掩盖问题 |
| ASIC SRAM | **未定义（仿真为 X）** | `empty` 判断若有 bug，X 会传播到 `dout`/LVDS，仿真能抓到——**不要在 TB 里预填 0 把 X 盖掉** |

建议 TB 用 `$readmemh` 预置一个可识别的非零模式，这样"读到不该读的单元"能被 scoreboard 抓到。

---

## 4. 关键路径估算

### 4.1 估算模型 `[假设]`

| 参数 | 取值 |
|:---|:---|
| 工艺 | 28 nm LP CMOS，0.9 V，25 °C |
| FO4 | 30 ps |
| 加法器 | 16-bit 前缀 ≈ 0.30 ns；24-bit ≈ 0.40；31-bit ≈ 0.50；41-bit ≈ 0.55；61-bit ≈ 0.78 |
| 乘法器 | 20×11 ≈ 0.90；31×10 ≈ 1.00；20×17 ≈ 1.05（Wallace/Radix-4 + 末级 CPA） |
| 比较器 | 15-bit ≈ 0.25；24-bit ≈ 0.30 |
| 布线/时钟偏斜预算 | 逻辑和的 +15% |
| 目标周期 | 500 MHz → 2.00 ns；1 GHz → 1.00 ns |

> 误差带 ±40%。真实数字必须跑综合 + STA。这里只用于判断"要不要加流水"。

### 4.2 估算表

| # | 路径 | 模块:行 | 逻辑级数 | 估算延迟 | @500 MHz (2.0 ns) | 建议 pipeline 级数 |
|:--:|:---|:---|:--:|:--:|:--:|:--:|
| 1 | `x·(GAIN-512)` 20×11 → `prod_s1` | `sys_error_corr.sv:87` | 12 | 1.05 ns | 0.53 T ✅ | 1 |
| 2 | `prod_s1·512` → +2²³ → `>>>24` → 24b 加 → 24b 比较 ×2 | `sys_error_corr.sv:73-74,106-109` | 24 | **2.60 ns** | **1.30 T ❌** | **3**（乘法 / 加+移位 / 加+比较） |
| 3 | 5 级积分器 61-bit 加法链 | `sinc5_decim.sv:70-76` | 46 | **3.90 ns** | **1.95 T ❌** | **3–5** |
| 4 | 5 级梳状 61-bit 减法链 | `sinc5_decim.sv:109-115` | 46 | **3.90 ns** | **1.95 T ❌** | **3–5** |
| 5 | CIC5 输出 61-bit 加 + 变量移位 | `sinc5_decim.sv:117-123` | 13 | 0.95 ns | 0.48 T ✅ | 1 |
| 6 | SINC1 32-bit 累加 + 舍入 + 变量移位 | `sinc1_decim.sv:65-66` | 14 | 0.95 ns | 0.48 T ✅ | 1 |
| 7 | `count = wr_ptr - rd_ptr`（15b）+ 4 个比较器 | `result_fifo.sv:144-149` | 9 | 0.65 ns | 0.33 T ✅ | 1（1 GHz 时寄存器化 count） |
| 8 | 16-bit 地址译码 + 22:1 × 8b rdata mux | `ad4080_regs.sv:213-242` | 7 | 0.50 ns | 0.25 T ✅ | 1 |
| 9 | `wm_raw` mux → `wm_quad` 对齐 → 15b 比较 → 3 输入 AND → `wr_en` | `fifo_capture.sv:57-59,79` | 9 | 0.80 ns | 0.40 T ✅ | 1 |
| 10 | 3× 20×17 乘法 → 2 级加 → `>>>15` → 饱和 | `sinc5_comp.sv:67-71,99-107` | 21 | **2.40 ns** | **1.20 T ❌** | **3** |
| 11 | 两点校准：21×21 + 21×21 + 42b 减 + ×12 | `cal_two_point.sv:101-105` | 18 | **2.60 ns** | 1.30 T ❌（**只跑一次**） | 2–4，或改多周期 FSM |
| 12 | `event_detect`：XOR → 13b 减 → 13b 比较 | `event_detect.sv:50-61` | 8 | 0.50 ns | 0.25 T ✅ | 1 |
| 13 | SPI `bitcnt` 自增 + 比较 | `spi_slave.sv:88-103` | 4 | 0.25 ns | 0.13 T ✅ | 1 |
| 14 | LVDS 20-bit 移位 + 输出 mux | `lvds_framer.sv:48-57` | 2 | 0.15 ns | 0.08 T ✅ | 1 |
| 15 | `gpio_ctrl`：4 路 4-bit 比较 + 8:1 路由 mux | `gpio_ctrl.sv:56-87` | 6 | 0.35 ns | 0.18 T ✅ | 1 |

> `cal_two_point` 是"启动一次、算完就停"的慢路径，**不应占用 1 拍预算**：建议 4 状态 FSM（IDLE→MUL→SUB→CLAMP→DONE），面积还更小。

### 4.3 三条必须动的路径

**(a) CIC5（P1-2）—— 当前最严重**

`integ_nxt[i] = integ[i] + integ_nxt[i-1]` 是 5 级串行，61 bit 固定。三种改法：

| 方案 | 面积 | 延迟 | 备注 |
|:---|:---|:---|:---|
| A. Hogenauer 位宽裁剪 | **−240 FF/数组** | 单级变窄，链仍 5 级 | 免费，先做 |
| B. 级间插寄存器（重定时） | +4×ACC_W FF（+244） | 单级 0.78 ns ✅ | 引入 4 拍额外延迟，需同步改 `valid`/`dec_en` 对齐 |
| C. carry-save 把 5 级加变 3:2 压缩树 | +部分积寄存器 | ~2 级 | 面积换延迟，CIC 一般不用 |

**推荐顺序：先 A，再 B。**

Hogenauer 裁剪（`LOG2R=8`）：第 k 级只需 `IN_W + (k+1)*8 + 1`，即 **29/37/45/53/61**；梳状器反向 **61/53/45/37/29**。
每数组 **225 bit**，而现状 5×61 = 305 bit → 每数组省 80 bit，三个数组省 **240 FF（26%）**，并且前 4 级的加法器位宽同步下降，延迟也从 3.9 ns 降到约 **2.6 ns**。

**(b) `sys_error_corr` 第 2 级（P1-3）**

现在 `prodk_s2` 与 `corr_s2` 都是 `assign`（`:73-74`）→ 乘法、41-bit 加、移位、24-bit 加、两个比较全在一拍，且 `corr_s2` 还在 `sum_s2`/`lo_s2`/`hi_s2` 三处重复出现（`:107-109`，综合可 CSE）。

```systemverilog
// 建议：把第 2 级拆成 2a（乘法）/ 2b（加+比较）
logic signed [PRODK_W-1:0] prodk_q;
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) prodk_q <= '0;
    else if (v_s1) prodk_q <= prod_s1 * K_GAIN_S;      // 左值 41 bit，宽度正确
end

logic signed [SUM_W-1:0] corr_q;
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) corr_q <= '0;
    else if (v_s1b) corr_q <= (prodk_q + (1 <<< (SHIFT-1))) >>> SHIFT;
end

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin sum_s2 <= '0; lo_s2 <= 0; hi_s2 <= 0; end
    else if (v_s2b) begin
        sum_s2 <= SUM_W'($signed({1'b0, x_s1b}) + corr_q + $signed(off_s1b));
        lo_s2  <= ($signed({1'b0, x_s1b}) + corr_q + $signed(off_s1b)) < 0;
        hi_s2  <= ($signed({1'b0, x_s1b}) + corr_q + $signed(off_s1b)) > MAX_CODE;
    end
end
```

> 注意 `valid` 链要跟着多打两拍，`valid_in→valid_out` 会从 3 拍变成 5 拍，接口文档要同步改。

**(c) `sinc5_comp` FIR（P1-4）**

默认系数 `C0=32768, C1=C2=0` 时综合器把 3 个乘法器**折叠成一根移位线**，面积/延迟看起来都很好——**这是假象**。一旦填真实补偿系数（`C1/C2≠0`），立刻是 3 个 20×17 乘法器 + 2 级加法，2.4 ns。

建议 3 级流水（乘 → 加 → 移位+饱和），或 1 个乘法器 + 3 拍时分复用（输出已经抽取过，时间充足）。

---

## 5. 面积浪费与优化

### 5.1 现状：2×CIC5 + 1×SINC1 常驻

`ad4080_dsp_chain.sv:66-82` 例化 `u_sinc1`、`u_sinc5`、`u_sinc5c`；`sinc5_comp.sv:45-49` 内部**又例化一个完整 `sinc5_decim`**。

| 资源 | 当前快照（ACC_W=61） |
|:---|:---|
| `sinc5_decim` 单元 | 15×61 = 915 + `cnt`11 + `dec_en`1 + `d_cur`11 + `l_cur`5 + `data_out`20 + `valid`1 = **964 FF** |
| 常驻 CIC5 | 2 个 → **1,928 FF** |
| `sinc1_decim` | `acc`32 + `cnt`11 + `d_cur`11 + `l_cur`5 + `data_out`20 + `valid`1 = **80 FF** |
| `sinc5_comp` FIR | `x1/x2`40 + `phase`1 + `primed`2 + `acc_r`39 + `v_r`1 + `data_out`20 + `valid`1 = **104 FF** |
| DSP 链合计（含 `sys_error_corr` 125） | **2,237 FF** |

> 注：`dsp_chain` 已用 `v1/v2/v3`（`:62-64`）给三条链路做了 CE 门控——**动态功耗优化已落地**，但触发器一个没少，面积问题不变。

### 5.2 优化方案（按收益/风险排序）

**方案 1（首选）：`sinc5_comp` 不再自带 CIC5**

```systemverilog
module sinc5_comp #(
    parameter int IN_W = 20,
    parameter int C0   = 32768,
    parameter int C1   = 0,
    parameter int C2   = 0
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic [IN_W-1:0]      s5_data,   // ← 共享的 CIC5 输出
    input  logic                 s5_valid,  // ← 共享的 CIC5 valid
    output logic [IN_W-1:0]      data_out,
    output logic                 valid_out
);
    // 内部只保留 2 倍抽取相位 + 3-tap FIR，删除 u_sinc5 例化
endmodule
```

`ad4080_dsp_chain` 侧：

```systemverilog
sinc5_decim #(.IN_W(IN_W)) u_sinc5 (
    .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
    .valid_in(v2 || v3), .data_in(corr_data), .dec_sel(dec_sel),
    .data_out(s5_data), .valid_out(s5_valid)
);

sinc5_comp #(.IN_W(IN_W)) u_sinc5c (
    .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
    .s5_data(s5_data), .s5_valid(s5_valid),      // 共享
    .data_out(s3_data), .valid_out(s3_valid)
);
```

> 注意 `v2 || v3`：SINC5 与 SINC5+comp 共用同一个 CIC5，但两者**不能同时选**（`filter_sel` 互斥），所以直接或起来即可；若未来要并行，需加一个旁路寄存器。

**收益：−964 FF，−5 加 −5 减（61-bit），零功能变化，约 −40% DSP 面积。**

**方案 2：SINC1 复用 CIC5 的第 1 级**

SINC1（一阶 CIC）数学上就是 CIC5 只保留 1 级。`u_sinc5` 内部的 `integ[0]` 就是 SINC1 的积分结果，只差一个 1 级梳状（`y[n]-y[n-D]`）。

```systemverilog
// sinc5_decim 增加抽头输出
output logic [ACC_W-1:0] integ0_tap;
assign integ0_tap = integ[0];

// SINC1 = 1 级 comb
always_ff @(posedge clk) if (dec_en) begin
    s1_d <= integ0_tap;
    s1_y <= integ0_tap - s1_d;
end
```

**收益：−80 FF，−1 个 32-bit 加法器，−整个 `sinc1_decim` 实例。** 代价：`DECIM` 必须共用（本来就是）。

**方案 3：编译期只保留被选中的链路**（若产品型号固定）

```systemverilog
generate
    if (FILTER_MODE == 1)      sinc1_decim u_f (...);
    else if (FILTER_MODE == 5) sinc5_decim u_f (...);
endgenerate
```
**收益：省 2/3 滤波器面积（≈−1,100 FF）。** 风险：失去运行时切换，只适合专用型号。

**方案 4：Hogenauer 裁剪（P2-2，与方案 1/2 叠加）**

runtime `dec_sel` 要求位宽覆盖最坏情况，但**每一级不必都是 61 bit**：29/37/45/53/61。每数组省 80 FF，三数组 ×2 个 CIC5 = **−480 FF**，同时把 5 级链从 3.9 ns 压到约 2.6 ns。

### 5.3 优化后预期

| 指标 | 当前 | 方案 1+4 | 变化 |
|:---|--:|--:|--:|
| DSP 链 FF | 2,237 | 793 | **−65%** |
| CIC5 单级路径 | 3.9 ns | 2.6 ns | −33% |
| 加减法器 | 21 | 10 | −52% |

---

## 6. 复位策略

### 6.1 现状：混用

| 模块 | 复位风格 | 位置 |
|:---|:---|:---|
| `ad4080_regs` | **异步** `negedge rst_n` | `ad4080_regs.sv:115` |
| `spi_slave` | 异步 | `spi_slave.sv:36, 60` |
| `sys_error_corr` | 异步 | `sys_error_corr.sv:76, 94` |
| `sinc1_decim` | 异步 | `sinc1_decim.sv:68` |
| `sinc5_decim` | 异步 | `sinc5_decim.sv:79, 100, 125` |
| `sinc5_comp` | 异步 | `sinc5_comp.sv:73` |
| `fifo_capture` | 异步 | `fifo_capture.sv:82` |
| `lvds_framer` | 异步 | `lvds_framer.sv:35` |
| `cal_two_point` | 异步 | `cal_two_point.sv:75, 92, 111` |
| **`result_fifo`** | **同步** `@(posedge clk)` + `if(!rst_n)` | `result_fifo.sv:157, 166, 181, 196` |

### 6.2 三个具体问题

**(1) 同一根 `rst_n` 上两种语义 → 释放沿不一致（P1-6）**

异步复位触发器在 `rst_n` 上升沿**立即**释放；同步复位触发器要等到**下一个时钟沿**。相差 0–1 拍。后果：

- `result_fifo` 的指针比 `fifo_capture` 的 `armed` 晚 1 拍释放；
- 若 `arm` 在释放后立即拉高，`armed` 已置位而 `wr_ptr/rd_ptr` 还是复位值 → 第一拍 `rd_en` 可能踩到 `empty` 边界，产生假的 `underflow`。

**(2) 异步复位释放无同步器 → 亚稳态**

`rst_n` 直接进 2,500+ 个异步复位端，释放沿与 `clk` 异步，每个触发器都满足 recovery/removal 违例条件。多数会自恢复，但 `result_fifo` 指针一旦亚稳态会直接破坏 FIFO 序。

**(3) 复位树扇出**

异步复位端 ≈ 2,590 个（见 10.1）。综合后会插 buffer 树；若 `rst_n` 来自焊盘且没有 `set_dont_touch_network`，工具可能在综合/PR 之间重复插 buffer 造成保持违例。

### 6.3 建议方案

```systemverilog
module reset_sync (
    input  logic clk,
    input  logic rst_n_async,
    output logic rst_n_sync
);
    logic [1:0] sync;
    always_ff @(posedge clk or negedge rst_n_async)
        if (!rst_n_async) sync <= 2'b00;
        else              sync <= {sync[0], 1'b1};
    assign rst_n_sync = sync[1];
endmodule
```

- `result_fifo` 改成 `always_ff @(posedge clk or negedge rst_n_sync)`，**不要**保留同步复位。
- `mem` 保持不复位（正确，见 3.5）。
- 若必须保留同步复位（例如为了 scan），则全设计统一，并在 SDC 里对 `rst_n` 加 `set_false_path`。

### 6.4 16K RAM 要不要复位？

**不要。** 理由：① 327,680 个复位端，复位树面积超过阵列本身；② SRAM 宏的复位脚（若有）只复位输出寄存器，不是阵列；③ FIFO 正确性由指针保证；④ 能复位阵列反而会掩盖"读了未写单元"的 bug。

当前 `result_fifo.sv:157-161` 是对的。唯一要补的是：ASIC 仿真中 SRAM 输出为 X，TB 必须能区分"合法 X"与"bug 导致的 X"，建议 `$readmemh` 预置可识别模式。

---

## 7. 位宽 / 符号审查

### 7.1 本轮已修复的两个真 bug（保留回归测试）

**(a) `sys_error_corr` 乘法位宽（上一版 P0）**

旧写法 `delta_s2 <= (prod_s1 * K_GAIN_S + (1 << 23)) >>> SHIFT;` 里 `(1<<23)` 是 32-bit 字面量，整个右值求值宽度被压到 32 bit，而乘积需要 40 bit → 0x3FF 增益下 `x > 0.78% FS` 就回绕。现改为：

```systemverilog
assign prodk_s2 = prod_s1 * K_GAIN_S;                             // 左值 41 bit → 求值 41 bit
assign corr_s2  = (prodk_s2 + (1 << (SHIFT-1))) >>> SHIFT;
```
**宽度正确**。但注意：`corr_s2`（24-bit）由 41-bit 表达式截断而来——`delta` 值域 ±16,384，24 bit 足够，**无风险**。

**回归测试建议**：`gain_code ∈ {0x001, 0x200, 0x3FF}` × `x ∈ {0, 1%, 10%, 50%, 100%} FS`，与实数模型比对，误差 ≤1 LSB。

**(b) `event_detect` 阈值符号（上一版功能错）**

```systemverilog
assign d12 = $signed(data_in[19:8] ^ 12'h800);   // offset-binary → 补码
```
`{hi_thresh[11], hi_thresh}` 是 13-bit 符号扩展，`d12`（12-bit signed）在比较时自动扩展到 13-bit signed，**正确**。`hi_clr_th` 范围 −4095..2047、`lo_clr_th` 0..4094，13-bit signed 装得下。

`if (clear_hi || !enable) hi_flag <= 0;` 与后面的置位在同一块内，**置位优先级更高**（后赋值覆盖），与注释一致。

### 7.2 新发现的位宽问题

| # | 位置 | 写法 | 风险 | 等级 |
|:--:|:---|:---|:---|:--:|
| 1 | `sinc5_comp.sv:61-64` | `c1_s = $signed({1'b0, C1[COEF_W-2:0]})`，`COEF_W=17` → 取 `C1[15:0]` 零扩展 | **负系数不可表示**：`C1=-1000`（0xFFFFFC18）→ `0xFC18` → **+64536**。Q1.15 补偿 FIR 通常含负抽头，填入真实系数后滤波器直接错 | **高** |
| 2 | `sinc5_decim.sv:58` | `shift_amt = 5*l_map`（实时），而累加用 `d_cur`（块边界锁存） | 改 `dec_sel` 时，块边界那一个输出被错误缩放；复位后第一块 `d_cur=2` 但 `shift_amt` 可能是 40 → 首输出≈0。`l_cur` 是死寄存器 | **高** |
| 3 | `sinc5_decim.sv:123,136` | `norm = (comb_ext + round) >> shift_amt`；`comb_ext` 按符号扩展但 `norm` 是 unsigned | 梳状器瞬态为负时 `>>` 逻辑右移 → 回绕成大正数，`data_out` 满幅跳变；无饱和 | 中 |
| 4 | `sinc1_decim.sv:87` | `data_out <= avg[IN_W-1:0]` | `avg` 值域 ≤2^20−1，当前正确；但无饱和，若 `d_cur` 与实际样本数不一致会静默截断 | 低 |
| 5 | `sinc5_decim.sv:35` | `ACC_W = IN_W + 5*8 + 1 = 61` | 位宽保守 +1 位是对的（满幅直流不溢出）；但**每级都用 61 bit** 是浪费，见 P2-2 | 低（面积） |
| 6 | `sinc5_decim.sv:122` | `shift_amt - 8'd1`，`shift_amt` 最小 5 | 无下溢风险；但若 `dec_map` 返回 `l=0` 会移 255 位。当前 `l≥1`，建议加断言 | 低 |
| 7 | `sinc5_comp.sv:59` | `q_w = (acc_r + (1<<14)) >>> 15`，`acc_r` 39-bit signed | 正确；但饱和判据用 `q_w`（41-bit）而非 `acc_r`，`C0>1.0` 时先移位后判饱和，语义仍正确 | 低 |
| 8 | `fifo_capture.sv:59` | `wm_quad = {wm_raw[13:2], 2'b00}` | 15-bit 输入取 [13:2] 丢掉最高位；`wm_raw ≤ 16384` 时 bit14=0 不影响，但**依赖 regs 的钳位** | 低 |
| 9 | `result_fifo.sv:113` | `AW'(DEPTH)` | `16384` 在 15 bit 里刚好；`DEPTH=32768` 会溢出成 0。`initial` 只检查 2 的幂，未检查 `DEPTH ≤ 2^(AW-1)` | 低 |
| 10 | `ad4080_regs.sv:164` | `crc_en_r <= (wdata[7:6]==2'b01) && (wdata[1:0]==2'b10)` | 与注释"必须互补"一致；但 `wdata[7:6]=01` 且 `[1:0]=10` 才使能，手册若允许其他组合需复核 | 低 |

### 7.3 关于 `>>` vs `>>>` 与舍入

- `sys_error_corr`：`>>>`，正确（signed）。
- `sinc5_decim`：`>>`，但 `comb_ext`/`norm` 是 unsigned 语境，**语义正确但负瞬态会回绕**（7.2 #3）。
- `sinc5_comp`：`>>>`，正确。
- `sinc1_decim`：无移位，切片，正确。
- `cal_two_point`：`>>>`，正确。

**舍入**：`sys_error_corr` +2²³、`sinc5_decim` +2^(shift−1)、`sinc5_comp` +2¹⁴、`sinc1_decim` +(D/2)，**四处都做了四舍五入，策略正确**。没有发现"该 `>>>` 却用 `>>`"的地方。

---

## 8. 可测性（DFT）

| 项目 | 现状 | 评价 / 建议 |
|:---|:---|:---|
| Scan 链 | 无 scan 结构；全部单沿 DFF + 无 gated clock（只有 CE），**scan 插入友好** | ✅ 需补：① 所有时钟走 `test_clk` mux；② `bit_en`/`valid_in` 等 CE 在 scan 下强制 1；③ `dco = bit_en`（`lvds_framer.sv:64`）是组合直通，scan 下需 mux |
| 异步复位 | 2,500+ 个异步复位端，无 reset mux | ⚠️ scan 下复位必须可控：加 `test_rst_n` mux，或 scan 时 `set_case_analysis rst_n=1` |
| 16K RAM 的 BIST | **完全没有**。`mem` 是裸 `reg` 数组，无 BIST 端口、无 bypass | ❌ **必须补**。327,680 bit 的 SRAM 不带 MBIST 无法验收。需要宏的 BIST collar + 顶层 `bist_en/bist_done/bist_fail` |
| RAM 的 scan bypass | 无 | ❌ 必须补。scan 捕获时 SRAM 的 `Q` 是不可控源，需 `ram_bypass` 把 `rd_data` 强制 0（见 3.4） |
| 寄存器可读回 | ✅ 22 个地址全部可读（`ad4080_regs.sv:213-242`），含 `0x14 DEVICE_STATUS` | ✅ 本设计最大的 DFT 优点：不用 scan 也能通过 SPI 做功能自检。`status` 的 8 位定义见 `:244-245` |
| 内部关键节点可观测 | 无 | ⚠️ 建议在 `sys_error_corr` 后、CIC5 后各加一个调试只读寄存器（复用现有 SPI 地址空间），否则现场只能看到 `dout`，无法区分"校正错"还是"滤波错" |
| 时钟 | 单时钟；`dco` 是组合输出 | ✅ 单时钟域对 DFT 最友好 |
| 存储器初始化 | `mem` 无初值 | ⚠️ ASIC 仿真为 X；建议 TB `$readmemh` 预置可识别模式 |

**DFT 结论**：结构上"好插 scan"，但 **SRAM 没有 BIST/bypass 是硬伤**，与 P0-4 是同一件事的两面——换宏时必须一起加 BIST collar。

---

## 9. 逐模块问题汇总表

| 模块 | 问题 | 依据（文件:行） | 影响 | 修改建议（SV 片段） |
|:---|:---|:---|:---|:---|
| `ad4080_top` | **端口与子模块不匹配，无法 elaborate** | `:67-74` vs `regs:55,86`；`:88` vs `dsp_chain:23-34` | **P0-1** | 统一命名：`.dec_sel(dec_sel)`、去掉 `.status_in`、`u_chain` 删 `DECIM` 参数并加 `.dec_sel(dec_sel)`；`status` 改为 `u_regs` 的输出 |
| `ad4080_top` | 4 个模块未集成；无 `cnv_p/cnv_n`/`gpio_*`/`alert` 端口 | `:18-52`；全工程 grep | **P0-2** 功能缺失 | 增端口 + 例化 `cnv_ctrl`/`event_detect`/`gpio_ctrl`/`cal_two_point`，把 regs 新输出接上 |
| `ad4080_top` | `status_in` 用 `assign` 生成再送回 regs | `:123-125` | 随 P0-1 一起改 | 改为 `u_regs` 直接输出 `status`，`status = u_regs.status` |
| `fifo_capture` | **`rd_fire` 未接 FIFO 的 `rd_en`** | `:80` vs `:139` | **P0-3** 前置数据被读空 | `.rd_en(rd_fire)`；同时 `underflow` 语义要重新定义（非法读不再进 FIFO，应由本模块自行置位） |
| `fifo_capture` | 自身无 watermark 钳位，依赖 `ad4080_regs` | `:57-59` | P1-9（单独复用时挂死） | 在本模块内也做 `(watermark==0\|\|>DEPTH)?…` 钳位，双重保险 |
| `fifo_capture` | mode 11 `rd_target=16384` 恒满假设 | `:67,126` | P1-9 `read_done` 不置位 | 用"实际存量"（`count`）作目标，或 `rd_target = min(DEPTH, captured 时 FIFO 占用)` |
| `fifo_capture` | `arm`/`event`/`rd_en` 无同步器 | `:92,101,80` | P1-8 | 顶层加 2FF |
| `result_fifo` | **`mem` 只能推成 FF 阵列** | `:131` | **P0-4** 不可流片 | 换 SDP 宏 + BIST/bypass（3.4） |
| `result_fifo` | `count` 组合减法 + 4 比较器 | `:144-149` | 0.65 ns（1 GHz 边际） | 寄存器化 count（加减 1），保持 full/empty 等价 |
| `result_fifo` | `initial $fatal` | `:117-126` | P2 工具兼容 | 包 `` `ifndef SYNTHESIS `` |
| `result_fifo` | 同步复位，与全设计异步混用 | `:157,166,181,196` | P1-6 | 统一为异步复位同步释放 |
| `result_fifo` | `AW'(DEPTH)` 溢出未检查 | `:113` | 低 | 加 `DEPTH ≤ 2**(AW-1)` 断言 |
| `sinc5_decim` | **5 级 61-bit 串行加减链** | `:70-76,109-115` | **P1-2** 3.9 ns | Hogenauer 裁剪 + 级间寄存器 |
| `sinc5_decim` | **`shift_amt` 用 `l_map` 而非 `l_cur`** | `:58` vs `:130-131,139` | **P1-1** 输出缩放错 | `assign shift_amt = {2'b0,l_cur,2'b00} + {4'b0,l_cur};` |
| `sinc5_decim` | `l_cur` 死寄存器 | `:131,139` | P2-3 | 按上条改为使用 |
| `sinc5_decim` | `rst_n \|\| filter_sync` 同条件 | `:101` | P1-7 | 拆嵌套 if |
| `sinc5_decim` | 位宽未裁剪，全 61 bit | `:35` | P2-2 −480 FF | 逐级 `IN_W+(k+1)*8+1` |
| `sinc5_decim` | 负瞬态无饱和 | `:117-123,136` | P2-6 | 对 `norm` 做 `[0, 2^IN_W-1]` 饱和 |
| `sinc5_decim` | 复位后首块 `d_cur=2` 但 `l_map` 可能=8 | `:130-131` | 首输出错 1 个样本 | 复位时也 `d_cur<=d_map; l_cur<=l_map` |
| `sinc1_decim` | 复位后首块固定 D=2 | `:72-73` | P2-8 | 同上 |
| `sinc1_decim` | 无饱和 | `:87` | 低 | 加饱和（防御） |
| `sinc5_comp` | **内部重复例化 CIC5** | `:45-49` | **P2-1** −964 FF | 改为 `s5_data/s5_valid` 输入（5.2 方案 1） |
| `sinc5_comp` | **负系数不可表示** | `:61-64` | **P1-5** 系数错 | `assign c1_s = $signed(C1[16:0]);`（17-bit 补码），或统一用 `$signed(C1[COEF_W-1:0])` |
| `sinc5_comp` | 3 乘法一级组合 | `:67-71` | P1-4 2.4 ns | 3 级流水 / 时分复用 |
| `sinc5_comp` | `rst_n \|\| filter_sync` 同条件 | `:74` | P1-7 | 拆嵌套 if |
| `sys_error_corr` | 第 2 级 2.6 ns | `:73-74,106-109` | P1-3 | 拆 2a/2b |
| `sys_error_corr` | 注释 2 级、实际 3 拍 | `:25` vs `:105-125` | P2-4 | 改注释或改实现 |
| `sys_error_corr` | `corr_s2` 在 3 处重复出现 | `:107-109` | 低 | 已由 CSE 合并；可提到 local 变量 |
| `ad4080_regs` | `status` 由本模块 `assign`，同时 `rdata` 读它 | `:218,244-245` | 无环（status 只依赖输入） | 保持 |
| `ad4080_regs` | 22 路 16-bit 全比较译码 | `:213-242` | 0.50 ns（可接受） | 低功耗可分段译码 |
| `spi_slave` | 过采样倍数未约束，CS 提前量不足会吞首沿 | `:36-48,75-88` | P1-10 整帧错位 | 见 2.2/2.3；把 `t_CSSCLK` 写进规格 |
| `spi_slave` | CS 高电平不清 `bitcnt` | `:83-86` | 低 残帧 | CS 高时持续清 |
| `cnv_ctrl` | **未被任何层次例化** | 全工程 | **P0-2** | 顶层例化并加 `cnv_p/cnv_n` 端口 |
| `cnv_ctrl` | XOR 在同步器之前 | `:23` | 低 窄脉冲 | 先 2FF 再 XOR |
| `gpio_ctrl` | **未被任何层次例化** | 全工程 | **P0-2** | 顶层例化，接 `funcs` 打包 |
| `gpio_ctrl` | `fsync_i`/`ext_evt_i` 无同步（来自 `gpio_i`） | `:61-65` | P1-8 | 输入先 2FF |
| `event_detect` | **未被任何层次例化** | 全工程 | **P0-2** | 顶层例化，接 `hi_clr/lo_clr` |
| `event_detect` | `!enable` 时清粘滞标志 | `:73-74` | 低 语义 | enable 只屏蔽更新，不清标志 |
| `cal_two_point` | **仅在 TB 例化** | `sim/tb/ad4080_cal_tb.sv` | **P0-2** | 接入 SPI 命令路径 |
| `cal_two_point` | `error` 恒 0 | `:127` | P2-5 | `if (y2 <= y1) error <= 1'b1;` |
| `cal_two_point` | 18 级 2.6 ns | `:101-105` | P1（低频，只跑一次） | 改多周期 FSM |
| `lvds_framer` | `dco = bit_en` 组合直通 | `:64` | 低 时钟质量/DFT | 寄存一拍或用 ODDR |
| `lvds_framer` | `valid_in` 抢占会截断当前帧 | `:42-47` | 低 丢帧 | 加 `busy` 反压或双缓冲 |
| `ad4080_dsp_chain` | 2×CIC5 + 1×SINC1 常驻 | `:66-82`；`sinc5_comp:45` | **P2-1** −964 FF | 见 5.2 |

---

## 10. 面积估算表

### 10.1 触发器与组合门 `[估算，28nm]`

| 模块 | 触发器 | 组合门（≈NAND2） | RAM bit |
|:---|--:|--:|--:|
| `ad4080_regs` | 152 | 1,600 | 0 |
| `spi_slave` | 71 | 600 | 0 |
| `sys_error_corr` | 125 | 6,500 | 0 |
| `sinc1_decim` | 80 | 350 | 0 |
| `sinc5_decim` ×2（含 `sinc5_comp` 内那份） | **1,928** | 14,000 | 0 |
| `sinc5_comp` FIR | 104 | 10,000 | 0 |
| `result_fifo` | 53 | 400 | **327,680** |
| `fifo_capture` | 49 | 350 | 0 |
| `lvds_framer` | 28 | 150 | 0 |
| `ad4080_top` 胶合 | 0 | 100 | 0 |
| **合计** | **≈2,590** | **≈34 k** | **327,680** |

> `sinc5_comp` 的 10,000 门只在"填真实系数"时成立。默认 `C0=32768,C1=C2=0` 时综合器会折叠成一根移位线，**看起来是 0 门**——不要被这个假象误导。

### 10.2 面积换算

| 项 | 计算 | 面积 |
|:---|:---|:---|
| 触发器（2,590 × 1.1 µm²） | 28nm DFF 含 mux 开销 `[假设]` | ≈ 2,850 µm² |
| 组合逻辑（34 k × 1.0 µm²） | NAND2 等效 `[假设]` | ≈ 34,000 µm² |
| **DSP 逻辑小计** | | **≈ 0.037 mm²** |
| 16K×20 SDP SRAM（含外围） | 327,680 × 0.12 × 1.35 | **≈ 0.053 mm²**（32-bit 宏 0.085） |
| **合计** | | **≈ 0.09 mm²**（不含 PLL/ADC/焊盘） |

### 10.3 优化收益

| 项 | 当前 | 方案 1+4 | 节省 |
|:---|--:|--:|--:|
| DSP 触发器 | 2,237 | 793 | **1,444（65%）** |
| CIC5 单级路径 | 3.9 ns | 2.6 ns | 1.3 ns |
| DSP 组合门 | ≈31 k | ≈18 k | 13 k |
| 面积 | ≈0.034 mm² | ≈0.020 mm² | 0.014 mm² |

> 注意：上一版 D=4 时 CIC5 只有 30 bit（488 FF/个）。改成运行时抽取后固定 61 bit，**D=4 场景下面积翻倍**。如果产品主要工作在低抽取率，建议加"编译期最大 D"参数（`MAX_LOG2D` 可配置为 2/5/8），按型号取窄位宽；否则必须做 Hogenauer 裁剪。

---

## 11. 综合脚本建议

### 11.1 时钟与 I/O 约束（SDC）

```tcl
#============================================================
# 1) 主时钟（500 MHz 目标；1 GHz 压力测试改 -period 1.000）
#============================================================
create_clock -name clk -period 2.000 -waveform {0 1.000} [get_ports clk]

#============================================================
# 2) SPI / CNV：不作为时钟使用（内部 2FF 过采样）
#    不 create_clock，按"异步输入 + 同步器"约束
#============================================================
set_false_path -from [get_ports {sclk cs_n sdi}]
set_max_delay 2.000 -datapath_only -from [get_ports {sclk cs_n sdi}]

# 若要把 SPI 当独立时钟域做预算（二选一，不要和上面同时用）
# create_clock -name sclk -period 80.000 [get_ports sclk]
# set_clock_groups -asynchronous -group {clk} -group {sclk}
# set_input_delay  -clock sclk -max 5.0 [get_ports {sdi cs_n}]
# set_output_delay -clock sclk -max 5.0 [get_ports sdo]

#============================================================
# 3) CNV / 事件 / 外部控制 / GPIO：来自焊盘，按异步处理
#    （建议先在 RTL 加 2FF，见 2.5，再只留 false_path）
#============================================================
set_false_path -from [get_ports {cnv_p cnv_n event arm rd_en filter_sync bit_en gpio_i[*]}]
set_max_delay 2.000 -datapath_only -from [get_ports {cnv_p cnv_n}]

#============================================================
# 4) 同步数据接口
#============================================================
set_input_delay  -clock clk -max 0.500 [get_ports {x[*] valid_in}]
set_input_delay  -clock clk -min 0.100 [get_ports {x[*] valid_in}]
set_output_delay -clock clk -max 0.500 [get_ports {dout[*] dout_valid}]
set_output_delay -clock clk -max 0.500 [get_ports {ser_data ser_frame status[*]}]
set_output_delay -clock clk -max 0.300 [get_ports dco]

#============================================================
# 5) 复位
#============================================================
set_dont_touch_network [get_nets rst_n]
set_ideal_network      [get_nets rst_n]
set_false_path -from [get_ports rst_n]
set_max_transition 0.200 [get_nets rst_n]

#============================================================
# 6) 例外：只写"确实成立"的
#============================================================
# 6.1 cal_two_point 若改成 FSM 多周期，再加：
# set_multicycle_path 4 -setup -from [get_pins u_cal/state_reg*/Q]
# set_multicycle_path 3 -hold  -from [get_pins u_cal/state_reg*/Q]

# 6.2 禁止对 SRAM 读写口加 false_path！
#     result_fifo 的 rd_ptr→mem→rd_data 是真实单周期路径。
#     ❌ set_false_path -through [get_pins u_fifo/mem*]

# 6.3 不要对 filter_sel 做 set_case_analysis
#     它是运行时寄存器，做 case analysis 会漏约束另外两条链路
```

### 11.2 综合选项建议

| 项 | 建议 | 理由 |
|:---|:---|:---|
| 时钟门控 | `set_clock_gating_check -setup 0.100 -hold 0.050` | 若后续插入 ICG |
| 复位 | `set_dont_touch_network rst_n` + `set_ideal_network` | 防止综合/PR 重复插 buffer |
| 乘法器 | 允许工具用 `DW02_mult` / 设计仓库乘法器；不强行 `set_implementation` | 31×10、20×17 都不大，标准单元综合即可 |
| 共享 | 打开 CSE / resource sharing（DC `-resource_sharing`，Genus `-share`） | `sys_error_corr` 的 `corr_s2` 写了 3 遍、`sinc5_comp` 3 个乘法 |
| 边界优化 | **不要**跨 `result_fifo` 边界做 `-boundary_optimization` | 会把指针逻辑推进/推出，破坏 SRAM 接口时序 |
| 最大扇出 | `set_max_fanout 32 [current_design]`；`rst_n`/`filter_sync`/`scan_en` 单独放宽 | `filter_sync` 扇出 1,000+ |
| 最大转换 | `set_max_transition 0.200 [current_design]` | 28nm 常规 |
| 面积/时序 | 先 `compile -map_effort high`；CIC5 不收敛优先加流水，**不要降频** | 降频违反 20 MSPS 指标 |
| SRAM | 综合阶段 `dont_touch` 包住宏 wrapper | 防止工具把宏的 `Q` 打平 |

### 11.3 综合前必须跑的检查（建议做成 CI）

```tcl
# 1) elaborate：端口/参数一致性（当前会在 P0-1 处失败）
# 2) 位宽 lint：乘法左值宽度 < 操作数宽度之和
# 3) latch / 组合环
# 4) 复位域交叉：同一信号既有 async 又有 sync 复位
# 5) 未例化模块清单（当前 cnv_ctrl / gpio_ctrl / event_detect / cal_two_point 是孤儿）
# 6) 端口悬空：clk 域输入未连接（当前 u_chain.dec_sel）
```

---

## 附录 A：快照与文件 mtime

| 文件 | mtime | 本次是否重读 |
|:---|:---|:---|
| `sys_error_corr.sv` | 13:30:51 | ✅ 重读（已改） |
| `sinc1_decim.sv` | 13:31:15 | ✅ 重读（已改） |
| `sinc5_decim.sv` | 13:31:59 | ✅ 重读（已改） |
| `sinc5_comp.sv` | 13:33:17 | ✅ 重读（已改） |
| `ad4080_dsp_chain.sv` | 13:33:34 | ✅ 重读（已改） |
| `event_detect.sv` | 13:33:47 | ✅ 重读（已改） |
| `gpio_ctrl.sv` | 13:34:35 | ✅ 重读（已改） |
| `ad4080_regs.sv` | 13:40:12 | ✅ 重读（已改） |
| `fifo_capture.sv` | 13:40:32 | ✅ 重读（已改） |
| `ad4080_top.sv` | 13:13:44 | ✅ 重读（未改 → 与子模块脱节） |
| `result_fifo.sv` | 13:17:12 | ✅ |
| `spi_slave.sv` | 13:11:23 | ✅ |
| `lvds_framer.sv` | 13:13:35 | ✅ |
| `cnv_ctrl.sv` | 13:20:25 | ✅ |
| `cal_two_point.sv` | 12:11:48 | ✅ |

## 附录 B：本次审查的覆盖范围与未做项

| 项 | 状态 |
|:---|:---|
| 15 个 RTL 文件逐行阅读 | ✅ 全覆盖（13:40 快照） |
| 组合环 / latch / 多驱动 | ✅ 静态确认 |
| CDC 结构 | ✅ 静态确认（倍数要求为推算） |
| 关键路径 | ⚠️ 手算模型，**未跑 STA** |
| 面积 | ⚠️ 手算模型，**未跑综合** |
| 功能仿真 | ❌ 本机无仿真器，**未跑** |
| 形式验证 / 等价性 | ❌ 未做 |
| 功耗 | ❌ 未评估（只给时钟门控的定性收益） |

## 附录 C：建议的修复顺序

1. **P0-1** 顶层接口对齐（改 `ad4080_top.sv` 十几行）——不修则一切无法验证
2. **P0-3** `fifo_capture` 的 `rd_fire` 接到 FIFO `rd_en`（改 1 行 + 重定义 underflow）
3. **P1-1** `sinc5_decim` 的 `shift_amt` 改用 `l_cur`（改 1 行）
4. **P1-5** `sinc5_comp` 系数改成 17-bit 补码
5. **P1-2 / P2-2** CIC5 Hogenauer 裁剪（免费，−480 FF，−1.3 ns）→ 级间流水
6. **P0-4** `result_fifo` 换 SRAM 宏 + BIST/bypass（需要工艺厂配合，提前启动）
7. **P0-2** 顶层集成 4 个孤儿模块
8. **P2-1** 共享 CIC5（−964 FF）
9. **P1-6** 复位统一为"异步复位、同步释放"
