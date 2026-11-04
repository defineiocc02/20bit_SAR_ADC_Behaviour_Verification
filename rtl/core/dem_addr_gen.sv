// 模块：dem_addr_gen；DEM状态到物理单元逻辑位置的组合逆映射。
// 职责：main执行8×8行/列旋转并跳过缺失的物理cell63，sub执行完整8单元环旋转。
// 端口/单位：数组下标为物理unit ID，数组值为温度计顺序中的逻辑位置；sid为9位状态。
// 时序：无时钟、复位或enable；上游负责冻结同一样本的sid。
// 几何：ch=sid[2:0]、rh=sid[5:3]、sh=sid[8:6]；主阵列逆映射包含缺失cell的压缩修正。
// 适用域：只支持63main/8sub及8×8几何，非法参数在elaboration拒绝；不可换成模63的简单环。
// 来源：dem.split_switch_command、P1接口合同及独立闭式参考；不执行开关计数或权重读取。
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
  // cell={row,col}，低位为列、高位为行；缺失cell坐标必须保持同一排列。
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
