// =============================================================================
// 文件名     : caaz20_pair.sv
// 模块名     : caaz20_pair
// 版本       : v1.0
// 功能       : CAAZ 相邻偶奇两笔的数字配对
//
// 设计说明   :
//   * 偶笔锁存、奇笔作差；floor((even−odd)/2) 用有符号算术右移实现，负差不能截断。
//   * fault_o 锁存直到 clear；raw bypass、坏样本与错 ID 各有独立退出路径。
//   * 单时钟域 clk_i；复位、清空、禁用优先于受理，异常路径不得静默接受。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
// -------------------------------------------------------------------------
// 端口按功能分组，slot 0 在最低位；总线位宽与定点格式见同目录 SPEC。
// -------------------------------------------------------------------------
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

// =========================================================================
// 2. 组合输出与连续性
// =========================================================================
// 输出在无效时清零，外部不得把上一拍残留当成新一笔结果。
  assign active_mode = mode_locked_q ? caaz_enabled_q : caaz_enable_i;
  assign pair_difference =
      $signed({even_code_q[19], even_code_q}) - $signed({raw_code_i[19], raw_code_i});
  assign out_code_o = $signed(out_difference_o[20:1]);

// =========================================================================
// 3. 组合逻辑与派生信号
// =========================================================================
// 所有分支必须给默认值，避免综合出锁存器；非法状态回落到安全输出。
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

// =========================================================================
// 4. 同步时序与状态转移
// =========================================================================
// 复位优先于受理；在途事务在清空/禁用同沿丢弃，ID 生命周期按样本绑定。
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
