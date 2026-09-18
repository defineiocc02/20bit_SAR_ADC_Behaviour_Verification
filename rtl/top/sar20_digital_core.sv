//===========================================================================
// sar20_digital_core.sv -- 20-bit SAR ADC 数字核顶层集成
//===========================================================================
// 职责一句话
//   把 slice 分配、DEM、开关译码、温度计展开、RDAC 扇出、相位控制、定点重构与
//   寄存器/状态子系统集成为一个可综合数字核，并对外提供一条确定的配置通路
//   （cfg_addr 译码 -> 寄存器写 / 回读）。
//   本模块**不做**任何算术判定，只做实例化、连线与地址译码。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §11（端口与 cfg_addr 映射，冻结）
//   docs/rtl/P2_INTERFACE.md §1（拓扑图）
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §4.6（analog_ovf 的构成）
//   rtl/README.md §3.1（sadc_enc 在核外）
//
// 单位契约
//   配置侧：Q30 权重（48 位无符号存放）、Q32 电压（64 位有符号）、0/1 控制位。
//   数据侧：adc2_code 12 位原始后端码；dout 20 位 offset-binary 码。全程无浮点。
//
// 参数来源分级
//   全部尺寸取 rtl_params.vh（[推导]/[假设]）。本模块**没有**模块参数：顶层换尺寸
//   必须重跑 tools/export_rtl_params.py，不允许就地改数字。
//
// 契约与不变量 / 适用域
//   * `cfg_ready = 1` 之前不发任何转换节拍（ctrl_fsm 的不变量），故 `dout_valid`
//     在未配置时恒 0（recon_core 的不变量）。两道门叠在一起，不依赖单点保证。
//   * `analog_ovf = rdac_ovf | adc2_over | ra_sat`（契约 §4.6），与 clip_low/high
//     **互不替代**；本模块把它送进 status_regs 做**独立**粘滞保留（ADR 0014）。
//   * `dem_state_gen.dem_en` 直接接 `calib_regs.dem_en`。头文件的 `DEM_ENABLE = 0`
//     是**复位默认值**（故意的，P1 §0.2）：运行时必须由软件写 0x1018 bit0 = 1 打开，
//     否则整条 DEM 退化为固定顺序且**不会报任何错**。
//   * `recon_core.w_rom` 直接接 `weight_store.w_q`（同一份存储，无镜像）。
//
// ---- 与 P2 §11 的偏离登记（逐条，全部在此列明，不静默改）------------------
//   D1. **新增输入端口 `inj_q`**（signed [V_BITS-1:0]）。P2 §11 的端口表**漏了**它，
//       但 recon_core 必需的 `I`（已知注入，Q32）无法从表内任何端口推出：
//       cfg_addr 映射里没有它的地址，sadc/adc2 回读也不是它。P2 §12 的 L3 链路
//       向量 `p2_l2_ramp.hex` / `p2_link.hex` 明确带 `inj_q` 列，TB 必须能驱动它。
//       故按"recon_core 的 start 接 ctrl_fsm、inj_q/adc2_code 来自顶层输入"的
//       明确要求补上该端口。**这是本文件相对 §11 的唯一端口改动。**
//   D2. `run` 无对应顶层端口，接 `cfg_ready`：配置生效后连续转换。若将来要"按需
//       单次转换"，应加端口而不是在内部造状态。
//   D3. `sw_valid` 无对应驱动源（rdac_drv 无 valid 输出）。本模块把它做成
//       `rdac_load` 打一拍的脉冲，语义 = "开关寄存器本拍刚被刷新"。
//   D4. DEM 状态捕获：新增寄存器 `sid_hold`，在 `dem_advance`（相位 10）那一拍
//       锁存当前 bank 的 sid，之后 dem_state_gen 才推进该 bank。若不捕获，
//       相位 11 的 rdac_load 与相位 15 的 recon_start 会看到**已推进**的状态，
//       整套开关码对不上本次样本。这是"先使用、后推进"（P1 §1）在本拓扑里的
//       落地方式，代价是 9 个触发器。
//   D5. `dither_gen` 的码经 `dith_q` 按 `valid` 门控寄存：被拒采样（valid=0）的
//       那一拍不更新。否则 swap_decode 会拿到一个已被丢弃的码（P1 §4 的约定）。
//   D6. 权重合法性（`0 < W < 2^47`、`Sigma W < 2^60`）在 `weight_store` 的**写入点**
//       守卫，而不是在 `calib_regs.validate`。原因：calib_regs 的冻结端口表没有权重
//       输入，无法执行这两条；引权重进去要加端口，违反"端口逐个照抄"。
//       连带后果：calib_regs 的 `ERR_W_RANGE` / `ERR_W_SUM` 永不触发，而
//       weight_store 的 `err_write` 会在这两种情形下拉高；本模块按可观测原因把它
//       映射成对应错误码，使错误码仍可区分。
//   D7. `ERR_*` 常量在本文件与 calib_regs.sv 各有一份同值副本 —— 没有可共用的
//       头文件（rtl/params/ 是生成物，禁止手工追加）。改一处必须改另一处。
//   D8. `sadc_enc` **不**在本核内实例化，粗码直接取顶层输入 `sadc_code`。
//       这是 rtl/README.md §3.1 的层级决定（编码器在核外，模拟侧负责其正确性），
//       P2 §11 的端口表也正是这么定义的。sadc_enc 由独立 TB 验证。
//   D9. 顶层**不**对 `sadc_code` / `adc2_code` 做采样锁存。P2 §11 未定义锁存口径，
//       而 L3 向量是按相位驱动码域的，故保持直连（可观测行为 = 相位 15 时线上
//       的值）。若模拟侧无法在整段转换期保持码稳定，应在此加锁存并新写 ADR。
//===========================================================================
`include "rtl_params.vh"

module sar20_digital_core (
    input  logic        clk,
    input  logic        rst_n,
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
    // ⚠️ 契约修正（2026-09-18，L3 测试发现）：`analog_ovf` 的构成里含模型的 `adc2_over`
    // （ADC2 输入电压落在 [`adc2_v_min`,`adc2_v_max`] **严格外侧**，见 `adi_model/adc2.py`
    // 的 `over = (v < vmin) | (v > vmax)`）。它**不可能从 `adc2_code` 反推** ——
    // 码到轨既可能"恰好在轨"（模型不算 over）也可能"越轨被夹住"（算 over）。
    // 所以它是与 `ra_sat`/`rdac_ovf` 同级的**模拟域回读输入**，必须由外部给出。
    input  logic                 adc2_over,
    input  logic signed [V_BITS-1:0] inj_q,     // D1：§11 漏列，见模块头偏离登记
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
    output logic [31:0]         status_word
);

  //=========================================================================
  // 0) 精化期一致性断言（零成本：只做常量比较，不生成逻辑）
  //=========================================================================
  // 目的：让"头文件被人手篡改 / 换配置时忘了重跑导出器 / TB 与 RTL 用了不同的
  //      参数集"这三类事故在**精化期**就炸掉，而不是在仿真里表现为某些路径恒 0。
  initial begin
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
    if (N_UNIT_MAIN + N_UNIT_SUB != N_UNIT_TOTAL)
      $fatal(1, "sar20_digital_core: 主+子单位数 != 总单位数");
  end

  //=========================================================================
  // 1) 错误码常量（D7：与 calib_regs.sv 同值副本，无共享头文件）
  //=========================================================================
  localparam logic [31:0] ERR_NONE        = 32'd0;
  localparam logic [31:0] ERR_W_RANGE     = 32'd1;
  localparam logic [31:0] ERR_W_SUM       = 32'd2;
  localparam logic [31:0] ERR_RANGE_EMPTY = 32'd3;
  localparam logic [31:0] ERR_V_RANGE     = 32'd4;
  localparam logic [31:0] ERR_CFG_WRITE   = 32'd5;

  localparam logic [W_BITS-1:0] W_MAX = {{(W_BITS-47){1'b0}}, 1'b1} << 47;  // 2^47

  //=========================================================================
  // 2) 配置地址译码
  //=========================================================================
  //  0x0000 + u*8        (u = 0..70)  权重数据（slice 由窗口寄存器选）
  //  0x1000 / 0x1008 / 0x1010         offset_q / adc2_min_q / adc2_max_q
  //  0x1018                           控制位 {…, sampling_mask_en, bridge_en, dem_en}
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

  // ---- 权重窗口寄存器 ----
  logic [4:0] cur_slice;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      cur_slice <= 5'd0;
    end else if (cfg_wr && is_win) begin
      cur_slice <= win_slice;
    end
  end

  // ---- 0x1018 控制位写：三个位分三拍写入（calib_regs.sel 一次只选一个）----
  // 请求拍把 ctrl_bits 锁存并把 ctrl_seq 置 3，随后 3->2->1 逐拍发 sel=3/4/5。
  logic [1:0] ctrl_seq;
  logic [2:0] ctrl_bits;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      ctrl_seq  <= 2'd0;
      ctrl_bits <= 3'd0;
    end else begin
      if (cfg_wr && is_ctrl && (ctrl_seq == 2'd0)) begin
        ctrl_seq  <= 2'd3;
        ctrl_bits <= cfg_wdata[2:0];
      end else if (ctrl_seq != 2'd0) begin
        ctrl_seq <= ctrl_seq - 2'd1;
      end
    end
  end

  //=========================================================================
  // 3) 寄存器子系统
  //=========================================================================
  logic [N_SLICES-1:0][N_UNIT_TOTAL-1:0][W_BITS-1:0] w_q;
  logic        ws_err_write;
  logic        ws_wr_en;

  assign ws_wr_en = cfg_wr && is_weight && (cfg_addr[2:0] == 3'b000)
                   && (dw_unit < 7'(N_UNIT_TOTAL));

  weight_store #(
      .P_N_SLICES (N_SLICES),
      .P_N_UNITS  (N_UNIT_TOTAL)
  ) u_wstore (
      .clk       (clk),
      .rst_n     (rst_n),
      .cfg_ready (cfg_ready),
      .wr_en     (ws_wr_en),
      .wr_slice  (cur_slice),
      .wr_unit   (dw_unit),
      .wr_data   (cfg_wdata[W_BITS-1:0]),
      .err_write (ws_err_write),
      .w_q       (w_q)
  );

  logic [31:0]              cal_err_code;
  logic signed [V_BITS-1:0] c_off, c_min, c_max;
  logic                     c_dem_en, c_bridge_en, c_smask_en;
  logic                     cal_wr_en;
  logic [3:0]               cal_sel;
  logic                     cal_data_b;

  assign cal_wr_en = (cfg_wr && (is_off | is_min | is_max)) || (ctrl_seq != 2'd0);

  always_comb begin
    cal_sel    = 4'd0;
    cal_data_b = 1'b0;
    if (cfg_wr && is_off) begin
      cal_sel = 4'd0;
    end else if (cfg_wr && is_min) begin
      cal_sel = 4'd1;
    end else if (cfg_wr && is_max) begin
      cal_sel = 4'd2;
    end else if (ctrl_seq == 2'd3) begin
      cal_sel    = 4'd3;
      cal_data_b = ctrl_bits[0];
    end else if (ctrl_seq == 2'd2) begin
      cal_sel    = 4'd4;
      cal_data_b = ctrl_bits[1];
    end else if (ctrl_seq == 2'd1) begin
      cal_sel    = 4'd5;
      cal_data_b = ctrl_bits[2];
    end
  end

  calib_regs #(
      .P_ADC2_BITS (ADC2_BITS)
  ) u_calib (
      .clk              (clk),
      .rst_n            (rst_n),
      .wr_en            (cal_wr_en),
      .sel              (cal_sel),
      .data_v           (cfg_wdata[V_BITS-1:0]),
      .data_b           (cal_data_b),
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

  // err_code 合并（D6）：calib_regs 的校验错误优先；否则反映 weight_store 的写拒绝。
  logic [31:0] merged_err;
  always_comb begin
    if (cal_err_code != ERR_NONE) begin
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
      merged_err = ERR_NONE;
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
      .P_PHASES (PHASES)
  ) u_ctrl (
      .clk          (clk),
      .rst_n        (rst_n),
      .cfg_ready    (cfg_ready),
      .run          (cfg_ready),          // D2
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
      .rst_n       (rst_n),
      .cfg_ready   (cfg_ready),
      .sample_en   (sample_en),
      .acq_slices  (acq_slices),
      .conv_slices (conv_slices),
      .conv_valid  (conv_valid),
      .bank        (bank),
      .sample_idx  (alloc_sample_idx),
      .group       (grp)
  );

  //=========================================================================
  // 6) DEM：状态序列 -> 地址逻辑位置
  //=========================================================================
  logic [8:0] sid_a, sid_b, sid_cur, sid_hold;

  dem_state_gen #(
      .A_RED (DEM_LCG_A_MOD),
      .W     (9)
  ) u_dem (
      .clk    (clk),
      .rst_n  (rst_n),
      .dem_en (c_dem_en),                 // 头文件复位默认 0（故意的，见模块头）
      .load   (1'b0),                     // 初值恒 0，与模型一致
      .init_a (9'd0),
      .init_b (9'd0),
      .en_a   (dem_advance && !bank),     // 只推进本次样本用到的那一个 bank
      .en_b   (dem_advance &&  bank),
      .sid_a  (sid_a),
      .sid_b  (sid_b)
  );

  assign sid_cur = bank ? sid_b : sid_a;

  // D4：在 dem_advance 那一拍捕获"本次样本用的"状态，之后再推进。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
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
  // 7) dither（片上随机源，统计验收；D5 按 valid 门控寄存）
  //=========================================================================
  logic signed [7:0] dith_code;
  logic              dith_valid;
  logic signed [7:0] dith_q;

  dither_gen #(
      .D    (DITHER_UNITS_RANGE),
      .SEED (32'h1357_9BDF)
  ) u_dith (
      .clk         (clk),
      .rst_n       (rst_n),
      .en          (dem_advance),
      .dither_code (dith_code),
      .valid       (dith_valid)
  );

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      dith_q <= 8'sd0;
    end else if (dem_advance && dith_valid) begin
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

  swap_decode u_swap (
      .coarse      (sadc_code),                     // D8：粗码取自顶层输入（编码器在核外）
      .dither_code ({{8{dith_q[7]}}, dith_q}),      // 显式符号扩展到 16 位
      .dem_en      (c_dem_en),
      .bridge_en   (c_bridge_en),
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
      .bank_dither  (dith_q),
      .main_on      (main_on),
      .sub_on       (sub_on),
      .dither_rail  (dither_rail)
  );

  //=========================================================================
  // 9) RDAC 扇出
  //=========================================================================
  rdac_drv u_rdrv (
      .clk         (clk),
      .rst_n       (rst_n),
      .load        (rdac_load),
      .slice_id    (conv_slices),
      .main_on     (main_on),
      .sub_on      (sub_on),
      .dither_rail (dither_rail),
      .slice_sel   (slice_sel),
      .main_sw     (main_sw),
      .sub_sw      (sub_sw),
      .dither_sw   (dither_sw)
  );

  always_ff @(posedge clk) begin                                       // D3
    if (!rst_n) sw_valid <= 1'b0;
    else        sw_valid <= rdac_load;
  end

  //=========================================================================
  // 10) 定点重构
  //=========================================================================
  logic [OUT_BITS-1:0] rc_dout;
  logic                rc_dout_valid, rc_clip_low, rc_clip_high;
  logic                rc_acc_ovf, rc_gain_err, rc_adc2_ovf, rc_busy;

  recon_core #(
      .P_N_ACTIVE  (N_ACTIVE),
      .P_N_MAIN    (N_UNIT_MAIN),
      .P_N_SUB     (N_UNIT_SUB),
      .P_N_SLICES  (N_SLICES),
      .P_ADC2_BITS (ADC2_BITS),
      .P_STAGES    (7),
      .P_DIT_N     (2 * DITHER_UNITS_RANGE),
      .P_DIT_END   (DITHER_SPLIT_IS_SUB ? N_UNIT_TOTAL : N_UNIT_MAIN)
  ) u_recon (
      .clk              (clk),
      .rst_n            (rst_n),
      .cfg_ready        (cfg_ready),
      .start            (recon_start),
      .clr_ovf          (cfg_clear_valid),
      .sampling_mask_en (c_smask_en),
      .slice_id         (conv_slices),
      .main_on          (main_on),
      .sub_on           (sub_on),
      .dither_rail      (dither_rail),
      .adc2_code        (adc2_code),
      .inj_q            (inj_q),          // D1
      .w_rom            (w_q),
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

  assign dout        = rc_dout;
  assign dout_valid  = rc_dout_valid;
  assign clip_low    = rc_clip_low;
  assign clip_high   = rc_clip_high;

  //=========================================================================
  // 11) 状态寄存器
  //=========================================================================
  logic        analog_ovf_raw;
  logic [31:0] status_clr_value_unused;

  // 契约 §4.6：analog_ovf = rdac_ovf | adc2_ovf | ra_sat，与 clip_low/high **互不替代**。
  // 注意三者的来源不同，不能混：
  //   rdac_ovf  / ra_sat  / adc2_over  <- 模拟域回读（输入端口）
  //   rc_adc2_ovf                      <- `adc2_dec` 的**寄存器溢出**标志（另一回事，结构性不可达）
  assign analog_ovf_raw = rdac_ovf | adc2_over | rc_adc2_ovf | ra_sat;

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
  logic [4:0] rd_slice;
  logic [6:0] rd_unit;

  assign rd_slice = (cur_slice < 5'(N_SLICES)) ? cur_slice : 5'd0;
  assign rd_unit  = (dw_unit   < 7'(N_UNIT_TOTAL)) ? dw_unit : 7'd0;

  always_comb begin
    cfg_rdata = 64'd0;
    if (is_weight) begin
      cfg_rdata = {{(64 - W_BITS){1'b0}}, w_q[rd_slice][rd_unit]};
    end else if (is_off) begin
      cfg_rdata = c_off;
    end else if (is_min) begin
      cfg_rdata = c_min;
    end else if (is_max) begin
      cfg_rdata = c_max;
    end else if (is_ctrl) begin
      cfg_rdata = {61'b0, c_smask_en, c_bridge_en, c_dem_en};
    end else if (is_stat) begin
      cfg_rdata = status_word;
    end else if (is_win) begin
      cfg_rdata = {59'b0, cur_slice};
    end else begin
      cfg_rdata = 64'd0;
    end
  end

  // ---- 本节以下信号**有意**未接出：它们是本核内部的观测/中间量，没有对应的
  //      顶层端口（P2 §11 未定义）。列出以便复核者知道它们不是漏连：
  //        phase / phase_onehot / acq_phase / sadc_latch / adc2_latch
  //        conv_valid / grp / acq_slices / fsm_sample_idx / alloc_sample_idx
  //        rc_busy / sadc_rdy / adc2_rdy / status_clr_value_unused
  //      综合器会把它们优化掉；它们不参与任何输出锥。

endmodule
