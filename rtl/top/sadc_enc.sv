//===========================================================================
// sadc_enc.sv -- 比较器阵列温度计码 -> 二进制粗码（核外）
//===========================================================================
// 职责一句话
//   把 SADC 比较器阵列的温度计判决向量编码成 B1 位二进制粗码，即 popcount。
//   本模块**不做**比较器本体、不做阈值生成、不做任何判决 —— 那些属模拟域。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §10；rtl/README.md §3.1（层级决定）；
//   adi_model 里 SADC 判决到粗码的那一步。
//
// 单位契约
//   无量纲整数。cmp_raw 的 bit i 表示 `x > thr[i]`；sadc_code = popcount(cmp_raw)
//   ∈ [0, 2^B1 - 1]。
//
// 参数来源分级
//   P_B1 默认取自 rtl_params.vh 的 B1（[推导]）；P_N_CMP = (1<<B1)-1 = 511 是
//   由 B1 推来的温度计宽度（[推导]）。允许重例化是为了让 TB 用小尺寸做定向用例。
//
// 契约与不变量 / 适用域
//   * **`cmp_raw` 必须是温度计码**（1 连续在前或连续在后）。若不是，popcount 仍
//     给出一个值 —— **RTL 不做合法性检查**，由模拟侧保证；TB 只对合法输入断言。
//     加一个"是否温度计"的检查会引入 O(2^B1) 的比较逻辑，收益为 0：
//     不合法的比较器输出本来就是模拟域的故障，应在那里检出。
//   * 纯组合，延迟 0。
//   * 累加器宽度取 `$clog2(P_N_CMP + 1)` = 9，恰好装下最大值 511，无截断风险。
//===========================================================================
`include "rtl_params.vh"

module sadc_enc #(
    parameter int P_B1    = B1,
    parameter int P_N_CMP = (1 << B1) - 1        // = 511
) (
    input  logic [P_N_CMP-1:0] cmp_raw,          // 比较器阵列温度计（bit i = x > thr[i]）
    output logic [P_B1-1:0]    sadc_code
);

  localparam int ACC_W = $clog2(P_N_CMP + 1);    // 9：装得下 [0, P_N_CMP]

  logic [ACC_W-1:0] acc;
  integer           i;

  always_comb begin
    acc = {ACC_W{1'b0}};
    for (i = 0; i < P_N_CMP; i = i + 1) begin
      acc = acc + {{(ACC_W-1){1'b0}}, cmp_raw[i]};
    end
    sadc_code = acc[P_B1-1:0];
  end

endmodule
