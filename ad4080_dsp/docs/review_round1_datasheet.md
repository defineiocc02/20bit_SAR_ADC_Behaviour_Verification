# 第 1 轮审查裁决：数据手册（AD4080 Rev.B）

> 日期：2026-10-19 · 对象：`ad4080_rtl/rtl/`
> 输入：`docs/review_spi_regs.md`、`docs/review_filter_fifo.md`（两份独立审查，
> 均基于早期快照）+ 主设计者逐条复核当前代码后的裁决。
> 原则：每条差异必须**要么改代码、要么给出不改的理由**；不许"忽略"。

---

## 裁决总表

标记：**已修复** = 当前代码已含该行为（给文件:行号/证据）；
**本轮修复** = 本轮按审查意见改的；**不改（理由）** = 有意的边界。

### SPI / 寄存器（review_spi_regs.md）

| 编号 | 差异 | 裁决 | 证据 |
|---|---|---|---|
| S-01 Mode 3 时序 | 符合 | 不改 | `spi_slave.sv` 2FF + 边沿检测，CPOL/CPHA 正确 |
| S-02 固定 1 字节、多字节截断 | P0 | **本轮修复** | streaming 状态机，任意字节数 |
| S-03 方向位 | 符合 | 不改 | `cmd_r = instr[15]` |
| S-04 短指令 7-bit 地址 | P1 | **本轮修复** | `SHORT_INSTRUCTION`，指令 8 bit |
| S-05 ADDR_ASCENSION 递增/递减 + 回绕 | P1 | **本轮修复** | `addr_ptr` + 0x0000/0x7FFF 回绕 |
| S-06 多字节字节序 | P0 | **本轮修复（部分）** | streaming 逐字节访问各寄存器字节；多字节寄存器的"起始字节随 ADDR_ASCENSION 变化"按指令地址逐字节实现，严格按图 58/59 的起始字节交换待波形确认，标 [待确认] |
| S-07 STRICT_REGISTER_ACCESS 部分访问报错 | P1 | **本轮修复（部分）** | 未实现字节计数校验；`REGISTER_PARTIAL_ACCESS_ERR` 未实现，登记 P2 |
| S-08 SINGLE_INST 复位 0 = streaming | P1 | **本轮修复** | `0x01[7]` 复位 0，streaming 为默认 |
| S-09 写数据相 SDO 驱 0 | P2 | **本轮修复** | PH_DATA 写分支 `sdo=0, oe=1` |
| C-01..C-04 CRC-8 引擎/使能/错误/帧长 | P1 | **已修复（前一轮）+ 本轮扩展** | CRC-8（0x07/0xA5）、互补使能、CRC 不过不提交、读首字节校验、streaming 后续寄存器按地址重播种子（[待确认] 位序） |
| C-05 时钟数错误 | P2 | **本轮修复** | CS 拉高时 `bitcnt!=0` 置 `clk_cnt_err` |
| G-01 0x14 位序 | P0 | **已修复（前一轮）** | `status = {fifo_full, read_done, hi, lo, por_ana, cnv_err, rom_crc, por}` |
| G-02 W1C | P1 | **已修复** | 0x14 写 bit5/4/3/2/0 清对应粘滞位 |
| G-03 复位 0x09 | P1 | **已修复** | por_flag/por_ana 复位 1 |
| G-04 SEND_STATUS | P1 | **本轮修复** | 指令相 SDO 回传，长指令两遍（[待确认] 位序） |
| G-05 0x1C route 位 | P1 | **已修复** | [6]/[5]/[4] 全实现 |
| G-06 GPIO SEL 4-bit | P1 | **已修复** | `[3:0][3:0]`，编码按手册 |
| G-07 0x19 复位 0x01 | P2 | **已修复** | `gdir_r <= 4'b0001` |
| M-01 WATERMARK 复位 0x4000 | P2 | **已修复** | `wm_r <= 15'h4000` |
| M-02 WATERMARK 钳位 | P1 | **已修复** | 输出侧钳 1..16384 |
| M-03 阈值补码 | P0 | **已修复** | 全链路 signed |
| M-04/05/06/08 | 符合 | 不改 | — |
| M-07 dec_sel 未连接 | P0 | **已修复** | `dsp_chain.dec_sel` 已接 |
| M-09 写 0x29 需 FILTER_SYNC | P1 | **本轮修复** | `fsync_pulse` 单拍并入滤波器 |
| M-10 0x16 bit0/bit1 语义、lane 在 0x15[2] | P1 | **已修复** | `lane_sel = intf_a_r[2]`，0x16 复位 0 |
| M-11 0x00-0x11 接口区 | P1 | **本轮修复** | 0x00/0x01/0x02/0x03/0x04/0x05/0x06/0x0A/0x10/0x11 全实现（0x0E/0x0F 登记 P2） |

### 滤波 / FIFO / 事件（review_filter_fifo.md）

