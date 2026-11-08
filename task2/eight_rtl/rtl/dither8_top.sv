`timescale 1ns/1ps

// =============================================================================
// 文件名     : dither8_top.sv
// 模块名     : dither8_top
// 版本       : v1.0
// 功能       : 把模块一的采集描述符翻成八片 RDAC 采样掩码，
//              并按 k = c - U 把外部粗码折成最终命令。
//
// 设计说明   :
//   * code_i 来自片外量化器，本模块不做量化、不决定模拟接轨；
//     掩码与同笔描述符靠 32 位样本 ID 配对回读，ID 整笔不变。
//   * 单时钟域 clk_i；rst_ni 低有效同步复位，clear_i 与 enable_i=0
//     丢弃在途事务，并把两个快照槽的数据与 valid 一起清。
//   * 同沿优先级：复位 > 清空/禁用 > 种子重载 > 采集申请 > 命令受理；
//     种子重载与采集申请同沿时两边都拒绝，不是各自生效。
//   * 接口陷阱：slot 0 位于 40 位 ID/掩码最低位；main 136 位、sub 40 位；
//     sub 掩码 bit4 dummy 恒低；code_i 为 9 位 MSB-first。
//   * command_code_o 与 bridge_correction_q32_o 是诊断端口，冻结契约
//     C3/C4 要求删除；删除前不要把这两个端口当成长期接口使用。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [C9] 极性由 k=c+U 改为 k=c-U。10 位无符号减法在 c<U 时回绕到
//      0x3FB..0x3FF，bit9 恒 1，原判据 !adjusted_code[9] 本已能拒绝下溢；
//      显式 upper_ok 只是位宽无关的加固与可读性，不是修 bug。
// [边界] c<U 时 upper_ok=0，command_eligible 不成立，模块一不会发布掩码；
//      command_code_o 另用 mask_valid_o 门控，避免残留 k 被当成新命令。
// =========================================================================
    // 1. 端口分组（同一 clk_i 域，slot 0 在最低位）
// =========================================================================
    // --- 全局信号 ---
module dither8_top (
    input  logic         clk_i,
    input  logic         rst_ni,  // 低有效同步复位；优先级高于种子重载与采集申请
    input  logic         clear_i,
    input  logic         enable_i,  // 为 0 时丢弃在途事务，等效于 clear_i
    // --- 采集申请与调度资格 ---
    input  logic         advance_i,
    input  logic [ 17:0] eligible_i,  // bit 序即物理 slice 序，18 片资源池
    input  logic         shuffle_i,
    // --- 模式配置（随一次成功采集冻结，之后改端口不影响在途组） ---
    input  logic         dem_enable_i,
    input  logic         bridge_enable_i,
    input  logic         dither_enable_i,
    // --- 种子重载（三个种子须同沿全非零才原子重载） ---
    input  logic         seed_load_i,
    input  logic [ 31:0] slice_seed_i,
    input  logic [ 31:0] dem_seed_i,
    input  logic [ 30:0] dither_seed_i,
    // --- 粗码输入（9 位已决码，ID 整笔不变） ---
    input  logic         code_valid_i,
    input  logic [  8:0] code_i,  // 9 位已决粗码，MSB-first；错 ID 请求在本模块判错
    input  logic [ 31:0] code_sample_id_i,  // 必须与 conv_sample_id_o 一致，整笔不变
    // --- 调度响应（一拍有效；stall 表示资格不足需上游重试） ---
    output logic         slot_accepted_o,
    output logic         stall_o,  // 资格不足的一拍响应，不表示本沿已接受
    output logic         seed_error_o,
    // --- 采集描述符（只在有效沿一拍有意义） ---
    output logic         acq_dither_valid_o,
    output logic [ 31:0] acq_sample_id_o,
    output logic [ 39:0] acq_slice_ids_o,
    output logic [  5:0] acq_word_o,  // 六位扰动字 D6..D1
    output logic [  4:0] acq_lower_eighths_o,  // L8 分子，单位为原生一档的八分之一
    output logic [ 39:0] acq_sample_sub_high_o,
    // --- 转换描述符（有效期内保持稳定，外部不得自行推算换组时刻） ---
    output logic         conv_ready_o,  // 当前转换组命中任一快照槽才有效
    output logic [ 31:0] conv_sample_id_o,
    output logic [ 39:0] conv_slice_ids_o,
    output logic [  5:0] conv_word_o,
    // 数值上等于扰动字：T8=8I+m 按本位权展开后与字逐位相同，故本端口不是独立观测量。
    output logic [  5:0] conv_total_eighths_o,
    output logic [ 39:0] conv_sample_sub_high_o,
    // --- 掩码与命令输出 ---
    output logic [  8:0] command_code_o,
    output logic [135:0] main_mask_o,  // 136 位主阵列转换掩码，按 slot 打包
    output logic [ 39:0] sub_mask_o,
    output logic [ 31:0] bridge_correction_q32_o,
    // --- 状态与错误 ---
    output logic         mask_valid_o,
    output logic         code_error_o
);
// =========================================================================
    // 2. 内部信号
// =========================================================================
    // request / publish 两级寄存把外部请求对齐到模块一描述符发布的那一个沿；
    // latest / previous 两槽快照是刻意的：晋升沿发布的字先在最新槽，下一沿
    // 移位到上一槽，未退休的旧组与刚晋升的新组可能同时各命中一槽。
  logic active, seed_accepted;
  logic dither_request_q, dither_publish_q;
  logic seed_request_q, seed_publish_q, seed_rejected_q;
  logic [30:0] dither_seed_request_q, dither_seed_publish_q;
  logic [39:0] scheduled_acq_ids;  // 模块一给出、仅在晋升沿有效的采集 slice 列表
  logic [31:0] scheduled_acq_sample_id;
  logic conv_group_valid, latest_word_pulse;
  logic latest_word_valid_q, previous_word_valid_q;  // 两槽各自的 valid，匹配时必须与 ID 同时比较
  logic [31:0] previous_sample_id_q;
  logic [5:0] previous_word_q;
  logic latest_match, previous_match;
  logic [5:0] selected_word;
  logic [2:0] upper_code;  // 值域 {0,1,4,5}，由 selected_word[5] 与 [3] 拼成
  logic upper_ok;
  logic [9:0] adjusted_code;  // 扩到 10 位再减，保留借位信息；下游只取低 9 位
  logic command_eligible;  // 合法性门禁，放在模块一之前

// =========================================================================
    // 3. 采样掩码函数与种子判据
// =========================================================================
    // 八片依有序 slot 分档：level 高三位取 word[5:3]，前 m=word[2:0] 个 slot
    // 再升一档。输出把 8C/4C/2C/1C 反序排到 bit0..3，bit4 dummy 恒 0。
    // 档 8 只把 8C 接高参考；打包顺序与四个二进制档位位相反，改动前先核对 SPEC。
  function automatic logic [39:0] sample_sub_high(input logic [5:0] word);
    logic [3:0] level;
    for (int slot = 0; slot < 8; slot++) begin
      level = {1'b0, word[5:3]} + {3'b0, (3'(slot) < word[2:0])};
      sample_sub_high[5*slot+:5] = {1'b0, level[0], level[1], level[2], level[3]};  // bit4 dummy 恒低，末个 1C 不参与选通
    end
  endfunction

    // 种子写入要求三个种子全非零且本沿无采集申请；任一不满足记 rejected
    // 并在下一沿报 seed_error_o。这里不检查 seed 与 DEM 的相位关系。
  assign active = rst_ni && !clear_i && enable_i;
  assign seed_accepted = seed_load_i && !advance_i && (slice_seed_i != '0)  // 同沿有采集申请即拒绝重载
                         && (dem_seed_i != '0) && (dither_seed_i != '0);

// =========================================================================
    // 4. 子模块例化
// =========================================================================
    // 模块一在此只承担调度与 Fig.11/21 转换掩码；acq_mask/conv_mask 特意悬空，
    // 因为八片采样位图由本模块按扰动字另算（sample_sub_high），两者不能混用。
    // 传给模块一的 code_i 是 adjusted_code[8:0]，即已扣除 U 的 k，不是原始粗码 c；
    // command_eligible 把合法性门禁放在模块一之前，非法码不会进入 DEM 状态机。
  module1_bridge_top u_module1 (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i),
      .enable_i(enable_i),
      .advance_i(advance_i && !seed_load_i),
      .eligible_i(eligible_i),
      .shuffle_i(shuffle_i),
      .dem_enable_i(dem_enable_i),
      .bridge_enable_i(bridge_enable_i),
      .seed_load_i(seed_accepted),
      .slice_seed_i(slice_seed_i),
      .dem_seed_i(dem_seed_i),
      .code_valid_i(command_eligible),
      .code_i(adjusted_code[8:0]),
      .code_sample_id_i(code_sample_id_i),
      .acq_ids_o(scheduled_acq_ids),
      .conv_ids_o(conv_slice_ids_o),
      .acq_mask_o(),
      .conv_mask_o(),
      .acq_valid_o(),
      .conv_valid_o(conv_group_valid),
      .acq_sample_id_o(scheduled_acq_sample_id),
      .conv_sample_id_o(conv_sample_id_o),
      .slot_accepted_o(slot_accepted_o),
      .stall_o(stall_o),
      .seed_error_o(),
      .slice_lfsr_state_o(),
      .dem_rng_state_o(),
      .acq_dem_enable_o(),
      .conv_dem_enable_o(),
      .acq_bridge_enable_o(),
      .conv_bridge_enable_o(),
      .acq_h_phase_o(),
      .acq_v_phase_o(),
      .conv_h_phase_o(),
      .conv_v_phase_o(),
      .main_mask_o(main_mask_o),
      .sub_mask_o(sub_mask_o),
      .correction_q32_o(bridge_correction_q32_o),
      .mask_valid_o(mask_valid_o),
      .code_error_o()
  );

    // 扰动字发生器同样用两级 publish：请求沿采 dither_enable_i，下一沿才放行，
    // 避免配置与模块一的采集接受沿错位而产生「字已变、描述符未变」的组合。
    // clear_i 与 !enable_i 都接到发生器复位，禁用时 PRBS31 也要回默认种子，
    // 否则重新使能后的第一笔字会依赖禁用前的历史状态。
  dither_word_gen u_word (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i || !enable_i),
      .dither_enable_i(dither_publish_q),
      .seed_load_i(seed_publish_q),
      .seed_i(dither_seed_publish_q),
      .sample_accept_i(slot_accepted_o),
      .sample_id_i(scheduled_acq_sample_id),
      .word_o(acq_word_o),
      .word_valid_o(latest_word_pulse),
      .word_sample_id_o(acq_sample_id_o),
      .rng_state_o(),
      .seed_error_o()
  );

// =========================================================================
    // 5. 时序状态（单时钟域同步时序）
// =========================================================================
    // 复位分支必须把两个快照槽、ID、字和采集 slice 列表一起清；只清 valid
    // 不清数据，复位释放后的下一笔会拿陈旧 ID 配出新字，波形上却看起来正常。
  always_ff @(posedge clk_i) begin
    if (!active) begin
      dither_request_q <= 1'b0;
      dither_publish_q <= 1'b0;
      seed_request_q <= 1'b0;
      seed_publish_q <= 1'b0;
      seed_rejected_q <= 1'b0;
      seed_error_o <= 1'b0;
      dither_seed_request_q <= '0;
      dither_seed_publish_q <= '0;
      latest_word_valid_q <= 1'b0;
      previous_word_valid_q <= 1'b0;
      previous_sample_id_q <= '0;
      previous_word_q <= '0;
      acq_slice_ids_o <= '0;
    end else begin
      dither_request_q <= dither_enable_i;
      dither_publish_q <= dither_request_q;
      seed_request_q <= seed_accepted;
      seed_publish_q <= seed_request_q;
      seed_rejected_q <= seed_load_i && !seed_accepted;
      seed_error_o <= seed_rejected_q;
      if (seed_accepted) dither_seed_request_q <= dither_seed_i;
      if (seed_request_q) dither_seed_publish_q <= dither_seed_request_q;

      if (slot_accepted_o) begin
        previous_word_valid_q <= latest_word_valid_q;
        previous_sample_id_q <= acq_sample_id_o;
        previous_word_q <= acq_word_o;
        latest_word_valid_q <= 1'b1;
        acq_slice_ids_o <= scheduled_acq_ids;
      end
    end
  end

// =========================================================================
    // 6. 描述符与命令输出
// =========================================================================
    // L8 = 16*D5 + m，m=word[2:0] <= 7 故 bit3 恒 0；{D5,1'b0,m} 与它逐位相同。
    // 这是位重排而非加法器，改位宽前先核对 SPEC 的分子定义，别顺手补成加法。
  assign acq_dither_valid_o = active && latest_word_pulse;
  assign acq_lower_eighths_o = {acq_word_o[4], 1'b0, acq_word_o[2:0]};
  assign acq_sample_sub_high_o = sample_sub_high(acq_word_o);

    // latest / previous 按 ID 二选一命中当前转换组；都未命中则 conv_ready_o=0，
    // 此刻送码由 code_error_o 明确拒绝，而不是被静默丢弃或错配到别的样本。
  assign latest_match = latest_word_valid_q && (acq_sample_id_o == conv_sample_id_o);
  assign previous_match = previous_word_valid_q && (previous_sample_id_q == conv_sample_id_o);
  assign conv_ready_o = active && conv_group_valid && (latest_match || previous_match);
  assign selected_word = slot_accepted_o ? acq_word_o : previous_word_q;  // 必须跟着晋升沿选，否则用上一笔的字配这一笔的掩码
  assign conv_word_o = conv_ready_o ? selected_word : 6'd0;
  // T8 与六位字同值：位权 32/16/8/4/2/1 恰是 8I+m 的展开，故这里是恒等透传而非缺逻辑。
  // 端口保留是为了对外声明量纲（1 个 LSB 等于 1/8 个原生档），不是额外的计算结果。
  assign conv_total_eighths_o = conv_word_o;
  assign conv_sample_sub_high_o = sample_sub_high(conv_word_o);

// =========================================================================
    // 7. 负注入算术（冻结契约 C9）
// =========================================================================
    // U 从已决码中减去，k = c - U；依据 US10505561B2 col 19-20 / PDF p33 / FIG.19。
    // 10 位无符号减法在 c<U 时回绕到 0x3FB..0x3FF，bit9 恒 1，因此原判据
    // !adjusted_code[9] 本来就能拒绝下溢；upper_ok 是加固与可读性，不是修 bug。
    // upper_code 只取 word[5] 与 word[3]，值域 {0,1,4,5}；其余位属分数档不进 U。
  assign upper_code = {selected_word[5], 1'b0, selected_word[3]};
  assign upper_ok = (code_i >= {6'b0, upper_code});
  assign adjusted_code = {1'b0, code_i} - {7'b0, upper_code};  // 10 位减法，c<U 时回绕但 bit9 恒 1
  assign command_eligible = code_valid_i && conv_ready_o && upper_ok;  // 三项同时成立才受理命令
    // mask_valid_o=0 时输出清零；外部不能把上一拍残留的 k 当成新命令。
  assign command_code_o = mask_valid_o ? adjusted_code[8:0] : 9'd0;
  assign code_error_o = active && code_valid_i && !mask_valid_o;  // 有请求却无合法掩码即报错，不静默丢样
endmodule
