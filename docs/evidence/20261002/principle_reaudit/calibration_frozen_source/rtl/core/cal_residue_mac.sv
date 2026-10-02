//===========================================================================
// cal_residue_mac.sv -- 定点残差分子与溢出资格检查（纯组合）
//===========================================================================
// 职责：由 recon_core 已捕获的一次样本计算 A1；增益、总权重、rails 与电压
// 必须属于同一 sample_id/configuration epoch。此模块不锁存输入、没有时钟
// 或复位；输入变化立即影响 a1/any_ovf，调用方在下一阶段统一采样。
//
// 算术：num = (fine_r - off_r)*2^W_FRAC - rails_s*2^V_FRAC
//             - total_s*inj_r；shifted = (num + gain_s*2^V_FRAC)*2^OUT_BITS。
// a1 传递 shifted >>> (V_FRAC + 1) 的二补码位型，div_floor.a 将其按有符号
// 数解释。权重/增益为无符号整数，电压与 rails 为有符号整数；乘法前的显式
// 扩展不可删除，否则表达式的符号/精度会改变。
//
// 异常：any_ovf 合并三个操作数、分子、分母尺度和 shifted 的受检范围。
// gain_s == 0 由 recon_core 独立上报 gain_err。any_ovf 为 1 时 a1 无有效
// 数值保证；调用方必须保留最后合法输出，不把截断位型当成合法校准结果。
//
// 综合：宽乘法与移位/加减法均在这一组合阶段；SUM_BITS 默认 64。下面的
// 130/132/97/117/63/22 位注释指默认配置。缩短路径须重新证明符号、溢出
// 及 floor 契约并核对流水延迟；不能只凭仿真正常范围缩窄受检算术。
// 来源：RTL_ARITHMETIC_CONTRACT.md；ADR 0018。
//===========================================================================
`include "rtl_params.vh"
module cal_residue_mac #(
    parameter int SUM_BITS = 64
) (
    input wire signed [V_BITS - 1:0] fine_r, off_r, inj_r,
    input wire [SUM_BITS - 1:0] gain_s, total_s,
    input wire signed [SUM_BITS + 1:0] rails_s,
    output wire [int'(ACC_BITS) - int'(V_FRAC) - 2:0] a1,
    output logic any_ovf
);
  localparam int W_RAIL = SUM_BITS + 2; // |rails| <= 3*2^60 < 2^62
  localparam int W_OP3 = SUM_BITS + int'(V_BITS) + 2; // 130：total * inj_q
  localparam int W_WIDE = W_OP3 + 2; // 132：受检操作数的精确宽度
  localparam int W_S1 = int'(ACC_BITS) + 1; // 97
  localparam int W_SHIFT = int'(ACC_BITS) + int'(OUT_BITS) + 1; // 117
  localparam int W_A = int'(ACC_BITS) - (int'(V_FRAC) + 1); // 63：shifted >>> 33
  localparam int W_TOP = W_SHIFT - int'(ACC_BITS) + 1; // 22：shifted[116:95]

  localparam logic [W_WIDE - 1:0] LIM_ONE = {{(W_WIDE - 1){1'b0}}, 1'b1};
  localparam logic [W_WIDE - 1:0] LIM_HI_U = LIM_ONE << (int'(ACC_BITS) - 1);
  localparam logic [W_WIDE - 1:0] LIM_LO_U = ~LIM_HI_U + LIM_ONE;
  localparam logic [SUM_BITS - 1:0] GAIN_MAX = {{(SUM_BITS - 1){1'b0}}, 1'b1}
      << (int'(ACC_BITS) - 1 - (int'(V_FRAC) + 1));

  logic signed [V_BITS:0] diff;
  logic signed [W_WIDE - 1:0] op1, op2, op3, num;
  logic signed [W_OP3 - 1:0] op3_w;
  logic signed [W_WIDE - 1:0] rails_ext;
  logic signed [W_S1 - 1:0] gv, s1;
  logic signed [W_SHIFT - 1:0] shifted;
  logic [W_TOP - 1:0] shifted_top;
  logic bad_op1, bad_op2, bad_op3, bad_num, bad_den;
  logic shifted_ok;

  assign diff = $signed(fine_r) - $signed(off_r);
  assign op1 = $signed({{(W_WIDE - (int'(V_BITS) + 1)){diff[V_BITS]}}, diff}) <<< W_FRAC;

  assign rails_ext = {{(W_WIDE - W_RAIL){rails_s[W_RAIL - 1]}}, rails_s};
  assign op2 = rails_ext <<< V_FRAC;

  // total 是**无符号**量：先显式零扩展到 W_OP3 再乘，避免"混一个无符号操作数
  // 就让整条表达式按无符号算"（P1 的 RTL-3）。
  assign op3_w = $signed({{(W_OP3 - SUM_BITS){1'b0}}, total_s}) * $signed(inj_r);
  assign op3 = {{(W_WIDE - W_OP3){op3_w[W_OP3 - 1]}}, op3_w};

  assign num = op1 - op2 - op3;

  assign bad_op1 = (op1 >= $signed(LIM_HI_U)) || (op1 < $signed(LIM_LO_U));
  assign bad_op2 = (op2 >= $signed(LIM_HI_U)) || (op2 < $signed(LIM_LO_U));
  assign bad_op3 = (op3 >= $signed(LIM_HI_U)) || (op3 < $signed(LIM_LO_U));
  assign bad_num = (num >= $signed(LIM_HI_U)) || (num < $signed(LIM_LO_U));
  assign bad_den = (gain_s >= GAIN_MAX);

  assign gv = $signed({{(W_S1 - SUM_BITS){1'b0}}, gain_s}) <<< V_FRAC;
  assign s1 = $signed(num[W_S1 - 1:0]) + gv;
  assign shifted = $signed({{(W_SHIFT - W_S1){s1[W_S1 - 1]}}, s1}) <<< OUT_BITS;

  // A1 = shifted >>> 33，取其低 W_A 位即精确值（前提：shifted 检查已通过）。
  assign a1 = shifted[W_A - 1 + (int'(V_FRAC) + 1) : int'(V_FRAC) + 1];
  assign shifted_top = shifted[W_SHIFT - 1:int'(ACC_BITS) - 1];

  always_comb begin
    // 位 116..95 全 0  =>  0 <= shifted < 2^95
    // 位 116..95 全 1  =>  -2^95 <= shifted < 0
    shifted_ok = (shifted_top == {W_TOP{1'b0}}) || (&shifted_top);
    any_ovf = bad_op1 | bad_op2 | bad_op3 | bad_num | bad_den | (!shifted_ok);
  end

endmodule
