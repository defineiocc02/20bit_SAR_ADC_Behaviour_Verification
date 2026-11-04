// 模块：adc2_dec；ADC2 原始码到归一化 Q32 码仓中心的组合译码。
// 职责：fine_q=min+round_half_even((2*code+1)*(max-min)/2^(P_ADC2_BITS+1))。
// 端口/单位：adc2_code 为无符号码；min/max/fine_q 为有符号 V/Vfs 的 Q32。
// 时序：无时钟、复位或握手；调用方必须冻结输入并对齐样本 valid。
// 精度：量程差保留 V_BITS+1 位，乘法保留完整宽度，余数比较实现 ties-to-even。
// 异常：fine_q 超出 V_BITS 有符号范围时饱和并置 ovf；min<max 由配置端检查。
// 适用域：生产 P_ADC2_BITS=12、V_BITS=64；参数实例用于独立算术验证。
// 来源：RTL_ARITHMETIC_CONTRACT.md §3.1；Q32、舍入及接口为项目工程合同。
`include "rtl_params.vh"

module adc2_dec #(
    parameter int P_ADC2_BITS = int'(ADC2_BITS)
) (
    input  logic [P_ADC2_BITS - 1:0]    adc2_code,
    input  logic signed [V_BITS - 1:0] adc2_min_q,
    input  logic signed [V_BITS - 1:0] adc2_max_q,
    output logic signed [V_BITS - 1:0] fine_q,
    output logic                      ovf
);

  localparam int K     = P_ADC2_BITS + 1;
  localparam int W_MUL = P_ADC2_BITS + 1 + int'(V_BITS) + 1; // 默认 78 位
  localparam int W_SUM = W_MUL + 1;                       // 默认 79 位

  logic [K - 1:0]            two_c_plus_1; // 2*code + 1 = {code, 1'b1}
  logic signed [V_BITS:0]    delta;        // max - min，多一位保存符号与差值
  logic signed [W_MUL - 1:0] delta_ext;
  logic signed [W_MUL - 1:0] n;
  logic signed [W_MUL - 1:0] q_shift;
  logic signed [W_MUL - 1:0] sh;
  logic [K - 1:0]            r_bits;
  logic [K - 1:0]            half;
  logic                      inc;
  logic signed [W_SUM - 1:0] sum_wide;
  logic signed [W_SUM - 1:0] min_ext;

  // 边界常量先在 W_SUM 位构造，再显式转为 signed 参与比较；不能先截到
  // V_BITS 位再比较，否则正上界 2^63 会被解释为负数。
  localparam logic [W_SUM - 1:0] V_ONE   = {{(W_SUM - 1){1'b0}}, 1'b1};
  localparam logic [W_SUM - 1:0] V_HI_U  = V_ONE << (int'(V_BITS) - 1); // 默认 2^63
  localparam logic [W_SUM - 1:0] V_LO_U  = ~V_HI_U + V_ONE;            // 默认 -2^63
  localparam logic [W_SUM - 1:0] V_MAX_U = V_HI_U - V_ONE;            // 默认 2^63 - 1

  // 组合段1：奇数码仓中心与有符号量程；delta保留V_BITS+1位。
  assign two_c_plus_1 = {adc2_code, 1'b1};
  assign delta       = $signed(adc2_max_q) - $signed(adc2_min_q);
  assign delta_ext   = {{(W_MUL - (int'(V_BITS) + 1)){delta[V_BITS]}}, delta};

  // 两个乘法操作数均显式转换为signed，避免无符号操作数改变乘积扩展。
  assign n = $signed({{(W_MUL - K){1'b0}}, two_c_plus_1}) * delta_ext;

  // 组合段 2：除以固定 2^K，余数比较实现 ties-to-even。此处不推断除法器；
  // 前面的 n 仍是量程可配置的乘法，综合时应单独查看其 DSP/LUT 映射。
  assign q_shift = $signed(n) >>> K;
  assign r_bits  = n[K - 1:0];
  assign half    = {{(K - 1){1'b0}}, 1'b1} << (K - 1);
  assign inc     = (r_bits > half) || ((r_bits == half) && q_shift[0]);
  assign sh      = q_shift + {{(W_MUL - 1){1'b0}}, inc};

  // 组合段 3：先宽位相加与溢出检测，再裁到接口位宽；溢出时钳位。
  assign min_ext  = {{(W_SUM - int'(V_BITS)){adc2_min_q[V_BITS - 1]}}, adc2_min_q};
  assign sum_wide = min_ext + {{(W_SUM - W_MUL){sh[W_MUL - 1]}}, sh};

  assign ovf = (sum_wide >= $signed(V_HI_U)) || (sum_wide < $signed(V_LO_U));
  assign fine_q = ovf
    ? (sum_wide[W_SUM - 1] ? V_LO_U[V_BITS - 1:0] : V_MAX_U[V_BITS - 1:0])
    : sum_wide[V_BITS - 1:0];

endmodule
