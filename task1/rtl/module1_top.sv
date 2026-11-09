// =============================================================================
// 文件名     : module1_top.sv
// 模块名     : module1_top
// 版本       : v1.0
// 功能       : 把 18 片切片调度与 Fig.11 映射接成同笔事务
//
// 设计说明   :
//   * 申请在 N 沿判定、N+1 沿发布；advance 与 seed_load 互斥，accept_seed 先做预筛。
//   * 两条随机流独立推进（LFSR 八步 vs xorshift32 一次），h 取高字节模 15、v 取状态[19:16]。
//   * legal_group 门禁、种子重载优先于随机推进；复位必须连 ID、相位与随机状态一起清。
//   * mask_valid_o 五项与条件同时成立；code_error 明确拒绝错 ID，不静默丢样。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module module1_top (
    // 全部端口同属 clk_i 域；rst_ni 同步低有效、clear_i 高有效，enable_i=0 等同于清空在途状态。
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic         clear_i,
    input  logic         enable_i,
    // advance_i 与 seed_load_i 不能同沿有效；顶层把 advance_i && !seed_load_i 传给调度器，种子事务独占一沿。
    input  logic         advance_i,
    // 18 位按物理片编号，1 表示已释放可再采样；新采集组从 eligible_i 与当前采集位图的补集里取。
    input  logic [ 17:0] eligible_i,
    input  logic         shuffle_i,
    // 模式位与两个种子在请求沿冻结，之后改端口不影响在途组，避免一笔里换规则。
    input  logic         dem_enable_i,
    input  logic         seed_load_i,
    // 两个 32 位种子须同沿非零且 advance_i=0；否则 accept_seed 为 0，下一沿报 seed_error_o。
    input  logic [ 31:0] slice_seed_i,
    input  logic [ 31:0] dem_seed_i,
    // 已决前缀可多次提交，必须带与当前转换组一致的 32 位样本 ID，否则 code_error_o=1 且掩码清零。
    input  logic         code_valid_i,
    input  logic [  8:0] code_i,
    input  logic [ 31:0] code_sample_id_i,
    // 40 位 ID 里第 s 槽占 [5s+:5]，slot 0 在最低位；18 位掩码按物理片编号，不按 slot。
    output logic [ 39:0] acq_ids_o,
    output logic [ 39:0] conv_ids_o,
    output logic [ 17:0] acq_mask_o,
    output logic [ 17:0] conv_mask_o,
    output logic         acq_valid_o,
    output logic         conv_valid_o,
    // 样本号从 0 起按模 2^32 递增，仅在成功分配时推进；stall 或拒绝时保持不变。
    output logic [ 31:0] acq_sample_id_o,
    output logic [ 31:0] conv_sample_id_o,
    // 三个单拍事件描述 N+1 的响应；新转换组资格取 slot_accepted_o && conv_valid_o。
    output logic         slot_accepted_o,
    output logic         stall_o,
    output logic         seed_error_o,
    // 两个随机流彼此独立：调度 LFSR 只在成功的 shuffle 分配推进八步，DEM xorshift32 每次成功分配推进一次。
    output logic [ 31:0] slice_lfsr_state_o,
    output logic [ 31:0] dem_rng_state_o,
    output logic         acq_dem_enable_o,
    output logic         conv_dem_enable_o,
    // h 相位为高字节模 15，v 相位取 DEM 状态位 [19:16]；两者按组冻结，acq/conv 各一份快照。
    output logic [  3:0] acq_h_phase_o,
    output logic [  3:0] acq_v_phase_o,
    output logic [  3:0] conv_h_phase_o,
    output logic [  3:0] conv_v_phase_o,
    // 主 136 位、子 40 位是组合输出，仅 mask_valid_o 有效时非零；非法组或错 ID 不会漏出残留掩码。
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic         mask_valid_o,
    output logic         code_error_o
);
  // 两个默认种子与调度器复位值一致，保证复位后可重放；改动需同步 Python 参考模型。
  localparam logic [31:0] DefaultSliceSeed = 32'h243f6a88;
  localparam logic [31:0] DefaultDemSeed = 32'h9e3779b9;

  // active 把复位、清空、禁用合成一个条件，时序复位与组合掩码／错误共用它，避免两处优先级不一致。
  logic active;
  logic accept_seed;
  logic seed_load_q, seed_error_q, dem_enable_q;
  logic [31:0] dem_seed_q;

  // scheduler_* 是调度器输出的副本，只在 slot_accepted 沿搬到对外端口。
  logic [39:0] scheduler_acq_ids, scheduler_conv_ids;
  logic [17:0] scheduler_acq_mask, scheduler_conv_mask;
  logic [31:0] scheduler_acq_sample_id, scheduler_conv_sample_id;
  logic [31:0] scheduler_rng_state;
  logic scheduler_acq_valid, scheduler_conv_valid;
  logic scheduler_slot_accepted, scheduler_stall, scheduler_seed_error;

  // mapped_* 由已冻结的转换组配置即时组合得出，不寄存；因此 conv_* 必须在使用前稳定。
  logic [135:0] mapped_main;
  logic [39:0] mapped_sub;
  logic mapping_legal;

  // xorshift32（13/17/5）每笔成功分配推进一次，与调度 LFSR 无关；旁路 DEM 时同样推进。
  function automatic logic [31:0] next_dem_rng(input logic [31:0] state);
    logic [31:0] updated;
    updated = state ^ (state << 13);
    updated = updated ^ (updated >> 17);
    next_dem_rng = updated ^ (updated << 5);
  endfunction

  // 把高字节折成 0～14 的 h 相位：半字节和最大 30，模 15 后恰好避开非法的 15。
  // 16 ≡ 1 (mod 15)。先加高低半字节，再将和约到 0～14。
  function automatic logic [3:0] horizontal_phase(input logic [7:0] random_byte);
    logic [4:0] sum;
    sum = {1'b0, random_byte[7:4]} + {1'b0, random_byte[3:0]};
    if (sum == 5'd30) horizontal_phase = 4'd0;
    else if (sum >= 5'd15) horizontal_phase = 4'(sum - 5'd15);
    else horizontal_phase = sum[3:0];
  endfunction

  // 发布转换组前校验 ID 与位图一致；非法组不给前缀映射资格，避免乱序 ID 配到别人的开关命令。
  // 发布前验证八个有序物理 ID 与位图一致。非法组不给前缀映射资格。
  function automatic logic legal_group(input logic [39:0] ids, input logic [17:0] mask);
    logic [17:0] onehot [8];
    logic [17:0] seen;
    // 越界 ID 移位后为零；重复 ID 在并集里只留下一个位置。
    for (int slot = 0; slot < 8; slot++) begin
      onehot[slot] = 18'd1 << ids[5*slot+:5];
    end
    seen = ((onehot[0] | onehot[1]) | (onehot[2] | onehot[3]))
           | ((onehot[4] | onehot[5]) | (onehot[6] | onehot[7]));
    legal_group = ($countones(seen) == 8) && (seen == mask);
  endfunction

  // 与 always_ff 的复位分支同条件，保证时序状态清零和组合输出停用发生在同一沿。
  assign active = rst_ni && !clear_i && enable_i;
  // 种子写入与分配互斥且两路种子非零；违规在下一沿由 seed_error_q 报出，两路状态保持旧值。
  assign accept_seed = seed_load_i && !advance_i && (slice_seed_i != 32'b0)
                       && (dem_seed_i != 32'b0);

  // advance_i 门掉 seed_load_i 后再给调度器：种子事务占用独立控制沿，不能同时触发一次分配。
  // 调度器只看到已筛过的 accept_seed，因此它不再重复判定非零与冲突（直接例化时除外）。
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

  // 复位分支必须连 ID、掩码、样本号、相位和两个随机状态一起清，否则释放后第一笔会配出旧 ID 新配置。
// =========================================================================
// 2. 时序逻辑与逐沿行为
// =========================================================================
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
      // 配置逐沿寄存是为了对齐 N 判定 / N+1 发布：dem_enable_q 保存的是 N 沿那一次的值。
      // 每个边沿保留调度器本次判定所用的配置，下沿随结果发布。
      dem_enable_q <= dem_enable_i;
      seed_load_q <= accept_seed;
      seed_error_q <= seed_load_i && !accept_seed;
      if (accept_seed) dem_seed_q <= dem_seed_i;

      // 三个事件和 LFSR 状态无条件照搬调度器；即使 stall，也要把 stall_o 显式发布一拍。
      slot_accepted_o <= scheduler_slot_accepted;
      stall_o <= scheduler_stall;
      // 顶层自己的种子违规与调度器上报的违规合并，任一为真即报；写种子只影响之后的分配。
      seed_error_o <= seed_error_q || scheduler_seed_error;
      slice_lfsr_state_o <= scheduler_rng_state;

      // 种子重载优先于随机推进：同沿不会既写种子又推进 xorshift32，避免新种子被立刻搅乱。
      if (seed_load_q) dem_rng_state_o <= dem_seed_q;
      // 只有成功分配才推进 DEM 随机流，stall 或空拍保持相位，保证同笔前缀可重放。
      else if (scheduler_slot_accepted) dem_rng_state_o <= next_dem_rng(dem_rng_state_o);

      // 晋升与新建在同一沿完成：旧采集组整体变成转换组，新组从补集取，首个接受沿 conv_valid 仍为 0。
      if (scheduler_slot_accepted) begin
        acq_ids_o <= scheduler_acq_ids;
        conv_ids_o <= scheduler_conv_ids;
        acq_mask_o <= scheduler_acq_mask;
        conv_mask_o <= scheduler_conv_mask;
        acq_valid_o <= scheduler_acq_valid;
        // conv_valid_o 额外加 legal_group 门禁：非法 ID／位图组合即使被调度器标为有效也不给映射资格。
        conv_valid_o <= scheduler_conv_valid && legal_group(scheduler_conv_ids,
                                                           scheduler_conv_mask);
        acq_sample_id_o <= scheduler_acq_sample_id;
        conv_sample_id_o <= scheduler_conv_sample_id;

        // 转换组沿用旧采集组的使能和相位，新采集组才取本沿冻结的 dem_enable_q 与新相位。
        conv_dem_enable_o <= acq_dem_enable_o;
        conv_h_phase_o <= acq_h_phase_o;
        conv_v_phase_o <= acq_v_phase_o;
        acq_dem_enable_o <= dem_enable_q;
        // 新相位取自推进前的 DEM 状态；即使该笔 dem_enable=0 也照常取，保证旁路笔的随机流不空转。
        acq_h_phase_o <= horizontal_phase(dem_rng_state_o[31:24]);
        // v 相位直接取状态位 [19:16]，共 16 个取值，与 h 的 0～14 不同值域。
        acq_v_phase_o <= dem_rng_state_o[19:16];
      end
    end
  end

  // 用转换组冻结的 dem_enable / h / v 映射，不用采集组；一个转换组可对多个前缀反复求同一组约束。
  // valid_o 反映 h=15 之类的非法相位，与 mask_valid_o 一起构成输出门禁。
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
  // mask_valid_o 同时要求 active、code_valid_i、conv_valid_o、映射合法且 ID 匹配，五项缺一即清零并报错。
  assign mask_valid_o = active && code_valid_i && conv_valid_o && mapping_legal
                        && (code_sample_id_i == conv_sample_id_o);
  // 活动状态下送来有效前缀却拿不到掩码就报错，不静默丢弃，便于上游发现错 ID 或时序错位。
  assign code_error_o = active && code_valid_i && !mask_valid_o;
  // 掩码用 mask_valid_o 门控清零，避免外部把上一前缀的残留位图当成新命令。
  assign main_mask_o = mask_valid_o ? mapped_main : '0;
  assign sub_mask_o = mask_valid_o ? mapped_sub : '0;
endmodule
