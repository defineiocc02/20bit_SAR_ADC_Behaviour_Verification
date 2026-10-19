//=============================================================================
// @file    spi_slave_tb.sv
// @brief   AD4080 配置 SPI 协议自检（Mode 3 / CRC-8 / streaming / 短指令）
// @details 直接驱动 SCLK/CS/SDI，采样 SDO；寄存器侧接真实 ad4080_regs，
//          通过 SPI 回读验证写入是否落在正确地址。
//          覆盖：
//            T1 单字节写/读（无 CRC）
//            T2 CRC 正确 -> 提交；CRC 错误 -> 不提交 + 0x11[3] 置位
//            T3 streaming：一帧两字节，ADDR_ASCENSION=1 递增写 0x1D/0x1E
//            T4 streaming 递减（默认）写 0x1E/0x1D
//            T5 SHORT_INSTRUCTION：8-bit 指令写 0x0A
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 1.0.0
//-----------------------------------------------------------------------------
// 运行：bash sim/run_all.sh（或 iverilog -g2012 -o spi.vvp sim/tb/spi_slave_tb.sv
//       rtl/spi_slave.sv rtl/ad4080_regs.sv && vvp spi.vvp）
//=============================================================================
`timescale 1ns/1ps

module spi_slave_tb;
    localparam int CLK_NS  = 10;     // 100 MHz
    localparam int HALF    = 50;     // SCLK 半周期 50 ns -> 10 MHz

    logic clk = 0;
    always #(CLK_NS/2) clk = ~clk;

    logic rst_n = 0;
    logic cs_n = 1, sclk = 1, sdi = 0;
    wire  sdo, sdo_oe;

    // slave <-> regs
    wire [15:0] addr;
    wire [7:0]  wdata, rdata;
    wire        we;
    wire        crc_err_pulse, clk_cnt_err_pulse;

    // regs 配置输出
    wire signed [11:0] offset_code;
    wire [9:0]  gain_code;
    wire [1:0]  filter_sel;
    wire [3:0]  dec_sel;
    wire        fsync_pulse;
    wire [1:0]  fifo_mode;
    wire [14:0] fifo_watermark;
    wire        fifo_arm;
    wire [10:0] event_hyst;
    wire [11:0] event_hi, event_lo;
    wire        event_en, hi_route, lo_route, cnv_err_route;
    wire [3:0]  gpio_dir, gpio_reg;
    wire [3:0][3:0] gpio_sel;
    wire        lvds_cnv_en, lane_sel, intf_mode, lvds_self_clk, lvds_mnc_en;
    wire [3:0]  lvds_cnv_clk_cnt;
    wire        crc_en, send_status, sdo_en, addr_asc;
    wire        single_inst, short_instr, sw_reset_pulse, cnv_err_flag;
    wire [7:0]  status;

    spi_slave u_spi (
        .clk(clk), .rst_n(rst_n),
        .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        .sdo(sdo), .sdo_oe(sdo_oe),
        .crc_en(crc_en), .send_status(send_status), .status_byte(status),
        .single_inst(single_inst), .short_instr(short_instr), .addr_asc(addr_asc),
        .addr(addr), .wdata(wdata), .we(we), .rdata(rdata),
        .crc_err_pulse(crc_err_pulse), .clk_cnt_err_pulse(clk_cnt_err_pulse)
    );

    ad4080_regs u_regs (
        .clk(clk), .rst_n(rst_n),
        .addr(addr), .wdata(wdata), .we(we), .rdata(rdata),
        .st_fifo_full(1'b0), .st_fifo_read_done(1'b0),
        .st_hi_flag(1'b0), .st_lo_flag(1'b0),
        .adc_cnv_err_pulse(1'b0),
        .crc_err_pulse(crc_err_pulse), .clk_cnt_err_pulse(clk_cnt_err_pulse),
        .hi_clr(), .lo_clr(),
        .offset_code(offset_code), .gain_code(gain_code),
        .filter_sel(filter_sel), .dec_sel(dec_sel), .fsync_pulse(fsync_pulse),
        .fifo_mode(fifo_mode), .fifo_watermark(fifo_watermark), .fifo_arm(fifo_arm),
        .wm_wb_valid(1'b0), .wm_wb_data(15'd0),
        .event_hyst(event_hyst), .event_hi(event_hi), .event_lo(event_lo),
        .event_en(event_en), .hi_route(hi_route), .lo_route(lo_route),
        .cnv_err_route(cnv_err_route),
        .gpio_dir(gpio_dir), .gpio_reg(gpio_reg), .gpio_sel(gpio_sel),
        .lvds_cnv_en(lvds_cnv_en), .lane_sel(lane_sel), .intf_mode(intf_mode),
        .lvds_self_clk(lvds_self_clk), .lvds_mnc_en(lvds_mnc_en),
        .lvds_cnv_clk_cnt(lvds_cnv_clk_cnt),
        .crc_en(crc_en), .send_status(send_status),
        .sdo_en(sdo_en), .addr_asc(addr_asc),
        .single_inst(single_inst), .short_instr(short_instr),
        .sw_reset_pulse(sw_reset_pulse), .cnv_err_flag(cnv_err_flag),
        .status(status)
    );

    int errors = 0;

    task automatic chk(input string name, input bit cond);
        if (!cond) begin
            $display("FAIL %s", name);
            errors++;
        end else begin
            $display("PASS %s", name);
        end
    endtask

    task automatic chk_eq8(input string name, input [7:0] got, input [7:0] exp);
        if (got !== exp) begin
            $display("FAIL %s: got=0x%02X exp=0x%02X", name, got, exp);
            errors++;
        end else begin
            $display("PASS %s: 0x%02X", name, got);
        end
    endtask

    // CRC-8: x^8+x^2+x+1, seed 0xA5, MSB first（与 RTL 同算法）
    function automatic [7:0] crc8_byte(input [7:0] c, input [7:0] b);
        integer i;
        logic [7:0] r;
        begin
            r = c;
            for (i = 7; i >= 0; i = i - 1)
                r = {r[6:0], 1'b0} ^ ((r[7] ^ b[i]) ? 8'h07 : 8'h00);
            crc8_byte = r;
        end
    endfunction

    // 发送一个 bit：下降沿改 SDI，上升沿由从机采样
    task automatic send_bit(input logic b);
        begin
            sclk = 0; sdi = b; #HALF;
            sclk = 1; #HALF;
        end
    endtask

    // 读一个 bit：在上升沿之后采样 SDO
    task automatic recv_bit(output logic b);
        begin
            sclk = 0; #HALF;
            sclk = 1; #1;
            b = sdo;
            #(HALF-1);
        end
    endtask

    // 一帧：16-bit 长指令 / 8-bit 短指令；N 个数据字节；可选 CRC
    //   crc_mode: 0 = 无 CRC, 1 = 正确 CRC, 2 = 故意错误 CRC
    task automatic frame(
        input logic        is_read,
        input logic        is_short,
        input logic [15:0] a,
        input int          nbytes,
        input logic [7:0]  d0,
        input logic [7:0]  d1,
        input int          crc_mode,
        output logic [7:0] r0,
        output logic [7:0] r1
    );
        logic [15:0] instr;
        logic [7:0]  crc;
        logic        b;
        int          i, k, nbits;

        // 指令字
        if (is_short) begin
            instr = {9'b0, is_read, a[6:0]};
            nbits = 8;
        end else begin
            instr = {is_read, a[14:0]};
            nbits = 16;
        end

        cs_n = 0; #(2*HALF);
        // 指令相
        for (i = nbits-1; i >= 0; i--) send_bit(instr[i]);

        // 数据相
        for (k = 0; k < nbytes; k++) begin
            logic [7:0] wb;
            logic [7:0] rb;
            wb = (k == 0) ? d0 : d1;
            rb = '0;
            for (i = 7; i >= 0; i--) begin
                if (is_read) begin
                    recv_bit(b);
                    rb = {rb[6:0], b};
                end else begin
                    send_bit(wb[i]);
                end
            end
            if (k == 0) r0 = rb; else r1 = rb;

            // CRC 字节（可选）
            //   写：CRC 覆盖“指令相 + 本字节数据”（streaming 第 2 字节起
            //       按手册改为“起始地址 + 数据”，这里用 addr_k 近似）。
            //   读：手册规定只校验“命令 + 起始地址”，即只覆盖指令相。
            if (crc_mode != 0) begin
                crc = 8'hA5;
                if (!is_short) crc = crc8_byte(crc, instr[15:8]);
                crc = crc8_byte(crc, instr[7:0]);
                if (!is_read) begin
                    if (k > 0) begin
                        logic [15:0] addr_k;
                        addr_k = addr_asc ? (a + k) : (a - k);
                        crc = crc8_byte(crc8_byte(8'hA5, addr_k[15:8]), addr_k[7:0]);
                    end
                    crc = crc8_byte(crc, wb);
                end
                if (crc_mode == 2) crc = crc ^ 8'h01;   // 故意错
                for (i = 7; i >= 0; i--) begin
                    if (is_read) begin
                        recv_bit(b);              // 器件回自己的 CRC，忽略
                    end else begin
                        send_bit(crc[i]);
                    end
                end
            end
        end

        cs_n = 1; #(2*HALF);
    endtask

    // 便捷封装
    task automatic wr(input [14:0] a, input [7:0] d, input int crc_mode);
        logic [7:0] r0, r1;
        begin
            frame(1'b0, short_instr, {1'b0,a}, 1, d, 8'h00, crc_mode, r0, r1);
        end
    endtask

    task automatic rd(input [14:0] a, output [7:0] d, input int crc_mode);
        logic [7:0] r0, r1;
        begin
            frame(1'b1, short_instr, {1'b0,a}, 1, 8'h00, 8'h00, crc_mode, r0, r1);
            d = r0;
        end
    endtask

    logic [7:0] v;

    initial begin
        $display("============================================================");
        $display(" AD4080 SPI protocol TB (Mode 3, CRC-8, streaming, short)");
        $display("============================================================");

        cs_n = 1; sclk = 1; sdi = 0;
        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (5) @(posedge clk);

        // ---------------- T1: 单字节写/读，无 CRC ----------------
        $display("\n---- T1 basic write/read (no CRC) ----");
        wr(15'h000A, 8'hA5, 0);
        rd(15'h000A, v, 0);
        chk_eq8("T1 scratch readback", v, 8'hA5);

        wr(15'h0027, 8'h34, 0);       // GAIN low
        rd(15'h0027, v, 0);
        chk_eq8("T1 GAIN low readback", v, 8'h34);

        // ---------------- T2: CRC ----------------
        $display("\n---- T2 CRC-8 good / bad ----");
        wr(15'h0010, 8'h42, 0);       // [7:6]=01 CRC_ENABLE, [1:0]=10 CRC_ENABLEB -> crc_en
        chk("T2 crc_en set by 0x10", crc_en === 1'b1);

        wr(15'h000A, 8'h3C, 1);       // 正确 CRC
        rd(15'h000A, v, 1);
        chk_eq8("T2 CRC-good write committed", v, 8'h3C);

        wr(15'h000A, 8'h77, 2);       // 错误 CRC
        rd(15'h000A, v, 1);
        chk_eq8("T2 CRC-bad write rejected", v, 8'h3C);

        rd(15'h0011, v, 1);
        chk("T2 CRC_ERR flag set (0x11[3])", v[3] === 1'b1);

        // 关 CRC，后续用无 CRC 帧
        wr(15'h0010, 8'h00, 1);
        chk("T2 crc_en cleared", crc_en === 1'b0);

        // ---------------- T3: streaming 递增 ----------------
        $display("\n---- T3 streaming, ADDR_ASCENSION=1 ----");
        wr(15'h0000, 8'h20, 0);       // ADDR_ASCENSION=1（SDO_ENABLE=0 不影响读）
        chk("T3 addr_asc set", addr_asc === 1'b1);
        wr(15'h0000, 8'h30, 0);       // ADDR_ASCENSION=1 + SDO_ENABLE=1

        begin
            logic [7:0] r0, r1;
            // 一帧两字节：0x1D=0x08, 0x1E=0x00 -> WATERMARK=0x0008
            frame(1'b0, 1'b0, 16'h001D, 2, 8'h08, 8'h00, 0, r0, r1);
        end
        rd(15'h001D, v, 0);
        chk_eq8("T3 WM low after streamed write", v, 8'h08);
        rd(15'h001E, v, 0);
        chk_eq8("T3 WM high after streamed write", v, 8'h00);

        // ---------------- T4: streaming 递减（默认） ----------------
        $display("\n---- T4 streaming, ADDR_ASCENSION=0 (decrement) ----");
        wr(15'h0000, 8'h10, 0);       // ADDR_ASCENSION=0
        chk("T4 addr_asc cleared", addr_asc === 1'b0);

        begin
            logic [7:0] r0, r1;
            // 一帧两字节：0x1E=0x40, 下一字节落到 0x1D=0x00 -> WM=0x4000
            frame(1'b0, 1'b0, 16'h001E, 2, 8'h40, 8'h00, 0, r0, r1);
        end
        rd(15'h001E, v, 0);
        chk_eq8("T4 WM high", v, 8'h40);
        rd(15'h001D, v, 0);
        chk_eq8("T4 WM low", v, 8'h00);

        // ---------------- T5: 短指令 ----------------
        $display("\n---- T5 SHORT_INSTRUCTION (7-bit address) ----");
        wr(15'h0001, 8'h08, 0);       // SHORT_INSTRUCTION=1
        chk("T5 short_instr set", short_instr === 1'b1);

        wr(15'h000A, 8'h5A, 0);       // 8-bit 指令写
        rd(15'h000A, v, 0);
        chk_eq8("T5 short-instruction write/read", v, 8'h5A);

        $display("\n============================================================");
        if (errors == 0) $display(" SPI TB PASS");
        else             $display(" SPI TB FAIL: %0d", errors);
        $display("============================================================");
        $finish;
    end

    // 超时保护
    initial begin
        #2_000_000;
        $display("FATAL: timeout");
        $finish;
    end

endmodule
