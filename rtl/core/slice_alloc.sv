// 模块：slice_alloc；兼容码域模式的固定A/B ping-pong分配。
// 职责：在18个物理ID空间中使用0..7与8..15两组；slice16/17不参与该兼容策略。
// 端口/单位：acq/conv/group为8个物理slice ID，bank为样本奇偶，sample_idx为32位计数。
// 时钟/复位：clk上升沿、同步低有效rst_n；cfg_ready&&sample_en推进，其他周期保持。
// 同沿规则：消费者读旧bank/ID，计数NBA生效后组合组别切换；conv[n]=acq[n-1]。
// 启动：seen_sample在首个推进后置位，计数回绕不重新进入无效预热；仅reset清历史。
// 适用域：固定8路兼容几何；完整8-of-18动态池由slice_pool_ctrl实现。
// 来源：timing.build_slice_plan的pingpong/spare_period=None分支及P2接口合同。
`include "rtl_params.vh"

module slice_alloc (
    input  logic                       clk,
    input  logic                       rst_n,
    input  logic                       cfg_ready,
    input  logic                       sample_en,     // 每样本第一拍的脉冲
    output logic [N_ACTIVE - 1:0][4:0] acq_slices,
    output logic [N_ACTIVE - 1:0][4:0] conv_slices,
    output logic                       conv_valid,
    output logic                       bank,          // 0/1 交替，驱动 DEM 的 en_a/en_b
    output logic [31:0]                sample_idx,
    output logic [N_ACTIVE - 1:0][4:0] group          // = conv_slices（便于清点）
);

  localparam logic [31:0] ONE = 32'd1;
  localparam logic [4:0]  EIGHT = 5'd8;

  logic [31:0] n;             // 已推进到的样本序号；复位后 0
  integer      a;
  logic        seen_sample;

  // 时序边界只保存样本编号与预热标志；没有存储 8 路 ID 的寄存器组。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      n <= 32'd0;
      seen_sample <= 1'b0;
    end else if (cfg_ready && sample_en) begin
      n <= n + ONE;
      seen_sample <= 1'b1;
    end
  end

  // bank = n % 2 只取最低位（n 无符号，n[0] 即奇偶）。
  assign bank       = n[0];
  assign conv_valid = seen_sample; // remains valid across the 32-bit counter wrap
  assign sample_idx = n;

  // 组合 ID 译码：循环变量 a 为活动通道号，赋给每个元素的值为物理 slice ID。
  always_comb begin
    for (a = 0; a < N_ACTIVE; a = a + 1) begin
      // parity=0: acq = [0..7], conv = [8..15] parity=1: acq = [8..15], conv = [0..7]
      acq_slices[a]  = bank ? (5'(a) + EIGHT) : 5'(a);
      conv_slices[a] = bank ? 5'(a) : (5'(a) + EIGHT);
    end
  end

  assign group = conv_slices;

endmodule
