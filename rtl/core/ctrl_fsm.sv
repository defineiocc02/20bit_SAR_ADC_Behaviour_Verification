//===========================================================================
// ctrl_fsm.sv -- 相位节拍生成 + 流水控制脉冲
//===========================================================================
// 职责一句话
//   产生 16 拍一循环的相位节拍（one-hot 与二进制各一份），并在指定相位输出
//   sample_en / sadc_latch / dem_advance / rdac_load / adc2_latch / recon_start
//   六个单拍脉冲，同时维护样本序号。
//   本模块**不做**任何数据通路运算，只是一个节拍源。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §7 的相位分配表（PHASES = 16，RTL 设计选择 [假设]）：
//       0     新样本；sample_en、slice_alloc 推进
//       0-6   采集/跟踪（acq_phase 为高）
//       7     采样沿 t1（无脉冲输出）
//       8     SADC 判决编码（sadc_latch）
//       9     保留
//       10    DEM 状态推进 + k 合并（dem_advance）
//       11    RDAC 置位（rdac_load）
//       12-14 RA 建立 + ADC2 采样量化；14 为 adc2_latch
//       15    后端译码 + 定点重构（recon_start）
//
// 单位契约
//   无量纲。phase ∈ [0, P_PHASES)；sample_idx 为已发出的 sample_en 脉冲数。
//
// 参数来源分级
//   P_PHASES 默认取自 rtl_params.vh 的 PHASES（[假设] —— RTL 设计选择，不是模型参数）。
//   允许重例化是为了让 L2/L3 TB 能换相位预算验证"固定延迟"口径。
//
// 契约与不变量 / 适用域
//   * **cfg_ready = 0 时**：phase 恒 0，**所有脉冲恒 0**（含 sample_en）——
//     即"未配置时不产生任何转换节拍"。这是 §7 明文要求的不变量。
//   * `run = 0`（已配置但未启动）时同样回到 phase 0 且不发脉冲；重新拉高 run 会从
//     相位 0 重新开始一个样本周期。这属适用域内的行为，如实登记。
//   * `sample_idx` 只在相位 0 且推进使能时递增（一个样本恰好加 1）。
//   * 脉冲宽度恰为 1 拍：相位寄存器的每个取值每 16 拍出现一次，脉冲是相位的
//     纯组合译码，故不会出现"宽脉冲"。
//   * 本模块与 slice_alloc 各自维护一个 sample_idx，两者在相位 0 同拍递增，
//     因此逐拍相等（由 TB 断言）。
//===========================================================================
`include "rtl_params.vh"

module ctrl_fsm #(
    parameter int P_PHASES = int'(PHASES)          // = 16
) (
    input  logic                        clk,
    input  logic                        rst_n,
    input  logic                        cfg_ready,
    input  logic                        run,             // 高 = 连续转换
    output logic [P_PHASES-1:0]         phase_onehot,    // 相位节拍（one-hot）
    output logic [$clog2(P_PHASES)-1:0] phase,
    output logic                        sample_en,       // 相位 0 的脉冲 = 新样本
    output logic                        acq_phase,       // 相位 0..6 为高：采集/跟踪
    output logic                        sadc_latch,      // 相位 8
    output logic                        dem_advance,     // 相位 10
    output logic                        rdac_load,       // 相位 11
    output logic                        adc2_latch,      // 相位 14
    output logic                        recon_start,     // 相位 15
    output logic [31:0]                 sample_idx
);

  initial begin
    if (P_PHASES < 16) $fatal(1, "ctrl_fsm: phase 15 must exist");
  end

  localparam int PW = $clog2(P_PHASES);    // 4

  localparam logic [PW-1:0] PH_LAST = PW'(P_PHASES - 1);
  localparam logic [PW-1:0] PH_ACQ_END = PW'(6);

  logic [PW-1:0] ph;
  logic          adv;                       // 节拍推进使能

  assign adv   = rst_n && cfg_ready && run;
  assign phase = ph;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      ph <= {PW{1'b0}};
    end else if (!cfg_ready) begin
      ph <= {PW{1'b0}};                     // 未配置：恒 0（§7 不变量）
    end else if (run) begin
      ph <= (ph == PH_LAST) ? {PW{1'b0}}
                            : ph + {{(PW-1){1'b0}}, 1'b1};
    end else begin
      ph <= {PW{1'b0}};                     // 已配置但未启动：空转在相位 0
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      sample_idx <= 32'd0;
    end else if (adv && (ph == {PW{1'b0}})) begin
      sample_idx <= sample_idx + 32'd1;
    end
  end

  always_comb begin
    sample_en    = adv && (ph == {PW{1'b0}});
    acq_phase    = adv && (ph <= PH_ACQ_END);
    sadc_latch   = adv && (ph == PW'(8));
    dem_advance  = adv && (ph == PW'(10));
    rdac_load    = adv && (ph == PW'(11));
    adc2_latch   = adv && (ph == PW'(14));
    recon_start  = adv && (ph == PW'(15));
    phase_onehot = adv ? ({{(P_PHASES-1){1'b0}}, 1'b1} << ph)
                       : {P_PHASES{1'b0}};
  end

endmodule
