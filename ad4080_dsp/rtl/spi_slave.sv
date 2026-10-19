//===========================================================================
// spi_slave.sv -- AD4080 configuration SPI slave (subset)
//===========================================================================
// 数据手册依据：AD4080 Rev.B p.34-43
//   - 4-wire SPI, Mode 3 (CPOL=1, CPHA=1): SCLK idle high, data updated on
//     falling edge, sampled on rising edge.
//   - Instruction: 1 command bit (1=read, 0=write) + 15-bit address.
//   - Data phase: 1 byte (this implementation).
//   - CRC-8 / streaming / short-instruction / status-on-SDO are NOT implemented.
//
// 结构：SCLK/CS 由 clk 过采样（要求 clk >= 8x SCLK），不把 SCLK 当第二时钟域。
//       this avoids CDC on the register file; it is a reference implementation,
//       not a characterized SPI PHY.
//
// 时序：CS 下降沿清计数器；16 个 SCLK 上升沿收指令；随后 8 个上升沿收/发数据；
//       写操作在最后一个上升沿产生一拍 we；读操作在第一个数据 falling 沿装 rdata。
//===========================================================================
`timescale 1ns/1ps

module spi_slave (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cs_n,
    input  logic        sclk,
    input  logic        sdi,
    output logic        sdo,
    output logic        sdo_oe,
    output logic [15:0] addr,
    output logic [7:0]  wdata,
    output logic        we,
    input  logic [7:0]  rdata
);

    // ---- 2FF synchronizers + edge detect ----
    logic cs_s0, cs_s1, sck_s0, sck_s1, sck_p;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cs_s0 <= 1'b1; cs_s1 <= 1'b1;
            sck_s0 <= 1'b1; sck_s1 <= 1'b1; sck_p <= 1'b1;
        end else begin
            cs_s0 <= cs_n;   cs_s1 <= cs_s0;
            sck_s0 <= sclk;  sck_s1 <= sck_s0; sck_p <= sck_s1;
        end
    end

    wire cs_fall  = cs_s1 & ~cs_s0;
    wire sck_rise = sck_s1 & ~sck_p;
    wire sck_fall = ~sck_s1 & sck_p;

    logic [15:0] instr;
    logic [7:0]  shin, shout;
    logic [5:0]  bitcnt;
    logic        cmd;
    logic        we_r;
    logic        sdo_r, oe_r;

    wire [15:0] instr_nxt = {instr[14:0], sdi};
    wire [7:0]  shin_nxt  = {shin[6:0], sdi};

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            instr  <= '0;
            shin   <= '0;
            shout  <= '0;
            bitcnt <= '0;
            cmd    <= 1'b0;
            addr   <= '0;
            wdata  <= '0;
            we_r   <= 1'b0;
            sdo_r  <= 1'b0;
            oe_r   <= 1'b0;
        end else begin
            we_r <= 1'b0;

            if (cs_fall) begin
                bitcnt <= '0;
                instr  <= '0;
                shin   <= '0;
                shout  <= '0;
                cmd    <= 1'b0;
                sdo_r  <= 1'b0;
                oe_r   <= 1'b0;
            end else if (cs_s1) begin
                // CS high: idle
                sdo_r <= 1'b0;
                oe_r  <= 1'b0;
            end else begin
                if (sck_rise) begin
                    if (bitcnt < 6'd16) begin
                        instr  <= instr_nxt;
                        bitcnt <= bitcnt + 1'b1;
                        if (bitcnt == 6'd15) begin
                            cmd  <= instr_nxt[15];
                            addr <= instr_nxt[14:0];
                        end
                    end else if (bitcnt < 6'd24) begin
                        shin   <= shin_nxt;
                        bitcnt <= bitcnt + 1'b1;
                        if (bitcnt == 6'd23) begin
                            wdata <= shin_nxt;
                            we_r  <= ~cmd;
                        end
                    end
                end

                if (sck_fall) begin
                    if (bitcnt == 6'd16) begin
                        shout <= rdata;
                        sdo_r <= rdata[7];
                        oe_r  <= cmd;
                    end else if (bitcnt > 6'd16 && bitcnt <= 6'd23) begin
                        shout <= {shout[6:0], 1'b0};
                        sdo_r <= shout[6];
                    end
                end
            end
        end
    end

    assign sdo    = sdo_r;
    assign sdo_oe = oe_r;
    assign we     = we_r;

endmodule
