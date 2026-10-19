//===========================================================================
// ad4080_regs.sv -- AD4080 configuration register subset
//===========================================================================
// 地址依据：AD4080 Rev.B p.74 / p.71 / p.64-69
//   0x25/0x26  OFFSET[11:0]      12-bit two's complement
//   0x27/0x28  GAIN[9:0]         10-bit straight binary, reset 0x200
//   0x29       FILTER_CONFIG     [1:0] FILTER_SEL, [6:3] SINC_DEC_RATE
//   0x1C       GENERAL_CONFIG    [1:0] FIFO_MODE
//   0x1D/0x1E  FIFO_WATERMARK    [14:0]
//   0x14       DEVICE_STATUS     read-only flags
//
// 本模块只做寄存器存取，不做 SPI 帧、不做 CRC、不做事件检测。
// 运行时抽取率 dec_rate 只作为配置输出；当前 sinc1/sinc5 的 DECIM 是编译期
// 参数，若要真正运行时改抽取率，需要把 DECIM 改成运行时端口并固定最大位宽。
//===========================================================================
`timescale 1ns/1ps

module ad4080_regs (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [15:0] addr,
    input  logic [7:0]  wdata,
    input  logic        we,
    output logic [7:0]  rdata,
    output logic signed [11:0] offset_code,
    output logic [9:0]  gain_code,
    output logic [1:0]  filter_sel,
    output logic [3:0]  dec_rate,
    output logic [1:0]  fifo_mode,
    output logic [14:0] fifo_watermark,
    input  logic [7:0]  status_in
);

    logic [11:0] offset_r;
    logic [9:0]  gain_r;
    logic [1:0]  fsel_r;
    logic [3:0]  dec_r;
    logic [1:0]  fmode_r;
    logic [14:0] wm_r;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            offset_r <= 12'sd0;
            gain_r   <= 10'h200;
            fsel_r   <= 2'b00;
            dec_r    <= 4'd0;
            fmode_r  <= 2'b00;
            wm_r     <= 15'd0;
        end else if (we) begin
            case (addr)
                16'h0025: offset_r[7:0]  <= wdata;
                16'h0026: offset_r[11:8] <= wdata[3:0];
                16'h0027: gain_r[7:0]    <= wdata;
                16'h0028: gain_r[9:8]    <= wdata[1:0];
                16'h0029: begin
                    fsel_r <= wdata[1:0];
                    dec_r  <= wdata[6:3];
                end
                16'h001C: fmode_r <= wdata[1:0];
                16'h001D: wm_r[7:0]  <= wdata;
                16'h001E: wm_r[14:8] <= wdata[6:0];
                default: ; // ignore
            endcase
        end
    end

    always_comb begin
        case (addr)
            16'h0025: rdata = offset_r[7:0];
            16'h0026: rdata = {4'b0, offset_r[11:8]};
            16'h0027: rdata = gain_r[7:0];
            16'h0028: rdata = {6'b0, gain_r[9:8]};
            16'h0029: rdata = {1'b0, dec_r, 1'b0, fsel_r};
            16'h001C: rdata = {6'b0, fmode_r};
            16'h001D: rdata = wm_r[7:0];
            16'h001E: rdata = {1'b0, wm_r[14:8]};
            16'h0014: rdata = status_in;
            default:  rdata = 8'h00;
        endcase
    end

    assign offset_code    = offset_r;
    assign gain_code      = gain_r;
    assign filter_sel     = fsel_r;
    assign dec_rate       = dec_r;
    assign fifo_mode      = fmode_r;
    assign fifo_watermark = wm_r;

endmodule
