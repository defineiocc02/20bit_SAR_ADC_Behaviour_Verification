//=============================================================================
// @file    event_detect.sv
// @brief   补码阈值、迟滞电平、粘滞事件与延迟 ALERT。
// @details 20 bit 补码 data[19:8] 对应 12 bit 阈值，hysteresis 为无符号阈值步长。
//          检测电平按迟滞保持，粘滞标志在新置位沿锁存，仅由 W1C 或复位清除。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；sample_valid 与配置须同步。
// 复位: rst_n 异步低有效；disable 清检测电平和 ALERT 流水，保留粘滞标志。
// 延迟: 接受样本沿更新检测电平/标志；ALERT 使用检测电平的两级样本延迟。
// 吞吐: 每个 clk 最多一个样本；ALERT 延迟按有效样本推进，标志不走该流水。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: 阈值步长为 256 个 20 bit ADC LSB；电压换算依赖实际满量程。
//     clear_hi/lo 优先于同沿新事件；ALERT 是电平信号，无新有效样本时保持。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module event_detect (
    input  logic        clk,                                // 系统时钟
    input  logic        rst_n,                              // 异步复位，低有效
    input  logic        enable,                             // INT_EVENT_EN
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

    // 迟滞分别约束置位和撤销阈值；下方 W1C 清除粘滞标志的优先级高于同沿新事件。
    assign hi_set     = (d12 >= hi_th_s);
    assign lo_set     = (d12 <= lo_th_s);
    assign hi_clr_evt = (d12 <  hi_clr_th);
    assign lo_clr_evt = (d12 >  lo_clr_th);

    // ALERT 延迟链按 sample_valid 推进，不延迟粘滞标志。
    logic hi_d1, hi_d2, lo_d1, lo_d2;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hi_dtct  <= 1'b0;
            lo_dtct  <= 1'b0;
            hi_d1    <= 1'b0;
            hi_d2    <= 1'b0;
            lo_d1    <= 1'b0;
            lo_d2    <= 1'b0;
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
                hi_d1 <= 1'b0; hi_d2 <= 1'b0;
                lo_d1 <= 1'b0; lo_d2 <= 1'b0;
            end else if (sample_valid) begin
                // 检测电平在置位与撤销之间保持迟滞状态。
                if (hi_set)      hi_dtct <= 1'b1;
                else if (hi_clr_evt) hi_dtct <= 1'b0;
                if (lo_set)      lo_dtct <= 1'b1;
                else if (lo_clr_evt) lo_dtct <= 1'b0;

                // 仅在检测电平的置位沿产生 FIFO 触发脉冲，并置粘滞标志。
                if (hi_set && !hi_dtct) begin
                    hi_pulse <= 1'b1;
                    hi_flag  <= 1'b1;
                end
                if (lo_set && !lo_dtct) begin
                    lo_pulse <= 1'b1;
                    lo_flag  <= 1'b1;
                end

                // ALERT 经过两级样本延迟，粘滞标志由置位沿直接更新。
                hi_d1 <= hi_dtct;
                hi_d2 <= hi_d1;
                lo_d1 <= lo_dtct;
                lo_d2 <= lo_d1;
            end

            // W1C 只清粘滞标志，不改变检测电平。
            if (clear_hi) hi_flag <= 1'b0;
            if (clear_lo) lo_flag <= 1'b0;
        end
    end

    // ALERT 为低有效电平；检测撤销后须按有效样本推进两级延迟，之后才回高。
    assign alert_n = ~(hi_d2 | lo_d2);

endmodule

`default_nettype wire
