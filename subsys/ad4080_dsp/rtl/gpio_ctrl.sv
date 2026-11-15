//=============================================================================
// @file    gpio_ctrl.sv
// @brief   GPIO 方向与已支持功能的组合路由。
// @details 仅当 spi_sdo_oe 有效时，SDO 才占用所选引脚；输入功能关闭输出使能。
//          FILTER_SYNC 是复位电平，不能按边沿事件解释。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.52–56/87–90；缺少时序绑定时 FILTER_RDY 不可用。
// 时钟域: 组合逻辑；gpio_i 必须已由顶层同步。
// 复位: 无状态，无复位。
// 延迟: 组合传播。
// 吞吐: 组合路由，不单独定义时钟吞吐。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 不含 pad/ESD/电气模型；不可用的 FILTER_RDY 选择释放 OE，并由寄存器堆拒绝。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module gpio_ctrl (
    input  logic [3:0]       gpio_i,                      // GPIO 引脚输入（已同步）
    input  logic [3:0]       gpio_dir,     // 为 1 时配置为输出。
    input  logic [3:0]       gpio_reg,     // GPIO_x_DATA
    input  logic [3:0][3:0]  gpio_sel,                    // 每引脚 4-bit SEL
    // 以下信号由 GPIO 功能选择码路由。
    input  logic             spi_sdo,                     // SEL 0000
    input  logic             spi_sdo_oe,                  // 仅在有效时允许 SPI 驱动所选引脚。
    input  logic             fifo_full,                   // SEL 0001
    input  logic             fifo_read_done,              // SEL 0010
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
            // 输入功能优先，1000/1001 不受 gpio_dir 影响，始终关闭输出使能。
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
                        4'b0000: begin
                            gpio_o[i]  = spi_sdo;
                            gpio_oe[i] = spi_sdo_oe;
                        end
                        4'b0001: gpio_o[i] = fifo_full;
                        4'b0010: gpio_o[i] = fifo_read_done;
                        // 器件 FILTER_RDY 的时序未绑定，选择此功能时释放输出使能。
                        4'b0011: begin
                            gpio_o[i] = 1'b0;
                            gpio_oe[i] = 1'b0;
                        end
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
