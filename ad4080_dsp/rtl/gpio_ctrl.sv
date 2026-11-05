//=============================================================================
// @file    gpio_ctrl.sv
// @brief   四路 GPIO 的方向与功能组合路由。
// @details gpio_sel 选择 SDO、FIFO、滤波 ready、阈值、ALERT、数据或输入功能。
//          输入功能强制关闭输出使能；filter_sync 将低电平输入转换为复位电平，不检测边沿。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: 纯组合逻辑；gpio_i 须先由顶层同步。
// 复位: 无状态，无复位。
// 延迟: 组合传播。
// 吞吐: 组合逻辑，不单独保证时钟吞吐。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: 不包含焊盘、电气驱动或 ESD 模型。FILTER_SYNC 输入持续低时持续使能复位；
//     外部事件输入为电平，不在模块内整形成脉冲。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module gpio_ctrl (
    input  logic [3:0]       gpio_i,                      // GPIO 引脚输入（已同步）
    input  logic [3:0]       gpio_dir,     // 为 1 时配置为输出。
    input  logic [3:0]       gpio_reg,     // GPIO_x_DATA
    input  logic [3:0][3:0]  gpio_sel,                    // 每引脚 4-bit SEL
    // 以下内部信号按功能选择码路由。
    input  logic             spi_sdo,                     // SEL 0000
    input  logic             fifo_full,                   // SEL 0001
    input  logic             fifo_read_done,              // SEL 0010
    input  logic             filt_rdy,     // 高有效，路由时取反
    input  logic             hi_dtct,                     // SEL 0100（电平，迟滞）
    input  logic             lo_dtct,                     // SEL 0101（电平，迟滞）
    input  logic             alert_n,                     // SEL 0110（已是低有效电平）
    output logic [3:0]       gpio_o,                      // GPIO 输出值
    output logic [3:0]       gpio_oe,                     // GPIO 输出使能
    output logic             filter_sync,                 // SEL 1000 路由出的同步复位电平。
    output logic             ext_event                    // SEL 1001 路由出的外部事件
);

    logic fsync_i, ext_evt_i;

    always_comb begin
        gpio_o     = 4'b0;
        gpio_oe    = 4'b0;
        fsync_i    = 1'b0;
        ext_evt_i  = 1'b0;

        for (int i = 0; i < 4; i++) begin
            // 1000/1001 输入功能不受 gpio_dir 影响，始终关闭输出使能。
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
