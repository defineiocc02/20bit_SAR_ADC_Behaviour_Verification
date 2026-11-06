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

  logic [31:0] advanced_lfsr;
  logic [17:0] available_mask;
  logic [39:0] next_ids;
  logic [17:0] next_mask;
  logic enough_slices;
  // 与 LFSR 同沿缓存：物理位置 p 的位表示游标是否在 p 或更早。
  logic [17:0] start_at_or_before;
  logic [4:0] rank_fixed [18], rank_shuffled [18];
  logic [17:0] mask_fixed, mask_shuffled;
  logic [39:0] ids_fixed, ids_shuffled;
  logic [17:0] physical_id_bit [5];

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

  // 资格计数与轮转起点无关，单独决定本拍能否接受请求。
  assign enough_slices = $countones(available_mask) >= 8;
  // 对每个物理位置直接形成环形前缀，避免轮转资格位或回转选择位图。
  for (genvar physical = 0; physical < 18; physical++) begin : gen_physical
    localparam logic [17:0] Earlier = 18'h3ffff >> (18 - physical);
    localparam logic [4:0] PhysicalId = 5'(physical);
    logic [17:0] earlier;

    for (genvar other = 0; other < 18; other++) begin : gen_earlier
      if (other < physical) begin : gen_before
        assign earlier[other] = start_at_or_before[other] || !start_at_or_before[physical];
      end else if (other > physical) begin : gen_after
        assign earlier[other] = start_at_or_before[other] && !start_at_or_before[physical];
      end else begin : gen_self
        assign earlier[other] = 1'b0;
      end
    end

    assign rank_fixed[physical] = 5'($countones(available_mask & Earlier));
    assign rank_shuffled[physical] = 5'($countones(available_mask & earlier));
    assign mask_fixed[physical] = available_mask[physical] && (rank_fixed[physical] < 5'd8);
    assign mask_shuffled[physical] = available_mask[physical] && (rank_shuffled[physical] < 5'd8);
    for (genvar bit_idx = 0; bit_idx < 5; bit_idx++) begin : gen_id_bits
      assign physical_id_bit[bit_idx][physical] = PhysicalId[bit_idx];
    end
  end

  // 每个 slot 只由环形序位相等的一个合格物理切片驱动。
  for (genvar slot = 0; slot < 8; slot++) begin : gen_output_slot
    logic [17:0] pick_fixed, pick_shuffled;
    for (genvar physical = 0; physical < 18; physical++) begin : gen_pick
      assign pick_fixed[physical] = available_mask[physical] && (rank_fixed[physical] == 5'(slot));
      assign pick_shuffled[physical] = available_mask[physical] && (rank_shuffled[physical] == 5'(slot));
    end
    for (genvar bit_idx = 0; bit_idx < 5; bit_idx++) begin : gen_bit
      assign ids_fixed[5*slot + bit_idx] = |(pick_fixed & physical_id_bit[bit_idx]);
      assign ids_shuffled[5*slot + bit_idx] = |(pick_shuffled & physical_id_bit[bit_idx]);
    end
  end

  // 两种排序并行完成，最后才应用模式位，避免模式输入穿过前缀计数。
  assign next_mask = shuffle_i ? mask_shuffled : mask_fixed;
  assign next_ids = shuffle_i ? ids_shuffled : ids_fixed;

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
      start_at_or_before <= 18'h3ffff << 5'(DefaultSeed[31:24] % 8'd18);
    end else if (seed_load_i) begin
      // 种子写入占用一个独立控制沿。冲突或零值均保留旧状态。
      if (advance_i || seed_i == 32'b0) seed_error_o <= 1'b1;
      else begin
        lfsr_state_o <= seed_i;
        start_at_or_before <= 18'h3ffff << scan_remainder(seed_i[31:24]);
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
          start_at_or_before <= 18'h3ffff << scan_remainder(advanced_lfsr[31:24]);
        end
      end
    end
  end
endmodule
