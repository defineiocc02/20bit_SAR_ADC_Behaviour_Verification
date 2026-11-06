// 模块：task1_slice_scheduler
// 职责：从 18 个物理切片中分配有序采集组，并在下一次接受沿将其整体晋升为转换组。
//
// eligible_i：高电平表示对应切片可重新进入采集。宏释放的确认与同步由上游负责。
// advance_i：请求分配。候选不足八片时 stall_o 拉高一拍，所有已分配状态保持。
// slot_accepted_o：本沿成功分配的事件脉冲。conv_valid_o 说明转换组有历史采样，
// 在空拍及拒绝时保持。因此下游以两者同时有效作为新转换组的接受资格。
//
// 所有端口同属 clk_i 域，在上升沿使用旧状态。同步低有效复位、clear_i 和 disable
// 清空分配历史并恢复默认种子，优先于种子载入和 advance。首个接受沿只建立采集组。
// ID 从 0 开始并按模 2^32 递增。每组 ID 紧凑排列，slot 0 位于最低五位。
//
// 固定模式从物理 ID 0 扫描。随机模式从旧 LFSR 高字节取模得到的游标开始扫描，
// 仅在成功分配时推进八步。这是可重放的环形优先选择，游标的统计分布有取模偏差。
`timescale 1ns/1ps
module task1_slice_scheduler (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        clear_i,
    input  logic        enable_i,
    input  logic        advance_i,
    input  logic [17:0] eligible_i,
    input  logic        shuffle_i,
    input  logic        seed_load_i,
    input  logic [31:0] seed_i,
    output logic [39:0] acq_ids_o,
    output logic [39:0] conv_ids_o,
    output logic [17:0] acq_mask_o,
    output logic [17:0] conv_mask_o,
    output logic        acq_valid_o,
    output logic        conv_valid_o,
    output logic [31:0] acq_sample_id_o,
    output logic [31:0] conv_sample_id_o,
    output logic        slot_accepted_o,
    output logic        stall_o,
    output logic        seed_error_o,
    output logic [31:0] lfsr_state_o
);
  localparam logic [31:0] DefaultSeed = 32'h243f6a88;
  localparam logic [31:0] LfsrTaps = 32'h80200003;

  logic [4:0] scan_start;
  logic [17:0] available_mask;
  logic [39:0] next_ids;
  logic [17:0] next_mask;
  logic [3:0] found;
  logic [5:0] candidate;

  function automatic logic [31:0] lfsr_advance(input logic [31:0] state);
    logic [31:0] next_state;
    next_state = state;
    for (int i = 0; i < 8; i++) begin
      next_state = (next_state >> 1) ^ (next_state[0] ? LfsrTaps : 32'b0);
    end
    lfsr_advance = next_state;
  endfunction

  assign available_mask = eligible_i & ~acq_mask_o;
  assign scan_start = shuffle_i ? 5'(lfsr_state_o[31:24] % 8'd18) : 5'd0;

  // 保留选择顺序。按物理编号从游标绕行一周，每个合格切片最多访问一次。
  always_comb begin
    next_ids = '0;
    next_mask = '0;
    found = '0;
    candidate = '0;
    for (int offset = 0; offset < 18; offset++) begin
      candidate = {1'b0, scan_start} + 6'(offset);
      if (candidate >= 6'd18) candidate = candidate - 6'd18;
      if (available_mask[candidate[4:0]] && found < 4'd8) begin
        next_ids[5 * found +: 5] = candidate[4:0];
        next_mask[candidate[4:0]] = 1'b1;
        found = found + 1'b1;
      end
    end
  end

  always_ff @(posedge clk_i) begin
    slot_accepted_o <= 1'b0;
    stall_o <= 1'b0;
    seed_error_o <= 1'b0;
    if (!rst_ni || clear_i || !enable_i) begin
      acq_ids_o <= '0;
      conv_ids_o <= '0;
      acq_mask_o <= '0;
      conv_mask_o <= '0;
      acq_valid_o <= 1'b0;
      conv_valid_o <= 1'b0;
      acq_sample_id_o <= '0;
      conv_sample_id_o <= '0;
      lfsr_state_o <= DefaultSeed;
    end else if (seed_load_i) begin
      // 种子写入占用一个独立控制沿。冲突或零值均保留旧状态。
      if (advance_i || seed_i == 32'b0) seed_error_o <= 1'b1;
      else lfsr_state_o <= seed_i;
    end else if (advance_i) begin
      if (found != 4'd8) begin
        stall_o <= 1'b1;
      end else begin
        conv_ids_o <= acq_ids_o;
        conv_mask_o <= acq_mask_o;
        conv_valid_o <= acq_valid_o;
        conv_sample_id_o <= acq_sample_id_o;
        acq_ids_o <= next_ids;
        acq_mask_o <= next_mask;
        acq_valid_o <= 1'b1;
        acq_sample_id_o <= acq_valid_o ? acq_sample_id_o + 32'd1 : 32'd0;
        slot_accepted_o <= 1'b1;
        if (shuffle_i) lfsr_state_o <= lfsr_advance(lfsr_state_o);
      end
    end
  end
endmodule
