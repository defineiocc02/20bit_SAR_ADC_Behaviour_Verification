//=============================================================================
// @file    sys_error_corr.sv
// @brief   非单位增益须显式绑定的有符号系统校正。
// @details GAIN=512 表示单位增益，OFFSET 采用有符号 ADC 码；非单位增益没有
//          默认物理映射。整数加半步并算术右移后，再饱和到 20 bit 补码范围。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.71/92/93；增益传递与舍入是显式数值约定，不代表器件内部实现。
// 时钟域: clk；样本与系数输入均同步。
// 复位: rst_n 低有效、异步置位；cancel 优先于输入 valid，清除在途有效标志。
// 延迟: 从接受输入的时钟沿到输出经过两个完整 clk 间隔。
// 吞吐: 每个 clk 最多接受一个样本。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 只支持 20/12/10 bit 接口；缺少活动映射时拒绝处理，不实现器件增益旁路时序。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sys_error_corr #(
    parameter int IN_W = 20,  // IN_W: 仅支持 20 bit 补码样本。
    parameter int OFFSET_W = 12,  // OFFSET_W: 仅支持 12 bit 补码 ADC 偏移码。
    parameter int GAIN_W = 10,  // GAIN_W: 仅支持 10 bit 增益码，512 为单位增益。
    parameter bit P_GAIN_MAP_VALID = 1'b0,  // P_GAIN_MAP_VALID: 调用方已绑定非单位增益映射。
    parameter int K_GAIN = -1,  // K_GAIN: 映射有效时，整数分子须在 1..1023。
    parameter int SHIFT = -1,  // SHIFT: 映射有效时，小数位数须在 1..30。
    parameter bit P_GAIN_CODE0_SUPPORTED = 1'b0  // P_GAIN_CODE0_SUPPORTED: 明确允许零增益码端点。
) (
    input  logic clk, rst_n, valid_in, cancel,
    input  logic [IN_W-1:0] x,
    input  logic signed [OFFSET_W-1:0] offset_code,
    input  logic [GAIN_W-1:0] gain_code,
    output logic [IN_W-1:0] y,
    output logic valid_out, clip_low, clip_high,
    output logic processing_reject
);
    localparam bit GAIN_BINDING_VALID=P_GAIN_MAP_VALID && K_GAIN>=1 && K_GAIN<=1023 && SHIFT>=1 && SHIFT<=30;
    localparam int PROD_W = 42;
    localparam int SUM_W = 44;
    localparam logic signed [SUM_W-1:0] MAX_CODE = 44'sd524287;
    localparam logic signed [SUM_W-1:0] MIN_CODE = -44'sd524288;
    initial begin
        if (IN_W != 20 || OFFSET_W != 12 || GAIN_W != 10)
            $fatal(1, "sys_error_corr: only the 20/12/10-bit contract is supported");
        if (P_GAIN_MAP_VALID && (K_GAIN < 1 || K_GAIN > 1023 || SHIFT < 1 || SHIFT > 30))
            $fatal(1, "sys_error_corr: explicit gain numerator/shift is missing or invalid");
    end

    logic signed [19:0] x_s1;
    logic signed [11:0] offset_s1;
    logic valid_s1;
    logic signed [PROD_W-1:0] scaled_gain;
    logic [19:0] sum_s2;
    logic valid_s2, low_s2, high_s2;
    wire gain_supported = (gain_code == 10'd512) ||
                          (GAIN_BINDING_VALID && ((gain_code != 10'd0) || P_GAIN_CODE0_SUPPORTED));

    generate
        if (GAIN_BINDING_VALID) begin : g_bound_gain
            logic signed [10:0] gain_delta_s1;
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) gain_delta_s1 <= '0;
                else if (cancel) gain_delta_s1 <= '0;
                else if (valid_in && gain_supported)
                    gain_delta_s1 <= $signed({1'b0,gain_code}) - 11'sd512;
            end
            localparam logic signed [10:0] NUMERATOR = 11'(K_GAIN);
            logic signed [30:0] product;
            logic signed [PROD_W-1:0] scaled_product;
            assign product = x_s1 * gain_delta_s1;
            assign scaled_product = product * NUMERATOR;
            assign scaled_gain = (scaled_product + (42'sd1 << (SHIFT-1))) >>> SHIFT;
        end else begin : g_unity_only
            assign scaled_gain = '0;
        end
    endgenerate

    wire signed [SUM_W-1:0] total =
        {{24{x_s1[19]}}, x_s1} + {{32{offset_s1[11]}}, offset_s1} +
        {{2{scaled_gain[PROD_W-1]}}, scaled_gain};

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x_s1 <= '0; offset_s1 <= '0;
            valid_s1 <= 1'b0; processing_reject <= 1'b0;
            sum_s2 <= '0; valid_s2 <= 1'b0; low_s2 <= 1'b0; high_s2 <= 1'b0;
            y <= '0; valid_out <= 1'b0; clip_low <= 1'b0; clip_high <= 1'b0;
        end else if (cancel) begin
            valid_s1 <= 1'b0; valid_s2 <= 1'b0; valid_out <= 1'b0;
            clip_low <= 1'b0; clip_high <= 1'b0; processing_reject <= 1'b0;
        end else begin
            processing_reject <= valid_in && !gain_supported;
            valid_s1 <= valid_in && gain_supported;
            if (valid_in && gain_supported) begin
                x_s1 <= $signed(x);
                offset_s1 <= offset_code;
            end
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                sum_s2 <= total[19:0];
                low_s2 <= total < MIN_CODE;
                high_s2 <= total > MAX_CODE;
            end
            valid_out <= valid_s2;
            clip_low <= valid_s2 && low_s2;
            clip_high <= valid_s2 && high_s2;
            if (valid_s2) begin
                if (low_s2) y <= 20'h80000;
                else if (high_s2) y <= 20'h7ffff;
                else y <= sum_s2[19:0];
            end
        end
    end
endmodule
`default_nettype wire
