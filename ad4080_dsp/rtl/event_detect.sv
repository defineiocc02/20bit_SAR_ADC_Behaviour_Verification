//=============================================================================
// @file    event_detect.sv
// @brief   HI/LO 阈值与迟滞事件检测（补码；level + sticky 双通道）
// @details 阈值 12-bit 补码，LSB=1.46484 mV=256 ADC LSB，直接对齐 data[19:8]。
//          迟滞电平通道 HI_DTCT/LO_DTCT：越过阈值置位，回到迟滞带内清除，
//          用于 GPIO(0100/0101)、ALERT(0110，低有效) 与 FIFO 事件触发。
//          状态通道 HI_STATUS/LO_STATUS：dtct 置位沿锁存，仅 W1C/复位清除。
//          ALERT 是电平型（dtct 消失即回高），不是粘滞型。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.54-56（Figure 82/84, Table 26）、
//            p.90-92（Table 55-59）、p.83（DEVICE_STATUS W1C）
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效
// 延迟     : sample_valid -> dtct 1 clk；-> 状态/ALERT 再延迟 2 个样本（手册 p.55）
// 吞吐     : 1 sample/clk
// 可综合性 : 是（两个 13-bit 比较器 + 少量触发器）
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版（无符号比较，错误）
//   2.0.0 2026-10-19 改为补码比较；增加 hi/lo 脉冲输出
//   3.0.0 2026-10-19 拆分 level/sticky 双通道；ALERT 改电平型低有效语义；
//                    状态/ALERT 增加 2 样本延迟
//-----------------------------------------------------------------------------
// 设计说明：
//   数据手册依据：AD4080 Rev.B p.54-56, p.90-92
//     - HI_THRESHOLD / LO_THRESHOLD：二进制补码，LSB = 1.46484 mV
//     - HYSTERESIS：无符号，LSB = 1.46484 mV
//     - HI_DTCT/LO_DTCT 是事件检测的**电平**输出（迟滞防抖）
//     - HI_STATUS/LO_STATUS 是 DEVICE_STATUS 里的粘滞位，写 1 清除
//   关键换算（这是旧版本出错的地方）：
//     p.20 Table 7 明确：20-bit 输出是二进制补码（0x00000 = 0 V）。
//     1 个阈值 LSB = 1.46484 mV = 256 个 20-bit ADC LSB = 2^8。
//     因此阈值正好对齐 data[19:8]，该切片本身就是 12-bit 补码，
//     直接 $signed(data_in[19:8])，不需要 offset-binary 的异或。
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

    // 置位/清除条件（同拍同时满足时以置位优先，避免毛刺漏事件）
    assign hi_set     = (d12 >= hi_th_s);
    assign lo_set     = (d12 <= lo_th_s);
    assign hi_clr_evt = (d12 <  hi_clr_th);
    assign lo_clr_evt = (d12 >  lo_clr_th);

    // 2 样本延迟链：手册 p.55 事件到状态位/ALERT 有 2 个 CNV 延迟
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

                // 状态/ALERT 的 2 样本延迟
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
