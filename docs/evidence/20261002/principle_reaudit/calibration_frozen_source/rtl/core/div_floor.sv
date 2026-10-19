//===========================================================================
// div_floor.sv -- 有符号 floor 除法（恢复余数法，组内逐位展开）
//===========================================================================
// 职责：q=floor(a/d)，a 为 P_W_A 位有符号整数，d 为 P_W_D 位无符号整数。
// floor 向负无穷取整，不能换成 SystemVerilog 的向零截断 `/` 运算。
// 负数结果为 -(abs_quotient + (remainder != 0))；正数直接采用幅度商。
// 最小负数的无符号幅度可由 P_W_A 位保存，不需要有符号 abs 临时变量。
//
// 捕获/状态：仅 !run && start 的上升沿捕获 a/d 的幅度、符号与零分母事件。
//   IDLE(run=0) --start--> RUN(run=1,cnt=0)；
//   RUN 且 cnt<N_CYC-1：消费 P_STAGES 位、更新余数/商、cnt 加 1；
//   RUN 且 cnt==N_CYC-1：同沿提交最终商/err，run 清零、done 拉高一拍；
//   IDLE 无 start：保留 q/err，done 在下一沿回到 0。
// RUN 期间所有 start 均忽略，包括最后计算沿；最终沿之后才能接受新请求。
// rst_n 是同步低有效复位，取消运算并清 q/err/busy/done 及内部状态。
//
// 延迟：N_CYC=ceil(P_W_A/P_STAGES)，W_PAD=N_CYC*P_STAGES，高位补零。
// 从接受沿 t 起，done 在 t+N_CYC 沿置位；默认 63/7 为 9 拍，生产 63/5
// 为 13 拍。每个组内商位按 MSB 优先排列到 qbits[P_STAGES-1-s]。
// a=1,d=1 必须得到 1；组内位序反转会把默认 7 位组的结果错写为 64。
//
// 异常/宽分母：d==0 仍占用固定 N_CYC 拍，提交 q=0、err=1；没有早退。
// 若 P_W_D>P_W_A，高位非零的 d 大于所有可能幅度，商幅度保持 0；负且
// 非零的 a 仍通过余数 floor 修正得到 -1。不能把 d 直接截断成窄分母。
//
// 综合：每拍级联 P_STAGES 个宽减法/借位测试，以一份减法同时得到比较和
// 余数更新；mag 固定从最高组取位，每拍左移，避免计数控制的逐级位选 mux。
// 改 P_STAGES 是组合路径长度与迭代拍数的取舍，调用方须重新验证相位预算。
// 不做输出饱和或校准溢出检查；由 recon_core/cal_output_stage 负责这些资格。
// 来源：RTL_ARITHMETIC_CONTRACT.md §3.5；div_wide_den_tb/p2_tb 定向验证。
//===========================================================================
`include "rtl_params.vh"

module div_floor #(
    parameter int P_W_A = ACC_BITS - (V_FRAC + 1),
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

  // A denominator >= 2^P_W_A is larger than every possible numerator
  // magnitude. Preserve its full-width meaning instead of truncating it:
  // quotient magnitude stays zero, while the final floor correction still
  // returns -1 for a negative nonzero numerator.
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
      // Consume a fixed high-order chunk; the magnitude shifts between cycles.
      // Avoid a counter-controlled variable bit selection on every unrolled stage.
      shifted = {r_chain[s][W_R - 2:0], mag[W_PAD - 1 - s]};
      // One widened unsigned subtraction supplies both the result and its
      // borrow. Do not infer a separate full-width compare plus subtract.
      trial_difference = {1'b0, shifted} - {1'b0, dv};
      // The high-order consumed bit produces the high-order bit of this group.
      // Reversing this index corrupts every multi-stage quotient chunk.
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
          // Final q_next already includes this cycle; do not commit old quo.
          // Nonzero remainder rounds a negative quotient downward by one LSB.
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
