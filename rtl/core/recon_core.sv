// 模块：recon_core；物理权重校正的接受、快照、算术与结果提交。
// 数据流：T/G/rails归约与ADC2码中心→stage-A快照→残差MAC→floor除法→饱和/ID/flags提交。
// 端口/单位：W为48位unsigned Q30；电压为64位signed Q32；输出为20位offset-binary码。
// 时钟/复位：clk上升沿、同步低有效rst_n；cfg_ready=0同步取消在途计算并清有效输出。
// 接受：cfg_ready&&start&&!busy冻结同一样本的所有数据；其后不得混入下一笔实时mask或系数。
// 周期：ceil(63/P_STAGES)+2完整拍；默认P7为11，P5为15；busy期间拒绝start。
// 同沿规则：div_done之后的提交沿可同时接受下一start，NBA保证提交使用旧sample_id/flags。
// 异常：acc_ovf/gain_err仍产生完成ID/flags但保留合法字；adc2_ovf单独上报；负商只向低端clip。
// 精度：96位受检域、宽MAC临时值及63位floor商；P_USE_ROW_TOTALS仅使用精确配置缓存。
// 节点：F/offset是ADC2输入（RA输出）域的V/Vfs Q32；inj是已知ADC输入域的V/Vfs Q32。
// 重构：num=(F-O)*2^30-rails*2^32-T*inj；word=floor((num+G*2^32)*2^20/(G*2^33))。
// G是本样本所装有效权重的信号和，本核仅支持已独立证明的AllSIGNAL子集，G=T；其归一化不代表物理估计无
// 误差。
// 权重量化在片外完成；运行时只有ADC2码中心用ties-to-even，其后移位与除法均为floor。
// 来源：RTL_ARITHMETIC_CONTRACT.md、ADR0014/0018；不生成DEM、存权重或运行学习器。
`timescale 1ns/1ps
`include "rtl_params.vh"

module recon_core #(
    parameter int P_N_ACTIVE = int'(N_ACTIVE),
    parameter int P_N_MAIN = int'(N_UNIT_MAIN),
    parameter int P_N_SUB = int'(N_UNIT_SUB),
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_ADC2_BITS = int'(ADC2_BITS),
    parameter int P_STAGES = 7,
    parameter bit P_USE_ROW_TOTALS = 0
) (
    input logic clk,
    input logic rst_n,
    input logic cfg_ready,
    input logic start,
    input logic [31:0] sample_id,
    input logic clr_ovf,
    input logic [P_N_ACTIVE - 1:0][4:0] slice_id,
    input logic [P_N_ACTIVE - 1:0][P_N_MAIN - 1:0] main_on,
    input logic [P_N_ACTIVE - 1:0][P_N_SUB - 1:0] sub_on,
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

  localparam int SUM_BITS = 64; // SUM_BITS: SigmaW < 2^60（契约 §5）留 4 位余量
  localparam int W_RAIL = SUM_BITS + 2; // W_RAIL: |rails| <= 3*2^60 < 2^62
  localparam int W_A = int'(ACC_BITS) - (int'(V_FRAC) + 1); // W_A: 63：shifted >>> 33
  // 延迟按接受start沿到dout_valid沿的完整间隔计数：阶段A快照、阶段B启动除法、 N_CYC次除法和提交的组
  // 合合同为N_CYC+2；P7=11、P5=15，必须能容纳16tick帧。
  // 对应延迟须由独立数学/协议夹具验证；这不是目标工艺的组合路径时序证明。

  // Width-qualified constants; signed clipping bounds stay signed. Use signed bounds for the signed
  // quotient; unsigned bounds break negatives.
  localparam logic signed [W_A - 1:0] ZERO_A = {W_A{1'b0}};
  localparam logic signed [W_A - 1:0] OUT_CNT = {{(W_A - 1){1'b0}}, 1'b1} << OUT_BITS;

  // 1) Total weight, effective gain and signed rails (combinational)
  wire [SUM_BITS - 1:0] sum_W, sum_Wa;
  wire signed [W_RAIL - 1:0] rails;
  wire invalid_slice;
  cal_weight_reduce #(
      .P_N_ACTIVE(P_N_ACTIVE),
      .P_N_MAIN(P_N_MAIN),
      .P_N_SUB(P_N_SUB),
      .P_N_SLICES(P_N_SLICES),
      .P_USE_ROW_TOTALS(P_USE_ROW_TOTALS),
      .SUM_BITS(SUM_BITS),
      .W_RAIL(W_RAIL)
  ) u_weight_reduce (
      .slice_id(slice_id),
      .main_on(main_on),
      .sub_on(sub_on),
      .w_rom(w_rom),
      .row_total(row_total),
      .sum_W(sum_W),
      .sum_Wa(sum_Wa),
      .rails(rails),
      .invalid_slice(invalid_slice)
  );

  // 2) 后端译码（M9）
  logic signed [V_BITS - 1:0] fine_c;
  logic adc2_o;

  adc2_dec #(.P_ADC2_BITS(P_ADC2_BITS)) u_adc2 (
      .adc2_code(adc2_code),
      .adc2_min_q(adc2_min_q),
      .adc2_max_q(adc2_max_q),
      .fine_q(fine_c),
      .ovf(adc2_o)
  );

  // 3) 流水寄存器（阶段 A 的锁存目标）
  logic [31:0] sample_id_r;
  logic signed [V_BITS - 1:0] fine_r, off_r, inj_r;
  logic [SUM_BITS - 1:0] gain_s, total_s;
  logic signed [W_RAIL - 1:0] rails_s;
  logic stage_b, ovf_pend, gerr_pend, a2ovf_pend, bad_slice_r;

  // 4) 受检算术（组合，在 stage B 那一拍求值）
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

  // 5) 除法器（唯一的运行时除法）
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

  // MAC先算术右移33位，相当于第一次floor；div_floor再除正G，等价于一次总分母floor。
  // 负商仍保留signed直到饱和比较，不能先截成20位把负值变成高端大码。
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

  // 6) 主状态机
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
      // 阶段 A：锁存本拍的求和与输入
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
        // 检错必须读阶段A已冻结的同笔数据；若在start沿读取这些寄存器，NBA只会看到前一笔数值，异常和
        // sample ID就会错代。busy期间不接受新的start。
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
