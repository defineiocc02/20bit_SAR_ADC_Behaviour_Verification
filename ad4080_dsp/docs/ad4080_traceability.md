# AD4080 数字 RTL 逐行可追溯表

> 日期：2026-10-19 · 规则：每一条手册功能都给出 手册页码 → RTL 文件:行号 → 证据摘句。
> 未实现项单独列在末尾，不允许混进"已实现"。

---

## 1. 数据通路

| 手册功能 | 手册依据 | RTL | 证据 |
|---|---|---|---|
| 20-bit 二进制补码输出 | p.20 Table 7 | 全链路 | `event_detect.sv:69` `$signed(data_in[19:8])`；`sys_error_corr.sv:110` `$signed(x)` |
| 系统误差校正 y=m·x+c | p.71 | `sys_error_corr.sv` | `:110` 乘法，`:97` 舍入移位，`:136-145` 饱和 |
| OFFSET 12-bit 补码、±2048 | p.93 Table 60 | `ad4080_regs.sv` | `0x25/0x26` 写分支；`offset_code` 输出 |
| GAIN 10-bit，0x200=1.0，±1.5594% | p.93 Table 61 | `ad4080_regs.sv` + `sys_error_corr.sv` | `0x27/0x28`；`K_GAIN=512`（分母 511） |
| 两点校准 | p.71 | `cal_two_point.sv` | `:53` 测试点补码，`:128` 增益积，`:147` 夹紧 |

## 2. 数字滤波

| 手册功能 | 手册依据 | RTL | 证据 |
|---|---|---|---|
| FILTER_SEL 四路径 | p.94 Table 62 | `ad4080_dsp_chain.sv` | `case(filter_sel)` |
| SINC_DEC_RATE N=2^(code+1) | p.94 | `sinc1_decim.sv:58` / `sinc5_decim.sv:64` | `dec_map` |
| 非法码落最大抽取 | p.94 | 同上 | `code>9/7` 分支 |
| SINC1 建立 2+D+1 | p.69 | `sinc1_decim.sv:123` | `settle_target = d_cur + 3` |
| SINC5 建立 2+5D+4 | p.69 | `sinc5_decim.sv:179` | `(d_cur<<2)+d_cur+6` |
| SINC5+comp 建立 2+35N+10 | p.69 | `sinc5_comp.sv:138-150` | `35N+12` |
| SINC5+comp 总抽取 N×2 | p.94 | `sinc5_comp.sv:111` | `phase==0` 才输出 |
| FILTER_SYNC 复位滤波器 | p.64, p.94 | `ad4080_regs.sv:317` → `fsync_pulse` | 写 0x29 产生脉冲 |

## 3. 结果 FIFO

| 手册功能 | 手册依据 | RTL | 证据 |
|---|---|---|---|
| 16K 深度 | p.57 | `result_fifo.sv` | `DEPTH=16384` |
| 四种 FIFO_MODE | p.57 Table 27 | `fifo_capture.sv` | `case(mode)` |
| 写 FIFO_MODE 即 arm | p.62 | `ad4080_regs.sv:303` | `fifo_arm_r<=1` |
| mode 00 复位捕获状态 | p.57 | `fifo_capture.sv:123` | 清 armed/event_seen/… |
| mode 11 四倍数 WATERMARK | p.91 | `fifo_capture.sv:81` | `wm_quad` |
| FIFO_FULL 收满即置位 | p.58 Fig.86 | `fifo_capture.sv:156` | `full_flag<=1` |
| FIFO_FULL 首读即清 | p.53 | `fifo_capture.sv:177` | `full_flag<=0` |
| mode 11 事件位置回写 | p.57 | `fifo_capture.sv:158` + `ad4080_regs.sv` | `wm_wb_valid/data` → `wm_r` |
| FIFO_READ_DONE | p.83 | `fifo_capture.sv` | `read_done` |

## 4. 事件检测 / 告警 / GPIO

