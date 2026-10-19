//===========================================================================
// ad4080_top.sv -- AD4080 digital subset top
//===========================================================================
// 数据通路：
//   x -> sys_error_corr -> SINC1/SINC5/bypass -> fifo_capture -> dout
//                                                    |
//                                                    +-> lvds_framer -> ser
//   SPI -> ad4080_regs -> offset/gain/filter_sel/fifo_mode/watermark
//
// 这是**数字子集**，不是完整 AD4080：
//   - SPI 只支持 16-bit 指令 + 单字节数据，无 CRC/streaming/短指令。
//   - LVDS 只做 SDR 数字并串，不含 DDR/差分 PHY。
//   - FIFO 实现 mode 00/01/10；mode 11 按 10 处理。
//   - 运行时抽取率 dec_rate 只寄存，链路的 DECIM 仍是编译期参数。
//===========================================================================
`timescale 1ns/1ps

module ad4080_top #(
    parameter int IN_W  = 20,
    parameter int DECIM = 4
) (
    input  logic        clk,
    input  logic        rst_n,

    // SPI
    input  logic        cs_n,
    input  logic        sclk,
    input  logic        sdi,
    output logic        sdo,
    output logic        sdo_oe,

    // ADC result
    input  logic        valid_in,
    input  logic [IN_W-1:0] x,

    // FIFO / filter control
    input  logic        filter_sync,
    input  logic        event,
    input  logic        arm,
    input  logic        rd_en,

    // LVDS framer
    input  logic        bit_en,
    output logic        ser_data,
    output logic        ser_frame,
    output logic        dco,

    // results
    output logic [IN_W-1:0] dout,
    output logic            dout_valid,
    output logic [7:0]      status
);

    logic [15:0] addr;
    logic [7:0]  wdata, rdata;
    logic        we;

    logic signed [11:0] offset_code;
    logic [9:0]         gain_code;
    logic [1:0]         filter_sel;
    logic [3:0]         dec_rate;
    logic [1:0]         fifo_mode;
    logic [14:0]        fifo_watermark;

    logic [7:0] status_in;

    ad4080_regs u_regs (
        .clk(clk), .rst_n(rst_n),
        .addr(addr), .wdata(wdata), .we(we), .rdata(rdata),
        .offset_code(offset_code), .gain_code(gain_code),
        .filter_sel(filter_sel), .dec_rate(dec_rate),
        .fifo_mode(fifo_mode), .fifo_watermark(fifo_watermark),
        .status_in(status_in)
    );

    spi_slave u_spi (
        .clk(clk), .rst_n(rst_n),
        .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        .sdo(sdo), .sdo_oe(sdo_oe),
        .addr(addr), .wdata(wdata), .we(we), .rdata(rdata)
    );

    logic [IN_W-1:0] chain_y;
    logic            chain_valid;
    logic            clip_low, clip_high;
    logic            ch_full, ch_empty, ch_ovf, ch_unf;

    ad4080_dsp_chain #(.IN_W(IN_W), .DECIM(DECIM), .USE_FIFO(1'b0)) u_chain (
        .clk(clk), .rst_n(rst_n), .valid_in(valid_in), .x(x),
        .offset_code(offset_code), .gain_code(gain_code),
        .filter_sel(filter_sel), .filter_sync(filter_sync),
        .rd_en(rd_en),
        .y(chain_y), .valid_out(chain_valid),
        .clip_low(clip_low), .clip_high(clip_high),
        .fifo_full(ch_full), .fifo_empty(ch_empty),
        .fifo_overflow(ch_ovf), .fifo_underflow(ch_unf)
    );

    logic            fifo_rd_valid;
    logic            fifo_full, fifo_empty, fifo_ovf, fifo_unf;
    logic            read_done, captured;

    fifo_capture #(.DATA_W(IN_W), .DEPTH(16384)) u_capture (
        .clk(clk), .rst_n(rst_n),
        .arm(arm), .event(event), .rd_en(rd_en),
        .mode(fifo_mode), .watermark(fifo_watermark),
        .valid_in(chain_valid), .data_in(chain_y),
        .rd_data(dout), .rd_valid(fifo_rd_valid),
        .full(fifo_full), .empty(fifo_empty),
        .overflow(fifo_ovf), .underflow(fifo_unf),
        .read_done(read_done), .captured(captured)
    );

    assign dout_valid = fifo_rd_valid;

    lvds_framer u_lvds (
        .clk(clk), .rst_n(rst_n),
        .valid_in(fifo_rd_valid), .data_in(dout),
        .bit_en(bit_en),
        .ser_data(ser_data), .ser_frame(ser_frame), .dco(dco)
    );

    assign status_in = {fifo_full, fifo_empty, fifo_ovf, fifo_unf,
                        clip_low, clip_high, captured, chain_valid};
    assign status    = status_in;

endmodule
