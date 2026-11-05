// 模块：analog_phase_ctrl；模拟宏边界的相位和注册使能。
// 职责：生成 phase、TP、RA AZ/amplify、参考 precharge/accurate 及数字事务 strobes。
// 端口/单位：phase 为 tick 编号；其余输出为使能电平或数字接受条件，均不代表晶体管时钟。
// 时钟/复位：clk 上升沿、同步低有效 rst_n；enable=0 同步取消并重启相位。
// 周期：消费者在前沿读取旧 phase；本模块同沿安装 next_phase 及对应注册模拟电平。
// 默认区间：TP=1..15；AZ=1..8；precharge=2..8；amplify/accurate=10..15及0。
// 启动：复位 phase=0 但注册使能全低，首次回绕后才形成稳态 phase0 电平。
// 适用域：P_PHASES>=16、9<=P_CAPTURE<P_REF_ON<P_PHASES；非法参数在 elaboration 拒绝。
// 来源：ADR0018 与项目16tick预算；模拟非交叠、本地采样保持和建立时间由模拟宏验收。
`timescale 1ns/1ps
module analog_phase_ctrl #(
    parameter int P_PHASES = 16,
    parameter int P_REF_ON = 10,
    parameter int P_CAPTURE = 9
) (
    input wire clk, rst_n, enable,
    output logic [$clog2(P_PHASES)-1:0] phase,
    output wire quiet_sample, bank_advance, residue_capture,
    output logic tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate
);
  initial begin
    if (P_PHASES < 16 || P_CAPTURE < 9 || P_REF_ON <= P_CAPTURE || P_REF_ON >= P_PHASES)
      $fatal(1, "analog_phase_ctrl: invalid schedule");
  end

  localparam int PW = $clog2(P_PHASES);
  wire [PW - 1:0] next_phase = (int'(phase) == P_PHASES - 1) ? '0 : phase + 1'b1;

  // Never drive an analog switch/clock from a multi-bit counter decoder: counter carry skew could
  // otherwise create extra TP edges. Registered controls retain a full dead tick between precharge
  // and accurate reference.
  always_ff @(posedge clk) begin
    if (!rst_n || !enable) begin
      phase <= '0;
      tp_clock <= 0;
      ra_az <= 0;
      ra_amplify <= 0;
      ref_precharge <= 0;
      ref_accurate <= 0;
    end else begin
      phase <= next_phase;
      tp_clock <= next_phase != 0;
      ra_az <= next_phase >= 1 && int'(next_phase) < P_CAPTURE;
      ra_amplify <= int'(next_phase) >= P_REF_ON || next_phase == 0;
      ref_precharge <= next_phase >= 2 && int'(next_phase) < P_CAPTURE;
      ref_accurate <= int'(next_phase) >= P_REF_ON || next_phase == 0;
    end
  end

  // Default steady-state schedule: AZ 1..8, precharge 2..8, both reference enables off at phase 9,
  // and amplify/accurate-reference 10..15 plus phase 0. Immediately after reset, phase is 0 with
  // all registered enables cleared; the steady-state phase-0 analog levels first appear on the
  // wraparound.
  assign quiet_sample = rst_n && enable && phase == 0;
  assign bank_advance = rst_n && enable && phase == 1;
  assign residue_capture = rst_n && enable && int'(phase) == P_CAPTURE;
endmodule
