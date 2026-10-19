//=============================================================================
// @file    sinc1_decim.sv
// @brief   SINC1 抽取滤波器（运行时抽取率，补码）
// @details 块平均 y=(1/D)Σx；D=2^(code+1)，code>9 夹到 1024；
//          累加位宽 IN_W+11，四舍五入后输出。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.20 Table 7, p.64-66 Table 28/29/62
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效；filter_sync 清相位
// 延迟     : D 个输入 + 1 clk
// 吞吐     : 1 sample/clk 输入，1/D 输出
// 可综合性 : 是；D=1024 时累加器 31 bit
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版（编译期 DECIM，无符号）
//   2.0.0 2026-10-19 运行时 dec_sel；改补码；块边界锁存
//   3.0.0 2026-10-19 增加 settled（settling = 2+D+1）
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.20 Table 7（输出为 20-bit 补码）、
//     p.64-66 Table 28/29/62：
//     FILTER_SEL=01 -> SINC1，D = 2,4,...,1024
//     SINC_DEC_RATE: D = 2^(code+1)，code>9 -> 1024
//     settling = 2 + D + 1 个 CNV
//
//   结构：块平均 y[k] = (1/D) * sum x[kD+i]
//   定点：有符号累加（20-bit 补码输入），ACC_W = IN_W+11（D=1024 时 30 位 + 符号）；
//         输出前四舍五入 (sum + D/2) >>> log2(D)。
//
//   运行时抽取率：dec_sel 在块边界锁存，块中途改码不影响当前块；
//     filter_sync 强制重锁（手册要求改 FILTER_SEL 后必须发 FILTER_SYNC）。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module sinc1_decim #(
    parameter int IN_W = 20
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out,    // 输出有效，单拍
    output logic                 settled       // 建立完成（2+D+1 个样本后）                // 输出有效，单拍
);

    localparam int ACC_W  = IN_W + 11;
    localparam int ACC_WX = ACC_W + 1;

    // 两个纯函数（Icarus 不支持 function 的 output 参数；拆开更可移植）
    function automatic [10:0] dec_d(input [3:0] s);
        begin
            if (s > 4'd9) dec_d = 11'd1024;
            else          dec_d = 11'd1 << (s + 4'd1);
        end
    endfunction

    function automatic [4:0] dec_l(input [3:0] s);
        begin
            if (s > 4'd9) dec_l = 5'd10;
            else          dec_l = {1'b0, s} + 5'd1;
        end
    endfunction

    logic [10:0] d_map, d_cur;
    logic [4:0]  l_map, l_cur;

    always_comb begin
        d_map = dec_d(dec_sel);
        l_map = dec_l(dec_sel);
    end

    logic signed [ACC_WX-1:0] acc;
    logic [10:0]              cnt;
    logic signed [ACC_WX-1:0] sum;
    logic signed [ACC_WX-1:0] avg;
    logic signed [ACC_WX-1:0] data_ext;

    // 显式符号扩展到累加位宽：避免工具对混合位宽的有符号加法做不同解释。
    assign data_ext = $signed({{(ACC_WX-IN_W){data_in[IN_W-1]}}, data_in});
    assign sum = acc + data_ext + $signed({{(ACC_WX-11){1'b0}}, (d_cur >> 1)});
    assign avg = sum >>> l_cur;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc       <= '0;
            cnt       <= '0;
            d_cur     <= 11'd2;
            l_cur     <= 5'd1;
            data_out  <= '0;
            valid_out <= 1'b0;
        end else if (filter_sync) begin
            acc       <= '0;
            cnt       <= '0;
            d_cur     <= d_map;
            l_cur     <= l_map;
            valid_out <= 1'b0;
        end else begin
            valid_out <= 1'b0;
            if (valid_in) begin
                if (cnt == d_cur - 11'd1) begin
                    data_out  <= avg[IN_W-1:0];
                    valid_out <= 1'b1;
                    acc       <= '0;
                    cnt       <= '0;
                    d_cur     <= d_map;
                    l_cur     <= l_map;
                end else begin
                    acc <= acc + data_ext;
                    cnt <= cnt + 11'd1;
                end
            end
        end
    end

    // ---------------- 建立计数（手册 p.69：settling = 2 + D + 1 个 CNV） ----------------
    localparam int SET_W = 16;
    logic [SET_W-1:0] settle_cnt;
    wire  [SET_W-1:0] settle_target = SET_W'(d_cur) + 16'd3;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            settle_cnt <= '0;
            settled    <= 1'b0;
        end else if (filter_sync) begin
            settle_cnt <= '0;
            settled    <= 1'b0;
        end else if (valid_in && !settled) begin
            if (settle_cnt == settle_target - 16'd1) begin
                settled <= 1'b1;
            end else begin
                settle_cnt <= settle_cnt + 16'd1;
            end
        end
    end

endmodule

`default_nettype wire
