`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_dsp_chain.sv
// 模块名     : ad4080_dsp_chain
// 版本       : v1.0
// 功能       : 对已完成的转换码校正，并按配置选择直通、SINC1、SINC5 或 PF1。
// 设计说明   :
//   * sys_error_corr 在两个完整核心时钟间隔后交付校正码，各滤波器继续按有效样本推进。
//   * FILTER_SYNC 撤销校正和滤波流水的旧数据，切换滤波选择要同时清除共享历史。
//   * valid_out 只给数值完成时刻，包含滤波启动瞬态。产品层按 CNV 决定何时发布和写 FIFO。
//   * AD4080 Rev.B pp.64–71/94 定义公开倍率与接口。PF1 系数由公开频响约束拟合。
// 变更记录   : SINC5 与 PF1 复用一份五阶 CIC，移去旧手填三抽头配置。
// =============================================================================
module ad4080_dsp_chain #(
    parameter int IN_W = 20,
    parameter bit P_GAIN_MAP_VALID = 1'b0,
    parameter int K_GAIN = -1,
    parameter int SHIFT = -1,
    parameter bit P_GAIN_CODE0_SUPPORTED = 1'b0
) (
    // --- 同域输入和会话配置 ---
    input logic clk, rst_n, valid_in,           // 每核心拍至多接收一个已完成的转换码。
    input logic [IN_W-1:0] x,                  // 已按输入端约定转换为有符号二补码。
    input logic signed [11:0] offset_code,      // 偏置在增益乘法前叠加。
    input logic [9:0] gain_code,                // 0x200 为单位增益。
    input logic [1:0] filter_sel,               // 0/1/2/3 对应直通、SINC1、SINC5、PF1。
    input logic [3:0] dec_sel,                  // 由当前选择的抽取器解释基础 N。
    input logic filter_sync,                    // 同沿撤销有效流水优先于接受新数据。
    // --- 数值及校正诊断 ---
    output logic [IN_W-1:0] y,                  // 仍为二补码，FIFO 码制转换在外部。
    output logic valid_out, clip_low, clip_high, // clip 属于校正流水，不能当作滤波结果标签。
    output logic processing_reject             // 非单位增益映射缺失的诊断。
);
    // =========================================================================
    // 1. 校正后的数值入口
    // =========================================================================
    logic [IN_W-1:0] corrected, s1_data, s5_data, comp_data;
    logic corrected_valid, s1_valid, s5_valid, comp_valid;
    logic gain_reject;
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
    // =========================================================================
    // 2. 五阶抽取的共同数值通路和发布选择
    // =========================================================================
    // SINC5 和 PF1 共享同一个 CIC，选择 10 直接送出其真码，选择 11 送入
    // 二倍抽取补偿阶段。选择切换同样经 filter_sync 清除整条共享历史。
    sinc5_comp #(.IN_W(IN_W)) u_comp (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(corrected_valid && filter_sel[1]), .data_in(corrected), .dec_sel(dec_sel),
        .pf1_enable_i(filter_sel==2'd3), .sinc5_data_o(s5_data), .sinc5_valid_o(s5_valid),
        .data_out(comp_data), .valid_out(comp_valid), .processing_reject()
    );
    assign processing_reject = gain_reject;
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
