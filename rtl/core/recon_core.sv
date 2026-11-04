//===========================================================================
// recon_core.sv -- 物理权重校正的数据捕获、算术和输出提交控制
//===========================================================================
// 数据流：物理权重归约 + ADC2 解码 -> stage-A 快照 -> 残差 MAC ->
//          迭代 floor 除法 -> 饱和裁剪 -> 输出提交。
// num = (F - O)*2^W_FRAC - rails*2^V_FRAC - total*I；
// word = floor(((num + gain*2^V_FRAC)*2^OUT_BITS >>> (V_FRAC+1))/gain)。
// 输出为 OUT_BITS 位 offset-binary 码，默认 20 位。此模块不生成 DEM、
// 不存储权重、不估计校准系数；论文/专利披露与实现差异见 ADR 0014/0018。
//
// 捕获边界：仅 cfg_ready && start && !busy 接受一次转换，同时锁存已归约
// 的权重、后端电压、offset、注入、sample_id 和后端异常。之后所有数值与
// 标志都来自这次快照；调用方必须保证配置期间权重稳定。
//
// 控制状态（编码为 stage_b 与除法器 run，没有另设枚举 FSM）：
//   IDLE：stage_b=0 且 div_busy=0，允许 start 捕获；
//   MAC_LAUNCH：stage_b=1，下一沿捕获本样本 MAC 异常并启动除法；
//   DIV_RUN：div_busy=1，busy 期间不接受 start；
//   COMMIT：div_done 的下一沿提交旧结果，此时 busy 已为 0，可同时接受
//           下一次 start。sample_id_r 的非阻塞赋值保证提交使用旧样本 ID。
// cfg_ready 下降取消流水：清 stage_b/待提交事件，除法器同步复位，输出清零。
// rst_n 为同步低有效复位；clear 错误不取消转换，由输出模块处理粘滞事件。
//
// 数值契约：权重 Q30 以 48 位无符号整数存储（合法权重 <2^47）；电压
// Q32 为 64 位有符号整数。rails 为有符号，gain/total 为无符号。
// 默认受检范围 [-2^95,2^95)，MAC 越界时置 acc_ovf；gain==0、无效 slice
// 或除法器 err 置 gain_err。两类错误仍产生有效的完成事件、保留最后合法字；
// 本次 result_flags 与粘滞 flags 的意义不同，下游必须保留 sample_id。
// 裁剪比较的两侧均为 signed，负 word 只产生 clip_low，不能改成无符号比较。
//
// 延迟：N_CYC=ceil(63/P_STAGES)，接受 start 到 dout_valid 为 N_CYC+2
// 个完整时钟间隔；默认 P_STAGES=7 为 11 拍，生产 P_STAGES=5 为 15 拍。
// 每次 busy 占用期间不接受新 start；输出提交沿可以接收下一次请求。
// 综合：前级组合归约/MAC 与组内 P_STAGES 级恢复除法均需单独查看 STA。
// 改 P_STAGES 要重算 16 相位预算；位宽/捕获沿/树拓扑不得凭面积估算删减。
// 来源：docs/rtl/RTL_ARITHMETIC_CONTRACT.md；p2_tb/全顶层回归。
//===========================================================================
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

      // ---- 未配置：输出强制为 0（放在最后，优先级最高）----
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
