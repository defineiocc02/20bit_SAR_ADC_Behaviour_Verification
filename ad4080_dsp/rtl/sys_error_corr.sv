//===========================================================================
// sys_error_corr.sv -- AD4080 system error correction: y = m*x + c
//===========================================================================
// 数据手册依据：AD4080 Rev.B p.71 "SYSTEM ERROR CORRECTION COEFFICIENTS"
//   y = m*x + c
//   OFFSET[11:0] : 12-bit two's complement, 1 LSB = ADC LSB,
//                  range -2048..+2047 LSB (0x800..0x7FF), default 0x000
//   GAIN[9:0]    : 10-bit straight binary, default 0x200 (=512),
//                  nominal range +/-1.5594% FS,
//                  resolution 1.5594%/512 = 0.00305% per LSB
//   => m = 1 + (GAIN - 512) * (0.015594 / 512)
//         = 1 + (GAIN - 512) * 3.045703125e-5
//
// 定点实现：
//   delta = x * (GAIN-512) * K_GAIN >>> SHIFT
//   K_GAIN = round(3.045703125e-5 * 2^24) = 511
//   SHIFT  = 24
//   y = x + delta + OFFSET
//   饱和到 [0, 2^IN_W-1]，clip_low/clip_high 逐拍输出。
//
// 流水：2 级。valid_in -> valid_out 延迟 2 个时钟。
// 适用域：x 为 20-bit 无符号 ADC 结果；OFFSET/GAIN 为已经算好的寄存器值。
//          本模块**不**做两点测量/系数估计（那是 cal_two_point.sv 的职责）。
//===========================================================================
`timescale 1ns/1ps

module sys_error_corr #(
    parameter int IN_W      = 20,
    parameter int OFFSET_W  = 12,
    parameter int GAIN_W    = 10,
    parameter int K_GAIN    = 511,
    parameter int SHIFT     = 24
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     valid_in,
    input  logic [IN_W-1:0]          x,
    input  logic signed [OFFSET_W-1:0] offset_code,
    input  logic [GAIN_W-1:0]        gain_code,
    output logic [IN_W-1:0]          y,
    output logic                     valid_out,
    output logic                     clip_low,
    output logic                     clip_high
);

    localparam int PROD_W  = IN_W + GAIN_W + 1;       // 20 + 11 = 31
    localparam int PRODK_W = PROD_W + 9;              // 31 + 9  = 40 (K=511)
    localparam int DELTA_W = PRODK_W - SHIFT;         // 40 - 24 = 16
    localparam int SUM_W   = IN_W + 4;                // 24
    localparam logic signed [IN_W:0] MAX_CODE = (1 << IN_W) - 1;
    localparam logic signed [8:0]    K_GAIN_S = K_GAIN;

    // Stage 1
    logic [IN_W-1:0]            x_s1;
    logic signed [OFFSET_W-1:0] off_s1;
    logic signed [GAIN_W:0]     gd_s1;
    logic signed [PROD_W-1:0]   prod_s1;
    logic                       v_s1;

    // Stage 2
    logic signed [PRODK_W-1:0]  prodk_s2;
    logic signed [DELTA_W-1:0]  delta_s2;
    logic signed [SUM_W-1:0]    sum_s2;
    logic                       v_s2;
    logic                       lo_s2, hi_s2;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x_s1   <= '0;
            off_s1 <= '0;
            gd_s1  <= '0;
            prod_s1<= '0;
            v_s1   <= 1'b0;
        end else if (valid_in) begin
            x_s1   <= x;
            off_s1 <= offset_code;
            gd_s1  <= $signed({1'b0, gain_code}) - 11'sd512;
            prod_s1<= $signed({1'b0, x}) * ($signed({1'b0, gain_code}) - 11'sd512);
            v_s1   <= 1'b1;
        end else begin
            v_s1   <= 1'b0;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prodk_s2 <= '0;
            delta_s2 <= '0;
            sum_s2   <= '0;
            v_s2     <= 1'b0;
            lo_s2    <= 1'b0;
            hi_s2    <= 1'b0;
            y        <= '0;
            valid_out<= 1'b0;
            clip_low <= 1'b0;
            clip_high<= 1'b0;
        end else begin
            v_s2 <= v_s1;
            if (v_s1) begin
                prodk_s2 <= prod_s1 * K_GAIN_S;
                delta_s2 <= (prod_s1 * K_GAIN_S + (1 << (SHIFT-1))) >>> SHIFT;
                sum_s2   <= $signed({1'b0, x_s1}) + ((prod_s1 * K_GAIN_S + (1 << (SHIFT-1))) >>> SHIFT)
                            + $signed(off_s1);
                lo_s2    <= ($signed({1'b0, x_s1}) + ((prod_s1 * K_GAIN_S + (1 << (SHIFT-1))) >>> SHIFT)
                            + $signed(off_s1)) < 0;
                hi_s2    <= ($signed({1'b0, x_s1}) + ((prod_s1 * K_GAIN_S + (1 << (SHIFT-1))) >>> SHIFT)
                            + $signed(off_s1)) > MAX_CODE;
            end
            if (v_s2) begin
                if (lo_s2) begin
                    y <= '0;
                    clip_low <= 1'b1;
                    clip_high<= 1'b0;
                end else if (hi_s2) begin
                    y <= {IN_W{1'b1}};
                    clip_low <= 1'b0;
                    clip_high<= 1'b1;
                end else begin
                    y <= sum_s2[IN_W-1:0];
                    clip_low <= 1'b0;
                    clip_high<= 1'b0;
                end
                valid_out <= 1'b1;
            end else begin
                valid_out <= 1'b0;
                clip_low  <= 1'b0;
                clip_high <= 1'b0;
            end
        end
    end

endmodule