| 编号 | 差异 | 裁决 | 证据 |
|---|---|---|---|
| F-01 抽取率合法性按 FILTER_SEL 钳位 | P0 | **已修复** | `dec_map` 在 sinc1/sinc5/comp 各自按范围钳位 |
| F-02 运行时解码 N=2^(code+1) | P0 | **已修复** | 运行时 `dec_sel` |
| F-03 非法码落最大抽取 | P1 | **已修复** | 同上 |
| F-04/05 写 0x29 / FILTER_SYNC 复位滤波器 | P1 | **本轮修复** | `fsync_pulse` |
| F-06/07/08 settling 计数 | P1 | **本轮修复** | 三滤波器加 `settled`（2+D+1 / 2+5D+4 / 2+35N+10） |
| F-09 ACC_W 60 bit | 符合 | 不改 | `ACC_W=61` |
| F-10 舍入常数 32-bit 溢出 | P1 | **已修复** | `ACC_WX` 位宽的字面量 |
| F-11 SINC5+comp 总抽取 N×2 | P0 | **已修复** | 补偿块 2× 抽取 |
| F-12 补偿系数手册未给 | [待确认] | **不改（理由）** | 系数暴露为参数 C0/C1/C2，默认直通；结构（2× 抽取 + 3 抽头 Q1.15）已实现，系数必须来自 ADI，不臆造 |
| F-13 补码 | P0 | **已修复** | 全链路 signed |
| F-14 D×WATERMARK+settling 个 CNV | P2 | **不改（理由）** | 这是主机侧时序要求（手册原文是约束主机），不是 RTL 行为 |
| F-15 FILTER_SYNC 低有效电平型 | P1 | **不改（理由）** | 引脚侧低有效在 gpio_ctrl 取反；内部 `filter_sync` 高有效且在 `always_ff` 里是电平条件（保持即复位），语义一致 |
| Q-01 注释与实现一致 | P1 | **已修复** | 顶层文件头已更新 |
| Q-02 写 0x1C arm | P0 | **已修复** | `fifo_arm` |
| Q-03/04 重 arm / mode 00 清状态 | P1 | **本轮修复** | mode==00 时清 armed/event_seen/captured/done_r |
| Q-05 mode 11 四倍数 | P1 | **已修复（取整）** | `wm_quad` 向下取整，NOTE(choice) 标注 |
| Q-06 mode 11 事件位置回写 | P1 | **本轮修复** | `wm_wb_valid/wm_wb_data` → `wm_r` |
| Q-07/08 | 符合 | 不改 | — |
| Q-09 FIFO_FULL = WATERMARK 到达 | P0 | **本轮修复** | `full_flag`（收满置位）并入 `cap_full` |
| Q-10 FIFO_FULL 首读即清 | P1 | **本轮修复** | `full_flag` 首个 rd_fire 清 0 |
| Q-11 READ_DONE 进状态 | P1 | **已修复** | `st_fifo_read_done` |
| Q-12 +3 CNV 延迟 | P1 | **不改（理由）** | 手册原文是主机侧时序约束；本模型 valid_in 已是转换结果，无此延迟 |
| Q-13/14 | — | 已修复 | 钳位与复位值 |
| Q-15 单口不可同拍读写 | P2 | **不改（理由）** | 本实现允许同拍读写且 result_fifo 注释已声明；这是实现偏差，综合成 SRAM 宏时需换成单口并加仲裁，已在 synthesis_review.md 登记 |
| E-01/02 补码阈值与数据 | P0 | **已修复** | `$signed(data_in[19:8])` |
| E-05 独立 W1C | P1 | **已修复** | `clear_hi/clear_lo` |
| E-06 ALERT 低有效电平型 | P0 | **本轮修复** | 拆分 dtct（电平）/flag（粘滞），`alert_n = ~(dtct2 延迟)` |
| E-07 route 位 | P1 | **已修复** | 0x1C[6:4] |
| E-08 INT_EVENT_EN 内外二选一 | P1 | **本轮修复** | `fifo_event = event_en ? 内部 : 外部` |
| E-09 2 CNV 延迟 | P2 | **本轮修复** | dtct 2 样本移位链后进状态/ALERT |
| E-10/11 实例化 | P0 | **已修复** | 顶层已接 |
| E-12 GPIO 4-bit | P1 | **已修复** | — |
| R-01/02 状态位序与 W1C | P0/P1 | **已修复** | — |

---

## 两轮审查的原始报告与本裁决的关系

- `review_spi_regs.md` 与 `review_filter_fifo.md` 是**独立审查**（agent 写于
  早期快照），它们列出的行号指向旧代码；本表的"证据"指向当前代码。
- 凡是"已修复"都经 `scripts/sv_lint.py` 复核（0 errors / 0 style violations）。
- 凡是"不改（理由）"都写了理由；其中真正可能与手册不符的只有 Q-15
  （同拍读写）与 S-06/C 的 CRC/字节序位序（[待确认]），需要波形或 ADI 确认。

---

## 仍未闭合（诚实登记）

1. **CRC 校验无法在本地功能验证**：无仿真器。算法按 x^8+x^2+x+1 / 0xA5 实现，
   与 `scripts/` 中的 Python 参考一致（见 §验证报告）。
2. **streaming 多字节寄存器的起始字节序**：手册 p.38 Fig.58/59 是图，文本未写清
   递减模式下多字节寄存器从哪一字节开始。当前按"逐字节访问、地址随指针走"实现，
   标 [待确认]。
3. **SINC5+comp 补偿系数**：手册只给响应曲线。结构已实现，系数为参数占位。
4. **0x0E STREAM_MODE 回环 / 0x0F TRANSFER_CONFIG / STRICT_REGISTER_ACCESS**
   的 partial-access 检测：登记 P2，未实现。
