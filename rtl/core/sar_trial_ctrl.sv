// 模块：sar_trial_ctrl；带可选Flash seed的二进制SAR顺序控制。
// 端口/单位：trial/resolved/seed为P_BITS位码，cmp_ge=1表示输入不小于当前trial。
// 时钟/复位：clk上升沿、同步低有效rst_n；reset/disable清全部状态。
// 同沿优先级：reset/disable>cancel>idle start>busy有效比较；busy start忽略但不阻止同沿比较。
// 数据：resolved只含已决位，trial另含当前试探位；只有resolved_valid能触发后级RDAC更新。
// 周期：每个busy&&cmp_valid接受一位；valid=0保持；最后一位清busy并脉冲done/resolved_valid。
// 取消：cancel保留码/index并清busy/事件；接受start时只保留seed高位并置首个未决trial。
// 适用域：P_BITS>=1、0<=P_SEED_BITS<P_BITS；所有比较器信号须满足统一clk时序。
// 来源：项目同步SAR宏合同；二进制与clocked握手不声明复制论文异步晶体管SAR。
module sar_trial_ctrl #(
    parameter int P_BITS = 9,
    parameter int P_SEED_BITS = 3
) (
    input wire clk, rst_n, enable, start, cancel,
    input wire [P_BITS - 1:0] seed_code,
    input wire cmp_valid, cmp_ge,
    output logic [P_BITS - 1:0] trial_code, resolved_code,
    output logic busy, done, resolved_valid,
    output wire compare_enable
);
  localparam int IW = (P_BITS > 1) ? $clog2(P_BITS) : 1;
  logic [IW - 1:0] bit_index;
  logic [P_BITS - 1:0] accepted;
  initial begin
    if (P_BITS < 1 || P_SEED_BITS < 0 || P_SEED_BITS >= P_BITS)
      $fatal(1, "sar_trial_ctrl: invalid seed/resolution");
  end

  assign compare_enable = enable && busy && !cancel;

  // The pending bit is applied to a temporary copy. resolved_code contains
  // only accepted decisions; trial_code additionally raises the next test bit.
  always_comb begin
    accepted = resolved_code;
    accepted[bit_index] = cmp_ge;
  end

  always_ff @(posedge clk) begin
    if (!rst_n || !enable) begin
      trial_code <= '0;
      resolved_code <= '0;
      bit_index <= '0;
      busy <= 0;
      done <= 0;
      resolved_valid <= 0;
    end else if (cancel) begin
      busy <= 0;
      done <= 0;
      resolved_valid <= 0;
    end else begin
      // Pulses refer to the update just committed by this rising edge. Other
      // always_ff blocks observe these pulses on the FOLLOWING rising edge.
      done <= 0;
      resolved_valid <= 0;
      if (start && !busy) begin
        // Keep only the flash seed MSBs; initialize the first unresolved bit.
        resolved_code <= (seed_code >> (P_BITS - P_SEED_BITS)) << (P_BITS - P_SEED_BITS);
        trial_code <= ((seed_code >> (P_BITS - P_SEED_BITS)) << (P_BITS - P_SEED_BITS)) |
            (P_BITS'(1) << (P_BITS - P_SEED_BITS - 1));
        bit_index <= IW'(P_BITS - P_SEED_BITS - 1);
        busy <= 1;
        resolved_valid <= 1;
      end else if (busy && cmp_valid) begin
        resolved_code <= accepted;
        resolved_valid <= 1;
        if (bit_index == 0) begin
          trial_code <= accepted;
          busy <= 0;
          done <= 1;
        end else begin
          trial_code <= accepted | (P_BITS'(1) << (bit_index - 1'b1));
          bit_index <= bit_index - 1'b1;
        end
      end
    end
  end
endmodule
