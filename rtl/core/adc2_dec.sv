//===========================================================================
// adc2_dec.sv -- 原始后端码 -> Q32 bin center（唯一的运行时舍入）
//===========================================================================
// 职责一句话
//   把 ADC2 的原始整数码变成**码仓中心**电压（归一化 V/Vfs，Q32 有符号），
//   并按 ties-to-even 舍入。纯组合。
//   本模块**不**做后端偏移扣除（那是 recon_core 里 (F - O) 的事）。
//
// 来源
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §3.1（唯一运行时舍入）：
//       n     = (2*adc2_code + 1) * (adc2_max_q - adc2_min_q)
//       fine  = adc2_min_q + round_half_even_shift(n, adc2_n_bits + 1)
//   既有判据：tests/unit/test_fixed_point.py::test_signed_ties_to_even
//       （除数 2 上固定了 -7->-4, -5->-2, -3->-2, -1->0, 1->0, 3->2, 5->2, 7->4）
//
// round_half_even_shift 的硬件形态（契约 §3.1 注记的落地）
//   q    = $signed(x) >>> k        // 算术右移 == floor(x / 2^k)
//   r    = x[k-1:0]                // 二补数低 k 位 == x mod 2^k == 余数
//                                  //   对负数同样成立（这正是"向 -inf"的体现）
//   half = 2^(k-1)
//   res  = q + ((r > half) || ((r == half) && q[0]))
//   除数 2^k 是 2 的幂 -> 硬件上只有移位 + 一组比较器，**无除法器**。
//
// 单位契约
//   电压一律为"归一化到 Vfs"的 Q32 有符号整数；本模块不做任何浮点运算，
//   也不出现伏特值（契约 §1.1 的措辞修正：禁止的是浮点伏特**运算**，
//   不是禁止用定点编码承载模拟观测量）。
//
// 参数来源分级
//   P_ADC2_BITS 默认取自 rtl_params.vh 的 ADC2_BITS（[披露]，ADR 0014）。
//   模块参数存在的唯一理由是让 L2 的全码 oracle 能用另一套尺寸重例化
//   （见 docs/rtl/P2_INTERFACE.md §12）；它不是第二份参数表。
//
// 契约与不变量 / 适用域
//   * 输入采样：纯组合，延迟 0。
//   * `ovf = 1` 表示 `adc2_min_q + sh` 装不进 V_BITS 位有符号寄存器；
//     此时 `fine_q` 输出**钳位值**（不是回绕值）。契约 §4 的 6 个受检操作数
//     不含本量，所以这个标志是**额外的安全网**，不是等价替换。
//   * 本模块**不**检查 `adc2_max_q > adc2_min_q`：那是载入期校验
//     （calib_regs.validate -> ERR_RANGE_EMPTY），不是逐样本的事。
//     空量程会让 delta = 0，输出恒等于 adc2_min_q —— 合法但不有用。
//===========================================================================
`include "rtl_params.vh"

module adc2_dec #(
    parameter int P_ADC2_BITS = ADC2_BITS
) (
    input  logic [P_ADC2_BITS-1:0]   adc2_code,
    input  logic signed [V_BITS-1:0] adc2_min_q,
    input  logic signed [V_BITS-1:0] adc2_max_q,
    output logic signed [V_BITS-1:0] fine_q,
    output logic                     ovf
);

  localparam int K     = P_ADC2_BITS + 1;
  localparam int W_MUL = P_ADC2_BITS + 1 + V_BITS + 1;   // 78：装得下 (2^K)*(|delta| < 2^64)
  localparam int W_SUM = W_MUL + 1;                      // 79

  logic [K-1:0]            two_c_plus_1;   // 2*code + 1 = {code, 1'b1}
  logic signed [V_BITS:0]  delta;          // max - min，65 位
  logic signed [W_MUL-1:0] delta_ext;
  logic signed [W_MUL-1:0] n;
  logic signed [W_MUL-1:0] q_shift;
  logic signed [W_MUL-1:0] sh;
  logic [K-1:0]            r_bits;
  logic [K-1:0]            half;
  logic                    inc;
  logic signed [W_SUM-1:0] sum_wide;
  logic signed [W_SUM-1:0] min_ext;

  localparam logic [W_SUM-1:0] V_ONE  = {{(W_SUM-1){1'b0}}, 1'b1};
  localparam logic [W_SUM-1:0] V_HI_U = V_ONE << (V_BITS - 1);        // 2^63
  localparam logic [W_SUM-1:0] V_LO_U = ~V_HI_U + V_ONE;              // -2^63
  localparam logic [W_SUM-1:0] V_MAX_U = V_HI_U - V_ONE;              // 2^63 - 1

  assign two_c_plus_1 = {adc2_code, 1'b1};
  assign delta        = $signed(adc2_max_q) - $signed(adc2_min_q);
  assign delta_ext    = {{(W_MUL - (V_BITS + 1)){delta[V_BITS]}}, delta};

  // n 用**有符号**乘：两个操作数都显式 $signed，避免 P1 的 RTL-3 那类
  // "表达式里混进无符号操作数 -> 整条按无符号算"的坑。
  assign n = $signed({{(W_MUL - K){1'b0}}, two_c_plus_1}) * delta_ext;

  assign q_shift = $signed(n) >>> K;
  assign r_bits  = n[K-1:0];
  assign half    = {{(K - 1){1'b0}}, 1'b1} << (K - 1);
  assign inc     = (r_bits > half) || ((r_bits == half) && q_shift[0]);
  assign sh      = q_shift + {{(W_MUL - 1){1'b0}}, inc};

  assign min_ext  = {{(W_SUM - V_BITS){adc2_min_q[V_BITS-1]}}, adc2_min_q};
  assign sum_wide = min_ext + {{(W_SUM - W_MUL){sh[W_MUL-1]}}, sh};

  assign ovf     = (sum_wide >= $signed(V_HI_U)) || (sum_wide < $signed(V_LO_U));
  assign fine_q  = ovf
                 ? (sum_wide[W_SUM-1] ? V_LO_U[V_BITS-1:0] : V_MAX_U[V_BITS-1:0])
                 : sum_wide[V_BITS-1:0];

endmodule
