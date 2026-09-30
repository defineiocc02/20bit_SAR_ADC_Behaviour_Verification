//===========================================================================
// dem_addr_gen.sv -- DEM 状态 -> 每个物理地址的逻辑位置
//===========================================================================
// 职责一句话
//   把 sid 解成一个"物理地址 -> 逻辑位置"的映射，供 unit_therm 做温度计比较。
//   本模块**不做**温度计展开、不做计数（那是 unit_therm 的事）。
//
// 来源
//   adi_model/dem.split_switch_command() 的 `order` 数组（**开关译码主路径**），
//   以及 adi_model/weight_calibration._terms() 消费它的方式：
//       plus[..., order] = take,  take[i] = 1 iff i < count
//   即"物理地址 order[i] 被打开  <=>  逻辑位置 i < count"。
//   RTL 需要的是**反向**映射 j(p)，于是 on[p] = (j(p) < count)。
//
// 单位契约
//   无量纲整数。main_logical[p] ∈ [0, N_MAIN)、sub_logical[q] ∈ [0, N_SUB)。
//
// 参数来源分级
//   N_MAIN / N_SUB / W_ROT / H_ROT 由 rtl_params.vh 给出（[推导]）；
//   闭式变换的等价性由 sim/ref/dem_closed_form.py 与 P1 向量穷举证明。
//
// 契约与不变量 / 适用域
//   * 闭式（已穷举验证：512 sid x 71 地址，0 失配）：
//       ch = sid mod W_ROT;  rh = (sid / W_ROT) mod H_ROT;  sh = sid / (W_ROT*H_ROT)
//       i* = ((H_ROT-1-rh) mod H_ROT)*W_ROT + ((W_ROT-1-ch) mod W_ROT)   // 唯一被过滤掉的 cell
//       cell(p) = ((p / W_ROT - rh) mod H_ROT)*W_ROT + ((p mod W_ROT - ch) mod W_ROT)
//       j(p)    = cell(p) - (cell(p) > i* ? 1 : 0)
//       j_sub(q) = (q - sh) mod N_SUB
//   * **适用域**：要求 W_ROT 与 H_ROT 都是 2 的幂（本拓扑 8/8），
//     故 rh/ch/sh 可用位切片实现；且要求 H_ROT*W_ROT == 64 时 i* 的 "唯一被过滤 cell"
//     前提成立。换拓扑必须重新验证 —— 生成器 export_rtl_vectors.py 会直接拒绝。
//   * 不变量：cell(p) != i*，对全部 p ∈ [0, N_MAIN) 成立（否则说明拓扑前提被破坏）。
//   * 纯组合，延迟 0。
//     main_logical/sub_logical 的数组下标始终是物理 unit ID，数组值才是
//     温度计序列中的逻辑位置；不能交换这两个方向，否则 DEM 与权重读取错位。
//   * initial 只用于参数合法性检查，不是运行时状态机。sid 的冻结、复位与
//     enable 均由上游 dem_state_gen/样本上下文负责，本模块不保存历史状态。
//===========================================================================
`include "rtl_params.vh"

module dem_addr_gen #(
    parameter int N_MAIN = int'(N_UNIT_MAIN),
    parameter int N_SUB  = int'(N_UNIT_SUB),
    parameter int W_ROT  = int'(DEM_ROT_WIDTH),
    parameter int H_ROT  = int'(DEM_ROT_HEIGHT)
) (
    input  logic [8:0]             sid,
    output logic [N_MAIN - 1:0][5:0] main_logical,
    output logic [N_SUB - 1:0][2:0]  sub_logical
);

  initial begin
    if (N_MAIN != 63 || N_SUB != 8 || W_ROT != 8 || H_ROT != 8)
      $fatal(1, "dem_addr_gen: only the verified 63/8, 8x8 topology is supported");
  end

  localparam int LW = $clog2(W_ROT);  // 3
  localparam int LH = $clog2(H_ROT);  // 3
  localparam int LS = 9 - LW - LH;   // 3

  logic [LW - 1:0] ch;
  logic [LH - 1:0] rh;
  logic [LS - 1:0] sh;

  // 组合段 1：低三位为列旋转、中三位为行旋转、高三位为子阵列旋转。
  assign ch = sid[LW - 1:0];
  assign rh = sid[LW +: LH];
  assign sh = sid[LW + LH +: LS];

  // 唯一被 order < N_MAIN 过滤掉的 cell（对应 order 值 = W_ROT*H_ROT-1）
  logic [LW + LH - 1:0] i_star;
  // cell = {row, col}，故**低位是列、高位是行**。早先把两半写反了，
  // 结果是 i* 与真实的被过滤 cell 差了 (row,col) 互换后的位置，
  // 只在 cell 落在两者之间时表现成差 1 —— P1 向量把它抓了出来。
  assign i_star[LW - 1:0]      = (LW'(W_ROT - 1) - LW'(ch)) & LW'({LW{1'b1}});
  assign i_star[LW + LH - 1:LW] = (LH'(H_ROT - 1) - LH'(rh)) & LH'({LH{1'b1}});

  // 组合段 2：生成时展开到每个物理单元；p/8、p%8 是 elaboration 常量，
  // 不是运行时除法器。旋转减法取低三位等价于模 8，不应改成模 63 的环。
  genvar p;
  generate
    for (p = 0; p < N_MAIN; p++) begin : g_main
      localparam int P_ROW = p / W_ROT;
      localparam int P_COL = p % W_ROT;

      logic [LH - 1:0] row_rot;
      logic [LW - 1:0] col_rot;
      logic [5:0]      cell_idx;

      assign row_rot = (LH'(P_ROW) - rh) & LH'({LH{1'b1}});
      assign col_rot = (LW'(P_COL) - ch) & LW'({LW{1'b1}});
      assign cell_idx = {row_rot, col_rot};

      // 跳过唯一的物理 cell 63 后，后续逻辑位置减一，保持 0..62 连续。
      assign main_logical[p] = cell_idx - 6'(cell_idx > i_star);
    end
  endgenerate

  // 子阵列是完整的 8 单元环，没有主阵列的过滤/压缩步骤。
  genvar q;
  generate
    for (q = 0; q < N_SUB; q++) begin : g_sub
      assign sub_logical[q] = (3'(q) - 3'(sh)) & 3'h7;
    end
  endgenerate

endmodule
