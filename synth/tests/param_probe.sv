//===========================================================================
// synth/tests/param_probe.sv -- `elaborate -parameters` 的活体探针
//===========================================================================
// 这不是 RTL 交付物，是综合流程的**测试夹具**。放在 synth/ 下是为了不污染 rtl/。
//
// 目的
//   验证 `run_synth.py --params` -> `run_synth.sh --params` -> `PARAMS` 环境变量
//   -> `run_dc.tcl` 里 `elaborate ... -parameters` 这条链路**真的把参数送进去了**。
//
// 为什么值得专门写一个夹具
//   `-parameters` 失效是**静默**的：参数被忽略也只是"用默认值跑完"，然后报出
//   一个看起来人畜无害的面积数字。拿 recon_core 扫 P_STAGES 时，如果这条链路
//   断了，6 个参数点会得到 6 个一模一样的数 —— 而这一步很容易被顺手解释成
//   "该参数对面积不敏感"。这类"缺失的证据被当成结论"是本次流程要防的事。
//
// 判据（实测结果见下，**不要照抄我原来设想的判据**）
//   正（唯一的硬判据）：--params "P_WIDTH=8" 与 --params "P_WIDTH=128" 的
//       Total cell area 必须显著不同。这个玩具设计的面积与 P_WIDTH 近似线性，
//       两者相同 => 参数没生效 => 红灯。
//       实测：P_WIDTH=8 -> AREA=35.280000 CELLS=40
//             P_WIDTH=128 -> AREA=564.872001 CELLS=642   （16x，绿灯）
//   旁证：--params "P_WIDTH=-4" 实测**没有失败**（AREA=26.460000 CELLS=30）。
//       DC W-2024.09-SP3 接受这个退化位宽，并不报错；但它的面积与默认值
//       （35.28）**不同**，说明参数串确实被解析并吃进去了 —— 这是第二个
//       "参数被消费"的信号。原先我写的判据是"非法值必须失败"，
//       实测证明那条假设是错的：**非法输入被拒绝 与 合法输入被采纳 是两件事**，
//       用后者证明链路更可靠。仅当两个不同 P_WIDTH 给出完全相同的面积/单元数时，
//       才说明 elaborate -parameters 这条链路断了。
//
// 面积与 P_WIDTH 的关系是刻意做得"大且单调"的：每个比特一个触发器 + 一个异或，
// 8 位与 128 位不可能撞到同一个面积。
//===========================================================================
module param_probe #(
    parameter int P_WIDTH = 8
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic [P_WIDTH-1:0] a,
    input  logic [P_WIDTH-1:0] b,
    output logic [P_WIDTH-1:0] y
);

  logic [P_WIDTH-1:0] acc;

  always_ff @(posedge clk) begin
    if (!rst_n) acc <= {P_WIDTH{1'b0}};
    else        acc <= a ^ b;
  end

  assign y = acc;

endmodule
