//=============================================================================
// @file    event_detect.sv
// @brief   补码阈值、迟滞电平、粘滞事件及延迟ALERT
// @details 20-bit补码data[19:8]对应12-bit阈值，hysteresis为无符号阈值步长。
//          dtct按迟滞保持；sticky flag在新置位沿锁存，只有W1C/复位清除。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : clk；sample_valid及配置必须同步
// 复位     : rst_n异步低有效；disable清电平和ALERT流水，保留sticky
// 延迟     : 接受样本沿更新dtct/flag；ALERT使用dtct的两级样本延迟，flag不走该流水
// 吞吐     : 最多1样本/clk；ALERT延迟按有效样本推进
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : 无
// 边界     : 阈值步长为256个20-bit ADC LSB；电压换算依赖实际满量程。
//          clear_hi/lo与新事件同沿时清除优先。ALERT为电平型，未产生新有效样本时保持。
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

    // 迟滞电平判据；下面W1C清sticky的优先级高于同沿新事件。
    assign hi_set     = (d12 >= hi_th_s);
    assign lo_set     = (d12 <= lo_th_s);
    assign hi_clr_evt = (d12 <  hi_clr_th);
    assign lo_clr_evt = (d12 >  lo_clr_th);

    // ALERT延迟链按sample_valid推进；不用于sticky flag。
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
                // 电平通道：迟滞防抖
                if (hi_set)      hi_dtct <= 1'b1;
                else if (hi_clr_evt) hi_dtct <= 1'b0;
                if (lo_set)      lo_dtct <= 1'b1;
                else if (lo_clr_evt) lo_dtct <= 1'b0;

                // 置位沿：进 FIFO 触发与粘滞位
                if (hi_set && !hi_dtct) begin
                    hi_pulse <= 1'b1;
                    hi_flag  <= 1'b1;
                end
                if (lo_set && !lo_dtct) begin
                    lo_pulse <= 1'b1;
                    lo_flag  <= 1'b1;
                end

                // ALERT的2级样本延迟；sticky flag由置位沿直接更新。
                hi_d1 <= hi_dtct;
                hi_d2 <= hi_d1;
                lo_d1 <= lo_dtct;
                lo_d2 <= lo_d1;
            end

            // W1C 只清粘滞位，不动电平通道
            if (clear_hi) hi_flag <= 1'b0;
            if (clear_lo) lo_flag <= 1'b0;
        end
    end

    // ALERT：低有效、电平型，阈值不再越界即回高
    assign alert_n = ~(hi_d2 | lo_d2);

endmodule

`default_nettype wire
