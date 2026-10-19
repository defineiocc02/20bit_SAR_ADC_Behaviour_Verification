//=============================================================================
// @file    ad4080_regs.sv
// @brief   AD4080 配置寄存器堆（按 Rev.B 逐位实现）
// @details 实现 0x00-0x0A / 0x10-0x11 / 0x14-0x17 / 0x19-0x1B / 0x1C-0x29；
//          写 0x1C 产生 FIFO arm 脉冲；写 0x29 产生 filter_sync 脉冲；
//          写 0x00 = 0x81 触发软复位（ADDR_ASCENSION 除外，手册 p.75）。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.73-94 Table 32-62
// 时钟域   : clk 单时钟（SPI 侧已由 spi_slave 过采样同步）
// 复位     : rst_n 异步低有效；另有 SW_RESET 软复位（配置寄存器，FIFO 不受影响）
// 延迟     : 写 1 clk；读组合
// 吞吐     : 1 次访问/clk
// 可综合性 : 是（纯寄存器 + 地址译码）
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版子集
//   2.0.0 2026-10-19 补齐 GENERAL_CONFIG/GPIO/LVDS/状态 W1C/watermark 钳位
//   3.0.0 2026-10-19 增加 0x00-0x0A 接口配置区、SW_RESET、FILTER_SYNC 脉冲、
//                    SDO_ENABLE/ADDR_ASCENSION/SINGLE_INST/SHORT_INSTRUCTION
//=============================================================================
// 设计说明：
//   地址与复位值全部取自 AD4080 Rev.B p.73-94：
//     0x00 INTERFACE_CONFIG_A   reset 0x10  [7] SW_RESET [5] ADDR_ASCENSION
//                                          [4] SDO_ENABLE [0] SW_RESETX
//     0x01 INTERFACE_CONFIG_B   reset 0x00  [7] SINGLE_INST [3] SHORT_INSTRUCTION
//     0x02 DEVICE_CONFIG        reset 0x00  [1:0] OPERATING_MODES
//     0x03 CHIP_TYPE            reset 0x07  只读
//     0x04 PRODUCT_ID_L         reset 0x50  只读
//     0x05 PRODUCT_ID_H         reset 0x00  只读
//     0x06 CHIP_GRADE           reset 0x02  只读
//     0x0A SCRATCH_PAD          reset 0x00  R/W 测试位
//     0x10 INTERFACE_CONFIG_C   reset 0x23  CRC_ENABLE[7:6]/STRICT[5]/SEND_STATUS[4]/CRC_ENABLEB[1:0]
//     0x11 INTERFACE_STATUS_A   reset 0x00  W1C：CRC_ERR / CLOCK_COUNT_ERR / ADDRESS_INVALID_ERR
//     0x14 DEVICE_STATUS        reset 0x09  {FIFO_FULL,FIFO_READ_DONE,HI,LO,POR_ANA,CNV_ERR,ROM_CRC,POR}
//     0x15 ADC_DATA_INTF_A      reset 0x40  [6]=1, [4] INTF_CHK_EN, [2] SPI_LVDS_LANES, [0] DATA_INTF_MODE
//     0x16 ADC_DATA_INTF_B      reset 0x00  [7:4] LVDS_CNV_CLK_CNT, [3] SELF_CLK, [2] MNC, [0] LVDS_CNV_EN
//     0x17 ADC_DATA_INTF_C      reset 0x20  [7] RX_CURRENT, [6:4] LVDS_VOD
//     0x19 GPIO_CONFIG_A        reset 0x01  [7:4] DATA, [3:0] GPO_EN
//     0x1A/0x1B GPIO_CONFIG_B/C reset 0x00  [7:4]/[3:0] = GPIO_x_SEL（4 bit）
//     0x1C GENERAL_CONFIG       reset 0x00  [7] INT_EVENT_EN, [6] HI_ROUTE,
//                                            [5] LO_ROUTE, [4] ADC_CNV_ERR_ROUTE, [1:0] FIFO_MODE
//     0x1D/0x1E FIFO_WATERMARK  reset 0x4000（15 bit，写 0 夹到 1，写 >16384 夹到 16384）
//     0x1F/0x20 EVENT_HYSTERESIS reset 0x0000
//     0x21/0x22 HI_THRESHOLD     reset 0x0000（补码）
//     0x23/0x24 LO_THRESHOLD     reset 0x0000（补码）
//     0x25/0x26 OFFSET           reset 0x0000（补码，LSB=0.00572 mV）
//     0x27/0x28 GAIN             reset 0x0200（0x200 = 1.0）
//     0x29 FILTER_CONFIG        reset 0x00  [6:3] SINC_DEC_RATE, [1:0] FILTER_SEL
//
//   行为说明：
//     - 写 0x1C 产生 fifo_arm 单拍脉冲（手册：a FIFO_MODE write command arms the FIFO）。
//     - 写 0x29 产生 fsync_pulse 单拍脉冲（手册 p.94：改 FILTER_SEL 后必须 FILTER_SYNC）。
//     - 写 0x14 的 bit5/4/3/2/0 是 W1C；hi_clr/lo_clr 送 event_detect 清粘滞位。
//     - 写 0x11 的 bit3/4/0 是 W1C，用于清 CRC/时钟数/地址错误。
//     - 写未映射地址置 ADDRESS_INVALID_ERR（0x11[0]），写入被丢弃。
//     - 未实现：0x0E STREAM_MODE 回环计数、0x0F TRANSFER_CONFIG（登记为 P2）。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module ad4080_regs (
    input  logic        clk,                                // 系统时钟
    input  logic        rst_n,                              // 异步复位，低有效
    input  logic [15:0] addr,                               // 寄存器地址，[14:0] 有效
    input  logic [7:0]  wdata,                              // 写数据
    input  logic        we,                                 // 写使能，单拍
    output logic [7:0]  rdata,                              // 读数据，组合输出

    // 状态输入（粘滞位在本模块内维护）
    input  logic        st_fifo_full,                       // FIFO 满或已收满 WATERMARK
    input  logic        st_fifo_read_done,                  // FIFO 读完
    input  logic        st_hi_flag,                         // 高阈值命中（已按 HI_ROUTE 门控）
    input  logic        st_lo_flag,                         // 低阈值命中（已按 LO_ROUTE 门控）
    input  logic        adc_cnv_err_pulse,                  // 转换错误脉冲（已按路由门控）
    input  logic        crc_err_pulse,                      // SPI CRC 错误脉冲
    input  logic        clk_cnt_err_pulse,                  // SPI 时钟数错误脉冲
    output logic        hi_clr,                             // 清 HI 粘滞位，单拍
    output logic        lo_clr,                             // 清 LO 粘滞位，单拍

    // 校正 / 滤波
    output logic signed [11:0] offset_code,                 // OFFSET 补码，1 LSB = 1 ADC LSB
    output logic [9:0]  gain_code,                          // GAIN，0x200 = 1.0
    output logic [1:0]  filter_sel,                         // 00 直通/01 SINC1/10 SINC5/11 SINC5+补偿
    output logic [3:0]  dec_sel,                            // SINC_DEC_RATE，N = 2^(code+1)
    output logic        fsync_pulse,                        // 写 0x29 产生的单拍滤波器同步

    // FIFO
    output logic [1:0]  fifo_mode,                          // 00 关闭/01 立即/10 事件最近/11 事件全部
    output logic [14:0] fifo_watermark,                     // 已钳位到 1..16384
    output logic        fifo_arm,                           // 写 0x1C 产生的单拍 arm
    input  logic        wm_wb_valid,                        // mode 11 回写事件位置
    input  logic [14:0] wm_wb_data,                         // 回写值

    // 事件检测
    output logic [10:0] event_hyst,                         // 迟滞，LSB = 1.46484 mV
    output logic [11:0] event_hi,                           // 高阈值，二进制补码
    output logic [11:0] event_lo,                           // 低阈值，二进制补码
    output logic        event_en,                           // INT_EVENT_EN
    output logic        hi_route,                           // 高检测路由到状态/告警/FIFO
    output logic        lo_route,                           // 低检测路由到状态/告警/FIFO
    output logic        cnv_err_route,                      // 转换错误路由

    // GPIO
    output logic [3:0]        gpio_dir,                     // 1 = 输出
    output logic [3:0]        gpio_reg,                     // GPIO_x_DATA
    output logic [3:0][3:0]   gpio_sel,                     // 每引脚 4-bit 功能选择

    // LVDS / 接口
    output logic        lvds_cnv_en,                        // CNV 电气模式：0 CMOS / 1 LVDS
    output logic        lane_sel,                           // 0 = 1 lane, 1 = 2 lanes
    output logic        intf_mode,                          // 0 = LVDS, 1 = SPI
    output logic        lvds_self_clk,                      // 自时钟模式（DCO 关闭）
    output logic        lvds_mnc_en,                        // Manchester 使能（本数字模型未实现）
    output logic [3:0]  lvds_cnv_clk_cnt,                   // LVDS 时钟对齐
    output logic        crc_en,                             // CRC 使能（互补位校验后）
    output logic        send_status,                        // 指令相在 SDO 回传状态

    // 接口配置
    output logic        sdo_en,                             // SDO_ENABLE（0x00[4]，复位 1）
    output logic        addr_asc,                           // ADDR_ASCENSION（0x00[5]，0=递减 1=递增）
    output logic        single_inst,                        // SINGLE_INST（0x01[7]，0=streaming）
    output logic        short_instr,                        // SHORT_INSTRUCTION（0x01[3]）
    output logic        sw_reset_pulse,                     // SW_RESET 触发脉冲（单拍）
    output logic        cnv_err_flag,                       // ADC_CNV_ERR 粘滞位（用于 ALERT 路由）

    output logic [7:0]  status                              // DEVICE_STATUS 当前值
);

    // ---------------- 寄存器本体 ----------------
    logic signed [11:0] offset_r;
    logic [9:0]  gain_r;
    logic [1:0]  fsel_r;
    logic [3:0]  dec_r;
    logic [1:0]  fmode_r;
    logic [14:0] wm_r;
    logic [10:0] hyst_r;
    logic [11:0] hi_r, lo_r;
    logic        int_event_r, hi_route_r, lo_route_r, cnv_route_r;
    logic [3:0]  gdir_r, gdata_r;
    logic [3:0][3:0] gsel_r;

    logic [7:0]  intf_a_r, intf_b_r, intf_c_r;
    logic [7:0]  intf_cfg_c_r;
    logic        crc_en_r, send_status_r;

    // 接口配置区
    logic        addr_asc_r, sdo_en_r;
    logic        single_inst_r, short_instr_r;
    logic [1:0]  op_mode_r;
    logic [7:0]  scratch_r;
    logic        sw_reset_r;

    logic        por_flag_r, por_ana_r, cnv_err_r, rom_crc_r;
    logic        crc_err_r, clk_cnt_err_r, addr_err_r;

    logic        fifo_arm_r;
    logic        hi_clr_r, lo_clr_r;
    logic        fsync_r;

    wire [14:0] wm_clamped = (wm_r == 15'd0)     ? 15'd1 :
                             (wm_r > 15'd16384)  ? 15'd16384 : wm_r;

    // SW_RESET：0x00 写 bit7=1 且 bit0=1（SW_RESET/SW_RESETX 同写）。
    // ASSUMPTION: 手册只说"两位必须同时写"，未说取值；按 ADI 同类器件惯例取 0x81。
    wire sw_reset_now = we && (addr == 16'h0000) && wdata[7] && wdata[0];

    // ---------------- 写 ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            offset_r      <= 12'sd0;
            gain_r        <= 10'h200;
            fsel_r        <= 2'b00;
            dec_r         <= 4'd0;
            fmode_r       <= 2'b00;
            wm_r          <= 15'h4000;
            hyst_r        <= 11'd0;
            hi_r          <= 12'd0;
            lo_r          <= 12'd0;
            int_event_r   <= 1'b0;
            hi_route_r    <= 1'b0;
            lo_route_r    <= 1'b0;
            cnv_route_r   <= 1'b0;
            gdir_r        <= 4'b0001;   // 0x19 reset 0x01
            gdata_r       <= 4'b0000;
            gsel_r        <= '0;
            intf_a_r      <= 8'h40;
            intf_b_r      <= 8'h00;
            intf_c_r      <= 8'h20;
            intf_cfg_c_r  <= 8'h23;
            crc_en_r      <= 1'b0;
            send_status_r <= 1'b0;
            addr_asc_r    <= 1'b0;
            sdo_en_r      <= 1'b1;      // 0x00 reset 0x10
            single_inst_r <= 1'b0;      // 0x01 reset 0（默认 streaming）
            short_instr_r <= 1'b0;
            op_mode_r     <= 2'b00;
            scratch_r     <= 8'h00;
            sw_reset_r    <= 1'b0;
            por_flag_r    <= 1'b1;
            por_ana_r     <= 1'b1;
            cnv_err_r     <= 1'b0;
            rom_crc_r     <= 1'b0;
            crc_err_r     <= 1'b0;
            clk_cnt_err_r <= 1'b0;
            addr_err_r    <= 1'b0;
            fifo_arm_r    <= 1'b0;
            hi_clr_r      <= 1'b0;
            lo_clr_r      <= 1'b0;
            fsync_r       <= 1'b0;
        end else if (sw_reset_now) begin
            // SW_RESET：除 ADDR_ASCENSION 外全部回到默认值（手册 p.75）。
            offset_r      <= 12'sd0;
            gain_r        <= 10'h200;
            fsel_r        <= 2'b00;
            dec_r         <= 4'd0;
            fmode_r       <= 2'b00;
            wm_r          <= 15'h4000;
            hyst_r        <= 11'd0;
            hi_r          <= 12'd0;
            lo_r          <= 12'd0;
            int_event_r   <= 1'b0;
            hi_route_r    <= 1'b0;
            lo_route_r    <= 1'b0;
            cnv_route_r   <= 1'b0;
            gdir_r        <= 4'b0001;
            gdata_r       <= 4'b0000;
            gsel_r        <= '0;
            intf_a_r      <= 8'h40;
            intf_b_r      <= 8'h00;
            intf_c_r      <= 8'h20;
            intf_cfg_c_r  <= 8'h23;
            crc_en_r      <= 1'b0;
            send_status_r <= 1'b0;
            sdo_en_r      <= 1'b1;
            single_inst_r <= 1'b0;
            short_instr_r <= 1'b0;
            op_mode_r     <= 2'b00;
            sw_reset_r    <= 1'b1;
            cnv_err_r     <= 1'b0;
            crc_err_r     <= 1'b0;
            clk_cnt_err_r <= 1'b0;
            addr_err_r    <= 1'b0;
            fifo_arm_r    <= 1'b0;
            hi_clr_r      <= 1'b0;
            lo_clr_r      <= 1'b0;
            fsync_r       <= 1'b0;
            // por_flag_r / por_ana_r / scratch_r / addr_asc_r 保持不变
        end else begin
            fifo_arm_r  <= 1'b0;
            hi_clr_r    <= 1'b0;
            lo_clr_r    <= 1'b0;
            fsync_r     <= 1'b0;
            sw_reset_r  <= 1'b0;

            // 状态粘滞位置位（与写操作独立）
            if (adc_cnv_err_pulse) cnv_err_r <= 1'b1;
            if (crc_err_pulse)     crc_err_r <= 1'b1;
            if (clk_cnt_err_pulse) clk_cnt_err_r <= 1'b1;

            if (wm_wb_valid) wm_r <= wm_wb_data;   // mode 11 捕获完成自动回写

            if (we) begin
                case (addr)
                    16'h0000: begin addr_asc_r <= wdata[5]; sdo_en_r <= wdata[4]; end
                    16'h0001: begin single_inst_r <= wdata[7]; short_instr_r <= wdata[3]; end
                    16'h0002: op_mode_r <= wdata[1:0];
                    16'h000A: scratch_r <= wdata;
                    16'h0010: begin
                        // CRC_ENABLE 与 CRC_ENABLEB 必须互补才生效
                        intf_cfg_c_r <= wdata;
                        crc_en_r     <= (wdata[7:6] == 2'b01) && (wdata[1:0] == 2'b10);
                        send_status_r<= wdata[4];
                    end
                    16'h0011: begin
                        if (wdata[3]) crc_err_r     <= 1'b0;
                        if (wdata[4]) clk_cnt_err_r <= 1'b0;
                        if (wdata[0]) addr_err_r    <= 1'b0;
                    end
                    16'h0014: begin
                        if (wdata[5]) hi_clr_r  <= 1'b1;
                        if (wdata[4]) lo_clr_r  <= 1'b1;
                        if (wdata[3]) por_ana_r <= 1'b0;
                        if (wdata[2]) cnv_err_r <= 1'b0;
                        if (wdata[0]) por_flag_r<= 1'b0;
                    end
                    16'h0015: intf_a_r <= wdata;
                    16'h0016: intf_b_r <= wdata;
                    16'h0017: intf_c_r <= wdata;
                    16'h0019: begin gdir_r <= wdata[3:0]; gdata_r <= wdata[7:4]; end
                    16'h001A: begin gsel_r[0] <= wdata[3:0]; gsel_r[1] <= wdata[7:4]; end
                    16'h001B: begin gsel_r[2] <= wdata[3:0]; gsel_r[3] <= wdata[7:4]; end
                    16'h001C: begin
                        int_event_r <= wdata[7];
                        hi_route_r  <= wdata[6];
                        lo_route_r  <= wdata[5];
                        cnv_route_r <= wdata[4];
                        fmode_r     <= wdata[1:0];
                        fifo_arm_r  <= 1'b1;
                    end
                    16'h001D: wm_r[7:0]    <= wdata;
                    16'h001E: wm_r[14:8]   <= wdata[6:0];
                    16'h001F: hyst_r[7:0]  <= wdata;
                    16'h0020: hyst_r[10:8] <= wdata[2:0];
                    16'h0021: hi_r[7:0]    <= wdata;
                    16'h0022: hi_r[11:8]   <= wdata[3:0];
                    16'h0023: lo_r[7:0]    <= wdata;
                    16'h0024: lo_r[11:8]   <= wdata[3:0];
                    16'h0025: offset_r[7:0]  <= wdata;
                    16'h0026: offset_r[11:8] <= wdata[3:0];
                    16'h0027: gain_r[7:0]    <= wdata;
                    16'h0028: gain_r[9:8]    <= wdata[1:0];
                    16'h0029: begin fsel_r <= wdata[1:0]; dec_r <= wdata[6:3]; fsync_r <= 1'b1; end
                    default: begin
                        // 未映射地址：记 ADDRESS_INVALID_ERR（0x11[0]），写入丢弃
                        addr_err_r <= 1'b1;
                    end
                endcase
            end
        end
    end

    // ---------------- 读 ----------------
    always_comb begin
        case (addr)
            16'h0000: rdata = {2'b00, addr_asc_r, sdo_en_r, 4'b0000};
            16'h0001: rdata = {single_inst_r, 3'b000, short_instr_r, 3'b000};
            16'h0002: rdata = {6'b0, op_mode_r};
            16'h0003: rdata = 8'h07;                 // CHIP_TYPE
            16'h0004: rdata = 8'h50;                 // PRODUCT_ID_L
            16'h0005: rdata = 8'h00;                 // PRODUCT_ID_H
            16'h0006: rdata = 8'h02;                 // CHIP_GRADE
            16'h000A: rdata = scratch_r;             // SCRATCH_PAD
            16'h0010: rdata = intf_cfg_c_r;
            16'h0011: rdata = {3'b0, clk_cnt_err_r, crc_err_r, 2'b0, addr_err_r};
            16'h0014: rdata = status;
            16'h0015: rdata = intf_a_r;
            16'h0016: rdata = intf_b_r;
            16'h0017: rdata = intf_c_r;
            16'h0019: rdata = {gdata_r, gdir_r};
            16'h001A: rdata = {gsel_r[1], gsel_r[0]};
            16'h001B: rdata = {gsel_r[3], gsel_r[2]};
            16'h001C: rdata = {int_event_r, hi_route_r, lo_route_r, cnv_route_r,
                               2'b00, fmode_r};
            16'h001D: rdata = wm_r[7:0];
            16'h001E: rdata = {1'b0, wm_r[14:8]};
            16'h001F: rdata = hyst_r[7:0];
            16'h0020: rdata = {5'b0, hyst_r[10:8]};
            16'h0021: rdata = hi_r[7:0];
            16'h0022: rdata = {4'b0, hi_r[11:8]};
            16'h0023: rdata = lo_r[7:0];
            16'h0024: rdata = {4'b0, lo_r[11:8]};
            16'h0025: rdata = offset_r[7:0];
            16'h0026: rdata = {4'b0, offset_r[11:8]};
            16'h0027: rdata = gain_r[7:0];
            16'h0028: rdata = {6'b0, gain_r[9:8]};
            16'h0029: rdata = {1'b0, dec_r, 1'b0, fsel_r};
            default:  rdata = 8'h00;
        endcase
    end

    assign status = {st_fifo_full, st_fifo_read_done, st_hi_flag, st_lo_flag,
                     por_ana_r, cnv_err_r, rom_crc_r, por_flag_r};

    // ---------------- 输出 ----------------
    assign offset_code    = offset_r;
    assign gain_code      = gain_r;
    assign filter_sel     = fsel_r;
    assign dec_sel        = dec_r;
    assign fsync_pulse    = fsync_r;
    assign fifo_mode      = fmode_r;
    assign fifo_watermark = wm_clamped;
    assign fifo_arm       = fifo_arm_r;
    assign event_hyst     = hyst_r;
    assign event_hi       = hi_r;
    assign event_lo       = lo_r;
    assign event_en       = int_event_r;
    assign hi_route       = hi_route_r;
    assign lo_route       = lo_route_r;
    assign cnv_err_route  = cnv_route_r;
    assign gpio_dir       = gdir_r;
    assign gpio_reg       = gdata_r;
    assign gpio_sel       = gsel_r;
    assign lvds_cnv_en    = intf_b_r[0];
    assign lane_sel       = intf_a_r[2];
    assign intf_mode      = intf_a_r[0];
    assign lvds_self_clk  = intf_b_r[3];
    assign lvds_mnc_en    = intf_b_r[2];
    assign lvds_cnv_clk_cnt = intf_b_r[7:4];
    assign crc_en         = crc_en_r;
    assign send_status    = send_status_r;
    assign sdo_en         = sdo_en_r;
    assign addr_asc       = addr_asc_r;
    assign single_inst    = single_inst_r;
    assign short_instr    = short_instr_r;
    assign sw_reset_pulse = sw_reset_r;
    assign cnv_err_flag   = cnv_err_r;
    assign hi_clr         = hi_clr_r;
    assign lo_clr         = lo_clr_r;

endmodule

`default_nettype wire
