// 模块：cal_sample_context；当前residue到下一周期fine的物理样本上下文。
// 职责：current→residue→fine，冻结ID、coarse/sid/dither、物理mask、rail、注入及CAAZ tag。
// CAAZ的34位{chop,AZ代,AZ资格}与同一residue保存，不从当前bank反推旧样本。
// 端口/单位：ID与状态为整数；main/sub/rail为物理选择位；injection为有符号Q32。
// 时钟/复位：clk上升沿、同步低有效rst_n；disable丢弃两份在途packet并清valid。
// 周期：默认phase9保存当前residue，下一周期quiet_sample转交给共享fine SAR。
// fine_valid是这一代context的有效电平，保持到下一次quiet，并非单周期完成脉冲。
// 此处还没有ADC2原码；下一周期共享fine SAR完成后，上层才把码和这份旧context一起重构。
// 同沿规则：capture和quiet若同时有效，fine读取旧residue，后执行的residue_valid清零优先，无旁路。
// 异常：current_bad随residue冻结；analog_bad在fine转交时采样，并与旧residue_bad合并。
// 来源：ADR0018；packet只携带数字决定，不携带模拟residue电压或学习系数。
`include "rtl_params.vh"
module cal_sample_context (
    input wire clk, rst_n, enable, residue_capture, quiet_sample, current_valid,
    input wire [31:0] current_id,
    input wire [8:0] current_coarse, current_sid,
    input wire signed [7:0] current_dither,
    input wire current_chop_neg,
    input wire [31:0] current_az_pair_id,
    input wire current_az_valid,
    input wire [N_ACTIVE - 1:0][4:0] current_slices,
    input wire [N_ACTIVE - 1:0][N_UNIT_MAIN - 1:0] current_main,
    input wire [N_ACTIVE - 1:0][N_UNIT_SUB - 1:0] current_sub,
    input wire [2 * DITHER_UNITS_RANGE - 1:0] current_rails,
    input wire current_sampling,
    input wire signed [63:0] current_injection,
    input wire analog_bad, current_bad,
    output logic fine_valid,
    output logic [31:0] fine_id,
    output logic [8:0] fine_coarse, fine_sid,
    output logic signed [7:0] fine_dither,
    output logic fine_chop_neg,
    output logic [31:0] fine_az_pair_id,
    output logic fine_az_valid,
    output logic [N_ACTIVE - 1:0][4:0] fine_slices,
    output logic [N_ACTIVE - 1:0][N_UNIT_MAIN - 1:0] fine_main,
    output logic [N_ACTIVE - 1:0][N_UNIT_SUB - 1:0] fine_sub,
    output logic [2 * DITHER_UNITS_RANGE - 1:0] fine_rails,
    output logic fine_sampling, fine_analog_bad,
    output logic signed [63:0] fine_injection
);
  // slot顺序与main/sub mask一起冻结。物理slice ID选权重，sid只解释开关置换， sample
  // ID负责时域配对；缺样时不能拿当前bank或当前sid补写旧样本元数据。
  localparam int CW = 32 + 9 + 9 + 8 + 34 + int'(N_ACTIVE) * (5 + int'(N_UNIT_MAIN) + int'(N_UNIT_SUB)) +
      2 * int'(DITHER_UNITS_RANGE) + 1 + 64;
  logic [CW - 1:0] residue;
  logic residue_valid, residue_bad;

  always_ff @(posedge clk) begin
    // Reset and disable are synchronous and discard both in-flight packets.
    if (!rst_n || !enable) begin
      residue <= '0;
      residue_valid <= 0;
      residue_bad <= 0;
      fine_valid <= 0;
      fine_id <= '0;
      fine_coarse <= '0;
      fine_sid <= '0;
      fine_dither <= '0;
      fine_chop_neg <= 1'b0;
      fine_az_pair_id <= '0;
      fine_az_valid <= 1'b0;
      fine_slices <= '0;
      fine_main <= '0;
      fine_sub <= '0;
      fine_rails <= '0;
      fine_sampling <= 0;
      fine_injection <= '0;
      fine_analog_bad <= 0;
    end else begin
      if (residue_capture) begin
        residue_valid <= current_valid;
        residue_bad <= current_bad;
        residue <= {current_id, current_coarse, current_sid, current_dither,
            current_chop_neg, current_az_pair_id, current_az_valid,
            current_slices, current_main, current_sub,
            current_rails, current_sampling, current_injection};
      end
      // 两个if都读沿前旧值。若外部误让capture/quiet同沿，fine仍取旧residue，
      // 最后residue_valid清零覆盖本沿capture的资格，不提供新样本直通旁路。
      // current_bad随RDAC决定先存；analog_bad在旧残差被fine采样时再取，二者时点不同。
      if (quiet_sample) begin
        fine_valid <= residue_valid;
        {fine_id, fine_coarse, fine_sid, fine_dither, fine_chop_neg, fine_az_pair_id,
            fine_az_valid, fine_slices, fine_main,
            fine_sub, fine_rails, fine_sampling, fine_injection} <= residue;
        fine_analog_bad <= analog_bad || residue_bad;
        residue_valid <= 0;
      end
    end
  end
endmodule
