// CAAZ 采样控制与完整重构码配对。换相、CBIAS 开关和实际捕获／保持由模拟宏执行。
// 所有事务属于同一 clk_i；上升沿受理后发控制或结果脉冲。同步复位、清空或禁用取消同沿事务。
// 依据 Wang 等 VLSI 2025 Fig.5/6；端口、优先级和定点格式见 task3/SPEC.md。
`timescale 1ns/1ps
module caaz20_pair (
    input  logic               clk_i,
    input  logic               rst_ni,
    input  logic               clear_i,
    input  logic               enable_i,
    input  logic               caaz_enable_i,
    input  logic               acq_valid_i,
    input  logic        [31:0] acq_sample_id_i,
    input  logic               raw_valid_i,
    input  logic        [31:0] raw_sample_id_i,
    input  logic signed [19:0] raw_code_i,
    input  logic               raw_bad_i,
    input  logic               raw_chop_neg_i,
    input  logic        [30:0] raw_az_pair_id_i,
    input  logic               raw_az_ok_i,
    output logic               acq_ctrl_valid_o,
    output logic        [31:0] acq_ctrl_sample_id_o,
    output logic               acq_chop_neg_o,
    output logic               acq_az_capture_o,
    output logic               acq_az_hold_o,
    output logic        [30:0] acq_az_pair_id_o,
    output logic               out_valid_o,
    output logic signed [19:0] out_code_o,
    output logic        [31:0] out_sample_id_o,
    output logic        [31:0] out_first_sample_id_o,
    output logic        [30:0] out_az_pair_id_o,
    output logic               out_pair_o,
    output logic signed [20:0] out_difference_o,
    output logic               fault_o,
    output logic               fault_pulse_o,
    output logic         [2:0] fault_code_o
);
  localparam logic [2:0] ErrMode = 3'd1;
  localparam logic [2:0] ErrAcqId = 3'd2;
  localparam logic [2:0] ErrRawId = 3'd3;
  localparam logic [2:0] ErrRawBad = 3'd4;
  localparam logic [2:0] ErrPolarity = 3'd5;
  localparam logic [2:0] ErrAz = 3'd6;

  logic mode_locked_q;
  logic caaz_enabled_q;
  logic [31:0] next_acq_id_q;
  logic [31:0] next_raw_id_q;
  logic signed [19:0] even_code_q;
  logic signed [20:0] pair_difference;
  logic active_mode;
  logic [2:0] reject_reason;

  // 每段进位并行从旧 ID 取得，避免 32 位串行进位影响数字时序预算。
  function automatic logic [31:0] next_id(input logic [31:0] id);
    next_id[ 7: 0] = id[ 7: 0] + 8'd1;
    next_id[15: 8] = id[15: 8] + {7'd0, &id[ 7:0]};
    next_id[23:16] = id[23:16] + {7'd0, &id[15:0]};
    next_id[31:24] = id[31:24] + {7'd0, &id[23:0]};
  endfunction

  assign active_mode = mode_locked_q ? caaz_enabled_q : caaz_enable_i;
  assign pair_difference =
      $signed({even_code_q[19], even_code_q}) - $signed({raw_code_i[19], raw_code_i});
  assign out_code_o = $signed(out_difference_o[20:1]);

  always_comb begin
    reject_reason = 3'd0;
    if (mode_locked_q && (caaz_enable_i != caaz_enabled_q)) begin
      reject_reason = ErrMode;
    end else if (acq_valid_i && (acq_sample_id_i != next_acq_id_q)) begin
      reject_reason = ErrAcqId;
    end else if (raw_valid_i) begin
      if ((raw_sample_id_i != next_raw_id_q) || (next_raw_id_q == next_acq_id_q)) begin
        reject_reason = ErrRawId;
      end else if (raw_bad_i) begin
        reject_reason = ErrRawBad;
      end else if (active_mode) begin
        if (raw_chop_neg_i != raw_sample_id_i[0]) begin
          reject_reason = ErrPolarity;
        end else if (!raw_az_ok_i || (raw_az_pair_id_i != raw_sample_id_i[31:1])) begin
          reject_reason = ErrAz;
        end
      end
    end
  end

  always_ff @(posedge clk_i) begin
    if (!rst_ni || clear_i || !enable_i) begin
      mode_locked_q <= 1'b0;
      caaz_enabled_q <= 1'b0;
      next_acq_id_q <= '0;
      next_raw_id_q <= '0;
      even_code_q <= '0;
      acq_ctrl_valid_o <= 1'b0;
      acq_ctrl_sample_id_o <= '0;
      acq_chop_neg_o <= 1'b0;
      acq_az_capture_o <= 1'b0;
      acq_az_hold_o <= 1'b0;
      acq_az_pair_id_o <= '0;
      out_valid_o <= 1'b0;
      out_sample_id_o <= '0;
      out_first_sample_id_o <= '0;
      out_az_pair_id_o <= '0;
      out_pair_o <= 1'b0;
      out_difference_o <= '0;
      fault_o <= 1'b0;
      fault_pulse_o <= 1'b0;
      fault_code_o <= '0;
    end else begin
      acq_ctrl_valid_o <= 1'b0;
      out_valid_o <= 1'b0;
      fault_pulse_o <= 1'b0;
      if (!fault_o) begin
        if (reject_reason != 0) begin
          fault_o <= 1'b1;
          fault_pulse_o <= 1'b1;
          fault_code_o <= reject_reason;
        end else begin
          if (acq_valid_i) begin
            mode_locked_q <= 1'b1;
            caaz_enabled_q <= active_mode;
            next_acq_id_q <= next_id(next_acq_id_q);
            acq_ctrl_valid_o <= 1'b1;
            acq_ctrl_sample_id_o <= acq_sample_id_i;
            acq_chop_neg_o <= active_mode && acq_sample_id_i[0];
            acq_az_capture_o <= active_mode && !acq_sample_id_i[0];
            acq_az_hold_o <= active_mode && acq_sample_id_i[0];
            acq_az_pair_id_o <= active_mode ? acq_sample_id_i[31:1] : '0;
          end
          if (raw_valid_i) begin
            next_raw_id_q <= next_id(next_raw_id_q);
            if (active_mode && !raw_sample_id_i[0]) begin
              even_code_q <= raw_code_i;
            end else begin
              out_valid_o <= 1'b1;
              out_pair_o <= active_mode;
              out_sample_id_o <= raw_sample_id_i;
              out_first_sample_id_o <= active_mode ? {raw_sample_id_i[31:1], 1'b0}
                                                   : raw_sample_id_i;
              out_az_pair_id_o <= active_mode ? raw_sample_id_i[31:1] : '0;
              out_difference_o <= active_mode ? pair_difference : $signed({raw_code_i, 1'b0});
            end
          end
        end
      end
    end
  end
endmodule
