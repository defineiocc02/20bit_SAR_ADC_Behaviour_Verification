// 模块：ctrl_fsm；兼容码域模式的固定相位节拍。
// 职责：输出one-hot/二进制相位及sample、SADC、DEM、RDAC、ADC2、recon六个单拍条件。
// 端口/单位：phase为tick编号；sample_idx为已发出的sample_en计数；不执行数据算术。
// 时钟/复位：clk上升沿、同步低有效rst_n；cfg_ready/run撤销立即抑制组合脉冲并于下一沿归相位0。
// 相位合同：sample0、SADC8、DEM10、RDAC11、ADC2 14、recon15；acquisition区间0..6。
// 同沿规则：消费者读取旧ph/sample_idx；sample_idx只由reset清零，停机时保持。
// 适用域：P_PHASES>=16，默认16；结构模式使用独立analog_phase_ctrl/sar_structural_ctrl。
// 来源：P2_INTERFACE.md §7、ADR0017；本模块组合译码只用于数字控制。
`include "rtl_params.vh"

module ctrl_fsm #(
    parameter int P_PHASES = int'(PHASES)          // = 16
) (
    input  logic                        clk,
    input  logic                        rst_n,
    input  logic                        cfg_ready,
    input  logic                        run,             // 高 = 连续转换
    output logic [P_PHASES - 1:0]         phase_onehot,    // 相位节拍（one-hot）
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

  localparam logic [PW - 1:0] PH_LAST = PW'(P_PHASES - 1);
  localparam logic [PW - 1:0] PH_ACQ_END = PW'(6);

  logic [PW - 1:0] ph;
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
                            : ph + {{(PW - 1){1'b0}}, 1'b1};
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
    phase_onehot = adv ? ({{(P_PHASES - 1){1'b0}}, 1'b1} << ph)
                       : {P_PHASES{1'b0}};
  end

endmodule
