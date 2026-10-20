//=============================================================================
// @file    sys_error_corr.sv
// @brief   系统误差校正 y = m·x + c（补码）
// @details OFFSET 12-bit 补码（1 LSB=1 ADC LSB），GAIN 10-bit（0x200=1.0，
//          两端 ±1.5594%）；delta=x·(GAIN-512)·512>>>24；输出饱和并报 clip。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 2.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.20 Table 7, p.71, p.93 Table 60/61
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效
// 延迟     : 2 clk
// 吞吐     : 1 sample/clk
// 可综合性 : 是；20x11 乘法 + 20x10 乘法，建议单拍内完成或用 DSP
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版（无符号，K=511）
//   2.0.0 2026-10-19 改补码；K=512；合并重复表达式
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B
//     p.20 Table 7：ADC 输出是 **20-bit 二进制补码**
//         0x00000 = 中点(0 V)，0x7FFFF = +FS-1LSB，0x80000 = -FS
//     p.71 / p.93 Table 60/61：
//         OFFSET[11:0] 补码，1 LSB = 0.00572 mV = 1 个 ADC LSB，范围 -2048..+2047
//         GAIN[9:0]    直二进制，0x200 = 1.0
//         0x3FF -> +1.5594% FS，0x001 -> -1.5594% FS
//     => m = 1 + (GAIN - 512) * (0.015594 / 511)
//
//   旧版本的两个错误（本次修正）：
//     1. 把 x 当无符号：负半轴（0x80000..0xFFFFF）会被当成接近满幅的正数，
//        校正结果整段错乱。
//     2. K_GAIN 取 511：斜率少 0.2%，满偏处约 16 LSB。正确值 512
//        （因为 2^24 × 0.015594/511 = 511.996）。
//
//   定点实现：
//     delta = (x * (GAIN-512) * 512 + 2^23) >>> 24
//     y     = clamp(x + delta + OFFSET, -2^19, 2^19-1)
//     流水 2 级：valid_in -> valid_out 延迟 2 个时钟。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module sys_error_corr #(
    parameter int IN_W      = 20,
    parameter int OFFSET_W  = 12,
    parameter int GAIN_W    = 10,
    parameter int K_GAIN    = 512,
    parameter int SHIFT     = 24
) (
    input  logic                       clk,
    input  logic                       rst_n,
    input  logic                       valid_in,
    input  logic [IN_W-1:0]            x,             // 二进制补码
    input  logic signed [OFFSET_W-1:0] offset_code,
    input  logic [GAIN_W-1:0]          gain_code,
    output logic [IN_W-1:0]            y,             // 二进制补码
    output logic                       valid_out,
    output logic                       clip_low,
    output logic                       clip_high          // 高于 +FS 饱和
);

    // 20-bit signed x 11-bit signed (gain-512) -> 31 bit signed
    localparam int PROD_W  = IN_W + GAIN_W + 1;        // 31
    localparam int PRODK_W = PROD_W + 11;              // 42
    localparam int SUM_W   = IN_W + 2;                 // 22
    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;   // +524287
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));       // -524288
    // NOTE: 512 在 10-bit 有符号里会溢出成 -512（10'sd512 == -512），
    //       必须用 11 bit。这个坑会让增益校正整体反号。
    localparam logic signed [10:0]     K_GAIN_S = K_GAIN[10:0];

    // ---- Stage 1 ----
    logic signed [IN_W-1:0]          x_s1;
    logic signed [OFFSET_W-1:0]      off_s1;
    logic signed [PROD_W-1:0]        prod_s1;
    logic                            v_s1;

    // ---- Stage 2 ----
    logic signed [PRODK_W-1:0]       prodk_s2;
    logic signed [17:0]              corr_s2;
    logic signed [SUM_W-1:0]         sum_s2;
    logic signed [SUM_W-1:0]         x_ext, off_ext, sum_comb;
    logic                            v_s2, lo_s2, hi_s2;

    // 显式符号扩展，保证三数相加在位宽与符号上无歧义。
    assign x_ext    = $signed({{(SUM_W-IN_W){x_s1[IN_W-1]}}, x_s1});
    assign off_ext  = $signed({{(SUM_W-OFFSET_W){off_s1[OFFSET_W-1]}}, off_s1});
    wire signed [SUM_W-1:0] corr_ext = $signed({{(SUM_W-18){corr_s2[17]}}, corr_s2});
    assign sum_comb = x_ext + corr_ext + off_ext;

    assign prodk_s2 = prod_s1 * K_GAIN_S;
    // WAIVER(width-truncate): 增益项 |delta| <= 2^19*1.56% = 8176，18 bit 足够；
    // 这里显式截到 SUM_W，高位是符号扩展，不损失有效信息。
    // verilator lint_off WIDTHTRUNC
    assign corr_s2  = (prodk_s2 + (1 << (SHIFT-1))) >>> SHIFT;   // 18 bit 足够（|delta|<=8176）
    // verilator lint_on WIDTHTRUNC

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x_s1    <= '0;
            off_s1  <= '0;
            prod_s1 <= '0;
            v_s1    <= 1'b0;
        end else if (valid_in) begin
            x_s1    <= $signed(x);
            off_s1  <= offset_code;
            // WAIVER(arith-width): 20-bit x 11-bit 有符号乘，结果 31 bit；
            // 不要把两个操作数都扩到 31 bit，否则综合会多出大量无效部分积。
            // verilator lint_off WIDTHEXPAND
            prod_s1 <= $signed(x) * ($signed({1'b0, gain_code}) - 11'sd512);
            // verilator lint_on WIDTHEXPAND
            v_s1    <= 1'b1;
        end else begin
            v_s1    <= 1'b0;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sum_s2    <= '0;
            v_s2      <= 1'b0;
            lo_s2     <= 1'b0;
            hi_s2     <= 1'b0;
            y         <= '0;
            valid_out <= 1'b0;
            clip_low  <= 1'b0;
            clip_high <= 1'b0;
        end else begin
            v_s2 <= v_s1;
            if (v_s1) begin
                sum_s2 <= sum_comb;
                lo_s2  <= sum_comb < $signed({{(SUM_W-IN_W){MIN_CODE[IN_W-1]}}, MIN_CODE});
                hi_s2  <= sum_comb > $signed({{(SUM_W-IN_W){MAX_CODE[IN_W-1]}}, MAX_CODE});
            end

            if (v_s2) begin
                if (lo_s2) begin
                    y         <= {1'b1, {(IN_W-1){1'b0}}};   // MIN_CODE
                    clip_low  <= 1'b1;
                    clip_high <= 1'b0;
                end else if (hi_s2) begin
                    y         <= {1'b0, {(IN_W-1){1'b1}}};   // MAX_CODE
                    clip_low  <= 1'b0;
                    clip_high <= 1'b1;
                end else begin
                    y         <= sum_s2[IN_W-1:0];
                    clip_low  <= 1'b0;
                    clip_high <= 1'b0;
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

`default_nettype wire
