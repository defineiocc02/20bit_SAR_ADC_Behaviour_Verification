// 模块：div_floor；组内逐位展开的固定周期有符号floor除法。
// 职责：q=floor(a/d)，a为P_W_A位signed，d为P_W_D位unsigned；负非整商向负无穷取整。
// 时钟/复位：clk上升沿、同步低有效rst_n；reset取消运算并清q/err/busy/done。
// 接受：仅!run&&start捕获操作数，RUN中start包括最终计算沿均忽略；完成后才能接新请求。
// 周期：N_CYC=ceil(P_W_A/P_STAGES)，接受沿t后t+N_CYC提交q/err并脉冲done。
// 异常：d=0仍占固定周期，输出q=0/err=1；宽分母高位不截丢，负非零小分子仍得-1。
// 精度：无符号幅度可容纳最小负数；P_W_A位余数按已消费前缀界限保存，组内MSB优先。
// 适用域：P_W_A>=2、P_W_D>=1、P_STAGES>=1；不做最终输出饱和或校准溢出资格检查。
// 来源：RTL_ARITHMETIC_CONTRACT.md §3.5；P_STAGES为路径长度/迭代周期的工程参数。
`timescale 1ns/1ps
`include "rtl_params.vh"

module div_floor #(
    parameter int P_W_A = int'(ACC_BITS) - (int'(V_FRAC) + 1),
    parameter int P_W_D = 60,
    parameter int P_STAGES = 7
) (
    input logic clk,
    input logic rst_n,
    input logic start,
    input logic signed [P_W_A - 1:0] a,
    input logic [P_W_D - 1:0] d,
    output logic signed [P_W_A - 1:0] q,
    output logic err,
    output logic busy,
    output logic done
);

  // After each consumed bit, 0 <= remainder <= consumed numerator prefix
  // <= |a| <= 2^(P_W_A-1). P_W_A bits therefore hold every shifted trial
  // remainder, even when the unsigned denominator has more bits than a.
  localparam int W_R = P_W_A;
  localparam int N_CYC = (P_W_A + P_STAGES - 1) / P_STAGES;
  localparam int W_PAD = N_CYC * P_STAGES;
  localparam int W_CNT = (N_CYC < 2) ? 1 : $clog2(N_CYC);

  logic [W_PAD - 1:0] mag;
  logic [W_R - 1:0] dv;
  logic dv_large;
  logic [W_R - 1:0] rem;
  logic [W_PAD - 1:0] quo;
  logic neg;
  logic [W_CNT - 1:0] cnt;
  logic run;
  logic err_r;

  logic [W_R - 1:0] r_chain [0:P_STAGES];
  logic [P_STAGES - 1:0] qbits;
  logic [W_R - 1:0] r_next;
  logic [W_PAD - 1:0] q_next;
  logic [W_R - 1:0] shifted;
  logic [W_R:0] trial_difference;
  wire input_d_large;

  // A denominator >= 2^P_W_A is larger than every possible numerator magnitude. Preserve its
  // full-width meaning instead of truncating it: quotient magnitude stays zero, while the final
  // floor correction still returns -1 for a negative nonzero numerator.
  if (P_W_D > W_R) begin : g_wide_denominator
    assign input_d_large = |d[P_W_D - 1:W_R];
  end else begin : g_narrow_denominator
    assign input_d_large = 1'b0;
  end

  integer s;
  initial begin
    if (P_W_A < 2 || P_W_D < 1 || P_STAGES < 1)
      $fatal(1, "div_floor: widths and unroll must be positive (numerator >= 2)");
  end

  assign busy = run;

  always_comb begin
    r_chain[0] = rem;
    for (s = 0; s < P_STAGES; s = s + 1) begin
      // Consume a fixed high-order chunk; the magnitude shifts between cycles. Avoid a
      // counter-controlled variable bit selection on every unrolled stage.
      shifted = {r_chain[s][W_R - 2:0], mag[W_PAD - 1 - s]};
      // One widened unsigned subtraction supplies both the result and its borrow. Do not infer a
      // separate full-width compare plus subtract.
      trial_difference = {1'b0, shifted} - {1'b0, dv};
      // The high-order consumed bit produces the high-order bit of this group. Reversing this index
      // corrupts every multi-stage quotient chunk.
      if (!dv_large && !trial_difference[W_R]) begin
        r_chain[s + 1] = trial_difference[W_R - 1:0];
        qbits[P_STAGES - 1 - s] = 1'b1;
      end else begin
        r_chain[s + 1] = shifted;
        qbits[P_STAGES - 1 - s] = 1'b0;
      end
    end
    r_next = r_chain[P_STAGES];
    q_next = (quo << P_STAGES) | {{(W_PAD - P_STAGES){1'b0}}, qbits};
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      mag <= {W_PAD{1'b0}};
      dv <= {W_R{1'b0}};
      dv_large <= 1'b0;
      rem <= {W_R{1'b0}};
      quo <= {W_PAD{1'b0}};
      neg <= 1'b0;
      cnt <= {W_CNT{1'b0}};
      run <= 1'b0;
      err_r <= 1'b0;
      q <= {P_W_A{1'b0}};
      err <= 1'b0;
      done <= 1'b0;
    end else begin
      done <= 1'b0;
      if (start && !run) begin
        mag <= {{(W_PAD - P_W_A){1'b0}}, (a[P_W_A - 1] ? (~a + 1'b1) : a)};
        dv <= W_R'(d);
        dv_large <= input_d_large;
        rem <= {W_R{1'b0}};
        quo <= {W_PAD{1'b0}};
        neg <= a[P_W_A - 1];
        cnt <= {W_CNT{1'b0}};
        run <= 1'b1;
        err_r <= (d == {P_W_D{1'b0}});
      end else if (run) begin
        if (int'(cnt) == N_CYC - 1) begin
          // Final q_next already includes this cycle; do not commit old quo. Nonzero remainder
          // rounds a negative quotient downward by one LSB.
          if (err_r)
            q <= {P_W_A{1'b0}};
          else
            q <= neg ? -(q_next[P_W_A - 1:0] +
                {{(P_W_A - 1){1'b0}}, (r_next != 0)}) : q_next[P_W_A - 1:0];
          err <= err_r;
          run <= 1'b0;
          done <= 1'b1;
        end else begin
          mag <= mag << P_STAGES;
          rem <= r_next;
          quo <= q_next;
          cnt <= cnt + {{(W_CNT - 1){1'b0}}, 1'b1};
        end
      end
    end
  end

endmodule
