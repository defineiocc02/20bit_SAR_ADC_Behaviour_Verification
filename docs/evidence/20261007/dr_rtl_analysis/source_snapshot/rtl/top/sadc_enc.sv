//===========================================================================
// sadc_enc.sv -- 比较器阵列判决向量 -> 二进制 population count
//===========================================================================
// 职责一句话
//   把 SADC 比较器阵列的温度计判决向量编码成 B1 位二进制粗码，即 popcount。
//   本模块**不做**比较器本体、不做阈值生成、不做任何判决 —— 那些属模拟域。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §10；docs/adr/0018-physical-calibration-and-structural-controls.md；
//   adi_model 里 SADC 判决到粗码的那一步。
//
// 单位契约
//   无量纲整数。cmp_raw 的 bit i 表示 `x > thr[i]`；sadc_code = popcount(cmp_raw)
//   ∈ [0, 2^B1 - 1]。
//
// 参数来源分级
//   standalone 默认 B1=9，511 个输入；结构模式在 sar_structural_ctrl 内例化
//   P_B1=3，编码 7 个共享 flash 比较器，提供粗 SAR 的 3 个种子位。
//   兼容模式接收核外已编码的 sadc_code。文件所在 top/ 不决定例化层级。
//
// 契约与不变量 / 适用域
//   * **`cmp_raw` 必须是温度计码**（1 连续在前或连续在后）。若不是，popcount 仍
//     给出 population count；**RTL 不做合法性检查**。输入气泡/亚稳态的电气
//     成因及数字容错策略需模拟宏接口另行定义，不能从本模块推出已处理。
//   * 纯组合，没有寄存器周期延迟；实际门延迟由综合和 STA 决定。
//   * 累加器 ACC_W=$clog2(P_N_CMP+1)，standalone 为 9，结构 flash 为 3。
//   * 补零到 LEAVES 个叶子，以平衡加法树计算 popcount，深度 log2(LEAVES)。
//     若以流水树替换，必须连同 flash_valid 和种子接收相位修改，不能孤立加拍。
//===========================================================================
`include "rtl_params.vh"

module sadc_enc #(
    parameter int P_B1    = int'(B1),
    parameter int P_N_CMP = (1 << P_B1) - 1        // = 511
) (
    input  logic [P_N_CMP-1:0] cmp_raw,          // 比较器阵列温度计（bit i = x > thr[i]）
    output logic [P_B1-1:0]    sadc_code
);

  localparam int ACC_W = $clog2(P_N_CMP + 1);    // 装得下 [0, P_N_CMP]

  localparam int LEAVES = 1 << $clog2(P_N_CMP);
  initial begin
    if (P_B1 < 1 || P_B1 > 16 || P_N_CMP < 1 || ACC_W > P_B1)
      $fatal(1, "sadc_enc: invalid comparator count/output width");
  end
  // Declare every tree edge before generate elaboration. Vivado 2018.3 drops
  // forward hierarchical g_node[2*i].count references as undriven nets.
  // split_var lets Verilator analyze the constant-index DAG per element; it
  // changes simulator partitioning only, not HDL connectivity or arithmetic.
  wire [ACC_W-1:0] count_tree [1:2*LEAVES-1] /* verilator split_var */;
  for (genvar i = 1; i < 2*LEAVES; i++) begin : g_node
    if (i < LEAVES)
      assign count_tree[i] = count_tree[2*i] + count_tree[2*i+1];
    else if (i-LEAVES < P_N_CMP)
      assign count_tree[i] = ACC_W'(cmp_raw[i-LEAVES]);
    else
      assign count_tree[i] = '0;
  end
  assign sadc_code = P_B1'(count_tree[1]);
endmodule
