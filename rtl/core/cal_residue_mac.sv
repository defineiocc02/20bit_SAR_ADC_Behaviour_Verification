// Calibrated residue equation and explicit overflow qualification (combinational).
// Inputs are one snapshotted physical sample. Denominator/gain must travel with
// numerator through the controller; never recompute gain from a newer allocation.
`include "rtl_params.vh"
module cal_residue_mac #(
    parameter int SUM_BITS = 64
) (
    input wire signed [V_BITS-1:0] fine_r, off_r, inj_r,
    input wire [SUM_BITS-1:0] gain_s, total_s,
    input wire signed [SUM_BITS+1:0] rails_s,
    output wire [int'(ACC_BITS)-int'(V_FRAC)-2:0] a1,
    output logic any_ovf
);
  localparam int W_RAIL   = SUM_BITS + 2;                // |rails| <= 3*2^60 < 2^62
  localparam int W_OP3    = SUM_BITS + int'(V_BITS) + 2;       // 130：total * inj_q
  localparam int W_WIDE   = W_OP3 + 2;                   // 132：受检操作数的精确宽度
  localparam int W_S1     = int'(ACC_BITS) + 1;                // 97
  localparam int W_SHIFT  = int'(ACC_BITS) + int'(OUT_BITS) + 1;     // 117
  localparam int W_A      = int'(ACC_BITS) - (int'(V_FRAC) + 1);     // 63：shifted >>> 33
  localparam int W_TOP    = W_SHIFT - int'(ACC_BITS) + 1;      // 22：shifted[116:95]

  localparam logic [W_WIDE-1:0] LIM_ONE  = {{(W_WIDE-1){1'b0}}, 1'b1};
  localparam logic [W_WIDE-1:0] LIM_HI_U = LIM_ONE << (int'(ACC_BITS) - 1);
  localparam logic [W_WIDE-1:0] LIM_LO_U = ~LIM_HI_U + LIM_ONE;
  localparam logic [SUM_BITS-1:0] GAIN_MAX = {{(SUM_BITS-1){1'b0}}, 1'b1}
                                             << (int'(ACC_BITS) - 1 - (int'(V_FRAC) + 1));

  logic signed [V_BITS:0]     diff;
  logic signed [W_WIDE-1:0]   op1, op2, op3, num;
  logic signed [W_OP3-1:0]    op3_w;
  logic signed [W_WIDE-1:0]   rails_ext;
  logic signed [W_S1-1:0]     gv, s1;
  logic signed [W_SHIFT-1:0]  shifted;
  logic [W_TOP-1:0]           shifted_top;
  logic                       bad_op1, bad_op2, bad_op3, bad_num, bad_den;
  logic                       shifted_ok;

  assign diff = $signed(fine_r) - $signed(off_r);
  assign op1  = $signed({{(W_WIDE - (int'(V_BITS) + 1)){diff[V_BITS]}}, diff}) <<< W_FRAC;

  assign rails_ext = {{(W_WIDE - W_RAIL){rails_s[W_RAIL-1]}}, rails_s};
  assign op2       = rails_ext <<< V_FRAC;

  // total 是**无符号**量：先显式零扩展到 W_OP3 再乘，避免"混一个无符号操作数
  // 就让整条表达式按无符号算"（P1 的 RTL-3）。
  assign op3_w = $signed({{(W_OP3-SUM_BITS){1'b0}}, total_s}) * $signed(inj_r);
  assign op3   = {{(W_WIDE - W_OP3){op3_w[W_OP3-1]}}, op3_w};

  assign num = op1 - op2 - op3;

  assign bad_op1 = (op1 >= $signed(LIM_HI_U)) || (op1 < $signed(LIM_LO_U));
  assign bad_op2 = (op2 >= $signed(LIM_HI_U)) || (op2 < $signed(LIM_LO_U));
  assign bad_op3 = (op3 >= $signed(LIM_HI_U)) || (op3 < $signed(LIM_LO_U));
  assign bad_num = (num >= $signed(LIM_HI_U)) || (num < $signed(LIM_LO_U));
  assign bad_den = (gain_s >= GAIN_MAX);

  assign gv      = $signed({{(W_S1 - SUM_BITS){1'b0}}, gain_s}) <<< V_FRAC;
  assign s1      = $signed(num[W_S1-1:0]) + gv;
  assign shifted = $signed({{(W_SHIFT - W_S1){s1[W_S1-1]}}, s1}) <<< OUT_BITS;

  // A1 = shifted >>> 33，取其低 W_A 位即精确值（前提：shifted 检查已通过）。
  assign a1          = shifted[W_A-1+(int'(V_FRAC)+1) : int'(V_FRAC)+1];
  assign shifted_top = shifted[W_SHIFT-1:int'(ACC_BITS)-1];

  always_comb begin
    // 位 116..95 全 0  =>  0 <= shifted < 2^95
    // 位 116..95 全 1  =>  -2^95 <= shifted < 0
    shifted_ok = (shifted_top == {W_TOP{1'b0}}) || (&shifted_top);
    any_ovf    = bad_op1 | bad_op2 | bad_op3 | bad_num | bad_den | (!shifted_ok);
  end

endmodule
