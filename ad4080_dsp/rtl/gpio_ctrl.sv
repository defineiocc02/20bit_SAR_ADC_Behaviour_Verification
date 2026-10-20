//=============================================================================
// @file    gpio_ctrl.sv
// @brief   四路 GPIO 方向与功能路由（手册 SEL 编码）
// @details SEL 为 4 bit：0000 SDO / 0001 FIFO_FULL / 0010 READ_DONE /
//          0011 FILTER_RDY(低) / 0100 HI / 0101 LO / 0110 ALERT(低) /
//          0111 GPIO_DATA / 1000 FILTER_SYNC(低) / 1001 EXT_EVENT。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.87-89 Table 52-54
// 时钟域   : gpio_i 已在顶层 2FF 同步；本模块为纯组合
// 复位     : 无状态（纯组合）
// 延迟     : 组合
// 吞吐     : 组合
// 可综合性 : 是
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版（3-bit SEL，编码错误）
//   2.0.0 2026-10-19 改为 4-bit SEL 并补全 1000/1001 输入功能
//   3.0.0 2026-10-19 0100/0101 接电平型 dtct；ALERT 用现成低有效电平
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.87-89（Table 52/53/54）
//     GPIO_CONFIG_A (0x19)：[7:4] GPIO_x_DATA，[3:0] GPO_x_EN（1=输出）
//     GPIO_CONFIG_B (0x1A)：[7:4] GPIO_1_SEL，[3:0] GPIO_0_SEL
//     GPIO_CONFIG_C (0x1B)：[7:4] GPIO_3_SEL，[3:0] GPIO_2_SEL
//     SEL 编码（4 bit，旧版本用 3 bit 是错的）：
//       0000 Configuration SPI SDO Data
//       0001 FIFO Full Flag
//       0010 FIFO Read Done Flag
//       0011 Filter Result Ready (Active Low)
//       0100 High Threshold Detect
//       0101 Low Threshold Detect
//       0110 Status Alert (Active Low)
//       0111 GPIO Data
//       1000 Filter Synchronization Input (Active Low)
//       1001 External Event Trigger Input for FIFO
//       1010 Do not use
//
//   说明：
//     - 1000/1001 是**输入**功能，无论 GPO_x_EN 写什么，该引脚都强制为输入。
//     - 复位值：0x19 = 0x01 -> GPIO_0 为输出，SEL=0000（SDO），与手册
//       "power-up 后 SDO 默认分配到 GPIO0" 一致。
//     - 本模块只做数字路由，不含焊盘/ESD/驱动强度。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module gpio_ctrl (
    input  logic [3:0]       gpio_i,                      // GPIO 引脚输入（已同步）
    input  logic [3:0]       gpio_dir,     // 1 = output
    input  logic [3:0]       gpio_reg,     // GPIO_x_DATA
    input  logic [3:0][3:0]  gpio_sel,                    // 每引脚 4-bit SEL
    // 可路由的内部信号
    input  logic             spi_sdo,                     // SEL 0000
    input  logic             fifo_full,                   // SEL 0001
    input  logic             fifo_read_done,              // SEL 0010
    input  logic             filt_rdy,     // 高有效，路由时取反
    input  logic             hi_dtct,                     // SEL 0100（电平，迟滞）
    input  logic             lo_dtct,                     // SEL 0101（电平，迟滞）
    input  logic             alert_n,                     // SEL 0110（已是低有效电平）
    output logic [3:0]       gpio_o,                      // GPIO 输出值
    output logic [3:0]       gpio_oe,                     // GPIO 输出使能
    output logic             filter_sync,                 // SEL 1000 路由出的同步脉冲
    output logic             ext_event                    // SEL 1001 路由出的外部事件
);

    logic fsync_i, ext_evt_i;

    always_comb begin
        gpio_o     = 4'b0;
        gpio_oe    = 4'b0;
        fsync_i    = 1'b0;
        ext_evt_i  = 1'b0;

        for (int i = 0; i < 4; i++) begin
            // 输入功能优先：1000/1001 永远按输入脚处理
            if (gpio_sel[i] == 4'b1000) begin
                gpio_oe[i] = 1'b0;
                gpio_o[i]  = 1'b0;
                fsync_i    = fsync_i | ~gpio_i[i];   // 低有效
            end else if (gpio_sel[i] == 4'b1001) begin
                gpio_oe[i] = 1'b0;
                gpio_o[i]  = 1'b0;
                ext_evt_i  = ext_evt_i | gpio_i[i];
            end else if (gpio_sel[i] == 4'b1010) begin
                gpio_oe[i] = 1'b0;
                gpio_o[i]  = 1'b0;
            end else begin
                gpio_oe[i] = gpio_dir[i];
                if (gpio_dir[i]) begin
                    case (gpio_sel[i])
                        4'b0000: gpio_o[i] = spi_sdo;
                        4'b0001: gpio_o[i] = fifo_full;
                        4'b0010: gpio_o[i] = fifo_read_done;
                        4'b0011: gpio_o[i] = ~filt_rdy;      // 低有效
                        4'b0100: gpio_o[i] = hi_dtct;      // 电平型检测
                        4'b0101: gpio_o[i] = lo_dtct;      // 电平型检测
                        4'b0110: gpio_o[i] = alert_n;       // 已是低有效
                        4'b0111: gpio_o[i] = gpio_reg[i];
                        default: gpio_o[i] = 1'b0;
                    endcase
                end else begin
                    gpio_o[i] = 1'b0;
                end
            end
        end
    end

    assign filter_sync = fsync_i;
    assign ext_event   = ext_evt_i;

endmodule

`default_nettype wire
