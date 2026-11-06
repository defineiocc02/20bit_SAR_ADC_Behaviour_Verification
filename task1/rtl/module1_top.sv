// 将切片调度与 Fig.11 映射接成同笔事务。请求在上升沿 N 判定，组、样本号、
// DEM 配置和接受或拒绝事件在 N+1 发布。slot 0 位于 ID 与开关掩码的低位。
// 每次成功分配采集组都从独立 DEM 随机流取一组相位，即使该笔关闭 DEM。
// 转换组使用原采集组的配置，组内全部已决前缀共享该配置。
// 两个种子必须非零，且种子写入沿不得请求分配。违规写入整笔拒绝。
// 复位、清空或关闭优先清除两级在途状态，组合掩码和错误同时停用。
`timescale 1ns/1ps
module module1_top (
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic         clear_i,
    input  logic         enable_i,
    input  logic         advance_i,
    input  logic [ 17:0] eligible_i,
    input  logic         shuffle_i,
    input  logic         dem_enable_i,
    input  logic         seed_load_i,
    input  logic [ 31:0] slice_seed_i,
    input  logic [ 31:0] dem_seed_i,
    input  logic         code_valid_i,
    input  logic [  8:0] code_i,
    input  logic [ 31:0] code_sample_id_i,
    output logic [ 39:0] acq_ids_o,
    output logic [ 39:0] conv_ids_o,
    output logic [ 17:0] acq_mask_o,
    output logic [ 17:0] conv_mask_o,
    output logic         acq_valid_o,
    output logic         conv_valid_o,
    output logic [ 31:0] acq_sample_id_o,
    output logic [ 31:0] conv_sample_id_o,
    output logic         slot_accepted_o,
    output logic         stall_o,
    output logic         seed_error_o,
    output logic [ 31:0] slice_lfsr_state_o,
    output logic [ 31:0] dem_rng_state_o,
    output logic         acq_dem_enable_o,
    output logic         conv_dem_enable_o,
    output logic [  3:0] acq_h_phase_o,
    output logic [  3:0] acq_v_phase_o,
    output logic [  3:0] conv_h_phase_o,
    output logic [  3:0] conv_v_phase_o,
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic         mask_valid_o,
    output logic         code_error_o
);
  localparam logic [31:0] DefaultSliceSeed = 32'h243f6a88;
  localparam logic [31:0] DefaultDemSeed = 32'h9e3779b9;

  logic active;
  logic accept_seed;
  logic seed_load_q, seed_error_q, dem_enable_q;
  logic [31:0] dem_seed_q;

  logic [39:0] scheduler_acq_ids, scheduler_conv_ids;
  logic [17:0] scheduler_acq_mask, scheduler_conv_mask;
  logic [31:0] scheduler_acq_sample_id, scheduler_conv_sample_id;
  logic [31:0] scheduler_rng_state;
  logic scheduler_acq_valid, scheduler_conv_valid;
  logic scheduler_slot_accepted, scheduler_stall, scheduler_seed_error;

  logic [135:0] mapped_main;
  logic [39:0] mapped_sub;
  logic mapping_legal;

  function automatic logic [31:0] next_dem_rng(input logic [31:0] state);
    logic [31:0] updated;
    updated = state ^ (state << 13);
    updated = updated ^ (updated >> 17);
    next_dem_rng = updated ^ (updated << 5);
  endfunction

  // 16 ≡ 1 (mod 15)。先加高低半字节，再将和约到 0～14。
  function automatic logic [3:0] horizontal_phase(input logic [7:0] random_byte);
    logic [4:0] sum;
    sum = {1'b0, random_byte[7:4]} + {1'b0, random_byte[3:0]};
    if (sum == 5'd30) horizontal_phase = 4'd0;
    else if (sum >= 5'd15) horizontal_phase = 4'(sum - 5'd15);
    else horizontal_phase = sum[3:0];
  endfunction

  // 发布前验证八个有序物理 ID 与位图一致。非法组不给前缀映射资格。
  function automatic logic legal_group(input logic [39:0] ids, input logic [17:0] mask);
    logic [17:0] seen;
    logic [4:0] slice_id;
    logic legal;
    seen = '0;
    legal = 1'b1;
    for (int slot = 0; slot < 8; slot++) begin
      slice_id = ids[5*slot+:5];
      if (slice_id >= 5'd18) legal = 1'b0;
      else if (seen[slice_id]) legal = 1'b0;
      else seen[slice_id] = 1'b1;
    end
    legal_group = legal && (seen == mask);
  endfunction

  assign active = rst_ni && !clear_i && enable_i;
  assign accept_seed = seed_load_i && !advance_i && (slice_seed_i != 32'b0)
                       && (dem_seed_i != 32'b0);

  task1_slice_scheduler u_slice_scheduler (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i),
      .enable_i(enable_i),
      .advance_i(advance_i && !seed_load_i),
      .eligible_i(eligible_i),
      .shuffle_i(shuffle_i),
      .seed_load_i(accept_seed),
      .seed_i(slice_seed_i),
      .acq_ids_o(scheduler_acq_ids),
      .conv_ids_o(scheduler_conv_ids),
      .acq_mask_o(scheduler_acq_mask),
      .conv_mask_o(scheduler_conv_mask),
      .acq_valid_o(scheduler_acq_valid),
      .conv_valid_o(scheduler_conv_valid),
      .acq_sample_id_o(scheduler_acq_sample_id),
      .conv_sample_id_o(scheduler_conv_sample_id),
      .slot_accepted_o(scheduler_slot_accepted),
      .stall_o(scheduler_stall),
      .seed_error_o(scheduler_seed_error),
      .lfsr_state_o(scheduler_rng_state)
  );

  always_ff @(posedge clk_i) begin
    if (!active) begin
      seed_load_q <= 1'b0;
      seed_error_q <= 1'b0;
      dem_seed_q <= '0;
      dem_enable_q <= 1'b0;
      acq_ids_o <= '0;
      conv_ids_o <= '0;
      acq_mask_o <= '0;
      conv_mask_o <= '0;
      acq_valid_o <= 1'b0;
      conv_valid_o <= 1'b0;
      acq_sample_id_o <= '0;
      conv_sample_id_o <= '0;
      slot_accepted_o <= 1'b0;
      stall_o <= 1'b0;
      seed_error_o <= 1'b0;
      slice_lfsr_state_o <= DefaultSliceSeed;
      dem_rng_state_o <= DefaultDemSeed;
      acq_dem_enable_o <= 1'b0;
      conv_dem_enable_o <= 1'b0;
      acq_h_phase_o <= '0;
      acq_v_phase_o <= '0;
      conv_h_phase_o <= '0;
      conv_v_phase_o <= '0;
    end else begin
      // 每个边沿保留调度器本次判定所用的配置，下沿随结果发布。
      dem_enable_q <= dem_enable_i;
      seed_load_q <= accept_seed;
      seed_error_q <= seed_load_i && !accept_seed;
      if (accept_seed) dem_seed_q <= dem_seed_i;

      slot_accepted_o <= scheduler_slot_accepted;
      stall_o <= scheduler_stall;
      seed_error_o <= seed_error_q || scheduler_seed_error;
      slice_lfsr_state_o <= scheduler_rng_state;

      if (seed_load_q) dem_rng_state_o <= dem_seed_q;
      else if (scheduler_slot_accepted) dem_rng_state_o <= next_dem_rng(dem_rng_state_o);

      if (scheduler_slot_accepted) begin
        acq_ids_o <= scheduler_acq_ids;
        conv_ids_o <= scheduler_conv_ids;
        acq_mask_o <= scheduler_acq_mask;
        conv_mask_o <= scheduler_conv_mask;
        acq_valid_o <= scheduler_acq_valid;
        conv_valid_o <= scheduler_conv_valid && legal_group(scheduler_conv_ids,
                                                           scheduler_conv_mask);
        acq_sample_id_o <= scheduler_acq_sample_id;
        conv_sample_id_o <= scheduler_conv_sample_id;

        conv_dem_enable_o <= acq_dem_enable_o;
        conv_h_phase_o <= acq_h_phase_o;
        conv_v_phase_o <= acq_v_phase_o;
        acq_dem_enable_o <= dem_enable_q;
        acq_h_phase_o <= horizontal_phase(dem_rng_state_o[31:24]);
        acq_v_phase_o <= dem_rng_state_o[19:16];
      end
    end
  end

  fig11_dem u_fig11_dem (
      .code_i(code_i),
      .dem_enable_i(conv_dem_enable_o),
      .horizontal_phase_i(conv_h_phase_o),
      .vertical_phase_i(conv_v_phase_o),
      .main_mask_o(mapped_main),
      .sub_mask_o(mapped_sub),
      .valid_o(mapping_legal)
  );

  // 当前转换组可提交多个已决前缀。调用方在发布之后附同笔 ID 驱动 code_valid_i。
  assign mask_valid_o = active && code_valid_i && conv_valid_o && mapping_legal
                        && (code_sample_id_i == conv_sample_id_o);
  assign code_error_o = active && code_valid_i && !mask_valid_o;
  assign main_mask_o = mask_valid_o ? mapped_main : '0;
  assign sub_mask_o = mask_valid_o ? mapped_sub : '0;
endmodule
