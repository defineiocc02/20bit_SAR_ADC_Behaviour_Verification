// 模块：cal_sample_context；current→residue→fine的实际物理决定和样本身份。
// control_word/state在所属acquisition边界已冻结；main/sub为实际已接受RDAC加载。
// 此包不携带C/G/heldV、a/term数组或估计器真值；common_q只表示显式绑定的已知项。
// clk上升沿同步低有效reset；disable取消两包资格。capture/quiet同沿时fine取旧包，最后的residue_valid
// 清零覆盖新capture资格，无同沿直通。current_bad随residue保存， analog_bad在旧residue转fine时采样。
// CAAZ实际chop/AZ代/资格随同一包保存。
`timescale 1ns/1ps
`include "rtl_params.vh"
module cal_sample_context (
    input wire clk, rst_n, enable, residue_capture, quiet_sample, current_valid,
    input wire [31:0] current_id,
    input wire [8:0] current_coarse,
    input wire [STATE_BITS-1:0] current_state,
    input wire [CONTROL_WORD_BITS-1:0] current_control_word,
    input wire current_chop_neg,
    input wire [31:0] current_az_pair_id,
    input wire current_az_valid,
    input wire [N_ACTIVE-1:0][4:0] current_slices,
    input wire [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] current_main,
    input wire [N_ACTIVE-1:0][N_UNIT_SUB-1:0] current_sub,
    input wire signed [V_BITS-1:0] current_known_common_q,
    input wire analog_bad, current_bad,
    output logic fine_valid,
    output logic [31:0] fine_id,
    output logic [8:0] fine_coarse,
    output logic [STATE_BITS-1:0] fine_state,
    output logic [CONTROL_WORD_BITS-1:0] fine_control_word,
    output logic fine_chop_neg,
    output logic [31:0] fine_az_pair_id,
    output logic fine_az_valid,
    output logic [N_ACTIVE-1:0][4:0] fine_slices,
    output logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] fine_main,
    output logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0] fine_sub,
    output logic fine_analog_bad,
    output logic signed [V_BITS-1:0] fine_known_common_q
);
  localparam int PACKET_BITS = 32 + 9 + int'(STATE_BITS) + int'(CONTROL_WORD_BITS) + 34 +
      int'(N_ACTIVE) * (5 + int'(N_UNIT_MAIN) + int'(N_UNIT_SUB)) + int'(V_BITS);
  logic [PACKET_BITS-1:0] residue;
  logic residue_valid, residue_bad;
  always_ff @(posedge clk) begin
    if (!rst_n || !enable) begin
      residue <= '0;
      residue_valid <= 1'b0;
      residue_bad <= 1'b0;
      fine_valid <= 1'b0;
      fine_id <= '0;
      fine_coarse <= '0;
      fine_state <= '0;
      fine_control_word <= '0;
      fine_chop_neg <= 1'b0;
      fine_az_pair_id <= '0;
      fine_az_valid <= 1'b0;
      fine_slices <= '0;
      fine_main <= '0;
      fine_sub <= '0;
      fine_known_common_q <= '0;
      fine_analog_bad <= 1'b0;
    end else begin
      if (residue_capture) begin
        residue_valid <= current_valid;
        residue_bad <= current_bad;
        residue <= {current_id, current_coarse, current_state, current_control_word,
                    current_chop_neg, current_az_pair_id, current_az_valid,
                    current_slices, current_main, current_sub, current_known_common_q};
      end
      if (quiet_sample) begin
        fine_valid <= residue_valid;
        {fine_id, fine_coarse, fine_state, fine_control_word,
         fine_chop_neg, fine_az_pair_id, fine_az_valid, fine_slices,
         fine_main, fine_sub, fine_known_common_q} <= residue;
        fine_analog_bad <= analog_bad || residue_bad;
        residue_valid <= 1'b0;
      end
    end
  end
endmodule
