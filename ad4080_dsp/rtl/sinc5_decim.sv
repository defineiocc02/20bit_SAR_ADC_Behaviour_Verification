//===========================================================================
// sinc5_decim.sv -- AD4080 SINC5 decimation filter (5-stage CIC)
//===========================================================================
// 数据手册依据：AD4080 Rev.B p.64-69
//   - SINC5 filter, decimation 2,4,...,256
//   - settling CNV pulses = 2 + 5*D + 4
//   - output rate = fS / D
//
// 结构：5 级积分器（输入速率） -> 抽取 R -> 5 级梳状器（输出速率）
//   H(z) = ((1 - z^-R)/(1 - z^-1))^5
//   直流增益 = R^5；输出前右移 5*log2(R) 归一化。
//
// 位宽：ACC_W = IN_W + 5*log2(R)。R=256 时 20+40=60 bit。
//       这是 CIC 的固有位增长；不能省，否则会溢出。
// 输出：20-bit，四舍五入；valid_out 单拍脉冲。
// filter_sync 清空积分器/梳状器/计数器，用于多片同步或 FIFO 记录起始。
//===========================================================================
`timescale 1ns/1ps

module sinc5_decim #(
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

    localparam int LOG2R = $clog2(DECIM);
    localparam int ACC_W = IN_W + 5*LOG2R;
    localparam int SHIFT = 5*LOG2R;

    logic [ACC_W-1:0] integ [0:4];
    logic [ACC_W-1:0] integ_nxt [0:4];
    logic [ACC_W-1:0] comb [0:4];
    logic [ACC_W-1:0] comb_d [0:4];
    logic [ACC_W-1:0] comb_in [0:5];
    logic [ACC_W-1:0] comb_nxt [0:4];
    logic [15:0]      cnt;
    logic             dec_en;

    // ---- integrator cascade (combinational next, registered on valid_in) ----
    always_comb begin
        integ_nxt[0] = integ[0] + data_in;
        integ_nxt[1] = integ[1] + integ_nxt[0];
        integ_nxt[2] = integ[2] + integ_nxt[1];
        integ_nxt[3] = integ[3] + integ_nxt[2];
        integ_nxt[4] = integ[4] + integ_nxt[3];
    end

    // ---- decimation counter ----
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt    <= '0;
            dec_en <= 1'b0;
        end else if (filter_sync) begin
            cnt    <= '0;
            dec_en <= 1'b0;
        end else if (valid_in) begin
            if (cnt == DECIM-1) begin
                cnt    <= '0;
                dec_en <= 1'b1;
            end else begin
                cnt    <= cnt + 1'b1;
                dec_en <= 1'b0;
            end
        end else begin
            dec_en <= 1'b0;
        end
    end

    // ---- integrator registers ----
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || filter_sync) begin
            for (int i=0;i<5;i++) integ[i] <= '0;
        end else if (valid_in) begin
            for (int i=0;i<5;i++) integ[i] <= integ_nxt[i];
        end
    end

    // ---- comb cascade (only at decimated rate) ----
    always_comb begin
        comb_in[0] = integ[4];
        for (int i=0;i<5;i++) begin
            comb_nxt[i] = comb_in[i] - comb_d[i];
            comb_in[i+1] = comb_nxt[i];
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || filter_sync) begin
            for (int i=0;i<5;i++) begin
                comb[i]   <= '0;
                comb_d[i] <= '0;
            end
            data_out  <= '0;
            valid_out <= 1'b0;
        end else begin
            valid_out <= 1'b0;
            if (dec_en) begin
                for (int i=0;i<5;i++) begin
                    comb[i]   <= comb_nxt[i];
                    comb_d[i] <= comb_in[i];
                end
                data_out  <= (comb_nxt[4] + (1 << (SHIFT-1))) >> SHIFT;
                valid_out <= 1'b1;
            end
        end
    end

endmodule
