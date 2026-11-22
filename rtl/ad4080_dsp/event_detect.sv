//=============================================================================
// @file    event_detect.sv
// @brief   有符号阈值、迟滞、粘滞事件与逻辑 ALERT 路由。
// @details 阈值使用样本高 12 bit 的有符号码，每个阈值码对应 256 个 ADC 码。
//          HI/LO 路由控制 ALERT；W1C 清除优先于同一时钟沿的新事件。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.54–56/90–92；不建模两个 CNV 周期的物理延迟。
// 时钟域: clk；sample_valid 和配置均同步。
// 复位: rst_n 低有效、异步置位；禁用时清检测电平，保留粘滞标志。
// 延迟: 接受结果的时钟沿更新检测电平和粘滞标志；ALERT 为组合路由。
// 吞吐: 每个 clk 最多处理一个样本。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 只检测处理后的逻辑结果；不将滤波 valid 延迟视为物理 CNV 时序。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module event_detect (
    input  logic        clk,                                // 系统时钟
    input  logic        rst_n,                              // 异步复位，低有效
    input  logic        enable,                             // INT_EVENT_EN
    input  logic        hi_route,                           // 高阈值检测参与 ALERT 路由。
    input  logic        lo_route,                           // 低阈值检测参与 ALERT 路由。
    input  logic        clear_hi,                           // W1C：写 1 清 HI_STATUS
    input  logic        clear_lo,                           // W1C：写 1 清 LO_STATUS
    input  logic        sample_valid,                       // 样本有效
    input  logic [19:0] data_in,                            // 20-bit 补码结果
    input  logic [11:0] hi_thresh,                          // 高阈值，补码
    input  logic [11:0] lo_thresh,                          // 低阈值，补码
    input  logic [10:0] hysteresis,                         // 迟滞，无符号
    output logic        hi_dtct,                            // 高检测电平（迟滞）
    output logic        lo_dtct,                            // 低检测电平（迟滞）
    output logic        hi_flag,                            // 高阈值粘滞状态位
    output logic        lo_flag,                            // 低阈值粘滞状态位
    output logic        hi_pulse,                           // 高检测置位沿
    output logic        lo_pulse,                           // 低检测置位沿
    output logic        alert_n                             // ALERT，低有效，电平型
);

    // 一个阈值码对应 256 个 20 bit 补码 ADC 码；低 8 bit 不参与阈值比较。
    wire [7:0] unused_threshold_subcode = data_in[7:0];
    logic signed [12:0] d12;
    logic signed [12:0] hi_th_s, lo_th_s, hyst_s;
    logic signed [12:0] hi_clr_th, lo_clr_th;
    logic               hi_set, lo_set, hi_clr_evt, lo_clr_evt;

    assign d12       = $signed({data_in[19], data_in[19:8]});   // 12-bit 补码 -> 13 bit
    assign hi_th_s   = $signed({hi_thresh[11], hi_thresh});
    assign lo_th_s   = $signed({lo_thresh[11], lo_thresh});
    assign hyst_s    = $signed({2'b00, hysteresis});
    assign hi_clr_th = hi_th_s - hyst_s;
    assign lo_clr_th = lo_th_s + hyst_s;

    // 迟滞分别约束置位和撤销阈值；下方 W1C 对粘滞标志的清除优先于同沿新事件。
    assign hi_set     = (d12 >= hi_th_s);
    assign lo_set     = (d12 <= lo_th_s);
    assign hi_clr_evt = (d12 <  hi_clr_th);
    assign lo_clr_evt = (d12 >  lo_clr_th);


    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hi_dtct  <= 1'b0;
            lo_dtct  <= 1'b0;
            hi_flag  <= 1'b0;
            lo_flag  <= 1'b0;
            hi_pulse <= 1'b0;
            lo_pulse <= 1'b0;
        end else begin
            hi_pulse <= 1'b0;
            lo_pulse <= 1'b0;

            if (!enable) begin
                hi_dtct <= 1'b0;
                lo_dtct <= 1'b0;
            end else if (sample_valid) begin
                // 检测电平通过不同的置位/撤销阈值保持迟滞。
                if (hi_set)      hi_dtct <= 1'b1;
                else if (hi_clr_evt) hi_dtct <= 1'b0;
                if (lo_set)      lo_dtct <= 1'b1;
                else if (lo_clr_evt) lo_dtct <= 1'b0;

                // 仅在检测电平的置位沿产生 FIFO 触发脉冲并置粘滞标志。
                if (hi_set && !hi_dtct) begin
                    hi_pulse <= 1'b1;
                    hi_flag  <= 1'b1;
                end
                if (lo_set && !lo_dtct) begin
                    lo_pulse <= 1'b1;
                    lo_flag  <= 1'b1;
                end

            end

            // W1C 只清粘滞标志，不改变检测电平。
            if (clear_hi) hi_flag <= 1'b0;
            if (clear_lo) lo_flag <= 1'b0;
        end
    end

    // ALERT: 处理后样本的低有效路由电平，不代表物理 CNV 延迟。
    assign alert_n = ~((hi_dtct & hi_route) | (lo_dtct & lo_route));

endmodule

`default_nettype wire
