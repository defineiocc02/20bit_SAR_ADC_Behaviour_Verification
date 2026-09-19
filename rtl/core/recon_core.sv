//===========================================================================
// recon_core.sv -- 物理权重校正流水控制（独立算术模块见 ADR0018）
//===========================================================================
// 职责一句话
//   把"本次开关状态 + 后端码 + 已知注入"变成最终的 20 bit offset-binary 码：
//       rails = Sigma W*s      gain = Sigma W*a      total = Sigma W
//       num   = (F - O)*wscale - rails*vscale - total*I
//       shifted = (num + gain*vscale) * count
//       word  = floor(shifted / (gain*2^33)) = floor((shifted >>> 33) / gain)
//       dout  = clamp(word, 0, 2^20 - 1)，clip_* 单独保留
//   本模块**不做** DEM 置换、**不做**地址译码、**不做**权重存取。
//
// 来源
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §3.2-§3.5 + §4（冻结）
//   adi_model/fixed_point.FixedPointReconstructor.reconstruct()
//   既有判据：tests/unit/test_fixed_point.py（4 项独立检验 + 斜坡）
//
// 单位契约
//   权重 Q30（48 bit 有符号，实为正）；电压 Q32（64 bit 有符号）；输出 20 bit。
//   全部为整数编码，**不做浮点伏特运算**（契约 §1.1 的措辞修正）。
//
// 参数来源分级
//   尺寸默认值一律取自 rtl_params.vh（[披露]/[推导]）。模块参数存在的唯一理由
//   是让 L2 的全码 oracle 能用 straight_backend 的尺寸（1/1/2, adc2_bits=20）
//   重例化（见 docs/rtl/P2_INTERFACE.md §12）。**不是第二份参数表。**
//
// 契约与不变量 / 适用域
//   * 求和口径：rails = sum_W - 2*sum_Won + sum_Wr，gain = Sigma W*a，
//     total = Sigma W。其中 a = 0 且 s 额外带 ±1 的只有**采样 dither 掩码单位**
//     （u in [P_DIT_END-P_DIT_N, P_DIT_END)）。这个展开式与 _terms 的语义逐项对应，
//     且比"直接算 Sigma W*s"少一层条件取负。
//   * 溢出语义（契约 §4）：6 个受检操作数中任一越出 [-2^95, 2^95) -> acc_ovf
//     **粘滞**置位且输出保持上一拍合法值，**绝不 wrap**。gain <= 0 是结构性错误，
//     走 gain_err，与溢出**分开**。
//   * **`dout_valid` 在溢出与结构性错误时同样是 1**（契约 §4.4 的表：这两种情形
//     dout 保持、但"时序不打断"）。原先只写了保持 dout 而没拉高 valid —— 那会让
//     下游以为这一次转换从未发生。由 T8b 的等待超时暴露。
//   * 降级计算的口径（必须与 Python 判定集合相同）：
//       Python 侧抛 OverflowError 当且仅当 {op1..op6 中任一越界} 或 {gain <= 0}。
//       RTL 侧在 ovf_ops 已成立时 shifted 的窄位宽计算可能不再精确 —— 但这不影响
//       可观测行为（两种情况下都是"置标志 + 保持输出"）。
//       反过来：ovf_ops 不成立时 shifted 的 117 位计算**不得回绕**，论证见下。
//   * 位宽论证（勿改宽度而不复核）：ovf_ops 不成立 ==> |num| < 2^95 且 denom < 2^95
//     ==> gain < 2^62 ==> |gain*vscale| < 2^94 ==> |s1| < 2^95 + 2^94 < 2^96
//     ==> |shifted| < 2^116（117 位装得下）。且 |shifted| < 2^95（shifted 检查通过）
//     ==> |A1| < 2^62 ==> 取 shifted[95:33] 即精确的 A1。
//   * 延迟：RECON_LAT = N_CYC + 2 = 11 个完整时钟间隔（P_STAGES=7），从接受 start 的沿算起。
//     含接受沿共 12 个沿；以 sim/tb/p2_tb.sv 的 T4 **实测**为准（11 个间隔）。
//   * cfg_ready = 0 时输出恒 0、dout_valid 恒 0。
//===========================================================================
`include "rtl_params.vh"

module recon_core #(
    parameter int P_N_ACTIVE  = int'(N_ACTIVE),
    parameter int P_N_MAIN    = int'(N_UNIT_MAIN),
    parameter int P_N_SUB     = int'(N_UNIT_SUB),
    parameter int P_N_SLICES  = int'(N_SLICES),
    parameter int P_ADC2_BITS = int'(ADC2_BITS),
    parameter int P_STAGES    = 7,
    parameter int P_DIT_N     = 2 * DITHER_UNITS_RANGE,
    parameter int P_DIT_END   = DITHER_SPLIT_IS_SUB ? int'(N_UNIT_TOTAL) : int'(N_UNIT_MAIN)
) (
    input  logic                                   clk,
    input  logic                                   rst_n,
    input  logic                                   cfg_ready,
    input  logic                                   start,
    input  logic [31:0]                            sample_id,
    input  logic                                   clr_ovf,
    input  logic                                   sampling_mask_en,
    input  logic [P_N_ACTIVE-1:0][4:0]             slice_id,
    input  logic [P_N_ACTIVE-1:0][P_N_MAIN-1:0]    main_on,
    input  logic [P_N_ACTIVE-1:0][P_N_SUB-1:0]     sub_on,
    input  logic [P_DIT_N-1:0]                     dither_rail,
    input  logic [P_ADC2_BITS-1:0]                 adc2_code,
    input  logic signed [V_BITS-1:0]               inj_q,
    input  logic [P_N_SLICES-1:0][P_N_MAIN+P_N_SUB-1:0][W_BITS-1:0] w_rom,
    input  logic signed [V_BITS-1:0]               offset_q,
    input  logic signed [V_BITS-1:0]               adc2_min_q,
    input  logic signed [V_BITS-1:0]               adc2_max_q,
    output logic [OUT_BITS-1:0]                    dout,
    output logic                                   dout_valid,
    output logic                                   clip_low,
    output logic                                   clip_high,
    output logic                                   acc_ovf,
    output logic                                   gain_err,
    output logic                                   adc2_ovf,
    output logic                                   busy,
    output logic [31:0]                            result_sample_id,
    output logic [4:0]                             result_flags
);

  localparam int N_U      = P_N_MAIN + P_N_SUB;
  localparam int SUM_BITS = 64;                          // SigmaW < 2^60（契约 §5）留 4 位余量
  localparam int W_RAIL   = SUM_BITS + 2;                // |rails| <= 3*2^60 < 2^62
  localparam int W_OP3    = SUM_BITS + int'(V_BITS) + 2;       // 130：total * inj_q
  localparam int W_WIDE   = W_OP3 + 2;                   // 132：受检操作数的精确宽度
  localparam int W_S1     = int'(ACC_BITS) + 1;                // 97
  localparam int W_SHIFT  = int'(ACC_BITS) + int'(OUT_BITS) + 1;     // 117
  localparam int W_A      = int'(ACC_BITS) - (int'(V_FRAC) + 1);     // 63：shifted >>> 33
  localparam int W_TOP    = W_SHIFT - int'(ACC_BITS) + 1;      // 22：shifted[116:95]
  localparam int DIT_BEG  = P_DIT_END - P_DIT_N;
  localparam int N_CYC    = (W_A + P_STAGES - 1) / P_STAGES;
  // 固定延迟。口径见 docs/rtl/P2_INTERFACE.md §4.3：从 `start` 那一拍起算 dout_valid
  // 所在沿与接受 start 沿之间的完整时钟间隔 => N_CYC + 2 = 11（P_STAGES=7）。
  // 由 sim/tb/p2_tb.sv 的 T4 **实测**；常量与实测不符时 T4 报错。
  localparam int RECON_LAT = N_CYC + 2;

  // ---- 常量（不允许就地造无符号字面量：P1 的 RTL-3 就是栽在符号混用上）----
  localparam logic [W_WIDE-1:0] LIM_ONE  = {{(W_WIDE-1){1'b0}}, 1'b1};
  localparam logic [W_WIDE-1:0] LIM_HI_U = LIM_ONE << (int'(ACC_BITS) - 1);
  localparam logic [W_WIDE-1:0] LIM_LO_U = ~LIM_HI_U + LIM_ONE;
  // ⚠️ 比较常量必须**显式声明为 signed**：见下面 clip_* 的真 bug 记录。
  localparam logic signed [W_A-1:0] ZERO_A  = {W_A{1'b0}};
  localparam logic signed [W_A-1:0] OUT_CNT = {{(W_A-1){1'b0}}, 1'b1} << OUT_BITS;
  localparam logic [SUM_BITS-1:0] GAIN_MAX = {{(SUM_BITS-1){1'b0}}, 1'b1}
                                             << (int'(ACC_BITS) - 1 - (int'(V_FRAC) + 1));

  //=========================================================================
  // 1) 三个掩码和（组合）
  //=========================================================================
  wire [SUM_BITS-1:0] sum_W, sum_Wa;
  wire signed [W_RAIL-1:0] rails;
  wire invalid_slice;
  cal_weight_reduce #(
      .P_N_ACTIVE(P_N_ACTIVE), .P_N_MAIN(P_N_MAIN), .P_N_SUB(P_N_SUB),
      .P_N_SLICES(P_N_SLICES), .P_DIT_N(P_DIT_N), .P_DIT_END(P_DIT_END),
      .SUM_BITS(SUM_BITS), .W_RAIL(W_RAIL)
  ) u_weight_reduce (
      .sampling_mask_en(sampling_mask_en), .slice_id(slice_id),
      .main_on(main_on), .sub_on(sub_on), .dither_rail(dither_rail), .w_rom(w_rom),
      .sum_W(sum_W), .sum_Wa(sum_Wa), .rails(rails), .invalid_slice(invalid_slice)
  );

  //=========================================================================
  // 2) 后端译码（M9）
  //=========================================================================
  logic signed [V_BITS-1:0] fine_c;
  logic                     adc2_o;

  adc2_dec #(.P_ADC2_BITS(P_ADC2_BITS)) u_adc2 (
      .adc2_code  (adc2_code),
      .adc2_min_q (adc2_min_q),
      .adc2_max_q (adc2_max_q),
      .fine_q     (fine_c),
      .ovf        (adc2_o)
  );

  //=========================================================================
  // 3) 流水寄存器（阶段 A 的锁存目标）
  //=========================================================================
  logic [31:0] sample_id_r;
  logic signed [V_BITS-1:0]   fine_r, off_r, inj_r;
  logic [SUM_BITS-1:0]        gain_s, total_s;
  logic signed [W_RAIL-1:0]   rails_s;
  logic                       stage_b, ovf_pend, gerr_pend, a2ovf_pend, bad_slice_r;

  //=========================================================================
  // 4) 受检算术（组合，在 stage B 那一拍求值）
  //=========================================================================
  wire [W_A-1:0] a1;
  wire any_ovf;
  cal_residue_mac u_residue_mac (
      .fine_r(fine_r), .off_r(off_r), .inj_r(inj_r),
      .gain_s(gain_s), .total_s(total_s), .rails_s(rails_s),
      .a1(a1), .any_ovf(any_ovf)
  );

  //=========================================================================
  // 5) 除法器（唯一的运行时除法）
  //=========================================================================
  logic                   div_busy, div_done, div_err;
  logic signed [W_A-1:0]  div_q;
  logic                   clip_lo_c, clip_hi_c;

  div_floor #(
      .P_W_A    (W_A),
      .P_W_D    (SUM_BITS),
      .P_STAGES (P_STAGES)
  ) u_div (
      .clk   (clk),
      .rst_n (rst_n && cfg_ready),
      .start (stage_b && cfg_ready),
      .a     (a1),
      .d     (gain_s),
      .q     (div_q),
      .err   (div_err),
      .busy  (div_busy),
      .done  (div_done)
  );

  // ⚠️ 真 bug 记录（2026-09-18，P2 的 T8 剪裁边界用例抓出）：
  // 原写法 `(div_q < {W_A{1'b0}})` / `(div_q >= OUT_COUNT)` 的右操作数是**无符号**
  // localparam/字面量。Verilog 规则：表达式里只要有一个无符号操作数就**整条按无符号算**，
  // 于是 div_q = -1（位型 0x7FFF...F）被当成巨大正数 ->
  //   **clip_low 永不置位、clip_high 反而置位**（dout 被剪到 2^20-1 而不是 0）。
  // 这与 P1 的 RTL-3 是**同一族**缺陷（有符号/无符号混用）。教训：比较有符号量时，
  // 两个操作数都要是 signed；不要拿 `{N{1'b0}}` 这种无符号字面量去比。
  // 为什么大用例全绿：T5/T6/T7/T9 里 word 恒在 [0, 2^20)，两个标志都该为 0 ——
  // 错误写法与正确写法给出**同一个答案**。只有"恰好跨过 0/2^20 边界"的定向行能区分。
  assign clip_lo_c = (div_q <  ZERO_A);
  assign clip_hi_c = (div_q >= OUT_CNT);
  assign busy      = stage_b | div_busy;

  wire [OUT_BITS-1:0] candidate_word = clip_lo_c ? '0 :
                                      clip_hi_c ? '1 : div_q[OUT_BITS-1:0];
  cal_output_stage u_output (
      .clk(clk), .rst_n(rst_n), .cfg_ready(cfg_ready), .clr_ovf(clr_ovf),
      .complete(div_done), .candidate(candidate_word), .candidate_sample_id(sample_id_r),
      .result_sample_id(result_sample_id), .result_flags(result_flags),
      .candidate_clip_low(clip_lo_c), .candidate_clip_high(clip_hi_c),
      .candidate_acc_ovf(ovf_pend), .candidate_gain_err(gerr_pend || div_err),
      .candidate_adc2_ovf(a2ovf_pend), .dout(dout), .dout_valid(dout_valid),
      .clip_low(clip_low), .clip_high(clip_high), .acc_ovf(acc_ovf),
      .gain_err(gain_err), .adc2_ovf(adc2_ovf)
  );

  //=========================================================================
  // 6) 主状态机
  //=========================================================================
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      sample_id_r <= '0;
      fine_r     <= {V_BITS{1'b0}};
      off_r      <= {V_BITS{1'b0}};
      inj_r      <= {V_BITS{1'b0}};
      gain_s     <= {SUM_BITS{1'b0}};
      total_s    <= {SUM_BITS{1'b0}};
      rails_s    <= {W_RAIL{1'b0}};
      stage_b    <= 1'b0;
      bad_slice_r <= 1'b0;
      ovf_pend   <= 1'b0;
      gerr_pend  <= 1'b0;
      a2ovf_pend <= 1'b0;
    end else begin
      // ---- 阶段 A：锁存本拍的求和与输入 ----
      if (cfg_ready && start && !busy) begin
        sample_id_r <= sample_id;
        bad_slice_r <= invalid_slice;
        fine_r     <= fine_c;
        off_r      <= offset_q;
        inj_r      <= inj_q;
        gain_s     <= sum_Wa;
        total_s    <= sum_W;
        rails_s    <= rails;
        a2ovf_pend <= adc2_o;
        stage_b    <= 1'b1;
      end else if (stage_b) begin
        // WARNING 真 bug 记录（2026-09-18，P2 的定向溢出用例抓出）：
        // any_ovf 与 gain_s == 0 都依赖 stage-A 寄存器，而 stage-A 是在 start 那一拍
        // 才锁存的 —— 那一拍寄存器里装的还是**上一个样本**的值。原先把这两个锁存写在
        // start 块里，于是 acc_ovf / gain_err **晚一个样本**才反映。
        // 为什么长期没暴露：2^20 全码 oracle、512 行真实掩码、2044 行斜坡**都不发生溢出**，
        // 两种写法给出同一个答案（恒 0）。**只有定向溢出激励能区分** ——
        // 大规模测试全绿 与 边界正确 是两件不同的事。
        // 修法：挪到 stage_b 那一拍（此时寄存器正是本样本的值）。
        // div_start = stage_b 与 a1 都不变，所以**延迟不变**。
        ovf_pend  <= any_ovf;
        gerr_pend <= bad_slice_r || (gain_s == {SUM_BITS{1'b0}});
        stage_b   <= 1'b0;
      end

      // ---- 未配置：输出强制为 0（放在最后，优先级最高）----
      if (!cfg_ready) begin
        stage_b <= 1'b0;
        ovf_pend <= 1'b0;
        gerr_pend <= 1'b0;
        a2ovf_pend <= 1'b0;
        bad_slice_r <= 1'b0;
        sample_id_r <= '0;
      end
    end
  end

endmodule
