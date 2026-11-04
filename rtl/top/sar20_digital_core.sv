// 模块：sar20_digital_core；配置、结构控制、物理权重校正与观测的完整数字核。
// 接口/单位：比较器与配置总线同属clk；W为48位unsigned Q30，电压为64位signed Q32，输出20位offset-binary。
// 时钟/复位：clk上升沿、同步低有效rst_n；无自动CDC；cfg_clear撤销epoch并取消在途转换。
// 配置：1278物理权重及三标量完整、min<max、dither互斥后validate；ACTIVE期间拒写。
// 控制写：0x1018的四位经三沿等待后原子安装；冲突不自动重试，clear取消未提交序列。
// 模式：P_STRUCTURAL为精化参数；默认双9-bit seeded SAR/共享3-bit Flash/18slice池/前代12-bit fine。
// 兼容模式：核外coarse/fine码在phase8/14捕获；两模式共享配置与定点重构，不支持运行时切换。
// 周期：16tick帧；重构延迟ceil(63/P_RECON_STAGES)+2，P5/P6/P7为15/13/11完整拍。
// 结果：dout_valid是完成事件，必须同时消费ID/flags/dout_analog_bad；错误可能保留上一合法字。
// 观测：P_CAL_OBSERVE启用748位同接受沿packet，无背压或片外传输保证；元数据来自冻结context。
// 错误优先级：bus>calibration>weight rejection>CAAZ pair>profile controller；status与flags语义不同。
// 实现边界：片外求系数、片上应用；不包含在线学习器、模拟宏、CDC或ASIC/板级时序签核。
// P_CAAZ_ENABLE默认关闭；开启仅支持结构模式，主输出为raw/2的pair，额外保留92位raw观测。
// 原748位cal_obs不变，34位CAAZ sideband另接；odd采样只保持CBIAS，不取消legacy RA reset。
// 来源：RTL_ARITHMETIC_CONTRACT.md、ADR0014/0017/0018；CAAZ为PKU C32-1来源约束下工程实现。
`include "rtl_params.vh"

module sar20_digital_core #(
    parameter bit P_STRUCTURAL = 1,
    parameter int P_RECON_STAGES = 7,
    parameter int P_REF_ON = 10,
    parameter int P_RESIDUE_CAPTURE = 9,
    parameter bit P_CAL_OBSERVE = 0,
    parameter bit P_CAAZ_ENABLE = 1'b0,
    parameter logic [31:0] P_SHUFFLE_SEED = 32'h6d2b79f5,
    parameter logic [31:0] P_DITHER_SEED = 32'h13579bdf
) (
    input  logic        clk,
    input  logic        rst_n,
    // Structural macro boundary; active when P_STRUCTURAL=1 (default).
    input wire [6:0] flash_therm,
    input wire flash_valid,
    input wire [1:0] coarse_cmp_valid, coarse_cmp_ge,
    input wire fine_cmp_valid, fine_cmp_ge,
    output wire [3:0] analog_phase,
    output wire quiet_sample, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate,
    output wire [17:0] acquiring_mask, converting_mask, aux_charge_enable, hold_low_enable,
    output wire [1:0] coarse_compare_enable,coarse_acquire_enable,
    output wire flash_acquire_enable,fine_acquire_enable,
    output wire [1:0][8:0] coarse_trial,
    output wire [1:0][7:0] quantizer_dither,
    output wire [3:0] acquisition_dither_rails,
    output wire [11:0] fine_trial,
    output wire fine_compare_enable, flash_sample,
    // ---- 配置/寄存器接口 ----
    input  logic        cfg_wr,
    input  logic [15:0] cfg_addr,
    input  logic [63:0] cfg_wdata,
    output logic [63:0] cfg_rdata,
    input  logic        cfg_validate,
    input  logic        cfg_clear_valid,
    output logic        cfg_ready,
    // ---- 模拟域回读（数字输入）----
    input  logic [B1-1:0]        sadc_code,
    input  logic                 sadc_rdy,
    input  logic [ADC2_BITS-1:0] adc2_code,
    input  logic                 adc2_rdy,
    input  logic                 ra_sat,
    input  logic                 rdac_ovf,
    // adc2_over表示真实后端电压严格超出配置区间；到轨码不能区分恰好到轨与越轨饱和。
    // 该标志与ra_sat/rdac_ovf均由外部模拟宏提供，不能由adc2_code反推。
    input  logic                 adc2_over,
    input  logic signed [V_BITS-1:0] inj_q,     // 本样本已知注入，Q32
    // ---- 数字输出到模拟域 ----
    output logic [N_SLICES-1:0]                       slice_sel,
    output logic [N_SLICES-1:0][N_UNIT_MAIN-1:0]      main_sw,
    output logic [N_SLICES-1:0][N_UNIT_SUB-1:0]       sub_sw,
    output logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] dither_sw,
    output logic                                      sw_valid,
    // ---- 最终输出 ----
    output logic [OUT_BITS-1:0] dout,
    output logic                dout_valid,
    output logic                clip_low,
    output logic                clip_high,
    output logic                analog_ovf,
    output logic                acc_ovf,
    // ---- 观测 ----
    output logic [31:0]         status_word,
    output logic [31:0]         dout_sample_id,
    output logic [4:0]          dout_flags,
    // Optional same-edge calibration packet; see the documented 748-bit layout.
    output wire                 cal_obs_valid,
    output wire [747:0]         cal_obs_data,
    output wire                 dout_analog_bad,
    // Optional CAAZ macro controls and sample-domain observation. The legacy
    // ra_az reset/preparation output remains active every cycle.
    input wire                  caaz_az_capture_ok,
    output wire                 caaz_input_chop_neg,
    output wire                 caaz_az_capture_en,
    output wire                 caaz_az_hold,
    output wire [33:0]          cal_obs_caaz_data,
    output wire                 caaz_raw_valid,
    output wire [91:0]          caaz_raw_data,
    output wire signed [20:0]   caaz_pair_difference,
    output wire                 caaz_pair_difference_valid,
    output wire [31:0]          caaz_pair_first_sample_id,
    output wire [31:0]          caaz_pair_id,
    output wire                 caaz_pair_fault,
    output wire [3:0]           caaz_pair_fault_code
);

  //=========================================================================
  // 0) 精化期一致性断言（零成本：只做常量比较，不生成逻辑）
  //=========================================================================
  // 精化期拒绝头文件、端口几何或实例参数与冻结数字合同不一致的配置。
  initial begin
    if (P_CAAZ_ENABLE && !P_STRUCTURAL)
      $fatal(1, "sar20_digital_core: CAAZ requires the structural profile");
    if (P_RECON_STAGES < 5 || P_RECON_STAGES > 63)
      $fatal(1, "sar20_digital_core: divider throughput exceeds 16-tick frame budget");
    if (N_ACTIVE      != 8)   $fatal(1, "sar20_digital_core: N_ACTIVE != 8");
    if (N_SLICES      != 18)  $fatal(1, "sar20_digital_core: N_SLICES != 18");
    if (N_UNIT_MAIN   != 63)  $fatal(1, "sar20_digital_core: N_UNIT_MAIN != 63");
    if (N_UNIT_SUB    != 8)   $fatal(1, "sar20_digital_core: N_UNIT_SUB != 8");
    if (N_UNIT_TOTAL  != 71)  $fatal(1, "sar20_digital_core: N_UNIT_TOTAL != 71");
    if (DAC_LEVELS    != 512) $fatal(1, "sar20_digital_core: DAC_LEVELS != 512");
    if (B1            != 9)   $fatal(1, "sar20_digital_core: B1 != 9");
    if (ADC2_BITS     != 12)  $fatal(1, "sar20_digital_core: ADC2_BITS != 12");
    if (OUT_BITS      != 20)  $fatal(1, "sar20_digital_core: OUT_BITS != 20");
    if (W_BITS        != 48)  $fatal(1, "sar20_digital_core: W_BITS != 48");
    if (V_BITS        != 64)  $fatal(1, "sar20_digital_core: V_BITS != 64");
    if (W_FRAC        != 30)  $fatal(1, "sar20_digital_core: W_FRAC != 30");
    if (V_FRAC        != 32)  $fatal(1, "sar20_digital_core: V_FRAC != 32");
    if (ACC_BITS      != 96)  $fatal(1, "sar20_digital_core: ACC_BITS != 96");
    if (PHASES        != 16)  $fatal(1, "sar20_digital_core: PHASES != 16");
    if (((1 << B1) - 1) != 511) $fatal(1, "sar20_digital_core: SADC 温度计宽度 != 511");
    if (int'(N_UNIT_MAIN) + int'(N_UNIT_SUB) != int'(N_UNIT_TOTAL))
      $fatal(1, "sar20_digital_core: 主+子单位数 != 总单位数");
  end

  //=========================================================================
  // 1) 错误码常量（静态共享头文件；不是参数导出器的生成物）
  //=========================================================================
  `include "rtl_error_codes.vh"

  localparam logic [W_BITS-1:0] W_MAX = 48'd1 << 47;  // 2^47

  //=========================================================================
  // 2) 配置地址译码
  //=========================================================================
  //  0x0000 + u*8        (u = 0..70)  权重数据（slice 由窗口寄存器选）
  //  0x1000 / 0x1008 / 0x1010         offset_q / adc2_min_q / adc2_max_q
  //  0x1018                           控制位 {…, quantizer_dither_en, sampling_mask_en, bridge_en, dem_en}
  //  0x1020                           status_word（只读）
  //  0x2000 + s*0x100                权重窗口：slice 号 s
  logic        is_weight, is_calib, is_win;
  logic        is_off, is_min, is_max, is_ctrl, is_stat;
  logic [6:0]  dw_unit;
  logic [4:0]  win_slice;

  assign is_weight  = (cfg_addr[15:12] == 4'h0);
  assign is_calib   = (cfg_addr[15:12] == 4'h1);
  assign is_win     = (cfg_addr[15:13] == 3'b001);
  assign dw_unit    = cfg_addr[9:3];
  assign win_slice  = cfg_addr[12:8] & 5'h1F;

  assign is_off     = is_calib && (cfg_addr[11:0] == 12'h000);
  assign is_min     = is_calib && (cfg_addr[11:0] == 12'h008);
  assign is_max     = is_calib && (cfg_addr[11:0] == 12'h010);
  assign is_ctrl    = is_calib && (cfg_addr[11:0] == 12'h018);
  assign is_stat    = is_calib && (cfg_addr[11:0] == 12'h020);

  // Cross-profile signals are declared before either controller first uses them.
  wire [17:0] legacy_slice_sel,structural_slice_sel;
  wire [17:0][62:0] legacy_main_sw,structural_main_sw;
  wire [17:0][7:0] legacy_sub_sw,structural_sub_sw;
  wire [17:0][3:0] legacy_dither_sw,structural_dither_sw;
  logic legacy_sw_valid;
  wire structural_sw_valid,structural_launch;
  wire [31:0] structural_error,structural_id;
  wire [8:0] structural_coarse, structural_sid;
  wire signed [7:0] structural_dither;
  wire structural_chop_neg;
  wire [31:0] structural_az_pair_id;
  wire structural_az_valid;
  wire [7:0][4:0] structural_ids;
  wire [7:0][62:0] structural_main;
  wire [7:0][7:0] structural_sub;
  wire [3:0] structural_rails;
  wire structural_sampling,structural_analog_bad;
  wire signed [63:0] structural_injection;
  wire [11:0] structural_fine;

  // ---- 权重窗口寄存器 ----
  logic [4:0] cur_slice;
  logic cfg_bus_ok, cfg_addr_valid, cfg_bus_reject, bad_weight_width;
  logic weight_addr_valid, window_addr_valid, bad_control_bits;
  wire epoch_rst_n = rst_n && !cfg_clear_valid;
  logic [31:0] bus_err;
  logic weights_ready;
  logic rc_busy;

  assign weight_addr_valid = is_weight && cfg_addr[11:10] == 0 && cfg_addr[2:0] == 0
                             && int'(dw_unit) < int'(N_UNIT_TOTAL);
  assign window_addr_valid = is_win && cfg_addr[7:0] == 0 && int'(win_slice) < int'(N_SLICES);
  assign cfg_addr_valid = weight_addr_valid || is_off || is_min || is_max || is_ctrl || window_addr_valid;
  assign bad_weight_width = is_weight && ((|cfg_wdata[63:int'(W_BITS)]) ||
                             cfg_wdata[W_BITS-1:0] == 0 || cfg_wdata[W_BITS-1:0] >= W_MAX);
  assign bad_control_bits = is_ctrl && (|cfg_wdata[63:4]);
  assign cfg_bus_ok = cfg_wr && cfg_addr_valid && !bad_weight_width && !bad_control_bits
                     && !cfg_ready && !cfg_validate && !cfg_clear_valid && ctrl_seq == 0;
  assign cfg_bus_reject = cfg_wr && !cfg_bus_ok;

  always_ff @(posedge clk) begin
    if (!rst_n || cfg_clear_valid)
      bus_err <= ERR_NONE;
    else if (cfg_bus_reject)
      bus_err <= bad_weight_width ? ERR_W_RANGE : ERR_CFG_WRITE;
    else if (ws_err_write)
      bus_err <= ERR_W_SUM;
    else if (cfg_validate && ctrl_seq == 0 && !rc_busy)
      bus_err <= ERR_NONE;
  end

  always_ff @(posedge clk) begin
    if (!epoch_rst_n) begin
      cur_slice <= 5'd0;
    end else if (cfg_bus_ok && is_win) begin
      cur_slice <= win_slice;
    end
  end

  // ---- 0x1018：保持三拍等待窗口，在 ctrl_seq=1 的时钟沿原子写入四位 ----
  // validate 冲突暂停序列；clear 取消尚未提交的整笔控制写。
  logic [1:0] ctrl_seq;
  logic [3:0] ctrl_bits;

  always_ff @(posedge clk) begin
    if (!rst_n || cfg_clear_valid) begin
      ctrl_seq  <= 2'd0;
      ctrl_bits <= 4'd0;
    end else begin
      if (cfg_bus_ok && is_ctrl) begin
        ctrl_seq  <= 2'd3;
        ctrl_bits <= cfg_wdata[3:0];
      end else if (ctrl_seq != 2'd0 && !cfg_validate) begin
        ctrl_seq <= ctrl_seq - 2'd1;
      end
    end
  end

  //=========================================================================
  // 3) 寄存器子系统
  //=========================================================================
  logic [N_SLICES-1:0][N_UNIT_TOTAL-1:0][W_BITS-1:0] w_q;
  wire [N_SLICES-1:0][63:0] row_total;
  wire [W_BITS-1:0] selected_weight;
  logic        ws_err_write;
  logic        ws_wr_en;

  assign ws_wr_en = cfg_bus_ok && is_weight && (cfg_addr[2:0] == 3'b000)
                   && (dw_unit < 7'(N_UNIT_TOTAL));

  weight_store #(
      .P_N_SLICES (int'(N_SLICES)),
      .P_N_UNITS  (int'(N_UNIT_TOTAL))
  ) u_wstore (
      .clk       (clk),
      .rst_n     (rst_n),
      .cfg_ready (cfg_ready),
      .clear_load(cfg_clear_valid),
      .load_complete(weights_ready),
      .wr_en     (ws_wr_en),
      .wr_slice  (cur_slice),
      .wr_unit   (dw_unit),
      .wr_data   (cfg_wdata[W_BITS-1:0]),
      .err_write (ws_err_write),
      .w_q       (w_q),
      .row_total (row_total),
      .selected_weight(selected_weight)
  );

  logic [31:0]              cal_err_code;
  logic signed [V_BITS-1:0] c_off, c_min, c_max;
  logic                     c_dem_en, c_bridge_en, c_smask_en, c_qdither_en;
  logic                     cal_wr_en;
  logic [3:0]               cal_sel;
  logic                     cal_data_b;

  assign cal_wr_en = cfg_bus_ok && (is_off | is_min | is_max);

  always_comb begin
    cal_sel    = 4'd0;
    cal_data_b = 1'b0;
    if (cfg_bus_ok && is_off) begin
      cal_sel = 4'd0;
    end else if (cfg_bus_ok && is_min) begin
      cal_sel = 4'd1;
    end else if (cfg_bus_ok && is_max) begin
      cal_sel = 4'd2;
    end
  end

  calib_regs #(
      .P_ADC2_BITS (int'(ADC2_BITS))
  ) u_calib (
      .clk              (clk),
      .rst_n            (rst_n),
      .wr_en            (cal_wr_en),
      .sel              (cal_sel),
      .data_v           (cfg_wdata[V_BITS-1:0]),
      .data_b           (cal_data_b),
      .controls_write   (ctrl_seq == 1 && !cfg_validate),
      .controls_data    (ctrl_bits),
      .quantizer_dither_en(c_qdither_en),
      .weights_ready    (weights_ready),
      .config_busy      (cfg_wr || ctrl_seq != 0 || rc_busy),
      .validate         (cfg_validate),
      .clear_valid      (cfg_clear_valid),
      .cfg_ready        (cfg_ready),
      .err_code         (cal_err_code),
      .offset_q         (c_off),
      .adc2_min_q       (c_min),
      .adc2_max_q       (c_max),
      .dem_en           (c_dem_en),
      .bridge_en        (c_bridge_en),
      .sampling_mask_en (c_smask_en)
  );

  // err_code 合并：总线协议错误优先，其次校验错误、权重拒绝。
  logic [31:0] merged_err, input_err;
  always_comb begin
    if (bus_err != ERR_NONE) begin
      merged_err = bus_err;
    end else if (cal_err_code != ERR_NONE) begin
      merged_err = cal_err_code;
    end else if (ws_err_write) begin
      if (cfg_ready) begin
        merged_err = ERR_CFG_WRITE;
      end else if ((cfg_wdata[W_BITS-1:0] == {W_BITS{1'b0}}) ||
                   (cfg_wdata[W_BITS-1:0] >= W_MAX)) begin
        merged_err = ERR_W_RANGE;
      end else begin
        merged_err = ERR_W_SUM;
      end
    end else begin
      merged_err = caaz_pair_fault ? ERR_CAAZ_PAIR :
          (P_STRUCTURAL ? structural_error : input_err);
    end
  end

  //=========================================================================
  // 4) 相位与流水控制
  //=========================================================================
  logic [PHASES-1:0]         phase_onehot;
  logic [$clog2(PHASES)-1:0] phase;
  logic                      sample_en, acq_phase;
  logic                      sadc_latch, dem_advance, rdac_load, adc2_latch, recon_start;
  logic [31:0]               fsm_sample_idx;

  ctrl_fsm #(
      .P_PHASES (int'(PHASES))
  ) u_ctrl (
      .clk          (clk),
      .rst_n        (epoch_rst_n),
      .cfg_ready    (cfg_ready && !P_STRUCTURAL),
      .run          (cfg_ready),          // 配置生效后连续运行
      .phase_onehot (phase_onehot),
      .phase        (phase),
      .sample_en    (sample_en),
      .acq_phase    (acq_phase),
      .sadc_latch   (sadc_latch),
      .dem_advance  (dem_advance),
      .rdac_load    (rdac_load),
      .adc2_latch   (adc2_latch),
      .recon_start  (recon_start),
      .sample_idx   (fsm_sample_idx)
  );

  //=========================================================================
  // 5) slice 分配
  //=========================================================================
  logic [N_ACTIVE-1:0][4:0] acq_slices, conv_slices, grp;
  logic                     conv_valid, bank;
  logic [31:0]              alloc_sample_idx;

  slice_alloc u_alloc (
      .clk         (clk),
      .rst_n       (epoch_rst_n),
      .cfg_ready   (cfg_ready),
      .sample_en   (sample_en),
      .acq_slices  (acq_slices),
      .conv_slices (conv_slices),
      .conv_valid  (conv_valid),
      .bank        (bank),
      .sample_idx  (alloc_sample_idx),
      .group       (grp)
  );

  // Fixed-phase synchronous interface: sample coarse at phase 8 and the fine
  // code/injection/analog flags together at phase 14. Ready is a deadline, not
  // a CDC synchronizer or a request to stall the 16-phase schedule.
  logic [B1-1:0] sadc_hold;
  logic [ADC2_BITS-1:0] adc2_hold;
  logic signed [V_BITS-1:0] inj_hold;
  logic sadc_ok, adc2_ok, analog_sample;
  wire legacy_launch = recon_start && sadc_ok && adc2_ok && !rc_busy;
  wire launch_recon = P_STRUCTURAL ? structural_launch : legacy_launch;
  always_ff @(posedge clk) begin
    if (!epoch_rst_n) begin
      sadc_hold <= '0;
      adc2_hold <= '0;
      inj_hold <= '0;
      sadc_ok <= 1'b0;
      adc2_ok <= 1'b0;
      analog_sample <= 1'b0;
      input_err <= ERR_NONE;
    end else begin
      if (sample_en) begin
        sadc_ok <= 1'b0;
        adc2_ok <= 1'b0;
        analog_sample <= 1'b0;
      end
      if (sadc_latch) begin
        sadc_ok <= sadc_rdy;
        if (sadc_rdy)
          sadc_hold <= sadc_code;
        else
          input_err <= ERR_SADC_NOT_READY;
      end
      if (adc2_latch) begin
        adc2_ok <= adc2_rdy;
        if (adc2_rdy) begin
          adc2_hold <= adc2_code;
          inj_hold <= inj_q;
          analog_sample <= rdac_ovf | adc2_over | ra_sat | rdac_over;
        end else
          input_err <= ERR_ADC2_NOT_READY;
      end
      if (recon_start && sadc_ok && adc2_ok && rc_busy)
        input_err <= ERR_RECON_BUSY;
    end
  end

  //=========================================================================
  // 6) DEM：状态序列 -> 地址逻辑位置
  //=========================================================================
  logic [8:0] sid_a, sid_b, sid_cur, sid_hold;

  dem_state_gen #(
      .A_RED (int'(DEM_LCG_A_MOD)),
      .W     (9)
  ) u_dem (
      .clk    (clk),
      .rst_n  (epoch_rst_n),
      .dem_en (c_dem_en),                 // 参数头复位默认0；运行时配置控制
      .load   (1'b0),                     // 初值恒 0，与模型一致
      .init_a (9'd0),
      .init_b (9'd0),
      .en_a   (dem_advance && !bank),     // 只推进本次样本用到的那一个 bank
      .en_b   (dem_advance &&  bank),
      .sid_a  (sid_a),
      .sid_b  (sid_b)
  );

  assign sid_cur = bank ? sid_b : sid_a;

  // 在 dem_advance 那一拍捕获"本次样本用的"状态，之后再推进。
  always_ff @(posedge clk) begin
    if (!epoch_rst_n) begin
      sid_hold <= 9'd0;
    end else if (dem_advance) begin
      sid_hold <= sid_cur;
    end
  end

  logic [N_UNIT_MAIN-1:0][5:0] main_logical;
  logic [N_UNIT_SUB-1:0][2:0]  sub_logical;

  dem_addr_gen u_daddr (
      .sid          (sid_hold),
      .main_logical (main_logical),
      .sub_logical  (sub_logical)
  );

  //=========================================================================
  // 7) dither（片上随机源，统计验收；按模式与 valid 门控寄存）
  //=========================================================================
  logic signed [7:0] dith_code;
  logic              dith_valid;
  logic signed [7:0] dith_q;

  dither_gen #(
      .D    (int'(DITHER_UNITS_RANGE)),
      .SEED (P_DITHER_SEED)
  ) u_dith (
      .clk         (clk),
      .rst_n       (epoch_rst_n),
      .en          (dem_advance && (c_smask_en || c_qdither_en)),
      .dither_code (dith_code),
      .valid       (dith_valid)
  );

  always_ff @(posedge clk) begin
    if (!epoch_rst_n) begin
      dith_q <= 8'sd0;
    end else if (dem_advance && (c_smask_en || c_qdither_en) && dith_valid) begin
      dith_q <= dith_code;
    end
  end

  //=========================================================================
  // 8) 开关译码 -> 温度计展开
  //=========================================================================
  logic [N_ACTIVE-1:0][6:0]             main_count;
  logic [N_ACTIVE-1:0][2:0]             sub_count;
  logic                                 rdac_over;
  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] main_on;
  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  sub_on;
  logic [2*DITHER_UNITS_RANGE-1:0]      dither_rail;

  // Separate named nets preserve the sampling/quantizer paths and provide
  // legal force/release points for L3 (do not force child input variables).
  wire signed [15:0] swap_dither_code;
  wire signed [7:0] sampling_dither_code;
  assign swap_dither_code = c_qdither_en ? -16'($signed(dith_q))
      : (c_smask_en ? (DITHER_SPLIT_IS_SUB ? 16'($signed(dith_q))
          : 16'($signed(dith_q)) * 16'(N_UNIT_SUB)) : 16'sd0);
  assign sampling_dither_code = c_smask_en ? dith_q : 8'sd0;

  swap_decode u_swap (
      .coarse      (sadc_hold),                     // 兼容模式：核外编码、相位 8 捕获
      .dither_code (swap_dither_code),      // 显式符号扩展到 16 位
      .dem_en      (c_dem_en),
      // Match the structural profile: bridge exchange is active only for
      // the upper half of the frozen nine-bit DEM-state space.
      .bridge_en   (c_bridge_en && sid_hold[8]),
      .sid_low     (sid_hold[2:0]),
      .main_count  (main_count),
      .sub_count   (sub_count),
      .rdac_over   (rdac_over)
  );

  unit_therm u_therm (
      .main_logical (main_logical),
      .sub_logical  (sub_logical),
      .main_count   (main_count),
      .sub_count    (sub_count),
      .bank_dither  (sampling_dither_code),
      .main_on      (main_on),
      .sub_on       (sub_on),
      .dither_rail  (dither_rail)
  );

  //=========================================================================
  // 9) RDAC 扇出
  //=========================================================================
  rdac_drv u_rdrv (
      .clk         (clk),
      .rst_n       (epoch_rst_n),
      .load        (rdac_load && sadc_ok),
      .slice_id    (conv_slices),
      .main_on     (main_on),
      .sub_on      (sub_on),
      .dither_rail (dither_rail),
      .slice_sel   (legacy_slice_sel),
      .main_sw     (legacy_main_sw),
      .sub_sw      (legacy_sub_sw),
      .dither_sw   (legacy_dither_sw)
  );

  always_ff @(posedge clk) begin                                       // 开关加载事件打一拍
    if (!epoch_rst_n)
      legacy_sw_valid <= 1'b0;
    else
      legacy_sw_valid <= rdac_load && sadc_ok;
  end


  // Production structure: registered analog controls, dual coarse SAR,
  // shared flash seed, fine SAR and previous-residue physical context.
  sar_structural_ctrl #(
      .P_CAAZ_ENABLE(P_CAAZ_ENABLE),
      .P_REF_ON(P_REF_ON),
      .P_RESIDUE_CAPTURE(P_RESIDUE_CAPTURE),
      .P_SHUFFLE_SEED(P_SHUFFLE_SEED),
      .P_DITHER_SEED(P_DITHER_SEED)
  ) u_structure (
      .clk(clk),
      .rst_n(epoch_rst_n),
      .enable(cfg_ready && P_STRUCTURAL),
      .dem_en(c_dem_en),
      .bridge_en(c_bridge_en),
      .sampling_en(c_smask_en),
      .quantizer_en(c_qdither_en),
      .flash_therm(flash_therm),
      .flash_valid(flash_valid),
      .coarse_cmp_valid(coarse_cmp_valid),
      .coarse_cmp_ge(coarse_cmp_ge),
      .fine_cmp_valid(fine_cmp_valid),
      .fine_cmp_ge(fine_cmp_ge),
      .injection_q(inj_q),
      .analog_bad(ra_sat || rdac_ovf || adc2_over),
      .recon_busy(rc_busy),
      .caaz_az_capture_ok(caaz_az_capture_ok),
      .caaz_input_chop_neg(caaz_input_chop_neg),
      .caaz_az_capture_en(caaz_az_capture_en),
      .caaz_az_hold(caaz_az_hold),
      .phase(analog_phase),
      .quiet_sample(quiet_sample),
      .tp_clock(tp_clock),
      .ra_az(ra_az),
      .ra_amplify(ra_amplify),
      .ref_precharge(ref_precharge),
      .ref_accurate(ref_accurate),
      .acquiring_mask(acquiring_mask),
      .converting_mask(converting_mask),
      .aux_charge_enable(aux_charge_enable),
      .hold_low_enable(hold_low_enable),
      .coarse_compare_enable(coarse_compare_enable),
      .coarse_trial(coarse_trial),
      .coarse_acquire_enable(coarse_acquire_enable),
      .flash_acquire_enable(flash_acquire_enable),
      .fine_acquire_enable(fine_acquire_enable),
      .quantizer_dither(quantizer_dither),
      .acquisition_dither_rails(acquisition_dither_rails),
      .fine_trial(fine_trial),
      .fine_compare_enable(fine_compare_enable),
      .flash_sample(flash_sample),
      .slice_sel(structural_slice_sel),
      .main_sw(structural_main_sw),
      .sub_sw(structural_sub_sw),
      .dither_sw(structural_dither_sw),
      .sw_valid(structural_sw_valid),
      .recon_start(structural_launch),
      .context_id(structural_id),
      .context_coarse(structural_coarse),
      .context_sid(structural_sid),
      .context_dither(structural_dither),
      .context_chop_neg(structural_chop_neg),
      .context_az_pair_id(structural_az_pair_id),
      .context_az_valid(structural_az_valid),
      .context_slices(structural_ids),
      .context_main(structural_main),
      .context_sub(structural_sub),
      .context_rails(structural_rails),
      .context_sampling(structural_sampling),
      .context_analog_bad(structural_analog_bad),
      .context_injection(structural_injection),
      .fine_code(structural_fine),
      .error_code(structural_error)
  );
  assign slice_sel = P_STRUCTURAL ? structural_slice_sel : legacy_slice_sel;
  assign main_sw = P_STRUCTURAL ? structural_main_sw : legacy_main_sw;
  assign sub_sw = P_STRUCTURAL ? structural_sub_sw : legacy_sub_sw;
  assign dither_sw = P_STRUCTURAL ? structural_dither_sw : legacy_dither_sw;
  assign sw_valid = P_STRUCTURAL ? structural_sw_valid : legacy_sw_valid;

  // The packet is sampled on the same edge as recon_core.start. The source
  // fields are already frozen in the previous-residue context. No backpressure
  // or off-chip transport is implied by this opt-in debug interface.
  assign cal_obs_valid = P_CAL_OBSERVE && P_STRUCTURAL && rst_n &&
      !cfg_clear_valid && structural_launch;
  assign cal_obs_data = P_CAL_OBSERVE && P_STRUCTURAL ? {
      structural_id, structural_coarse, structural_sid, structural_dither,
      structural_ids, structural_main, structural_sub, structural_rails,
      structural_sampling, structural_analog_bad, structural_injection,
      structural_fine
  } : '0;
  assign cal_obs_caaz_data = P_CAL_OBSERVE && P_CAAZ_ENABLE ?
      {structural_chop_neg, structural_az_pair_id, structural_az_valid} : '0;

  //=========================================================================
  // 10) 定点重构
  //=========================================================================
  logic [OUT_BITS-1:0] rc_dout;
  logic                rc_dout_valid, rc_clip_low, rc_clip_high;
  logic                rc_acc_ovf, rc_gain_err, rc_adc2_ovf;
  wire [31:0] raw_result_sample_id;
  wire [4:0] raw_result_flags;

  recon_core #(
      .P_N_ACTIVE  (int'(N_ACTIVE)),
      .P_N_MAIN    (int'(N_UNIT_MAIN)),
      .P_N_SUB     (int'(N_UNIT_SUB)),
      .P_N_SLICES  (int'(N_SLICES)),
      .P_ADC2_BITS (int'(ADC2_BITS)),
      .P_STAGES    (P_RECON_STAGES),
      .P_USE_ROW_TOTALS(1),
      .P_DIT_N     (2 * DITHER_UNITS_RANGE),
      .P_DIT_END   (DITHER_SPLIT_IS_SUB ? int'(N_UNIT_TOTAL) : int'(N_UNIT_MAIN))
  ) u_recon (
      .clk              (clk),
      .rst_n            (rst_n && !cfg_clear_valid),
      .cfg_ready        (cfg_ready),
      .start            (launch_recon),
      .sample_id        (P_STRUCTURAL ? structural_id : alloc_sample_idx),
      .result_sample_id (raw_result_sample_id),
      .result_flags     (raw_result_flags),
      .clr_ovf          (cfg_clear_valid),
      .sampling_mask_en (P_STRUCTURAL ? structural_sampling : c_smask_en),
      .slice_id         (P_STRUCTURAL ? structural_ids : conv_slices),
      .main_on          (P_STRUCTURAL ? structural_main : main_on),
      .sub_on           (P_STRUCTURAL ? structural_sub : sub_on),
      .dither_rail      (P_STRUCTURAL ? structural_rails : dither_rail),
      .adc2_code        (P_STRUCTURAL ? structural_fine : adc2_hold),
      .inj_q            (P_STRUCTURAL ? structural_injection : inj_hold),          // 已捕获的同样本注入
      .w_rom            (w_q),
      .row_total        (row_total),
      .offset_q         (c_off),
      .adc2_min_q       (c_min),
      .adc2_max_q       (c_max),
      .dout             (rc_dout),
      .dout_valid       (rc_dout_valid),
      .clip_low         (rc_clip_low),
      .clip_high        (rc_clip_high),
      .acc_ovf          (rc_acc_ovf),
      .gain_err         (rc_gain_err),
      .adc2_ovf         (rc_adc2_ovf),
      .busy             (rc_busy)
  );

  assign clip_low = P_CAAZ_ENABLE ? 1'b0 : rc_clip_low;
  assign clip_high = P_CAAZ_ENABLE ? 1'b0 : rc_clip_high;

  // The reconstruction is single-in-flight and completes before the next
  // 16-phase launch. Carry the matching analog flag through that transaction.
  logic pending_analog_bad;
  logic pending_chop_neg;
  logic [31:0] pending_az_pair_id;
  logic pending_az_valid;
  always_ff @(posedge clk) begin
    if (!rst_n || cfg_clear_valid || !cfg_ready) begin
      pending_analog_bad <= 1'b0;
      pending_chop_neg <= 1'b0;
      pending_az_pair_id <= '0;
      pending_az_valid <= 1'b0;
    end else if (launch_recon) begin
      pending_analog_bad <= P_STRUCTURAL ? structural_analog_bad : analog_sample;
      pending_chop_neg <= structural_chop_neg;
      pending_az_pair_id <= structural_az_pair_id;
      pending_az_valid <= structural_az_valid;
    end
  end
  wire raw_result_analog_bad = rc_dout_valid && pending_analog_bad;
  assign caaz_raw_valid = P_CAAZ_ENABLE && rc_dout_valid;
  assign caaz_raw_data = P_CAAZ_ENABLE ? {
      raw_result_sample_id, rc_dout, pending_chop_neg, pending_az_pair_id,
      pending_az_valid, raw_result_flags, raw_result_analog_bad
  } : '0;

  // Complete calibrated words are paired. CAAZ off is a zero-latency bypass;
  // CAAZ on emits one pair for two raw records, with the odd sample's ID.
  caaz_pair_processor #(.P_CAAZ_ENABLE(P_CAAZ_ENABLE)) u_caaz_pair (
      .clk(clk),
      .rst_n(rst_n),
      .epoch_clear(cfg_clear_valid || !cfg_ready),
      .in_valid(rc_dout_valid),
      .in_code(rc_dout),
      .in_sample_id(raw_result_sample_id),
      .in_chop_neg(pending_chop_neg),
      .in_az_pair_id(pending_az_pair_id),
      .in_az_valid(pending_az_valid),
      .in_flags(raw_result_flags),
      .in_analog_bad(raw_result_analog_bad),
      .out_valid(dout_valid),
      .out_code(dout),
      .out_sample_id(dout_sample_id),
      .out_first_sample_id(caaz_pair_first_sample_id),
      .out_az_pair_id(caaz_pair_id),
      .out_flags(dout_flags),
      .out_analog_bad(dout_analog_bad),
      .pair_difference(caaz_pair_difference),
      .pair_difference_valid(caaz_pair_difference_valid),
      .fault(caaz_pair_fault),
      .fault_pulse(),
      .pair_pending(),
      .fault_code(caaz_pair_fault_code)
  );

  //=========================================================================
  // 11) 状态寄存器
  //=========================================================================
  logic        analog_ovf_raw;
  logic [31:0] status_clr_value_unused;

  // 结构模式在 residue_capture 冻结 RDAC 范围错误，并在下一 quiet_sample
  // 将模拟回读异常与旧 residue_bad 合并到 fine context；兼容模式在相位 14
  // 捕获模拟异常。接受重构时进入独立粘滞状态。
  // 注意三者的来源不同，不能混：
  //   rdac_ovf  / ra_sat  / adc2_over  <- 模拟域回读（输入端口）
  //   rc_adc2_ovf                      <- ADC2数字译码范围溢出；合法量程下不可达
  assign analog_ovf_raw = (launch_recon && (P_STRUCTURAL ? structural_analog_bad : analog_sample)) | rc_adc2_ovf;

  status_regs u_status (
      .clk               (clk),
      .rst_n             (rst_n),
      .clr               (cfg_clear_valid),
      .ev_acc_ovf        (rc_acc_ovf),
      .ev_gain_err       (rc_gain_err),
      .ev_adc2_ovf       (rc_adc2_ovf),
      .ev_clip_low       (rc_clip_low),
      .ev_clip_high      (rc_clip_high),
      .ev_analog_ovf     (analog_ovf_raw),
      .err_code          (merged_err),
      .acc_ovf_sticky    (acc_ovf),
      .gain_err_sticky   (),
      .adc2_ovf_sticky   (),
      .clip_low_last     (),
      .clip_high_last    (),
      .analog_ovf_sticky (analog_ovf),
      .status_word       (status_word),
      .status_clr_value  (status_clr_value_unused)
  );

  //=========================================================================
  // 12) cfg_rdata 回读
  //=========================================================================
  always_comb begin
    cfg_rdata = 64'd0;
    if (weight_addr_valid) begin
      cfg_rdata = {{(64 - W_BITS){1'b0}}, selected_weight};
    end else if (is_off) begin
      cfg_rdata = c_off;
    end else if (is_min) begin
      cfg_rdata = c_min;
    end else if (is_max) begin
      cfg_rdata = c_max;
    end else if (is_ctrl) begin
      cfg_rdata = {60'b0, c_qdither_en, c_smask_en, c_bridge_en, c_dem_en};
    end else if (is_stat) begin
      cfg_rdata = {32'b0, status_word};
    end else if (window_addr_valid) begin
      cfg_rdata = {59'b0, cur_slice};
    end else begin
      cfg_rdata = 64'd0;
    end
  end

  // 未接出顶层的观测量：phase_onehot/acq_phase、分配器组信息和样本序号。
  // ready、phase、锁存脉冲与 rc_busy 均参与功能控制，不能视为未使用输入。

endmodule