| 手册功能 | 手册依据 | RTL | 证据 |
|---|---|---|---|
| 阈值 12-bit 补码 | p.92 | `event_detect.sv:69` | 补码比较 |
| 迟滞 11-bit 无符号 | p.91 | `event_detect.sv:73-75` | `hyst_s` |
| HI/LO 迟滞电平（HI_DTCT/LO_DTCT） | p.54-56 | `event_detect.sv:108-115` | `hi_dtct/lo_dtct` |
| 状态位粘滞 + W1C | p.55, p.83 | `event_detect.sv` + `ad4080_regs.sv:284` | `hi_flag` + `0x14` W1C |
| ALERT 低有效电平型 | p.53, p.55 | `event_detect.sv:137` | `alert_n = ~(hi_d2|lo_d2)` |
| 2 CNV 延迟 | p.55 | `event_detect.sv:126-131` | `hi_d1/hi_d2` |
| HI_ROUTE/LO_ROUTE/CNV_ERR_ROUTE | p.90 | `ad4080_regs.sv` + `ad4080_top.sv:159` | `event_en ? 内部 : 外部` |
| GPIO SEL 4-bit 编码 | p.88-89 | `gpio_ctrl.sv:81-104` | 10 个编码 |
| GPIO 复位 0x01（GPIO0=SDO） | p.87 | `ad4080_regs.sv` | `gdir_r<=4'b0001` |
| FILTER_SYNC/EXT_EVENT 输入路由 | p.88-89 | `gpio_ctrl.sv:81-88` | 1000/1001 |

## 5. SPI / 接口

| 手册功能 | 手册依据 | RTL | 证据 |
|---|---|---|---|
| Mode 3（CPOL=1, CPHA=1） | p.34 | `spi_slave.sv` | 2FF + 边沿检测 |
| 16-bit 指令（1+15） | p.35 | `spi_slave.sv:126` | `instr_done` |
| 短指令（1+7） | p.76 | `spi_slave.sv` | `instr_len_r` |
| streaming（默认）+ 地址递增/递减 + 回绕 | p.35-36, p.75-76 | `spi_slave.sv:119` | `addr_next` |
| 写数据相 SDO 驱 0 | p.35 | `spi_slave.sv` PH_DATA | `sdo=0, oe=1` |
| CRC-8 x^8+x^2+x+1，种子 0xA5 | p.40-41 | `spi_slave.sv:104` | `crc_step` |
| CRC 互补使能 | p.81 | `ad4080_regs.sv:273` | `wdata[7:6]==01 && wdata[1:0]==10` |
| CRC 不过不提交写 | p.40 | `spi_slave.sv:246` | `we_r` 条件 |
| 读 CRC 只验首字节 | p.42 | `spi_slave.sv:242` | `!cmd_r \|\| first_byte` |
| 时钟数错误 | p.82 | `spi_slave.sv` | `clk_err_r` |
| SEND_STATUS 指令相回传 | p.81 | `spi_slave.sv` PH_INSTR | `stat_sr` |
| SW_RESET（0x81，ADDR_ASCENSION 除外） | p.75 | `ad4080_regs.sv:170` | `sw_reset_now` |
| SDO_ENABLE | p.75 | `ad4080_regs.sv` + `ad4080_top.sv:177` | `sdo_en` |
| 只读 ID 0x03/0x04/0x05/0x06 | p.77-78 | `ad4080_regs.sv` | 0x07/0x50/0x00/0x02 |
| DEVICE_STATUS 0x14 位序+复位 0x09 | p.83 | `ad4080_regs.sv:284` | `status` 拼接 |
| INTERFACE_STATUS 0x11 W1C | p.82 | `ad4080_regs.sv:279` | `crc_err_r` 等 |

## 6. LVDS / CNV

| 手册功能 | 手册依据 | RTL | 证据 |
|---|---|---|---|
| MSB first | p.45-53 | `lvds_framer.sv:76` | `shreg[19]` |
| 1/2 lane | p.84 | `lvds_framer.sv:58` | `lane2` |
| 空闲重复上一帧 | p.66 Fig.97 | `lvds_framer.sv:83` | `phase==last` 重装 |
| CNV CMOS/LVDS | p.33, p.45 | `cnv_ctrl.sv` | `lvds_en` 选择 |
| ADC_CNV_ERR（忙时 CNV） | p.83 | `cnv_ctrl.sv` | `busy` 检测 |

---

## 未实现 / 待确认（不许混进"已实现"）

| 项 | 状态 | 说明 |
|---|---|---|
| 0x0E STREAM_MODE 回环、0x0F TRANSFER_CONFIG | 未实现 | P2 |
| STRICT_REGISTER_ACCESS 部分访问报错 | 未实现 | P2 |
| streaming 多字节寄存器起始字节序 | [待确认] | 手册 Fig.58/59 是图 |
| SINC5+comp 补偿系数 | [待确认] | 手册只给响应曲线；参数 C0/C1/C2 占位 |
| LVDS DDR PHY / 差分驱动 / Manchester | 未实现 | 电气层，不属于数字 RTL |
| SPI 回读数据通道（DCS/DCLK） | 未实现 | 需另立数据 SPI |
| 仿真验证 | 未做 | 本机无 Verilator/Icarus/VCS |
