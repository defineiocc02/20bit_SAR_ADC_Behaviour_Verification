//===========================================================================
// lvds_framer.sv -- AD4080 LVDS result framer (digital SDR subset)
//===========================================================================
// 数据手册依据：AD4080 Rev.B p.45-53
//   - Result data is transmitted MSB first.
//   - Data lanes use DDR; up to 800 Mbps per lane.
//   - DCO is the echoed data clock.
//
// 本模块只做**数字并串转换**：20-bit 结果 -> 位流 + frame。
// 为了可读性，这里用 SDR（每个 bit_en 移 1 bit）；真实 AD4080 是 DDR，
// 需要工艺/FPGA 的 ODDR 原语或 2:1 mux，本模块不冒充 DDR PHY。
// bit_en 是 20 x sample rate 的单拍使能（由 PLL/clock-enable 产生），
// 单时钟域，避免把 bit_clk 当第二时钟造成多驱动/CDC。
//
// 时序：valid_in 装 shreg；随后 20 个 bit_en 脉冲移出 20 bit；
//       frame 在第一个 bit 为高。
//===========================================================================
`timescale 1ns/1ps

module lvds_framer (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        valid_in,
    input  logic [19:0] data_in,
    input  logic        bit_en,
    output logic        ser_data,
    output logic        ser_frame,
    output logic        dco
);

    logic [19:0] shreg;
    logic [4:0]  bitcnt;
    logic        active;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shreg     <= '0;
            bitcnt    <= '0;
            active    <= 1'b0;
            ser_data  <= 1'b0;
            ser_frame <= 1'b0;
        end else if (valid_in) begin
            shreg     <= data_in;
            bitcnt    <= '0;
            active    <= 1'b1;
            ser_data  <= 1'b0;
            ser_frame <= 1'b0;
        end else if (active && bit_en) begin
            ser_data  <= shreg[19];
            ser_frame <= (bitcnt == 5'd0);
            shreg     <= {shreg[18:0], 1'b0};
            if (bitcnt == 5'd19) begin
                active <= 1'b0;
                bitcnt <= '0;
            end else begin
                bitcnt <= bitcnt + 1'b1;
            end
        end else if (!active) begin
            ser_data  <= 1'b0;
            ser_frame <= 1'b0;
        end
    end

    assign dco = bit_en;

endmodule
