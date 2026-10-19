//===========================================================================
// sinc1_decim.sv -- AD4080 SINC1 decimation filter (boxcar)
//===========================================================================
// 数据手册依据：AD4080 Rev.B p.64-69
//   - SINC1 filter, decimation 2,4,...,1024
//   - settling CNV pulses = 2 + D + 1
//   - output rate = fS / D
//
// 本模块实现**块平均**（block boxcar）：每 D 个输入求一次平均，输出一个结果。
// 对 SINC1 抽取器，块平均与滑动平均在抽取点上的值相同（矩形窗）。
//    y[k] = (1/D) * sum_{i=0}^{D-1} x[kD+i]
//
// 定点：输入 20-bit 无符号；累加位宽 20+log2(D)；输出 20-bit，
//       四舍五入 (sum + D/2) >> log2(D)。
// filter_sync 清空累加器/计数器，用于多片同步或 FIFO 记录起始。
// 延迟：D 个输入周期 + 1 个输出周期；valid_out 单拍脉冲。
//===========================================================================
`timescale 1ns/1ps

module sinc1_decim #(
    parameter int IN_W  = 20,
    parameter int DECIM = 4
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,
    output logic [IN_W-1:0]      data_out,
    output logic                 valid_out
);

    localparam int LOG2D = $clog2(DECIM);
    localparam int ACC_W = IN_W + LOG2D;

    logic [ACC_W-1:0] acc;
    logic [ACC_W:0]   sum;
    logic [15:0]      cnt;

    always_comb begin
        sum = {1'b0, acc} + {1'b0, data_in} + (DECIM / 2);
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc       <= '0;
            cnt       <= '0;
            data_out  <= '0;
            valid_out <= 1'b0;
        end else if (filter_sync) begin
            acc       <= '0;
            cnt       <= '0;
            valid_out <= 1'b0;
        end else begin
            valid_out <= 1'b0;
            if (valid_in) begin
                if (DECIM == 1) begin
                    data_out  <= data_in;
                    valid_out <= 1'b1;
                    acc       <= '0;
                    cnt       <= '0;
                end else if (cnt == DECIM-1) begin
                    data_out  <= sum[LOG2D +: IN_W];
                    valid_out <= 1'b1;
                    acc       <= '0;
                    cnt       <= '0;
                end else begin
                    acc <= acc + data_in;
                    cnt <= cnt + 1'b1;
                end
            end
        end
    end

endmodule
