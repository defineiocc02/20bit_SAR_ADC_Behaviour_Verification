//===========================================================================
// swap_decode.sv -- 粗码 + dither -> 每 slice 的主/子单位计数
//===========================================================================
// 职责一句话
//   依次做四件事：拼出命令 k、裁剪、可选的桥接零和交换、拆成 (main_count, sub_count)。
//   本模块**不做**温度计展开（unit_therm 的事），也不做 DEM 地址置换（dem_addr_gen 的事）。
//
// 来源
//   adi_model/mapper.Mapper.encode()：k = coarse*units_per_lsb1 + k0（dither 另列）
//   adi_model/dem.split_switch_command()：clip、桥接零和交换、divmod(code, n_sub)
//   adi_model/sim.SimResult.rdac_over：溢出口径 (k < 0) | (k > dac_levels-1)
//
// 单位契约
//   无量纲整数。coarse [码仓号]；dither_code [RDAC 单位当量]（调用方保证为整数：
//   契约 §A4 拒绝小数掩码）；main_count ∈ [0, N_UNIT_MAIN]；sub_count ∈ [0, N_UNIT_SUB)。
//
// 参数来源分级
//   本模块**没有**模块参数：全部尺寸直接取 `rtl_params.vh` 的常量（[推导]，由
//   tools/export_rtl_params.py 从 Config 生成）。刻意的选择 —— 模块参数若与头文件
//   常量同名，参数默认值会自引用（`parameter int N_ACTIVE = N_ACTIVE`），
//   而另起一套名字就等于在 RTL 里维护第二份参数表（契约 §9 禁止）。
//   换配置请重跑导出器，不要就地改数字。
//
// 契约与不变量 / 适用域
//   * `amount = min(1, min(code, DAC_LEVELS-1-code))` 这道守卫使 slice_code 恒落在
//     [0, DAC_LEVELS-1]：code = 0 或 code = DAC_LEVELS-1 时 amount = 0，桥接不会把码
//     推出量程。这是"桥接零和交换不越界"的结构性理由，由 TB 穷举确认。
//   * 由该守卫可知 slice_code ≥ 0，故 `>> 3` 与 `[2:0]` 就是精确的
//     `divmod(slice_code, N_SUB)`（N_SUB = 8）。**注意**：这里靠的是
//     "非负"这一前提，不是"算术右移对负数也是 floor"—— 对 7 位的 main_count 而言，
//     负数经 `[9:3]` 截取会得到错值。适用域之外的行为不在契约内。
//   * 桥接仅在 `dem_en && bridge_en` 时生效（对应模型的 `dem_enable and dem_bridge_enable`）。
//   * 纯组合，延迟 0。
//===========================================================================
`include "rtl_params.vh"

module swap_decode (
    input  logic [8:0]               coarse,
    input  logic signed [15:0]       dither_code,
    input  logic                     dem_en,
    input  logic                     bridge_en,
    input  logic [2:0]               sid_low,
    output logic [N_ACTIVE-1:0][6:0] main_count,
    output logic [N_ACTIVE-1:0][2:0] sub_count,
    output logic                     rdac_over
);

  localparam int N_ACT = N_ACTIVE;
  localparam int LV    = DAC_LEVELS;
  localparam int HALF  = N_ACT / 2;

  // ⚠️ 全部走**有符号** localparam。Verilog 的规则是"表达式里只要有一个无符号
  // 操作数，整个表达式就按无符号算"：早先写成
  //     $signed({9'b0,coarse}) * 18'(UNITS_PER_LSB1) + 18'(K0) + $signed(dither_code)
  // 时 `18'(...)` 是无符号的，于是负 dither 变成大正数（-8 -> 65528），
  // k_cmd 不再是 -8，裁剪与溢出判定在**整段越界用例**上全错。
  // P1 的 85 条定向用例（dither -8..8）抓出来的就是这条。
  localparam signed [17:0] UL1  = 18'(UNITS_PER_LSB1);
  localparam signed [17:0] KLO  = 18'(K0);
  localparam signed [17:0] LMAX = 18'(DAC_LEVELS - 1);
  localparam signed [17:0] ZERO = 18'sd0;

  // ---- 1) 命令拼装（k 未裁剪；溢出口径取它，与 SimResult.rdac_over 一致） ----
  logic signed [17:0] k_cmd;
  assign k_cmd = $signed({9'b0, coarse}) * UL1 + KLO + $signed(dither_code);

  assign rdac_over = (k_cmd < ZERO) || (k_cmd > LMAX);

  // ---- 2) 裁剪到可实现的 DAC 电平 ----
  logic [8:0] code;
  assign code = (k_cmd < ZERO) ? 9'd0
                : (k_cmd > LMAX) ? 9'(LV - 1)
                                 : k_cmd[8:0];

  // ---- 3) 桥接零和交换的幅度：min(1, min(code, LV-1-code)) ----
  logic [8:0] head, tail, amount;
  assign head   = code;
  assign tail   = 9'(LV - 1) - code;
  assign amount = (head < tail) ? ((head < 9'd1) ? 9'd0 : 9'd1)
                                : ((tail < 9'd1) ? 9'd0 : 9'd1);

  // ---- 4) 逐 slice：符号 + 拆分 ----
  genvar a;
  generate
    for (a = 0; a < N_ACT; a++) begin : g_slice
      logic [2:0]         rank;
      logic               sign_pos, sign_neg;
      logic signed [10:0] delta, slice_code;

      // 取模必须在**能装下 N_ACT 的宽度**里做。写成 3'(N_ACT) 会把 8 截成 0，
      // 于是变成"对常量 0 取模"，rank 全错、整段符号逻辑失效 ——
      // 只在桥接打开时才表现出来（P1 的 8192 穷举把它抓了出来）。
      assign rank     = (32'(a) - 32'(sid_low)) % 32'(N_ACT);
      assign sign_pos = (32'(rank) < 32'(HALF));
      assign sign_neg = !sign_pos && (32'(rank) < 32'(N_ACT));

      assign delta = (dem_en && bridge_en)
                     ? (sign_pos ? $signed({2'b0, amount})
                                 : (sign_neg ? -$signed({2'b0, amount}) : 11'sd0))
                     : 11'sd0;

      assign slice_code = $signed({2'b0, code}) + delta;

      assign main_count[a] = slice_code[9:3];   // 非负前提下的 floor(x/8)
      assign sub_count[a]  = slice_code[2:0];   // 非负前提下的 x mod 8
    end
  endgenerate

endmodule
