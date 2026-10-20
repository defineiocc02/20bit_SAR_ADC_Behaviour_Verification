//=============================================================================
// @file    spi_slave.sv
// @brief   AD4080 配置 SPI 从机（Mode 3；streaming / 短指令 / CRC-8 / 状态回传）
// @details 指令相 = 1 命令位 + 15 或 7 地址位（SHORT_INSTRUCTION）；
//          数据相为任意字节数：SINGLE_INST=0（默认 streaming）时按
//          ADDR_ASCENSION 自动增/减地址指针并在 0x0000/0x7FFF 回绕。
//          CRC-8（0x07，种子 0xA5）按寄存器逐字节校验；写操作 CRC 不过不提交。
//          SEND_STATUS=1 时指令相在 SDO 回传状态字节（长指令回传两遍）。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.34-43（Figure 53/54/66）、p.75-76（0x00/0x01）、
//            p.81-82（0x10/0x11）
// 时钟域   : clk 单时钟；sclk/cs_n 异步输入，2FF 同步 + 边沿检测
// 复位     : rst_n 异步低有效
// 延迟     : 写提交在字节尾（无 CRC）或 CRC 尾（有 CRC），单拍 we
// 吞吐     : 1 字节/8 个 SCLK
// 可综合性 : 是
// 依赖     : ad4080_regs（经 addr/wdata/we/rdata）
// 修订记录 :
//   1.0.0 2026-10-19 初版（单字节、无 CRC）
//   2.0.0 2026-10-19 增加 CRC-8、SEND_STATUS、时钟数检查
//   3.0.0 2026-10-19 增加 streaming（SINGLE_INST=0）、短指令、地址递增/递减回绕、
//                    写数据相 SDO 驱 0、状态字节长指令回传两遍
//-----------------------------------------------------------------------------
// 设计说明：
//   SCLK/CS 由 clk 过采样（2FF + 边沿检测），要求 clk >= 8 x SCLK；
//   寄存器堆只有一个时钟域，不存在 CDC。
//   ASSUMPTION: streaming 模式下第 2 个及以后寄存器的 CRC 种子构成（"seeded
//   with the starting address and the corresponding data"），手册文本未给位序，
//   这里按 CRC8(0xA5, addr[15:8], addr[7:0], data) 实现。
//   ASSUMPTION: SEND_STATUS 的位序/重复次数手册只用图表示（Figure 53），
//   这里按"状态字节 MSB first、长指令回传两遍、短指令回传一遍"实现。
//   TODO(P2): 0x0E STREAM_MODE 回环计数、0x0F TRANSFER_CONFIG 未实现。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module spi_slave (
    input  logic        clk,                                // 系统时钟，>= 8 x sclk
    input  logic        rst_n,                              // 异步复位，低有效
    input  logic        cs_n,                               // 片选，低有效
    input  logic        sclk,                               // SPI 时钟，Mode 3，空闲高
    input  logic        sdi,                                // 主出从入
    output logic        sdo,                                // 从出主入
    output logic        sdo_oe,                             // SDO 输出使能
    // 接口配置
    input  logic        crc_en,                             // CRC 使能
    input  logic        send_status,                        // 指令相回传状态字节
    input  logic [7:0]  status_byte,                        // 要回传的状态字节
    input  logic        single_inst,                        // 1 = 单指令，0 = streaming
    input  logic        short_instr,                        // 1 = 7-bit 地址，0 = 15-bit
    input  logic        addr_asc,                           // 1 = 递增，0 = 递减
    // 寄存器接口
    output logic [15:0] addr,                               // 当前字节对应的寄存器地址
    output logic [7:0]  wdata,                              // 写数据（CRC 通过后提交）
    output logic        we,                                 // 写使能单拍
    input  logic [7:0]  rdata,                              // 待读回的寄存器值
    // 错误
    output logic        crc_err_pulse,                      // CRC 错误脉冲
    output logic        clk_cnt_err_pulse                   // 帧长错误脉冲
);

    // ---------------- 同步与边沿 ----------------
    logic cs_s0, cs_s1, sck_s0, sck_s1, sck_p;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cs_s0 <= 1'b1; cs_s1 <= 1'b1;
            sck_s0 <= 1'b1; sck_s1 <= 1'b1; sck_p <= 1'b1;
        end else begin
            cs_s0 <= cs_n;  cs_s1 <= cs_s0;
            sck_s0 <= sclk; sck_s1 <= sck_s0; sck_p <= sck_s1;
        end
    end

    wire cs_fall  = cs_s1 & ~cs_s0;
    wire cs_rise  = ~cs_s1 & cs_s0;
    wire sck_rise = sck_s1 & ~sck_p;
    wire sck_fall = ~sck_s1 & sck_p;

    // ---------------- 状态 ----------------
    localparam logic [1:0] PH_INSTR = 2'd0;
    localparam logic [1:0] PH_DATA  = 2'd1;
    localparam logic [1:0] PH_CRC   = 2'd2;

    logic [1:0]  phase;
    logic [4:0]  bitcnt;        // 组内位数：INSTR 0..15，其余 0..7
    logic [4:0]  instr_len_r;   // 8 或 16（CS 下降沿锁存）
    logic        cmd_r;         // 1 = 读
    logic [15:0] instr;
    logic [15:0] addr_ptr;
    logic [7:0]  byte_cnt;
    logic [7:0]  shin, shout, stat_sr;
    logic [2:0]  stat_cnt;
    logic [7:0]  wdata_r;
    logic        sdo_r, oe_r;
    logic        crc_err_r, clk_err_r;

    // ---------------- CRC-8 (poly 0x07, seed 0xA5, MSB first) ----------------
    logic [7:0] crc_calc, crc_rx;
    wire  [7:0] crc_step = {crc_calc[6:0], 1'b0} ^
                           ((crc_calc[7] ^ sdi) ? 8'h07 : 8'h00);
    wire  [7:0] crc_rx_nxt = {crc_rx[6:0], sdi};

    // Verilog-2001 风格函数体：Icarus 与 Yosys 的 SV 前端都接受
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

    // 下一个地址（streaming 增/减 + 回绕）
    wire [15:0] addr_next = addr_asc ? (addr_ptr == 16'h7FFF ? 16'h0000 : addr_ptr + 16'd1)
                                     : (addr_ptr == 16'h0000 ? 16'h7FFF : addr_ptr - 16'd1);

    wire [15:0] instr_nxt = {instr[14:0], sdi};
    wire [7:0]  shin_nxt  = {shin[6:0], sdi};

    // 指令末位 / 数据字节末位 / CRC 字节末位
    wire instr_done = (phase == PH_INSTR) && sck_rise && (bitcnt == instr_len_r - 5'd1);
    wire byte_done  = (phase == PH_DATA)  && sck_rise && (bitcnt == 5'd7);
    wire crc_done   = (phase == PH_CRC)   && sck_rise && (bitcnt == 5'd7);

    // 本帧是否在第一字节（CRC 语义按手册分首字节/后续字节）
    wire first_byte = (byte_cnt == 8'd0);

    // 写提交必须与 addr/wdata **同拍**：we 若是寄存输出，等它拉高时
    // streaming 已经把 addr 推到下一个寄存器（默认递减 -> 写到 addr-1）。
    wire we_byte = byte_done && !cmd_r && !crc_en;
    wire we_crc  = crc_done  && !cmd_r && crc_en && (crc_rx_nxt == crc_calc);
    wire we_comb = we_byte | we_crc;

    // ---------------- 主时序 ----------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase       <= PH_INSTR;
            bitcnt      <= '0;
            instr_len_r <= 5'd16;
            cmd_r       <= 1'b0;
            instr       <= '0;
            addr_ptr    <= '0;
            byte_cnt    <= '0;
            shin        <= '0;
            shout       <= '0;
            stat_sr     <= '0;
            stat_cnt    <= '0;
            wdata_r     <= '0;
            addr        <= '0;
            sdo_r       <= 1'b0;
            oe_r        <= 1'b0;
            crc_calc    <= 8'hA5;
            crc_rx      <= '0;
            crc_err_r   <= 1'b0;
            clk_err_r   <= 1'b0;
        end else begin
            crc_err_r <= 1'b0;
            clk_err_r <= 1'b0;

            if (cs_fall) begin
                phase       <= PH_INSTR;
                bitcnt      <= '0;
                instr_len_r <= short_instr ? 5'd8 : 5'd16;
                cmd_r       <= 1'b0;
                instr       <= '0;
                addr_ptr    <= '0;
                byte_cnt    <= '0;
                shin        <= '0;
                shout       <= '0;
                stat_sr     <= status_byte;
                stat_cnt    <= '0;
                crc_calc    <= 8'hA5;
                crc_rx      <= '0;
                sdo_r       <= send_status ? status_byte[7] : 1'b0;
                oe_r        <= send_status;
            end else if (cs_rise) begin
                // 帧必须在组边界结束：bitcnt 应为 0
                if (bitcnt != 5'd0) clk_err_r <= 1'b1;
                sdo_r <= 1'b0;
                oe_r  <= 1'b0;
            end else if (cs_s1) begin
                sdo_r <= 1'b0;
                oe_r  <= 1'b0;
            end else begin
                // ================= 上升沿：采样 =================
                if (sck_rise) begin
                    case (phase)
                        PH_INSTR: begin
                            instr    <= instr_nxt;
                            bitcnt   <= bitcnt + 5'd1;
                            crc_calc <= crc_step;
                            if (instr_done) begin
                                // 长指令：instr_nxt[15]=cmd, [14:0]=addr
                                // 短指令：instr_nxt[7]=cmd,  [6:0]=addr
                                if (instr_len_r == 5'd16) begin
                                    cmd_r    <= instr_nxt[15];
                                    addr_ptr <= {1'b0, instr_nxt[14:0]};
                                    addr     <= {1'b0, instr_nxt[14:0]};
                                end else begin
                                    cmd_r    <= instr_nxt[7];
                                    addr_ptr <= {9'b0, instr_nxt[6:0]};
                                    addr     <= {9'b0, instr_nxt[6:0]};
                                end
                                phase  <= PH_DATA;
                                bitcnt <= '0;
                            end
                        end
                        PH_DATA: begin
                            shin   <= shin_nxt;
                            bitcnt <= bitcnt + 5'd1;
                            // 写：按 sdi 逐位入 CRC。
                            // 读：首字节的 CRC 只覆盖指令；后续字节在字节尾按
                            //     刚发出的 rdata 整字节更新（不能用 sdi，主机在读时
                            //     送的是 padding）。
                            if (!cmd_r) crc_calc <= crc_step;
                            if (byte_done) begin
                                if (!cmd_r) wdata_r <= shin_nxt;   // 先存，等 CRC
                                if (cmd_r && !first_byte) crc_calc <= crc8_byte(crc_calc, rdata);
                                if (!crc_en) begin
                                    // 无 CRC：本字节即完成
                                    byte_cnt <= byte_cnt + 8'd1;
                                    if (!single_inst) begin
                                        addr_ptr <= addr_next;
                                        addr     <= addr_next;
                                    end
                                end else begin
                                    phase  <= PH_CRC;
                                    crc_rx <= '0;
                                end
                                bitcnt <= '0;
                            end
                        end
                        PH_CRC: begin
                            crc_rx  <= crc_rx_nxt;
                            bitcnt  <= bitcnt + 5'd1;
                            if (crc_done) begin
                                // 校验：写全部字节；读只验首字节（手册：读只校验命令与起始地址）
                                if (!cmd_r || first_byte) begin
                                    if (crc_rx_nxt != crc_calc) crc_err_r <= 1'b1;
                                end
                                // CRC 通过由 we_crc 在同拍产生，这里不再寄存
                                byte_cnt <= byte_cnt + 8'd1;
                                phase    <= PH_DATA;
                                bitcnt   <= '0;
                                // streaming：更新地址指针，并为下一寄存器重播种子
                                if (!single_inst) begin
                                    addr_ptr <= addr_next;
                                    addr     <= addr_next;
                                    crc_calc <= crc8_byte(crc8_byte(8'hA5, addr_next[15:8]), addr_next[7:0]);
                                end
                            end
                        end
                        default: phase <= PH_INSTR;
                    endcase
                end

                // ================= 下降沿：更新 SDO =================
                if (sck_fall) begin
                    case (phase)
                        PH_INSTR: begin
                            if (send_status) begin
                                // 状态字节 MSB first，长指令传两遍，短指令传一遍
                                stat_sr  <= {stat_sr[6:0], 1'b0};
                                sdo_r    <= stat_sr[6];
                                if (stat_cnt == 3'd7) begin
                                    stat_sr  <= status_byte;
                                    stat_cnt <= '0;
                                    // 短指令到第 8 位后不再重载
                                    if (instr_len_r == 5'd8) begin
                                        stat_sr <= 8'h00;
                                    end
                                end else begin
                                    stat_cnt <= stat_cnt + 3'd1;
                                end
                            end
                        end
                        PH_DATA: begin
                            if (bitcnt == 5'd0) begin
                                // 新字节第一个下降沿：读时装载 rdata；写时驱 0
                                shout <= rdata;
                                sdo_r <= cmd_r ? rdata[7] : 1'b0;
                                oe_r  <= 1'b1;
                            end else begin
                                shout <= {shout[6:0], 1'b0};
                                sdo_r <= cmd_r ? shout[6] : 1'b0;
                            end
                        end
                        PH_CRC: begin
                            if (cmd_r) begin
                                // 读：器件在 CRC 相回送自己的校验
                                if (bitcnt == 5'd0) begin
                                    shout <= crc_calc;
                                    sdo_r <= crc_calc[7];
                                    oe_r  <= 1'b1;
                                end else begin
                                    shout <= {shout[6:0], 1'b0};
                                    sdo_r <= shout[6];
                                end
                            end else begin
                                oe_r  <= 1'b0;   // 写：主机发 CRC，本机不驱动
                                sdo_r <= 1'b0;
                            end
                        end
                        default: ;
                    endcase
                end
            end
        end
    end

`ifdef FORMAL
    // ------------------------------------------------------------------
    // 形式不变量（仅 -DFORMAL 时编译，不参与综合）
    //   1) 写提交必须落在当前指令地址上：这是仿真抓到过的 addr 偏移 bug
    //   2) 读事务绝不能产生写
    //   3) 地址只用 15 位
    // ------------------------------------------------------------------
    always_comb begin
        assert (!we || (addr == addr_ptr));
        assert (!we || !cmd_r);
        assert (addr[15] == 1'b0);
        assert (phase <= PH_CRC);
    end
`endif

    assign sdo               = sdo_r;
    assign sdo_oe            = oe_r;
    assign we    = we_comb;
    assign wdata = we_byte ? shin_nxt : wdata_r;
    assign crc_err_pulse     = crc_err_r;
    assign clk_cnt_err_pulse = clk_err_r;

endmodule

`default_nettype wire
