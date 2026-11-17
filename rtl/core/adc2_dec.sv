`timescale 1ns/1ps

// =============================================================================
// 文件名     : adc2_dec.sv
// 模块名     : adc2_dec
// 版本       : v1.0
// 功能       : 将无符号码转换为 Q32 归一化电压的码仓中心。
// 设计说明   : 实现 min + round_half_even((2*code+1)*(max-min)/2^(P_ADC2_BITS+1))。
//              码宽和定点格式由调用方显式提供，作为 recon_core 的独立算术依赖。
//              组合路径使用全宽乘积和有符号宽位相加，裁宽在溢出判决之后。
//              调用方保证 min < max，并使三个输入与同一笔样本对齐。
// 变更记录   : 去除退役整链位宽范围，按实参说明独立组件的数值边界。
// =============================================================================
`include "rtl_params.vh"

module adc2_dec #(
    parameter int P_ADC2_BITS = int'(ADC2_BITS)
) (
    // --- 码仓与上下界 ---
    input  logic [P_ADC2_BITS - 1:0]    adc2_code, // 原始无符号码
    input  logic signed [V_BITS - 1:0] adc2_min_q, // Q32 下边界
    input  logic signed [V_BITS - 1:0] adc2_max_q, // Q32 上边界
    // --- 换算结果 ---
    output logic signed [V_BITS - 1:0] fine_q,     // 钳位后的 Q32 码仓中心
    output logic                      ovf        // 精确结果超出有符号输出范围
);

  // ===========================================================================
  // 1. 操作数和饱和边界宽度
  // ===========================================================================
  localparam int K     = P_ADC2_BITS + 1;
  localparam int W_MUL = P_ADC2_BITS + 1 + int'(V_BITS) + 1; // 容纳扩展的量程差和奇数码心
  localparam int W_SUM = W_MUL + 1;                       // 加上下边界时额外保留一位

  logic [K - 1:0]            two_c_plus_1; // 2*code + 1 = {code, 1'b1}
  logic signed [V_BITS:0]    delta;        // max - min，多一位保存符号与差值
  logic signed [W_MUL - 1:0] delta_ext;
  logic signed [W_MUL - 1:0] n;
  logic signed [W_MUL - 1:0] q_shift;
  logic signed [W_MUL - 1:0] sh;
  logic [K - 1:0]            r_bits;
  logic [K - 1:0]            half;
  logic                      inc;
  logic signed [W_SUM - 1:0] sum_wide;
  logic signed [W_SUM - 1:0] min_ext;

  // 边界常量先在 W_SUM 位构造，再显式转为 signed 参与比较；不能先截到 V_BITS 位再比较，否则正上界
  // 2^63 会被解释为负数。
  localparam logic [W_SUM - 1:0] V_ONE   = {{(W_SUM - 1){1'b0}}, 1'b1};
  localparam logic [W_SUM - 1:0] V_HI_U  = V_ONE << (int'(V_BITS) - 1); // V_HI_U: 默认 2^63
  localparam logic [W_SUM - 1:0] V_LO_U  = ~V_HI_U + V_ONE;            // V_LO_U: 默认 -2^63
  localparam logic [W_SUM - 1:0] V_MAX_U = V_HI_U - V_ONE;            // V_MAX_U: 默认 2^63 - 1

  // ===========================================================================
  // 2. 全宽乘积与半 LSB 平局判定
  // ===========================================================================
  // delta 保留 V_BITS+1 位，使量程跨越零点时减法也能容纳全跨度。
  assign two_c_plus_1 = {adc2_code, 1'b1};
  assign delta       = $signed(adc2_max_q) - $signed(adc2_min_q);
  assign delta_ext   = {{(W_MUL - (int'(V_BITS) + 1)){delta[V_BITS]}}, delta};

  // 两个乘法操作数均显式转换为signed，避免无符号操作数改变乘积扩展。
  assign n = $signed({{(W_MUL - K){1'b0}}, two_c_plus_1}) * delta_ext;

  // 组合段 2：除以固定 2^K，余数比较实现 ties-to-even。此处不推断除法器；前面的 n 仍是量程可配置的
  // 乘法，综合时应单独查看其 DSP/LUT 映射。
  assign q_shift = $signed(n) >>> K;
  assign r_bits  = n[K - 1:0];
  assign half    = {{(K - 1){1'b0}}, 1'b1} << (K - 1);
  assign inc     = (r_bits > half) || ((r_bits == half) && q_shift[0]);
  assign sh      = q_shift + {{(W_MUL - 1){1'b0}}, inc};

  // ===========================================================================
  // 3. 饱和判决
  // ===========================================================================
  // 宽位和用于判定饱和。过早裁宽会令大正值回绕至负区间。
  assign min_ext  = {{(W_SUM - int'(V_BITS)){adc2_min_q[V_BITS - 1]}}, adc2_min_q};
  assign sum_wide = min_ext + {{(W_SUM - W_MUL){sh[W_MUL - 1]}}, sh};

  assign ovf = (sum_wide >= $signed(V_HI_U)) || (sum_wide < $signed(V_LO_U));
  assign fine_q = ovf
    ? (sum_wide[W_SUM - 1] ? V_LO_U[V_BITS - 1:0] : V_MAX_U[V_BITS - 1:0])
    : sum_wide[V_BITS - 1:0];

endmodule
