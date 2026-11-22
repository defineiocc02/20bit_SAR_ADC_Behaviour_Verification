// =============================================================================
// 文件名     : sys_error_corr.sv
// 模块名     : sys_error_corr
// 版本       : v1.0
// 功能       : 对有符号 ADC 样本先加 OFFSET，再按显式映射乘 GAIN 并饱和。
// 设计说明   :
//   * Rev. B p.64 图 96 给出运算顺序，p.92/93 给出寄存器字段。
//     增益传递与舍入由工程绑定。GAIN=512 表示单位增益。
//   * 样本和系数同沿锁存，接受到输出相隔两个完整周期，最大每拍一笔。
//     异步复位清状态，cancel 同沿优先于接收和输出，清在途有效标志。
//   * 20/12/10 位接口。无映射时仅受理单位增益，不引入增益旁路时序。
// 变更记录   : 2026-11-20 实现图 96 的先加后乘，并保留偏置额外符号位。
// @file    sys_error_corr.sv
// @brief   有符号系统校正。
// @details 按显式工程倍率对加偏置后的结果舍入，最终量化到 20 位补码。
// @author  Project maintainers
// @date    2026-11-20
// @version v1.0
// 手册依据: AD4080 Rev. B p.64 图 96 及 p.71/92/93。
// 时钟域: clk 单域。复位: rst_n 低有效异步。
// 可综合性: 定宽整数结构。目标工艺的映射和时序需独立验收。
// =============================================================================
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
    // --- 采样和系数输入 ---
    input  logic clk, rst_n,             // 复位异步断言
    input  logic valid_in, cancel,       // cancel 优先于 valid_in 和本拍输出
    input  logic [IN_W-1:0] x,            // 位串按 20 位补码解释
    input  logic signed [OFFSET_W-1:0] offset_code, // 与输入码同一 LSB
    input  logic [GAIN_W-1:0] gain_code,  // 10 位直码，0x200 表示单位
    // --- 输出及诊断 ---
    output logic [IN_W-1:0] y,            // valid_out 为高时才提交结果
    output logic valid_out, clip_low, clip_high, // 两侧饱和只伴随有效样本
    output logic processing_reject       // 未受理的 valid_in 同沿给出原因入口
);
    // =========================================================================
    // 1. 加偏置和乘法的精确位宽
    // =========================================================================
    localparam bit GAIN_BINDING_VALID=P_GAIN_MAP_VALID && K_GAIN>=1 && K_GAIN<=1023 && SHIFT>=1 && SHIFT<=30;
    localparam int BIASED_W = IN_W + 1;       // 负端最小 -526336，正端最大 526334
    localparam int DELTA_W = GAIN_W + 1;     // 有符号表示增益码减去 512
    localparam int NUMERATOR_W = 11;         // 有符号表示 K_GAIN 1..1023
    localparam int PRODUCT_W = BIASED_W + DELTA_W;
    localparam int SCALED_W = PRODUCT_W + NUMERATOR_W;
    localparam int SUM_W = SCALED_W + 1;     // 保留增量和偏置后样本相加的进位
    localparam logic signed [SUM_W-1:0] MAX_CODE = (SUM_W'(1) <<< (IN_W - 1)) - SUM_W'(1);
    localparam logic signed [SUM_W-1:0] MIN_CODE = -(SUM_W'(1) <<< (IN_W - 1));
    initial begin
        if (IN_W != 20 || OFFSET_W != 12 || GAIN_W != 10)
            $fatal(1, "sys_error_corr: only the 20/12/10-bit contract is supported");
        if (P_GAIN_MAP_VALID && (K_GAIN < 1 || K_GAIN > 1023 || SHIFT < 1 || SHIFT > 30))
            $fatal(1, "sys_error_corr: explicit gain numerator/shift is missing or invalid");
    end

    // 两个加数先分别符号扩展。先裁剪或在 20 位相加都可能损坏随后的压缩增益结果。
    wire signed [BIASED_W-1:0] biased_input = BIASED_W'($signed(x)) + BIASED_W'(offset_code);
    logic signed [BIASED_W-1:0] biased_s1;
    logic valid_s1;
    logic signed [SCALED_W-1:0] scaled_gain;
    logic [IN_W-1:0] sum_s2;
    logic valid_s2, low_s2, high_s2;
    wire gain_supported = (gain_code == 10'd512) ||
                          (GAIN_BINDING_VALID && ((gain_code != 10'd0) || P_GAIN_CODE0_SUPPORTED));

    // =========================================================================
    // 2. 同笔增益与定点舍入
    // =========================================================================
    generate
        if (GAIN_BINDING_VALID) begin : g_bound_gain
            logic signed [DELTA_W-1:0] gain_delta_s1;

            // 增益差和偏置后样本在同一沿登记，支持相邻拍的配置系数不同。
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) gain_delta_s1 <= '0;
                else if (cancel) gain_delta_s1 <= '0;
                else if (valid_in && gain_supported)
                    gain_delta_s1 <= $signed({1'b0,gain_code}) - 11'sd512;
            end
            localparam logic signed [NUMERATOR_W-1:0] NUMERATOR = NUMERATOR_W'(K_GAIN);
            localparam logic signed [SCALED_W-1:0] ROUND_HALF = SCALED_W'(1) <<< (SHIFT - 1);
            logic signed [PRODUCT_W-1:0] product;
            logic signed [SCALED_W-1:0] scaled_product;

            // 使用完整的 32/43 位乘积。合法输入的幅值上界低于 2^39，
            // 连同最大半步仍可放入有符号 43 位。负半步同样向正无穷舍入。
            assign product = biased_s1 * gain_delta_s1;
            assign scaled_product = product * NUMERATOR;
            assign scaled_gain = (scaled_product + ROUND_HALF) >>> SHIFT;
        end else begin : g_unity_only
            assign scaled_gain = '0;
        end
    endgenerate

    // y = biased + round(biased × (gain - 1))。整数 biased 使其等价于 round(biased × gain)。
    wire signed [SUM_W-1:0] total = SUM_W'(biased_s1) + SUM_W'(scaled_gain);

    // =========================================================================
    // 3. 两段提交与区间检查
    // =========================================================================
    // 先用完整 44 位判定饱和，再从已登记标志选择限幅码。取消时数据可保持旧值，
    // 但本拍输出和在途 valid 均无效，下一笔仍按原延迟退休。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            biased_s1 <= '0;
            valid_s1 <= 1'b0; processing_reject <= 1'b0;
            sum_s2 <= '0; valid_s2 <= 1'b0; low_s2 <= 1'b0; high_s2 <= 1'b0;
            y <= '0; valid_out <= 1'b0; clip_low <= 1'b0; clip_high <= 1'b0;
        end else if (cancel) begin
            valid_s1 <= 1'b0; valid_s2 <= 1'b0; valid_out <= 1'b0;
            clip_low <= 1'b0; clip_high <= 1'b0; processing_reject <= 1'b0;
        end else begin
            processing_reject <= valid_in && !gain_supported;
            valid_s1 <= valid_in && gain_supported;
            if (valid_in && gain_supported) biased_s1 <= biased_input;
            valid_s2 <= valid_s1;
            if (valid_s1) begin
                sum_s2 <= total[IN_W-1:0];
                low_s2 <= total < MIN_CODE;
                high_s2 <= total > MAX_CODE;
            end
            valid_out <= valid_s2;
            clip_low <= valid_s2 && low_s2;
            clip_high <= valid_s2 && high_s2;
            if (valid_s2) begin
                if (low_s2) y <= MIN_CODE[IN_W-1:0];
                else if (high_s2) y <= MAX_CODE[IN_W-1:0];
                else y <= sum_s2;
            end
        end
    end
endmodule
`default_nettype wire
