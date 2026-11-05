// 模块：rdac_drv；活动slice物理开关命令的同步寄存扇出。
// 职责：按物理slice ID将8路main/sub/采样rail写入18路输出，未选row在load时清零。
// 端口/单位：所有mask/rail为数字0/1选择，slice_sel正常为8-hot；不包含模拟底板驱动电路。
// 时钟/复位：clk上升沿、同步低有效rst_n；reset>load>保持，load外输出保持原值。
// 同沿规则：单个时序过程先清零后散射；重复ID由循环最后一路覆盖，越界ID忽略。
// 异常边界：本模块不另报ID错误；分配器合同及后续cal_weight_reduce的invalid_slice负责检测。
// 集成：兼容mode在phase11加载，结构mode由已决SAR update驱动；本模块不自行解码相位。
// 来源：P2_INTERFACE.md §6与物理开关接口；非交叠、电荷注入和建立时间由模拟域验收。
`include "rtl_params.vh"

module rdac_drv (
    input  logic                                  clk,
    input  logic                                  rst_n,
    input  logic                                  load,        // 一拍脉冲
    input  logic [N_ACTIVE - 1:0][4:0]              slice_id,
    input  logic [N_ACTIVE - 1:0][N_UNIT_MAIN - 1:0] main_on,
    input  logic [N_ACTIVE - 1:0][N_UNIT_SUB - 1:0]  sub_on,
    input  logic [2 * DITHER_UNITS_RANGE - 1:0]      dither_rail,
    output logic [N_SLICES - 1:0]                   slice_sel,
    output logic [N_SLICES - 1:0][N_UNIT_MAIN - 1:0] main_sw,
    output logic [N_SLICES - 1:0][N_UNIT_SUB - 1:0]  sub_sw,
    output logic [N_SLICES - 1:0][2 * DITHER_UNITS_RANGE - 1:0] dither_sw
);

  integer s, a;

  // 单个时序过程内先清零、后散射；非阻塞赋值的语句顺序是功能的一部分。
  // 不拆到多个 always_ff，也不将覆盖语义改成掩码 OR，以免重复 ID 行为变化。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      slice_sel <= '0;
      main_sw   <= '0;
      sub_sw    <= '0;
      dither_sw <= '0;
    end else if (load) begin
      // 先整体清零，再按物理号写入 8 个活动 slice —— 于是"未被选中的物理 slice 恒
      // 0"是结构性成立的，而不是靠调用方保证。
      slice_sel <= '0;
      for (s = 0; s < N_SLICES; s = s + 1) begin
        main_sw[s]   <= '0;
        sub_sw[s]    <= '0;
        dither_sw[s] <= '0;
      end
      for (a = 0; a < N_ACTIVE; a = a + 1) begin
        // Both compatibility banks and the full 18-slice pool are legal.
        if (int'(slice_id[a]) < int'(N_SLICES)) begin
          slice_sel[slice_id[a]] <= 1'b1;
          main_sw[slice_id[a]]   <= main_on[a];
          sub_sw[slice_id[a]]    <= sub_on[a];
          dither_sw[slice_id[a]] <= dither_rail;
        end
      end
    end
  end

endmodule
