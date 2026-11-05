// 模块：rdac_drv；活动slice物理开关命令的同步寄存扇出。
// 职责：按实际物理slice ID将main/sub bool mask寄存到物理row，未选row在load时清零。
// 端口/单位：mask为0/1选择，slice_sel正常为8-hot；不包含模拟底板驱动电路。
// 时钟/复位：clk上升沿、同步低有效rst_n；reset>load>保持，load外输出保持原值。
// 同沿规则：单个时序过程先清零后散射；重复ID由循环最后一路覆盖，越界ID忽略。
// 异常边界：本模块不另报ID错误；分配器合同及后续cal_weight_reduce的invalid_slice负责检测。
// 集成：只接受绑定有效的已解析SAR update；不自行选择码到mask映射或解码相位。
// 来源：P2_INTERFACE.md §6与物理开关接口；非交叠、电荷注入和建立时间由模拟域验收。
`timescale 1ns/1ps
`include "rtl_params.vh"

module rdac_drv (
    input  logic                                  clk,
    input  logic                                  rst_n,
    input  logic                                  load,        // 一拍脉冲
    input  logic [N_ACTIVE - 1:0][4:0]              slice_id,
    input  logic [N_ACTIVE - 1:0][N_UNIT_MAIN - 1:0] main_on,
    input  logic [N_ACTIVE - 1:0][N_UNIT_SUB - 1:0]  sub_on,
    output logic [N_SLICES - 1:0]                   slice_sel,
    output logic [N_SLICES - 1:0][N_UNIT_MAIN - 1:0] main_sw,
    output logic [N_SLICES - 1:0][N_UNIT_SUB - 1:0]  sub_sw
);

  integer s, a;

  // 单个时序过程内先清零、后散射；非阻塞赋值的语句顺序是功能的一部分。
  // 不拆到多个 always_ff，也不将覆盖语义改成掩码 OR，以免重复 ID 行为变化。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      slice_sel <= '0;
      main_sw   <= '0;
      sub_sw    <= '0;
    end else if (load) begin
      // 先整体清零，再按物理号写入 8 个活动 slice —— 于是"未被选中的物理 slice 恒 0"是结构性成立的，
      // 而不是靠调用方保证。
      slice_sel <= '0;
      for (s = 0; s < N_SLICES; s = s + 1) begin
        main_sw[s]   <= '0;
        sub_sw[s]    <= '0;
      end
      for (a = 0; a < N_ACTIVE; a = a + 1) begin
        // Only declared physical rows can be driven.
        if (int'(slice_id[a]) < int'(N_SLICES)) begin
          slice_sel[slice_id[a]] <= 1'b1;
          main_sw[slice_id[a]]   <= main_on[a];
          sub_sw[slice_id[a]]    <= sub_on[a];
        end
      end
    end
  end

endmodule
