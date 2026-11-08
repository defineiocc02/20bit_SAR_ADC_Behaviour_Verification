// =============================================================================
// 文件名     : slice_scheduler.sv
// 模块名     : slice_scheduler
// 版本       : v1.0
// 功能       : 从 18 片资源池挑出有序 8 片采集组的调度器
//
// 设计说明   :
//   * eligible_i 的 18 位对应物理释放资格；资格不足时 stall 一拍并保持全部状态。
//   * 样本 ID 按 2^32 模递增，slot 0 的 5 位 ID 在输出最低位。
//   * 环形前缀 rank 的固定/随机双路并行；游标用 16 ≡ -2 (mod 18) 折算。
//   * 接口陷阱：seed_load_i 错误分支在 module1_top 集成路径不可达，但直接例化本模块可达。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module task1_slice_scheduler (
    // 全端口同属 clk_i 域，上升沿使用旧状态；rst_ni 同步低有效，clear_i 高有效，enable_i=0 视同清空。
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        clear_i,
    input  logic        enable_i,
    // 每沿最多请求一个采集槽；候选不足八片时 stall_o 拉高一拍，组、ID 和随机状态全部保持。
    input  logic        advance_i,
    // 18 位按物理片编号，1 表示该片已被上游释放、允许重新采样；释放确认与跨时钟同步不在这里。
    input  logic [17:0] eligible_i,
    // 0 为固定从片 0 扫描，1 为从 LFSR 游标起的环形扫描；模式在本沿绑定，不随在途组改变。
    input  logic        shuffle_i,
    // 种子写入独占一个控制沿，与 advance 冲突或零值都判错；顶层已先筛过，这里为直接例化再兜一次。
    input  logic        seed_load_i,
    input  logic [31:0] seed_i,
    // 8 个 5 位物理片号紧凑排进 40 位，slot 0 在最低五位；18 位掩码按物理片编号而非 slot。
    output logic [39:0] acq_ids_o,
    output logic [39:0] conv_ids_o,
    output logic [17:0] acq_mask_o,
    output logic [17:0] conv_mask_o,
    output logic        acq_valid_o,
    output logic        conv_valid_o,
    // 样本号从 0 起按模 2^32 递增，只在成功分配时推进；stall／拒绝／复位都不改它。
    output logic [31:0] acq_sample_id_o,
    output logic [31:0] conv_sample_id_o,
    // slot_accepted_o 是本沿单拍事件；conv_valid_o 说明转换组确有历史采样，下游须两者同时有效才认新转换组。
    output logic        slot_accepted_o,
    output logic        stall_o,
    // seed_error_o 只报本模块的种子违规；顶层还会与自身判定合并后对外发布。
    output logic        seed_error_o,
    output logic [31:0] lfsr_state_o
);
  // 默认种子与 module1_top 的 DefaultSliceSeed 一致；LfsrTaps 是 32 位 Galois 抽头，改任一个都会破坏重放。
  localparam logic [31:0] DefaultSeed = 32'h243f6a88;
  localparam logic [31:0] LfsrTaps = 32'h80200003;

  // advanced_lfsr 是一次推进八步的结果，仅在成功的 shuffle 分配沿写回，空拍不推进。
  logic [31:0] advanced_lfsr;
  // 可再采样集合 = 上游已释放 且 不在当前采集组里；采集组未晋升前不能重复选同一片。
  logic [17:0] available_mask;
  logic [39:0] next_ids;
  logic [17:0] next_mask;
  logic enough_slices;
  // 与 LFSR 同沿缓存：物理位置 p 的位表示游标是否在 p 或更早。
  logic [17:0] start_at_or_before;
  // 固定序与随机序各算一套 rank 和位图，两套并行计算、互不依赖。
  logic [4:0] rank_fixed [18], rank_shuffled [18];
  logic [17:0] mask_fixed, mask_shuffled;
  logic [39:0] ids_fixed, ids_shuffled;
  logic [17:0] physical_id_bit [5];

  // Galois LFSR 右移八次，每次按 bit0 决定是否异或抽头；一次成功分配恰好消耗八步。
  function automatic logic [31:0] lfsr_advance(input logic [31:0] state);
    logic [31:0] next_state;
    next_state = state;
    for (int i = 0; i < 8; i++) begin
      next_state = (next_state >> 1) ^ (next_state[0] ? LfsrTaps : 32'b0);
    end
    lfsr_advance = next_state;
  endfunction

  assign available_mask = eligible_i & ~acq_mask_o;
  // 把高字节 x=16h+l 折成模 18 的游标：16≡-2 (mod 18)，差值落在 -30～15，只需加 18 或 36 归正。
  // x=16*h+l，且 16≡-2 (mod 18)，差值介于 -30 和 15。
  function automatic logic [4:0] scan_remainder(input logic [7:0] value);
    logic signed [5:0] delta;
    delta = $signed({2'b0, value[3:0]}) - $signed({1'b0, value[7:4], 1'b0});
    if (!delta[5]) scan_remainder = delta[4:0];
    else if (delta < -6'sd18) scan_remainder = 5'($unsigned(delta) + 6'd36);
    else scan_remainder = 5'($unsigned(delta) + 6'd18);
  endfunction

  // 只在 shuffle 分配时采用推进后的状态；固定模式下 LFSR 完全不推进。
  assign advanced_lfsr = lfsr_advance(lfsr_state_o);

  // 资格计数与轮转起点无关，单独决定本拍能否接受请求。
  // 计数只看 available_mask 的 ones 数，与从哪个游标起扫无关，因此可先判能否接受再决定排序。
  assign enough_slices = $countones(available_mask) >= 8;
  // 对每个物理位置直接形成环形前缀，避免轮转资格位或回转选择位图。
  // Earlier 是位置 p 之前的连续低位掩码，rank_fixed 用它数出 p 在固定序里的名次。
  for (genvar physical = 0; physical < 18; physical++) begin : gen_physical
    localparam logic [17:0] Earlier = 18'h3ffff >> (18 - physical);
    localparam logic [4:0] PhysicalId = 5'(physical);
    logic [17:0] earlier;

    // 环形「早于 p」的判定：p 之前的位按前缀直接取，p 之后的位要绕回起点，自身恒为 0。
    for (genvar other = 0; other < 18; other++) begin : gen_earlier
      if (other < physical) begin : gen_before
        assign earlier[other] = start_at_or_before[other] || !start_at_or_before[physical];
      end else if (other > physical) begin : gen_after
        assign earlier[other] = start_at_or_before[other] && !start_at_or_before[physical];
      end else begin : gen_self
        assign earlier[other] = 1'b0;
      end
    end

    // 名次 = 在 p 之前（含环形绕回）的合格片数；小于 8 的片才进入新采集组。
    assign rank_fixed[physical] = 5'($countones(available_mask & Earlier));
    assign rank_shuffled[physical] = 5'($countones(available_mask & earlier));
    assign mask_fixed[physical] = available_mask[physical] && (rank_fixed[physical] < 5'd8);
    assign mask_shuffled[physical] = available_mask[physical] && (rank_shuffled[physical] < 5'd8);
    // 把 5 位物理片号按 bit 拆成 18 位向量，slot 打包时按位或还原，避免用变量索引做选择器。
    for (genvar bit_idx = 0; bit_idx < 5; bit_idx++) begin : gen_id_bits
      assign physical_id_bit[bit_idx][physical] = PhysicalId[bit_idx];
    end
  end

  // 每个 slot 只由环形序位相等的一个合格物理切片驱动。
  // 每个 slot 由「名次恰好等于 slot」的那一片驱动，故 8 个 slot 与 8 片一一对应、无重复。
  for (genvar slot = 0; slot < 8; slot++) begin : gen_output_slot
    logic [17:0] pick_fixed, pick_shuffled;
    for (genvar physical = 0; physical < 18; physical++) begin : gen_pick
      assign pick_fixed[physical] = available_mask[physical] && (rank_fixed[physical] == 5'(slot));
      assign pick_shuffled[physical] = available_mask[physical] && (rank_shuffled[physical] == 5'(slot));
    end
    // pick 与 physical_id_bit 按位与再或归约，等价于把命中片的片号写进该 slot。
    for (genvar bit_idx = 0; bit_idx < 5; bit_idx++) begin : gen_bit
      assign ids_fixed[5*slot + bit_idx] = |(pick_fixed & physical_id_bit[bit_idx]);
      assign ids_shuffled[5*slot + bit_idx] = |(pick_shuffled & physical_id_bit[bit_idx]);
    end
  end

  // 两种排序并行完成，最后才应用模式位，避免模式输入穿过前缀计数。
  // 模式位放在最末一级，shuffle_i 不改变任何 rank 的生成逻辑，便于两条路径等价对拍。
  assign next_mask = shuffle_i ? mask_shuffled : mask_fixed;
  assign next_ids = shuffle_i ? ids_shuffled : ids_fixed;

  // 三个脉冲每沿先默认清零，分支里再按需置起，避免漏写导致上一拍事件被重复解释。
// =========================================================================
// 2. 时序逻辑与逐沿行为
// =========================================================================
  always_ff @(posedge clk_i) begin
    slot_accepted_o <= 1'b0;
    stall_o <= 1'b0;
    seed_error_o <= 1'b0;
    // 复位／清空／禁用优先级最高：连 start_at_or_before 一起恢复默认游标，只清 valid 会留下旧扫描起点。
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
      // 游标复位成默认种子高字节模 18 后的环形前缀，与 lfsr_state_o 保持同一相位。
      start_at_or_before <= 18'h3ffff << 5'(DefaultSeed[31:24] % 8'd18);
    // 种子写入单独占一沿：它既不晋升组也不发 accept，写完后下一沿才可能开始新的分配。
    end else if (seed_load_i) begin
      // 种子写入占用一个独立控制沿。冲突或零值均保留旧状态。
      // 这条错误分支经 module1_top 集成时不可达（顶层已筛过冲突和零值），直接例化的自检会覆盖它。
      if (advance_i || seed_i == 32'b0) seed_error_o <= 1'b1;
      else begin
        lfsr_state_o <= seed_i;
        start_at_or_before <= 18'h3ffff << scan_remainder(seed_i[31:24]);
      end
    // 只有 advance 且 seed_load=0 才进入分配；候选不足时仅拉 stall，不碰任何已发布的组与状态。
    end else if (advance_i) begin
      if (!enough_slices) begin
        stall_o <= 1'b1;
      end else begin
        // 晋升与新建在同一沿：旧采集组整体搬到 conv_*，新组从补集取，首个接受沿 conv_valid 仍为 0。
        conv_ids_o <= acq_ids_o;
        conv_mask_o <= acq_mask_o;
        conv_valid_o <= acq_valid_o;
        conv_sample_id_o <= acq_sample_id_o;
        acq_ids_o <= next_ids;
        acq_mask_o <= next_mask;
        acq_valid_o <= 1'b1;
        // 首笔从 0 起，之后每成功分配加一；用 acq_valid_o 判断是否为首次，而不是另设计数器。
        acq_sample_id_o <= acq_valid_o ? acq_sample_id_o + 32'd1 : 32'd0;
        slot_accepted_o <= 1'b1;
        // 只有成功的随机分配才推进 LFSR 八步并刷新游标；固定模式与 stall 都保持随机状态，保证重放。
        if (shuffle_i) begin
          lfsr_state_o <= advanced_lfsr;
          start_at_or_before <= 18'h3ffff << scan_remainder(advanced_lfsr[31:24]);
        end
      end
    end
  end
endmodule
