//=============================================================================
// @file    ad4080_dsp_chain.sv
// @brief   单一路径的有符号校正与数值滤波链。
// @details 选择直通、SINC1、SINC5，或显式绑定系数的三抽头补偿路径。
//          捕获存储由顶层管理，本模块不重复配置 FIFO，也不提供替代路径。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.64–71/94；增益与 COMP 算法采用显式工程规则。
// 时钟域: clk；样本、系数与 filter_sync 均由调用方同步。
// 复位: rst_n 低有效、异步置位；filter_sync 取消校正和滤波在途有效结果。
// 延迟: 校正经过两个完整 clk 间隔，再叠加所选滤波器的流水延迟。
// 吞吐: 每个 clk 最多接收一个输入；抽取率决定输出间隔。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: sys_error_corr、sinc1_decim、sinc5_decim、sinc5_comp。
// 边界: valid_out 只表示数值有效，可能含启动瞬态；不生成基于 CNV 的 FILTER_RDY。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none
module ad4080_dsp_chain #(
    parameter int IN_W = 20,
    parameter bit P_GAIN_MAP_VALID = 1'b0,
    parameter int K_GAIN = -1,
    parameter int SHIFT = -1,
    parameter bit P_GAIN_CODE0_SUPPORTED = 1'b0,
    parameter bit P_COMP_COEFF_VALID = 1'b0,
    parameter int C0 = 32'h40000000,
    parameter int C1 = 32'h40000000,
    parameter int C2 = 32'h40000000
) (
    input logic clk, rst_n, valid_in,
    input logic [IN_W-1:0] x,
    input logic signed [11:0] offset_code,
    input logic [9:0] gain_code,
    input logic [1:0] filter_sel,
    input logic [3:0] dec_sel,
    input logic filter_sync,
    output logic [IN_W-1:0] y,
    output logic valid_out, clip_low, clip_high,
    output logic processing_reject
);
    logic [IN_W-1:0] corrected, s1_data, s5_data, comp_data;
    logic corrected_valid, s1_valid, s5_valid, comp_valid;
    logic gain_reject, comp_reject;
    sys_error_corr #(.IN_W(IN_W), .P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),
        .K_GAIN(K_GAIN), .SHIFT(SHIFT), .P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED)) u_corr (
        .clk(clk), .rst_n(rst_n), .valid_in(valid_in), .cancel(filter_sync), .x(x),
        .offset_code(offset_code), .gain_code(gain_code),
        .y(corrected), .valid_out(corrected_valid), .clip_low(clip_low), .clip_high(clip_high),
        .processing_reject(gain_reject)
    );
    sinc1_decim #(.IN_W(IN_W)) u_sinc1 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(corrected_valid && filter_sel==2'd1), .data_in(corrected), .dec_sel(dec_sel),
        .data_out(s1_data), .valid_out(s1_valid)
    );
    sinc5_decim #(.IN_W(IN_W)) u_sinc5 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(corrected_valid && filter_sel==2'd2), .data_in(corrected), .dec_sel(dec_sel),
        .data_out(s5_data), .valid_out(s5_valid)
    );
    sinc5_comp #(.IN_W(IN_W), .P_COMP_COEFF_VALID(P_COMP_COEFF_VALID),
        .C0(C0), .C1(C1), .C2(C2)) u_comp (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(corrected_valid && filter_sel==2'd3), .data_in(corrected), .dec_sel(dec_sel),
        .data_out(comp_data), .valid_out(comp_valid), .processing_reject(comp_reject)
    );
    assign processing_reject = gain_reject | comp_reject;
    always_comb begin
        case (filter_sel)
            2'd1: begin y=s1_data; valid_out=s1_valid; end
            2'd2: begin y=s5_data; valid_out=s5_valid; end
            2'd3: begin y=comp_data; valid_out=comp_valid; end
            default: begin y=corrected; valid_out=corrected_valid; end
        endcase
    end
endmodule
`default_nettype wire
