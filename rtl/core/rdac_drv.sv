//===========================================================================
// rdac_drv.sv -- 8 个活动 slice 的开关码扇出到 18 个物理 slice
//===========================================================================
// 职责一句话
//   把"8 个活动 slice 的 unary 开关掩码 + 采样 dither 轨"按物理 slice 号写进
//   对应的寄存器位置，其余物理 slice 清零。输出是**寄存器**，load 之外保持不变。
//   本模块**不做**计数计算（swap_decode 的事）也不做温度计展开（unit_therm 的事）。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §6；拓扑图（P2 §1）里 unit_therm -> rdac_drv 的那条
//   扇出边。物理含义对应 adi_model 里"开关命令 -> 物理底板"的那一步散射。
//
// 单位契约
//   无量纲 0/1 开关码。slice_sel 为 18 位 one-hot（未选中的物理 slice 恒 0）。
//
// 参数来源分级
//   尺寸直接取 rtl_params.vh（[推导]）。本模块无模块参数（理由同 slice_alloc）。
//
// 契约与不变量 / 适用域
//   * **边界声明（勿误解）**：本模块只是**数字侧的寄存器扇出**，**不是**底板开关
//     驱动电路。真实驱动的时序、电荷注入、非交叠时钟属模拟域，由 Spectre 承接。
//   * `slice_id` 必须两两不同（INV-4b）。若出现重复，本模块的行为是"后者覆盖前者"
//     （循环里最后一次非阻塞赋值胜出），**不报错** —— 这是刻意的：把错误报在
//     slice_alloc 的断言里，而不是在这里做隐式防护（防护会掩盖上游的 bug）。
//   * `load` 之外输出保持（寄存器），故相位 11 之后的整段转换期开关码稳定。
//   * 复位为全 0（无 slice 被选中）。
//===========================================================================
`include "rtl_params.vh"

module rdac_drv (
    input  logic                                  clk,
    input  logic                                  rst_n,
    input  logic                                  load,        // 一拍脉冲
    input  logic [N_ACTIVE-1:0][4:0]              slice_id,
    input  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0]  main_on,
    input  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]   sub_on,
    input  logic [2*DITHER_UNITS_RANGE-1:0]       dither_rail,
    output logic [N_SLICES-1:0]                   slice_sel,
    output logic [N_SLICES-1:0][N_UNIT_MAIN-1:0]  main_sw,
    output logic [N_SLICES-1:0][N_UNIT_SUB-1:0]   sub_sw,
    output logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] dither_sw
);

  integer s, a;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      slice_sel <= '0;
      main_sw   <= '0;
      sub_sw    <= '0;
      dither_sw <= '0;
    end else if (load) begin
      // 先整体清零，再按物理号写入 8 个活动 slice —— 于是"未被选中的物理 slice
      // 恒 0"是结构性成立的，而不是靠调用方保证。
      slice_sel <= '0;
      for (s = 0; s < N_SLICES; s = s + 1) begin
        main_sw[s]   <= '0;
        sub_sw[s]    <= '0;
        dither_sw[s] <= '0;
      end
      for (a = 0; a < N_ACTIVE; a = a + 1) begin
        // slice_id[a] ∈ [0, 15]，恒 < N_SLICES = 18，索引安全。
        slice_sel[slice_id[a]]  <= 1'b1;
        main_sw[slice_id[a]]    <= main_on[a];
        sub_sw[slice_id[a]]     <= sub_on[a];
        dither_sw[slice_id[a]]  <= dither_rail;
      end
    end
  end

endmodule
