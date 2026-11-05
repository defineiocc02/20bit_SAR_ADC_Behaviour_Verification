// 模块：sar_structural_ctrl；双coarse SAR、共享Flash及前代fine的结构控制。
// 职责：当前采样的3-bit Flash seed+双9-bit SAR，与前代residue的12-bit fine SAR并行。
// 端口/单位：比较器valid/data同属clk；9/12位码无符号，dither的8位packed值按二补数解释。
// 时钟/复位：clk上升沿、同步低有效rst_n；disable取消两代事务并清状态/错误。
// 前沿相位：0转交context/换bank；1推进pool并启动SAR；2..7粗比较；8截止；9保存residue。
// 前代fine：2..13比较，14锁存done/code，15在context有效且重构空闲时发start。
// 同沿规则：消费者读旧sid/随机值/已决码；RDAC只跟随resolved update，不加载pending trial。
// 配置：sampling与quantizer互斥；bridge在enable且sid[8]=1时活动；采样dither与RDAC使用匹配单位。
// 异常：valid缺失不停止全局相位，到deadline取消；错误保持至reset/disable或后续错误覆盖。
// 上下文：冻结实际物理ID/mask/rails及coarse/sid/dither，供下一周期fine与可选观测packet使用。
// 可选CAAZ：input chop属于acquiring，AZcapture属于current RA，fine tag属于旧residue。
// legacy ra_az每cycle保持；新增even-only capture只更新CBIAS，实际模拟开关/保持须独立验收。
// DEM联动：同一个dem_en同时驱动slice洗牌、bank内单元状态与bridge资格，没有独立停洗牌位。
// 模拟边界：quiet后chop已准备下一笔，物理acquiring组须到下一advance才换；
// 前笔电荷必须由宏内关闭锁存/inhibit保持，裸TP与组使能相与可能在这一tick重开采样。
// 来源：ADR0018及项目16tick预算；CAAZ见PKU_VLSI2025_C32_1_PAPER PDF2 Fig5/6。
`include "rtl_params.vh"
module sar_structural_ctrl #(
    parameter bit P_CAAZ_ENABLE = 0,
    parameter int P_REF_ON = 10,
    parameter int P_RESIDUE_CAPTURE = 9,
    parameter logic [31:0] P_SHUFFLE_SEED = 32'h6d2b79f5,
    parameter logic [31:0] P_DITHER_SEED = 32'h13579bdf
) (
    input wire clk, rst_n, enable, dem_en, bridge_en, sampling_en, quantizer_en,
    input wire [6:0] flash_therm,
    input wire flash_valid,
    input wire [1:0] coarse_cmp_valid, coarse_cmp_ge,
    input wire fine_cmp_valid, fine_cmp_ge,
    input wire signed [63:0] injection_q,
    input wire analog_bad, recon_busy,
    input wire caaz_az_capture_ok,
    output wire caaz_input_chop_neg,
    output wire caaz_az_capture_en,
    output wire caaz_az_hold,
    output wire [3:0] phase,
    output wire quiet_sample, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate,
    output wire [17:0] acquiring_mask, converting_mask,
    output wire [17:0] aux_charge_enable, hold_low_enable,
    output wire [1:0] coarse_compare_enable,
    output logic [1:0] coarse_acquire_enable,
    output logic flash_acquire_enable, fine_acquire_enable,
    output wire [1:0][8:0] coarse_trial,
    output wire [1:0][7:0] quantizer_dither,
    output wire [3:0] acquisition_dither_rails,
    output wire [11:0] fine_trial,
    output wire fine_compare_enable,
    output wire flash_sample,
    output wire [17:0] slice_sel,
    output wire [17:0][62:0] main_sw,
    output wire [17:0][7:0] sub_sw,
    output wire [17:0][3:0] dither_sw,
    output logic sw_valid,
    output wire recon_start,
    output wire [31:0] context_id,
    output wire [8:0] context_coarse, context_sid,
    output wire signed [7:0] context_dither,
    output wire context_chop_neg,
    output wire [31:0] context_az_pair_id,
    output wire context_az_valid,
    output wire [7:0][4:0] context_slices,
    output wire [7:0][62:0] context_main,
    output wire [7:0][7:0] context_sub,
    output wire [3:0] context_rails,
    output wire context_sampling, context_analog_bad,
    output wire signed [63:0] context_injection,
    output logic [11:0] fine_code,
    output logic [31:0] error_code
);
  `include "rtl_error_codes.vh"
  wire advance, capture;
  analog_phase_ctrl #(
      .P_REF_ON(P_REF_ON),
      .P_CAPTURE(P_RESIDUE_CAPTURE)
  ) u_phase (
      .clk(clk),
      .rst_n(rst_n),
      .enable(enable),
      .phase(phase),
      .quiet_sample(quiet_sample),
      .bank_advance(advance),
      .residue_capture(capture),
      .tp_clock(tp_clock),
      .ra_az(ra_az),
      .ra_amplify(ra_amplify),
      .ref_precharge(ref_precharge),
      .ref_accurate(ref_accurate)
  );

  wire [7:0][4:0] acq_ids, conv_ids;
  wire conv_valid;
  wire [31:0] current_id;
  wire acquisition_primed;
  wire current_chop_neg;
  wire [31:0] current_az_pair_id;
  wire current_az_valid;
  slice_pool_ctrl #(
      .P_SEED(P_SHUFFLE_SEED)
  ) u_pool (
      .clk(clk),
      .rst_n(rst_n),
      .enable(enable),
      .advance(advance),
      .shuffle(dem_en),
      .acquiring_ids(acq_ids),
      .converting_ids(conv_ids),
      .acquiring_mask(acquiring_mask),
      .converting_mask(converting_mask),
      .conversion_valid(conv_valid),
      .sample_id(current_id),
      .acquisition_primed(acquisition_primed)
  );

  // 旧ra_az继续每周期准备放大器/储能节点；若旧宏仍把它接到CBIAS采集开关， odd会重新抽取AZ噪声。
  // 新capture/hold引脚必须在真实宏中独立映射，不能仅改数字标签。
  caaz_cycle_ctrl #(.P_CAAZ_ENABLE(P_CAAZ_ENABLE)) u_caaz_cycle (
      .clk(clk),
      .rst_n(rst_n),
      .enable(enable),
      .quiet_sample(quiet_sample),
      .acquisition_primed(acquisition_primed),
      .phase(phase),
      .az_capture_ok(caaz_az_capture_ok),
      .input_chop_neg(caaz_input_chop_neg),
      .az_capture_en(caaz_az_capture_en),
      .az_hold(caaz_az_hold),
      .current_chop_neg(current_chop_neg),
      .current_az_pair_id(current_az_pair_id),
      .current_az_valid(current_az_valid)
  );

  // Aux supplies parasitic/boost charge while acquiring (patent13 Fig2/11). Optional capacitor
  // precharge embodiments require a separate analog macro.
  assign aux_charge_enable = (enable && rst_n) ? acquiring_mask : '0;
  assign hold_low_enable = (enable && rst_n) ? ~acquiring_mask : '1;
  assign flash_sample = quiet_sample;

  // Analog acquisition/compare enables must not see a multi-bit phase decode. Register the
  // next-phase decisions alongside the phase counter. In particular, carry skew at phase 7->8 must
  // not resemble phase 14 and reopen fine acquisition or cancel a comparator evaluation between
  // clock edges.
  logic coarse_cancel, fine_cancel;
  logic bank;
  wire [2:0] flash_code;
  sadc_enc #(.P_B1(3)) u_flash (
      .cmp_raw(flash_therm),
      .sadc_code(flash_code)
  );
  wire [1:0] coarse_busy, coarse_done, coarse_update;
  wire [1:0][8:0] resolved;
  for (genvar b = 0; b < 2; b++) begin : g_coarse
    sar_trial_ctrl #(
        .P_BITS(9),
        .P_SEED_BITS(3)
    ) u_sar (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .start(advance && flash_valid && bank == 1'(b)),
        .cancel(coarse_cancel),
        .seed_code({flash_code, 6'b0}),
        .cmp_valid(coarse_cmp_valid[b]),
        .cmp_ge(coarse_cmp_ge[b]),
        .trial_code(coarse_trial[b]),
        .resolved_code(resolved[b]),
        .busy(coarse_busy[b]),
        .done(coarse_done[b]),
        .resolved_valid(coarse_update[b]),
        .compare_enable(coarse_compare_enable[b])
    );
  end

  wire fine_busy, fine_done, fine_update;
  wire [11:0] fine_resolved;
  wire context_valid;
  sar_trial_ctrl #(
      .P_BITS(12),
      .P_SEED_BITS(0)
  ) u_fine (
      .clk(clk),
      .rst_n(rst_n),
      .enable(enable),
      .start(advance && context_valid),
      .cancel(fine_cancel),
      .seed_code(12'd0),
      .cmp_valid(fine_cmp_valid),
      .cmp_ge(fine_cmp_ge),
      .trial_code(fine_trial),
      .resolved_code(fine_resolved),
      .busy(fine_busy),
      .done(fine_done),
      .resolved_valid(fine_update),
      .compare_enable(fine_compare_enable)
  );

  wire [8:0] sid_a, sid_b;
  logic [8:0] sid;
  dem_state_gen u_dem (
      .clk(clk),
      .rst_n(rst_n && enable),
      .dem_en(dem_en),
      .load(1'b0),
      .init_a(9'd0),
      .init_b(9'd0),
      .en_a(advance && !bank),
      .en_b(advance && bank),
      .sid_a(sid_a),
      .sid_b(sid_b)
  );
  wire [62:0][5:0] main_order;
  wire [7:0][2:0] sub_order;
  dem_addr_gen u_order (
      .sid(sid),
      .main_logical(main_order),
      .sub_logical(sub_order)
  );
  wire signed [7:0] random_dither;
  wire random_valid;
  logic signed [7:0] acquiring_dither, current_dither;
  logic signed [63:0] current_injection;
  // D来自头文件整数range（当前2）；没有运行时幅度寄存器，也没有额外的dR范围增强。
  // analog dither如需使用，由外部模拟宏实际注入并提供同笔ADC输入域Q32 injection_q。
  dither_gen #(.SEED(P_DITHER_SEED)) u_random (
      .clk(clk),
      .rst_n(rst_n && enable),
      .en(advance),
      .dither_code(random_dither),
      .valid(random_valid)
  );
  for (genvar b = 0; b < 2; b++) begin : g_qdither
    assign quantizer_dither[b] = !quantizer_en ? 8'd0 :
        ((bank == 1'(b)) ? current_dither : acquiring_dither);
  end

  // For D=2: negative rails followed by positive rails, matching unit_therm.
  for (genvar d = 0; d < 4; d++) begin : g_acq_rails
    assign acquisition_dither_rails[d] = sampling_en && ((int'(d) - 2) < int'($signed(acquiring_dither)));
  end
  wire [7:0][6:0] main_count;
  wire [7:0][2:0] sub_count;
  wire [7:0][62:0] main_on;
  wire [7:0][7:0] sub_on;
  wire [3:0] rails;
  wire rdac_over;
  // sampling在采集轨引入扰动，RDAC随后加同单位量；quantizer由宏在比较器处施加dQ，
  // 这里从RDAC命令减去同一dQ。采样扰动选sub时每单位为一个子单位，选main需乘N_UNIT_SUB。
  // 这两种配置互斥。补偿符号定义在斩波后的ADC码域；不能盲目随input chop再翻一次符号。
  wire signed [15:0] sampling_dither = DITHER_SPLIT_IS_SUB
      ? 16'(current_dither) : (16'(current_dither) * 16'(N_UNIT_SUB));
  wire signed [15:0] rdac_dither_adjust = quantizer_en ? -16'(current_dither)
      : (sampling_en ? sampling_dither : 16'sd0);
  swap_decode u_decode (
      .coarse(resolved[bank]),
      .dither_code(rdac_dither_adjust),
      .dem_en(dem_en),
      .bridge_en(bridge_en && sid[8]),
      .sid_low(sid[2:0]),
      .main_count(main_count),
      .sub_count(sub_count),
      .rdac_over(rdac_over)
  );
  unit_therm u_units (
      .main_logical(main_order),
      .sub_logical(sub_order),
      .main_count(main_count),
      .sub_count(sub_count),
      .bank_dither(sampling_en ? current_dither : 8'sd0),
      .main_on(main_on),
      .sub_on(sub_on),
      .dither_rail(rails)
  );

  // resolved_valid与已决码在SAR沿后更新，driver到下一沿才读到它们；因此默认载荷为
  // Flash前缀+6个已决码共7次。重复的已决码仍是一次真实负载事件，不可去重成trial轨迹。
  wire load_rdac = enable && conv_valid && coarse_update[bank] && phase >= 2 && phase <= 8;
  rdac_drv u_rdac (
      .clk(clk),
      .rst_n(rst_n && enable),
      .load(load_rdac),
      .slice_id(conv_ids),
      .main_on(main_on),
      .sub_on(sub_on),
      .dither_rail(rails),
      .slice_sel(slice_sel),
      .main_sw(main_sw),
      .sub_sw(sub_sw),
      .dither_sw(dither_sw)
  );

  logic coarse_ok, fine_ok;
  cal_sample_context u_context (
      .clk(clk),
      .rst_n(rst_n),
      .enable(enable),
      .residue_capture(capture),
      .quiet_sample(quiet_sample),
      .current_valid(conv_valid && coarse_ok),
      .current_id(current_id),
      .current_coarse(resolved[bank]),
      .current_sid(sid),
      .current_dither(current_dither),
      .current_chop_neg(current_chop_neg),
      .current_az_pair_id(current_az_pair_id),
      .current_az_valid(current_az_valid),
      .current_slices(conv_ids),
      .current_main(main_on),
      .current_sub(sub_on),
      .current_rails(rails),
      .current_sampling(sampling_en),
      .current_injection(current_injection),
      .analog_bad(analog_bad),
      .current_bad(rdac_over),
      .fine_valid(context_valid),
      .fine_id(context_id),
      .fine_coarse(context_coarse),
      .fine_sid(context_sid),
      .fine_dither(context_dither),
      .fine_chop_neg(context_chop_neg),
      .fine_az_pair_id(context_az_pair_id),
      .fine_az_valid(context_az_valid),
      .fine_slices(context_slices),
      .fine_main(context_main),
      .fine_sub(context_sub),
      .fine_rails(context_rails),
      .fine_sampling(context_sampling),
      .fine_analog_bad(context_analog_bad),
      .fine_injection(context_injection)
  );

  assign recon_start = enable && phase == 15 && context_valid && fine_ok && !recon_busy;

  always_ff @(posedge clk) begin
    // Synchronous reset/disable cancels both transactions and clears errors.
    if (!rst_n || !enable) begin
      flash_acquire_enable <= 0;
      fine_acquire_enable <= 0;
      coarse_cancel <= 0;
      fine_cancel <= 0;
      coarse_acquire_enable <= 2'b00;
      bank <= 0;
      sid <= '0;
      acquiring_dither <= 0;
      current_dither <= 0;
      current_injection <= 0;
      coarse_ok <= 0;
      fine_ok <= 0;
      fine_code <= 0;
      sw_valid <= 0;
      error_code <= 0;
    end else begin
      // Fixed 16-phase look-ahead: next phase is (phase+1) modulo 16. These registered values
      // occupy the interval AFTER this rising edge: flash acquisition is low only at phase 1; fine
      // acquisition is high at phases 14, 15 and 0. This is the implemented macro boundary timing.
      flash_acquire_enable <= phase != 0;
      fine_acquire_enable <= phase >= 13;
      // Keep cancellation asserted through the edge that clears busy. Its release then cannot race
      // busy's falling edge in compare_enable. Coarse cancel spans current phases 8..9, fine cancel
      // spans 14..15. done is therefore sampled on the deadline edge before SAR clears it.
      coarse_cancel <= phase == 7 || phase == 8;
      fine_cancel <= phase == 13 || phase == 14;
      sw_valid <= load_rdac;
      if (quiet_sample) begin
        // 同沿先读旧acquiring数据归给本笔current，再由下方advance在下一tick 安装下一采集组/扰动；
        // 不能将实时bank或随机数直接接到旧residue重构。
        bank <= !bank;
        current_dither <= acquiring_dither;
        current_injection <= injection_q;
        coarse_ok <= 0;
        fine_ok <= 0;
      end
      if (advance) begin
        coarse_acquire_enable <= bank ? 2'b01 : 2'b10;
        // DEM与PRNG同沿也推进，但这里冻结旧输出；sid按当前coarse bank消费，新random码归下一采集组。
        // sample ID/物理slice ID/DEM sid是三种不同索引。
        sid <= bank ? sid_b : sid_a;
        acquiring_dither <= ((sampling_en || quantizer_en) && random_valid) ? random_dither : 8'sd0;
        if (!flash_valid) error_code <= ERR_SADC_NOT_READY;
      end
      if (phase == 8) begin
        coarse_ok <= coarse_done[bank];
        if (conv_valid && !coarse_done[bank]) error_code <= ERR_SADC_NOT_READY;
      end
      if (phase == 14) begin
        fine_ok <= fine_done;
        fine_code <= fine_resolved;
        if (context_valid && !fine_done) error_code <= ERR_ADC2_NOT_READY;
      end
      // Error is retained until reset/disable or a later fault overwrites it. If fault conditions
      // coincide, the last executed assignment wins: RECON_BUSY > RDAC_RANGE > ADC2_NOT_READY >
      // SADC_NOT_READY.
      if (capture && conv_valid && rdac_over) error_code <= ERR_RDAC_RANGE;
      if (phase == 15 && context_valid && fine_ok && recon_busy) error_code <= ERR_RECON_BUSY;
    end
  end
endmodule
