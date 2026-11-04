// 模块：cal_sample_context；当前residue到下一周期fine的物理样本上下文。
// 职责：current→residue packet→fine packet，冻结ID、coarse/sid/dither、物理mask、rail与注入。
// 端口/单位：ID与状态为整数；main/sub/rail为物理选择位；injection为有符号Q32。
// 时钟/复位：clk上升沿、同步低有效rst_n；disable丢弃两份在途packet并清valid。
// 周期：默认phase9保存当前residue，下一周期quiet_sample转交给共享fine SAR。
// 同沿规则：capture和quiet若同时有效，fine读取旧residue，后执行的residue_valid清零优先，无旁路。
// 异常：current_bad随residue冻结；analog_bad在fine转交时采样，并与旧residue_bad合并。
// 来源：ADR0018；packet只携带数字决定，不携带模拟residue电压或学习系数。
`include "rtl_params.vh"
module cal_sample_context (
    input wire clk, rst_n, enable, residue_capture, quiet_sample, current_valid,
    input wire [31:0] current_id,
    input wire [8:0] current_coarse, current_sid,
    input wire signed [7:0] current_dither,
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
    output logic [N_ACTIVE - 1:0][4:0] fine_slices,
    output logic [N_ACTIVE - 1:0][N_UNIT_MAIN - 1:0] fine_main,
    output logic [N_ACTIVE - 1:0][N_UNIT_SUB - 1:0] fine_sub,
    output logic [2 * DITHER_UNITS_RANGE - 1:0] fine_rails,
    output logic fine_sampling, fine_analog_bad,
    output logic signed [63:0] fine_injection
);
  // Packed packet layout is local; named ports preserve independent debug.
  localparam int CW = 32 + 9 + 9 + 8 + int'(N_ACTIVE) * (5 + int'(N_UNIT_MAIN) + int'(N_UNIT_SUB)) +
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
            current_slices, current_main, current_sub,
            current_rails, current_sampling, current_injection};
      end
      // Both if blocks use pre-edge values because assignments are nonblocking.
      // If both strobes were asserted, fine receives the OLD residue and the
      // later residue_valid assignment clears it. The production phase schedule
      // keeps these strobes apart; no same-edge capture bypass is implemented.
      // analog_bad is sampled at transfer, while current_bad was frozen earlier.
      if (quiet_sample) begin
        fine_valid <= residue_valid;
        {fine_id, fine_coarse, fine_sid, fine_dither, fine_slices, fine_main,
            fine_sub, fine_rails, fine_sampling, fine_injection} <= residue;
        fine_analog_bad <= analog_bad || residue_bad;
        residue_valid <= 0;
      end
    end
  end
endmodule
