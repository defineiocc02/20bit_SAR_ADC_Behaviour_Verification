//===========================================================================
// slice_alloc.sv -- 18-of-8 物理 slice 分配（A/B ping-pong）
//===========================================================================
// 职责一句话
//   从 18 个物理 slice 中按 A/B ping-pong 选出 8 个，给出"本样本正在采集的
//   8 个 slice"与"本样本正在转换的 8 个 slice"，并输出 bank 与样本序号。
//   本模块**不做**开关译码（swap_decode / unit_therm 的事），也**不做**把
//   开关码扇出到物理 slice（rdac_drv 的事）。
//
// 来源
//   adi_model/timing.build_slice_plan 的 strategy="pingpong"、spare_period=None 分支：
//       groups  = arange(2*N_ACTIVE).reshape(2, N_ACTIVE)   // [0..7] 与 [8..15]
//       parity  = n % 2
//       acq[n]  = groups[parity]
//       conv[n] = groups[1 - parity]
//       valid   = n >= 1
//
// 单位契约
//   无量纲。acq_slices/conv_slices 的元素 ∈ [0, 16)（见 INV-4d）；sample_idx 为
//   已发出的 sample_en 脉冲计数（复位后为 0）。
//
// 参数来源分级
//   尺寸直接取 rtl_params.vh（[推导]）。本模块**没有**模块参数：与头文件常量同名的
//   参数会自引用（`parameter int N_ACTIVE = N_ACTIVE`），另起名字则等于在 RTL 里维护
//   第二份参数表 —— 两者都被 P1 §0.4 禁止。
//
// 契约与不变量 / 适用域
//   * INV-4a（所有权传递，18-of-8 的核心）：conv[n] 逐元素等于 acq[n-1]。
//   * INV-4b：acq_slices 内部无重复；conv_slices 内部无重复。
//   * INV-4c：同一 n 上 conv_slices 与 acq_slices 不相交。
//   * INV-4d：元素恒 < 16。如实登记 —— n_slices = 18，但 pingpong 只用 0..15，
//     物理 slice 16/17 在 pingpong 策略下**永远不被选中**（与模型一致，不是 bug）。
//   * `bank` 取 `n % 2`。P2 §5 要求它与模型实测值核对（由
//     tools/export_rtl_vectors.py 的 `all(result.bank == arange(n) % 2)` 报告）。
//     若模型给出相反极性，改这里而不是改 TB。
//   * 推进条件 = `cfg_ready && sample_en`。`sample_en` 由 ctrl_fsm 在相位 0 给出，
//     而 ctrl_fsm 在 cfg_ready=0 时**不发**任何脉冲；这里的 cfg_ready 门是冗余的
//     第二道防线，代价为 0。
//   * acq/conv/bank/sample_idx 为计数器 n 的组合函数；conv_valid 由首样本
//     标志保持，避免 n 在 2^32 次采样回绕时重新进入无效预热态。
//===========================================================================
`include "rtl_params.vh"

module slice_alloc (
    input  logic                       clk,
    input  logic                       rst_n,
    input  logic                       cfg_ready,
    input  logic                       sample_en,     // 每样本第一拍的脉冲
    output logic [N_ACTIVE-1:0][4:0]   acq_slices,
    output logic [N_ACTIVE-1:0][4:0]   conv_slices,
    output logic                       conv_valid,
    output logic                       bank,          // 0/1 交替，驱动 DEM 的 en_a/en_b
    output logic [31:0]                sample_idx,
    output logic [N_ACTIVE-1:0][4:0]   group          // = conv_slices（便于清点）
);

  localparam logic [31:0] ONE = 32'd1;
  localparam logic [4:0]  EIGHT = 5'd8;

  logic [31:0] n;             // 已推进到的样本序号；复位后 0
  integer      a;
  logic seen_sample;

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

  always_comb begin
    for (a = 0; a < N_ACTIVE; a = a + 1) begin
      // parity=0: acq = [0..7], conv = [8..15]
      // parity=1: acq = [8..15], conv = [0..7]
      acq_slices[a]  = bank ? (5'(a) + EIGHT) : 5'(a);
      conv_slices[a] = bank ? 5'(a)           : (5'(a) + EIGHT);
    end
  end

  assign group = conv_slices;

endmodule
