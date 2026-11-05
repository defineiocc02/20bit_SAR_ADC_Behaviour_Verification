// 模块：sadc_enc；比较器判决向量的平衡population-count编码。
// 职责：sadc_code=popcount(cmp_raw)，补零到2次幂叶数的组合加法树。
// 端口/单位：cmp_raw bit i表示x>threshold[i]的数字判决，输出为P_B1位无符号码。
// 时序：无时钟、复位或寄存器周期；实际门延迟由映射与STA决定。
// 适用域：P_B1=1..16，1<=P_N_CMP<2^P_B1；standalone默认9/511，结构Flash实例为3/7。
// 边界：不生成阈值或比较器，不检查温度计气泡/亚稳态；任意输入仍只得到其置1位数。
// 集成：增加流水必须同步修改flash_valid与种子接受相位；split_var不改变硬件连接。
// 来源：P2_INTERFACE.md §10、ADR0018；比较器电气行为属于外部模拟宏。
`timescale 1ns/1ps
`include "rtl_params.vh"

module sadc_enc #(
    parameter int P_B1    = int'(B1),
    parameter int P_N_CMP = (1 << P_B1) - 1        // P_N_CMP: = 511
) (
    input  logic [P_N_CMP-1:0] cmp_raw,          // 比较器阵列温度计（bit i = x > thr[i]）
    output logic [P_B1-1:0]    sadc_code
);

  localparam int ACC_W = $clog2(P_N_CMP + 1);    // ACC_W: 装得下 [0, P_N_CMP]

  localparam int LEAVES = 1 << $clog2(P_N_CMP);
  initial begin
    if (P_B1 < 1 || P_B1 > 16 || P_N_CMP < 1 || ACC_W > P_B1)
      $fatal(1, "sadc_enc: invalid comparator count/output width");
  end
  // Declare every tree edge before generate elaboration. Vivado 2018.3 drops forward hierarchical
  // g_node[2*i].count references as undriven nets.
  // split_var lets Verilator analyze the constant-index DAG per element; it
  // changes simulator partitioning only, not HDL connectivity or arithmetic.
  wire [ACC_W-1:0] count_tree [1:2*LEAVES-1] /* verilator split_var */;
  for (genvar i = 1; i < 2*LEAVES; i++) begin : g_node
    if (i < LEAVES) begin : g_branch
      assign count_tree[i] = count_tree[2*i] + count_tree[2*i+1];
    end else if (i-LEAVES < P_N_CMP) begin : g_input
      assign count_tree[i] = ACC_W'(cmp_raw[i-LEAVES]);
    end else begin : g_padding
      assign count_tree[i] = '0;
    end
  end
  assign sadc_code = P_B1'(count_tree[1]);
endmodule
