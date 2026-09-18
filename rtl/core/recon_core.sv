//===========================================================================
// recon_core.sv -- 定点重构核（全数字核里唯一算术上完整的部分）
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
//   * 延迟：RECON_LAT = N_CYC + 2 = 11 拍（P_STAGES=7）。**口径**：start 那一拍记作第 1 拍。
//     初稿写 N_CYC+3=12 是另一种口径；以 sim/tb/p2_tb.sv 的 T4 **实测**为准（实测 11）。
//   * cfg_ready = 0 时输出恒 0、dout_valid 恒 0。
//===========================================================================
`include "rtl_params.vh"

module recon_core #(
    parameter int P_N_ACTIVE  = N_ACTIVE,
    parameter int P_N_MAIN    = N_UNIT_MAIN,
    parameter int P_N_SUB     = N_UNIT_SUB,
    parameter int P_N_SLICES  = N_SLICES,
    parameter int P_ADC2_BITS = ADC2_BITS,
    parameter int P_STAGES    = 7,
    parameter int P_DIT_N     = 2 * DITHER_UNITS_RANGE,
    parameter int P_DIT_END   = DITHER_SPLIT_IS_SUB ? N_UNIT_TOTAL : N_UNIT_MAIN
) (
    input  logic                                   clk,
    input  logic                                   rst_n,
    input  logic                                   cfg_ready,
    input  logic                                   start,
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
    output logic                                   busy
);

  localparam int N_U      = P_N_MAIN + P_N_SUB;
  localparam int SUM_BITS = 64;                          // SigmaW < 2^60（契约 §5）留 4 位余量
  localparam int W_RAIL   = SUM_BITS + 2;                // |rails| <= 3*2^60 < 2^62
  localparam int W_OP3    = SUM_BITS + V_BITS + 2;       // 130：total * inj_q
  localparam int W_WIDE   = W_OP3 + 2;                   // 132：受检操作数的精确宽度
  localparam int W_S1     = ACC_BITS + 1;                // 97
  localparam int W_SHIFT  = ACC_BITS + OUT_BITS + 1;     // 117
  localparam int W_A      = ACC_BITS - (V_FRAC + 1);     // 63：shifted >>> 33
  localparam int W_TOP    = W_SHIFT - ACC_BITS + 1;      // 22：shifted[116:95]
  localparam int DIT_BEG  = P_DIT_END - P_DIT_N;
  localparam int N_CYC    = (W_A + P_STAGES - 1) / P_STAGES;
  // 固定延迟。口径见 docs/rtl/P2_INTERFACE.md §4.3：从 `start` 那一拍起算 dout_valid
  // 所在的拍编号（start 那一拍记作第 1 拍）=> N_CYC + 2 = 11（P_STAGES=7）。
  // 由 sim/tb/p2_tb.sv 的 T4 **实测**；常量与实测不符时 T4 报错。
  localparam int RECON_LAT = N_CYC + 2;

  // ---- 常量（不允许就地造无符号字面量：P1 的 RTL-3 就是栽在符号混用上）----
  localparam logic [W_WIDE-1:0] LIM_ONE  = {{(W_WIDE-1){1'b0}}, 1'b1};
  localparam logic [W_WIDE-1:0] LIM_HI_U = LIM_ONE << (ACC_BITS - 1);
  localparam logic [W_WIDE-1:0] LIM_LO_U = ~LIM_HI_U + LIM_ONE;
  // ⚠️ 比较常量必须**显式声明为 signed**：见下面 clip_* 的真 bug 记录。
  localparam logic signed [W_A-1:0] ZERO_A  = {W_A{1'b0}};
  localparam logic signed [W_A-1:0] OUT_CNT = {{(W_A-1){1'b0}}, 1'b1} << OUT_BITS;
  localparam logic [SUM_BITS-1:0] GAIN_MAX = {{(SUM_BITS-1){1'b0}}, 1'b1}
                                             << (ACC_BITS - 1 - (V_FRAC + 1));

  //=========================================================================
  // 1) 三个掩码和（组合）
  //=========================================================================
  logic [W_BITS-1:0]          wsel [0:P_N_ACTIVE-1][0:N_U-1];
  logic                       onsel[0:P_N_ACTIVE-1][0:N_U-1];
  logic                       mksel[0:P_N_ACTIVE-1][0:N_U-1];
  logic                       rlsel[0:P_N_ACTIVE-1][0:N_U-1];
  logic [SUM_BITS-1:0]        psW  [0:P_N_ACTIVE-1];
  logic [SUM_BITS-1:0]        psWa [0:P_N_ACTIVE-1];
  logic [SUM_BITS-1:0]        psWon[0:P_N_ACTIVE-1];
  logic signed [W_RAIL-1:0]   psWr [0:P_N_ACTIVE-1];
  logic [SUM_BITS-1:0]        sum_W, sum_Wa, sum_Won;
  logic signed [W_RAIL-1:0]   sum_Wr;
  logic signed [W_RAIL-1:0]   rails;

  integer ai, ui;

  always_comb begin
    for (ai = 0; ai < P_N_ACTIVE; ai = ai + 1) begin
      psW[ai]   = {SUM_BITS{1'b0}};
      psWa[ai]  = {SUM_BITS{1'b0}};
      psWon[ai] = {SUM_BITS{1'b0}};
      psWr[ai]  = {W_RAIL{1'b0}};
      for (ui = 0; ui < N_U; ui = ui + 1) begin
        wsel[ai][ui]  = w_rom[slice_id[ai]][ui];
        onsel[ai][ui] = (ui < P_N_MAIN) ? main_on[ai][ui] : sub_on[ai][ui - P_N_MAIN];
        mksel[ai][ui] = sampling_mask_en && (ui >= DIT_BEG) && (ui < P_DIT_END);
        rlsel[ai][ui] = (ui >= DIT_BEG) ? dither_rail[ui - DIT_BEG] : 1'b0;

        psW[ai] = psW[ai] + {{(SUM_BITS - W_BITS){1'b0}}, wsel[ai][ui]};
        if (!mksel[ai][ui]) begin
          psWa[ai] = psWa[ai] + {{(SUM_BITS - W_BITS){1'b0}}, wsel[ai][ui]};
        end
        if (onsel[ai][ui]) begin
          psWon[ai] = psWon[ai] + {{(SUM_BITS - W_BITS){1'b0}}, wsel[ai][ui]};
        end
        if (mksel[ai][ui]) begin
          psWr[ai] = psWr[ai]
                   + (rlsel[ai][ui]
                      ?  $signed({{(W_RAIL - W_BITS){1'b0}}, wsel[ai][ui]})
                      : -$signed({{(W_RAIL - W_BITS){1'b0}}, wsel[ai][ui]}));
        end
      end
    end
    sum_W   = {SUM_BITS{1'b0}};
    sum_Wa  = {SUM_BITS{1'b0}};
    sum_Won = {SUM_BITS{1'b0}};
    sum_Wr  = {W_RAIL{1'b0}};
    for (ai = 0; ai < P_N_ACTIVE; ai = ai + 1) begin
      sum_W   = sum_W   + psW[ai];
      sum_Wa  = sum_Wa  + psWa[ai];
      sum_Won = sum_Won + psWon[ai];
      sum_Wr  = sum_Wr  + psWr[ai];
    end
    rails = $signed({2'b0, sum_W}) - $signed({1'b0, sum_Won, 1'b0}) + $signed(sum_Wr);
  end

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
  logic signed [V_BITS-1:0]   fine_r, off_r, inj_r;
  logic [SUM_BITS-1:0]        gain_s, total_s;
  logic signed [W_RAIL-1:0]   rails_s;
  logic                       stage_b, ovf_pend, gerr_pend, a2ovf_pend;

  //=========================================================================
  // 4) 受检算术（组合，在 stage B 那一拍求值）
  //=========================================================================
  logic signed [V_BITS:0]     diff;
  logic signed [W_WIDE-1:0]   op1, op2, op3, num;
  logic signed [W_OP3-1:0]    op3_w;
  logic signed [W_WIDE-1:0]   rails_ext, total_ext, inj_ext;
  logic signed [W_S1-1:0]     gv, s1;
  logic signed [W_SHIFT-1:0]  shifted;
  logic [W_A-1:0]             a1;
  logic [W_TOP-1:0]           shifted_top;
  logic                       bad_op1, bad_op2, bad_op3, bad_num, bad_den;
  logic                       shifted_ok, any_ovf;

  assign diff = $signed(fine_r) - $signed(off_r);
  assign op1  = $signed({{(W_WIDE - (V_BITS + 1)){diff[V_BITS]}}, diff}) <<< W_FRAC;

  assign rails_ext = {{(W_WIDE - W_RAIL){rails_s[W_RAIL-1]}}, rails_s};
  assign op2       = rails_ext <<< V_FRAC;

  // total 是**无符号**量：先显式零扩展到 W_OP3 再乘，避免"混一个无符号操作数
  // 就让整条表达式按无符号算"（P1 的 RTL-3）。
  assign op3_w = $signed({{(W_OP3-SUM_BITS){1'b0}}, total_s}) * $signed(inj_r);
  assign op3   = {{(W_WIDE - W_OP3){op3_w[W_OP3-1]}}, op3_w};

  assign num = op1 - op2 - op3;

  assign bad_op1 = (op1 >= $signed(LIM_HI_U)) || (op1 < $signed(LIM_LO_U));
  assign bad_op2 = (op2 >= $signed(LIM_HI_U)) || (op2 < $signed(LIM_LO_U));
  assign bad_op3 = (op3 >= $signed(LIM_HI_U)) || (op3 < $signed(LIM_LO_U));
  assign bad_num = (num >= $signed(LIM_HI_U)) || (num < $signed(LIM_LO_U));
  assign bad_den = (gain_s >= GAIN_MAX);

  assign gv      = $signed({{(W_S1 - SUM_BITS){1'b0}}, gain_s}) <<< V_FRAC;
  assign s1      = $signed(num[W_S1-1:0]) + gv;
  assign shifted = $signed({{(W_SHIFT - W_S1){s1[W_S1-1]}}, s1}) <<< OUT_BITS;

  // A1 = shifted >>> 33，取其低 W_A 位即精确值（前提：shifted 检查已通过）。
  assign a1          = shifted[W_A-1+(V_FRAC+1) : V_FRAC+1];
  assign shifted_top = shifted[W_SHIFT-1:ACC_BITS-1];

  always_comb begin
    // 位 116..95 全 0  =>  0 <= shifted < 2^95
    // 位 116..95 全 1  =>  -2^95 <= shifted < 0
    shifted_ok = (shifted_top == {W_TOP{1'b0}}) || (&shifted_top);
    any_ovf    = bad_op1 | bad_op2 | bad_op3 | bad_num | bad_den | (!shifted_ok);
  end

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
      .rst_n (rst_n),
      .start (stage_b),
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

  //=========================================================================
  // 6) 主状态机
  //=========================================================================
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      fine_r     <= {V_BITS{1'b0}};
      off_r      <= {V_BITS{1'b0}};
      inj_r      <= {V_BITS{1'b0}};
      gain_s     <= {SUM_BITS{1'b0}};
      total_s    <= {SUM_BITS{1'b0}};
      rails_s    <= {W_RAIL{1'b0}};
      stage_b    <= 1'b0;
      ovf_pend   <= 1'b0;
      gerr_pend  <= 1'b0;
      a2ovf_pend <= 1'b0;
      dout       <= {OUT_BITS{1'b0}};
      dout_valid <= 1'b0;
      clip_low   <= 1'b0;
      clip_high  <= 1'b0;
      acc_ovf    <= 1'b0;
      gain_err   <= 1'b0;
      adc2_ovf   <= 1'b0;
    end else begin
      dout_valid <= 1'b0;
      if (clr_ovf) begin
        acc_ovf  <= 1'b0;
        gain_err <= 1'b0;
        adc2_ovf <= 1'b0;
      end

      // ---- 阶段 A：锁存本拍的求和与输入 ----
      if (start && !busy) begin
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
        gerr_pend <= (gain_s == {SUM_BITS{1'b0}});
        stage_b   <= 1'b0;
      end

      // ---- 阶段 C：除法完成 -> 结算输出 ----
      if (div_done) begin
        adc2_ovf <= adc2_ovf | a2ovf_pend;
        if (ovf_pend) begin
          acc_ovf    <= 1'b1;              // 粘滞
          dout_valid <= 1'b1;              // 契约 §4.4：溢出**不打断时序**（dout 保持上一拍值）
        end else if (gerr_pend || div_err) begin
          gain_err   <= 1'b1;              // 结构性错误；输出保持
          dout_valid <= 1'b1;              // 契约 §4.4 表内 gain <= 0 一行同样是 dout_valid = 1
        end else begin
          dout       <= clip_lo_c ? {OUT_BITS{1'b0}}
                      : clip_hi_c ? {OUT_BITS{1'b1}}
                      : div_q[OUT_BITS-1:0];
          clip_low   <= clip_lo_c;
          clip_high  <= clip_hi_c;
          dout_valid <= 1'b1;
        end
      end

      // ---- 未配置：输出强制为 0（放在最后，优先级最高）----
      if (!cfg_ready) begin
        dout       <= {OUT_BITS{1'b0}};
        dout_valid <= 1'b0;
        clip_low   <= 1'b0;
        clip_high  <= 1'b0;
      end
    end
  end

endmodule
