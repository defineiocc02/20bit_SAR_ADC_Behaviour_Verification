// 模块：recon_core；物理权重校正的接受、快照、算术与结果提交。
// 数据流：T/G/rails归约与ADC2码中心→stage-A快照→残差MAC→floor除法→饱和/ID/flags提交。
// 端口/单位：W为48位unsigned Q30；电压为64位signed Q32；输出为20位offset-binary码。
// 时钟/复位：clk上升沿、同步低有效rst_n；cfg_ready=0同步取消在途计算并清有效输出。
// 接受：cfg_ready&&start&&!busy冻结同一样本的所有数据；其后不得混入下一笔实时mask或系数。
// 周期：ceil(63/P_STAGES)+2完整拍；默认P7为11，P5为15；busy期间拒绝start。
// 同沿规则：div_done之后的提交沿可同时接受下一start，NBA保证提交使用旧sample_id/flags。
// 异常：acc_ovf/gain_err仍产生完成ID/flags但保留合法字；adc2_ovf单独上报；负商只向低端clip。
// 精度：96位受检域、宽MAC临时值及63位floor商；P_USE_ROW_TOTALS仅使用精确配置缓存。
// 来源：RTL_ARITHMETIC_CONTRACT.md、ADR0014/0018；不生成DEM、存权重或运行学习器。
`include "rtl_params.vh"

module recon_core #(
    parameter int P_N_ACTIVE = int'(N_ACTIVE),
    parameter int P_N_MAIN = int'(N_UNIT_MAIN),
    parameter int P_N_SUB = int'(N_UNIT_SUB),
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_ADC2_BITS = int'(ADC2_BITS),
    parameter int P_STAGES = 7,
    parameter bit P_USE_ROW_TOTALS = 0,
    parameter int P_DIT_N = 2 * DITHER_UNITS_RANGE,
    parameter int P_DIT_END = DITHER_SPLIT_IS_SUB ? int'(N_UNIT_TOTAL) : int'(N_UNIT_MAIN)
) (
    input logic clk,
    input logic rst_n,
    input logic cfg_ready,
    input logic start,
    input logic [31:0] sample_id,
    input logic clr_ovf,
    input logic sampling_mask_en,
    input logic [P_N_ACTIVE - 1:0][4:0] slice_id,
    input logic [P_N_ACTIVE - 1:0][P_N_MAIN - 1:0] main_on,
    input logic [P_N_ACTIVE - 1:0][P_N_SUB - 1:0] sub_on,
    input logic [P_DIT_N - 1:0] dither_rail,
    input logic [P_ADC2_BITS - 1:0] adc2_code,
    input logic signed [V_BITS - 1:0] inj_q,
    input logic [P_N_SLICES - 1:0][P_N_MAIN + P_N_SUB - 1:0][W_BITS - 1:0] w_rom,
    input logic [P_N_SLICES - 1:0][63:0] row_total,
    input logic signed [V_BITS - 1:0] offset_q,
    input logic signed [V_BITS - 1:0] adc2_min_q,
    input logic signed [V_BITS - 1:0] adc2_max_q,
    output logic [OUT_BITS - 1:0] dout,
    output logic dout_valid,
    output logic clip_low,
    output logic clip_high,
    output logic acc_ovf,
    output logic gain_err,
    output logic adc2_ovf,
    output logic busy,
    output logic [31:0] result_sample_id,
    output logic [4:0] result_flags
);

  localparam int N_U = P_N_MAIN + P_N_SUB;
  localparam int SUM_BITS = 64; // SigmaW < 2^60（契约 §5）留 4 位余量
  localparam int W_RAIL = SUM_BITS + 2; // |rails| <= 3*2^60 < 2^62
  localparam int W_OP3 = SUM_BITS + int'(V_BITS) + 2; // 130：total * inj_q
  localparam int W_WIDE = W_OP3 + 2; // 132：受检操作数的精确宽度
  localparam int W_S1 = int'(ACC_BITS) + 1; // 97
  localparam int W_SHIFT = int'(ACC_BITS) + int'(OUT_BITS) + 1; // 117
  localparam int W_A = int'(ACC_BITS) - (int'(V_FRAC) + 1); // 63：shifted >>> 33
  localparam int W_TOP = W_SHIFT - int'(ACC_BITS) + 1; // 22：shifted[116:95]
  localparam int DIT_BEG = P_DIT_END - P_DIT_N;
  localparam int N_CYC = (W_A + P_STAGES - 1) / P_STAGES;
  // 固定延迟。口径见 docs/rtl/P2_INTERFACE.md §4.3：从 `start` 那一拍起算 dout_valid
  // 所在沿与接受 start 沿之间的完整时钟间隔 => N_CYC + 2 = 11（P_STAGES=7）。
  // 由 sim/tb/p2_tb.sv 的 T4 **实测**；常量与实测不符时 T4 报错。
  localparam int RECON_LAT = N_CYC + 2;

  // ---- Width-qualified constants; signed clipping bounds stay signed. ----
  localparam logic [W_WIDE - 1:0] LIM_ONE = {{(W_WIDE - 1){1'b0}}, 1'b1};
  localparam logic [W_WIDE - 1:0] LIM_HI_U = LIM_ONE << (int'(ACC_BITS) - 1);
  localparam logic [W_WIDE - 1:0] LIM_LO_U = ~LIM_HI_U + LIM_ONE;
  // Use signed bounds for the signed quotient; unsigned bounds break negatives.
  localparam logic signed [W_A - 1:0] ZERO_A = {W_A{1'b0}};
  localparam logic signed [W_A - 1:0] OUT_CNT = {{(W_A - 1){1'b0}}, 1'b1} << OUT_BITS;
  localparam logic [SUM_BITS - 1:0] GAIN_MAX = {{(SUM_BITS - 1){1'b0}}, 1'b1}
      << (int'(ACC_BITS) - 1 - (int'(V_FRAC) + 1));

  //=========================================================================
  // 1) Total weight, effective gain and signed rails (combinational)
  //=========================================================================
  wire [SUM_BITS - 1:0] sum_W, sum_Wa;
  wire signed [W_RAIL - 1:0] rails;
  wire invalid_slice;
  cal_weight_reduce #(
      .P_N_ACTIVE(P_N_ACTIVE),
      .P_N_MAIN(P_N_MAIN),
      .P_N_SUB(P_N_SUB),
      .P_N_SLICES(P_N_SLICES),
      .P_DIT_N(P_DIT_N),
      .P_DIT_END(P_DIT_END),
      .P_USE_ROW_TOTALS(P_USE_ROW_TOTALS),
      .SUM_BITS(SUM_BITS),
      .W_RAIL(W_RAIL)
  ) u_weight_reduce (
      .sampling_mask_en(sampling_mask_en),
      .slice_id(slice_id),
      .main_on(main_on),
      .sub_on(sub_on),
      .dither_rail(dither_rail),
      .w_rom(w_rom),
      .row_total(row_total),
      .sum_W(sum_W),
      .sum_Wa(sum_Wa),
      .rails(rails),
      .invalid_slice(invalid_slice)
  );

  //=========================================================================
  // 2) 后端译码（M9）
  //=========================================================================
  logic signed [V_BITS - 1:0] fine_c;
  logic adc2_o;

  adc2_dec #(.P_ADC2_BITS(P_ADC2_BITS)) u_adc2 (
      .adc2_code(adc2_code),
      .adc2_min_q(adc2_min_q),
      .adc2_max_q(adc2_max_q),
      .fine_q(fine_c),
      .ovf(adc2_o)
  );

  //=========================================================================
  // 3) 流水寄存器（阶段 A 的锁存目标）
  //=========================================================================
  logic [31:0] sample_id_r;
  logic signed [V_BITS - 1:0] fine_r, off_r, inj_r;
  logic [SUM_BITS - 1:0] gain_s, total_s;
  logic signed [W_RAIL - 1:0] rails_s;
  logic stage_b, ovf_pend, gerr_pend, a2ovf_pend, bad_slice_r;

  //=========================================================================
  // 4) 受检算术（组合，在 stage B 那一拍求值）
  //=========================================================================
  wire [W_A - 1:0] a1;
  wire any_ovf;
  cal_residue_mac u_residue_mac (
      .fine_r(fine_r),
      .off_r(off_r),
      .inj_r(inj_r),
      .gain_s(gain_s),
      .total_s(total_s),
      .rails_s(rails_s),
      .a1(a1),
      .any_ovf(any_ovf)
  );

  //=========================================================================
  // 5) 除法器（唯一的运行时除法）
  //=========================================================================
  logic div_busy, div_done, div_err;
  logic signed [W_A - 1:0] div_q;
  logic clip_lo_c, clip_hi_c;

  div_floor #(
      .P_W_A(W_A),
      .P_W_D(SUM_BITS),
      .P_STAGES(P_STAGES)
  ) u_div (
      .clk(clk),
      .rst_n(rst_n && cfg_ready),
      .start(stage_b && cfg_ready),
      .a(a1),
      .d(gain_s),
      .q(div_q),
      .err(div_err),
      .busy(div_busy),
      .done(div_done)
  );

  // Both comparison operands are signed. In particular, -1 must clip low,
  // not become a large unsigned value and clip high.
  assign clip_lo_c = (div_q < ZERO_A);
  assign clip_hi_c = (div_q >= OUT_CNT);
  assign busy = stage_b | div_busy;

  wire [OUT_BITS - 1:0] candidate_word = clip_lo_c ? '0 :
      clip_hi_c ? '1 : div_q[OUT_BITS - 1:0];
  cal_output_stage u_output (
      .clk(clk),
      .rst_n(rst_n),
      .cfg_ready(cfg_ready),
      .clr_ovf(clr_ovf),
      .complete(div_done),
      .candidate(candidate_word),
      .candidate_sample_id(sample_id_r),
      .result_sample_id(result_sample_id),
      .result_flags(result_flags),
      .candidate_clip_low(clip_lo_c),
      .candidate_clip_high(clip_hi_c),
      .candidate_acc_ovf(ovf_pend),
      .candidate_gain_err(gerr_pend || div_err),
      .candidate_adc2_ovf(a2ovf_pend),
      .dout(dout),
      .dout_valid(dout_valid),
      .clip_low(clip_low),
      .clip_high(clip_high),
      .acc_ovf(acc_ovf),
      .gain_err(gain_err),
      .adc2_ovf(adc2_ovf)
  );

  //=========================================================================
  // 6) 主状态机
  //=========================================================================
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      sample_id_r <= '0;
      fine_r <= {V_BITS{1'b0}};
      off_r <= {V_BITS{1'b0}};
      inj_r <= {V_BITS{1'b0}};
      gain_s <= {SUM_BITS{1'b0}};
      total_s <= {SUM_BITS{1'b0}};
      rails_s <= {W_RAIL{1'b0}};
      stage_b <= 1'b0;
      bad_slice_r <= 1'b0;
      ovf_pend <= 1'b0;
      gerr_pend <= 1'b0;
      a2ovf_pend <= 1'b0;
    end else begin
      // ---- 阶段 A：锁存本拍的求和与输入 ----
      if (cfg_ready && start && !busy) begin
        sample_id_r <= sample_id;
        bad_slice_r <= invalid_slice;
        fine_r <= fine_c;
        off_r <= offset_q;
        inj_r <= inj_q;
        gain_s <= sum_Wa;
        total_s <= sum_W;
        rails_s <= rails;
        a2ovf_pend <= adc2_o;
        stage_b <= 1'b1;
      end else if (stage_b) begin
        // These tests must use stage-A registers of the accepted sample.
        // Evaluating/latching them on the start edge would observe the prior
        // sample and shift error attribution by one conversion.
        ovf_pend <= any_ovf;
        gerr_pend <= bad_slice_r || (gain_s == {SUM_BITS{1'b0}});
        stage_b <= 1'b0;
      end

      // 配置取消最后覆盖本沿的待提交资格与ID。
      if (!cfg_ready) begin
        stage_b <= 1'b0;
        ovf_pend <= 1'b0;
        gerr_pend <= 1'b0;
        a2ovf_pend <= 1'b0;
        bad_slice_r <= 1'b0;
        sample_id_r <= '0;
      end
    end
  end

endmodule
