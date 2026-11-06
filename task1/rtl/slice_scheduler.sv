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

  logic [4:0] scan_start, scan_cursor;
  logic [31:0] advanced_lfsr;
  logic [17:0] available_mask;
  logic [39:0] next_ids;
  logic [17:0] next_mask;
  logic enough_slices;
  logic [17:0] scan_available;
  logic [17:0] scan_selected;
  logic [35:0] selected_shifted;
  logic [4:0] scan_rank [18];
  logic [17:0] candidate_bit [5];

  function automatic logic [31:0] lfsr_advance(input logic [31:0] state);
    logic [31:0] next_state;
    next_state = state;
    for (int i = 0; i < 8; i++) begin
      next_state = (next_state >> 1) ^ (next_state[0] ? LfsrTaps : 32'b0);
    end
    lfsr_advance = next_state;
  endfunction

  assign available_mask = eligible_i & ~acq_mask_o;
  // x=16*h+l，且 16≡-2 (mod 18)，差值介于 -30 和 15。
  function automatic logic [4:0] scan_remainder(input logic [7:0] value);
    logic signed [5:0] delta;
    delta = $signed({2'b0, value[3:0]}) - $signed({1'b0, value[7:4], 1'b0});
    if (!delta[5]) scan_remainder = delta[4:0];
    else if (delta < -6'sd18) scan_remainder = 5'($unsigned(delta) + 6'd36);
    else scan_remainder = 5'($unsigned(delta) + 6'd18);
  endfunction

  assign advanced_lfsr = lfsr_advance(lfsr_state_o);
  assign scan_start = shuffle_i ? scan_cursor : 5'd0;

  // 资格计数与轮转起点无关，单独决定本拍能否接受请求。
  assign enough_slices = $countones(available_mask) >= 8;
  assign scan_available = 18'({available_mask, available_mask} >> scan_start);
  for (genvar lane = 0; lane < 18; lane++) begin : gen_scan
    localparam logic [17:0] Earlier = 18'h3ffff >> (18 - lane);
    logic [5:0] physical_sum;
    logic [4:0] physical_id;
    assign physical_sum = {1'b0, scan_start} + 6'(lane);
    assign physical_id = (physical_sum >= 6'd18)
                         ? 5'(physical_sum - 6'd18) : physical_sum[4:0];
    assign scan_rank[lane] = 5'($countones(scan_available & Earlier));
    assign scan_selected[lane] = scan_available[lane] && (scan_rank[lane] < 5'd8);
    for (genvar bit_idx = 0; bit_idx < 5; bit_idx++) begin : gen_id_bits
      assign candidate_bit[bit_idx][lane] = physical_id[bit_idx];
    end
  end
  assign selected_shifted = {18'b0, scan_selected} << scan_start;
  assign next_mask = selected_shifted[35:18] | selected_shifted[17:0];

  // 每个 slot 只由一个优先序对应的物理切片驱动。
  for (genvar slot = 0; slot < 8; slot++) begin : gen_slot
    logic [17:0] pick;
    for (genvar lane = 0; lane < 18; lane++) begin : gen_lane
      assign pick[lane] = scan_available[lane] && (scan_rank[lane] == 5'(slot));
    end
    for (genvar bit_idx = 0; bit_idx < 5; bit_idx++) begin : gen_bit
      assign next_ids[5*slot + bit_idx] = |(pick & candidate_bit[bit_idx]);
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
      scan_cursor <= 5'(DefaultSeed[31:24] % 8'd18);
    end else if (seed_load_i) begin
      // 种子写入占用一个独立控制沿。冲突或零值均保留旧状态。
      if (advance_i || seed_i == 32'b0) seed_error_o <= 1'b1;
      else begin
        lfsr_state_o <= seed_i;
        scan_cursor <= scan_remainder(seed_i[31:24]);
      end
    end else if (advance_i) begin
      if (!enough_slices) begin
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
        if (shuffle_i) begin
          lfsr_state_o <= advanced_lfsr;
          scan_cursor <= scan_remainder(advanced_lfsr[31:24]);
        end
      end
    end
  end
endmodule
