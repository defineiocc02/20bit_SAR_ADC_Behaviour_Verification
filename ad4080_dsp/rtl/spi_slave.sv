//=============================================================================
// @file    spi_slave.sv
// @brief   将 Mode 3 配置 SPI 转换为同步寄存器字节事务。
// @details SCLK/CS 由 clk 过采样，提供长/短指令、流式地址方向、可选 CRC 和状态
//          输出。仅在选中且字节完整/CRC 有效的边界提交写入，CS 退出丢弃未完成帧状态。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk>=8*SCLK；CS/SCLK 先同步，SDI 须满足相对 SCLK 的稳定窗口。
// 复位: rst_n 异步低有效；禁止 we 并清帧状态。
// 延迟: 同步后的数据/CRC 末位在同域寄存器接受沿提交，CS 须留足同步裕量。
// 吞吐: 数据字节占 8 个 SCLK，启用 CRC 时另加 8 个；指令占 8/16 个。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 经 addr/wdata/we/rdata 与 ad4080_regs 连接。
// 边界: 仅同步 CS 及其延迟寄存器参与帧控制，首级同步寄存器只供下一级。
//     CRC 后续字节播种与状态位序为本工程规则，仍缺器件/原文一致性证据。
//     不实现 SPI 结果 FIFO 读口、STREAM_MODE 计数或 TRANSFER_CONFIG。
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

    // 帧状态只使用第二级同步量及其延迟值，首级寄存器不直接参与边沿判定。
    logic cs_s0, cs_s1, cs_p, sck_s0, sck_s1, sck_p;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cs_s0 <= 1'b1; cs_s1 <= 1'b1; cs_p <= 1'b1;
            sck_s0 <= 1'b1; sck_s1 <= 1'b1; sck_p <= 1'b1;
        end else begin
            cs_s0 <= cs_n;  cs_s1 <= cs_s0; cs_p <= cs_s1;
            sck_s0 <= sclk; sck_s1 <= sck_s0; sck_p <= sck_s1;
        end
    end

    wire cs_fall  = cs_p & ~cs_s1;
    wire cs_rise  = ~cs_p & cs_s1;
    wire sck_rise = sck_s1 & ~sck_p;
    wire sck_fall = ~sck_s1 & sck_p;


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

    // CRC-8 使用多项式 0x07、初值 0xA5，按 MSB 先行推进。
    logic [7:0] crc_calc, crc_rx;
    wire  [7:0] crc_step = {crc_calc[6:0], 1'b0} ^
                           ((crc_calc[7] ^ sdi) ? 8'h07 : 8'h00);
    wire  [7:0] crc_rx_nxt = {crc_rx[6:0], sdi};

    // 保留 Verilog-2001 式函数体，兼容本工程 Icarus/Yosys 前端使用方式。
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

    // 流式访问按方向推进并在 15 bit 地址域回绕；single_inst 为 1 时抑制地址推进。
    wire [15:0] addr_next = addr_asc ? (addr_ptr == 16'h7FFF ? 16'h0000 : addr_ptr + 16'd1)
                                     : (addr_ptr == 16'h0000 ? 16'h7FFF : addr_ptr - 16'd1);

    wire [15:0] instr_nxt = {instr[14:0], sdi};
    wire [7:0]  shin_nxt  = {shin[6:0], sdi};

    // 指令末位 / 数据字节末位 / CRC 字节末位
    wire instr_done = (phase == PH_INSTR) && sck_rise && (bitcnt == instr_len_r - 5'd1);
    wire byte_done  = (phase == PH_DATA)  && sck_rise && (bitcnt == 5'd7);
    wire crc_done   = (phase == PH_CRC)   && sck_rise && (bitcnt == 5'd7);

    // CRC 处理区分本帧首字节与后续字节，具体覆盖范围采用本工程的接口规则。
    wire first_byte = (byte_cnt == 8'd0);

    // 写提交须与 addr/wdata 同拍。若将 we 再寄存，流式地址会先推进到下一
    // 寄存器；默认递减时将错写到 addr-1。
    wire we_byte = byte_done && !cmd_r && !crc_en;
    wire we_crc  = crc_done  && !cmd_r && crc_en && (crc_rx_nxt == crc_calc);
    // 提交与状态机共用同步帧边界，CS 取消后的待处理 SCLK 边沿不得提交不完整字节。
    wire frame_active = rst_n && !cs_s1 && !cs_fall && !cs_rise;
    wire we_comb = frame_active && (we_byte | we_crc);


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
                phase    <= PH_INSTR;
                bitcnt   <= '0;
                instr    <= '0;
                addr_ptr <= '0;
                byte_cnt <= '0;
                shin     <= '0;
                shout    <= '0;
                stat_sr  <= '0;
                stat_cnt <= '0;
                wdata_r  <= '0;
                crc_calc <= 8'hA5;
                crc_rx   <= '0;
                sdo_r <= 1'b0;
                oe_r  <= 1'b0;
            end else if (cs_s1) begin
                phase  <= PH_INSTR;
                bitcnt <= '0;
                sdo_r <= 1'b0;
                oe_r  <= 1'b0;
            end else begin
                // 同步后的 SCLK 上升沿采样指令、数据和 CRC。
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
                            // 写入按 sdi 逐位更新 CRC。读取的首字节只覆盖指令，后续
                            // 字节在字节尾使用刚发出的 rdata 更新；主机 sdi 是填充数据，
                            // 不能代替实际读回字节。
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

                // 同步后的 SCLK 下降沿更新 SDO，供下个采样沿使用。
                if (sck_fall) begin
                    case (phase)
                        PH_INSTR: begin
                            if (send_status) begin
                            // 状态字节按 MSB 先行发送，长指令传两遍，短指令传一遍。
                                stat_sr  <= {stat_sr[6:0], 1'b0};
                                sdo_r    <= stat_sr[7];
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

    // 仅在 -DFORMAL 下检查提交地址、读写方向和 15 bit 地址域。addr_ptr 在
    // cs_fall 清零而 addr 保留上一帧值，因此仅在 we=1 时要求两者相等。
    initial begin
        addr     = 16'd0;
        addr_ptr = 16'd0;
    end

    always_comb begin
        assert (!we || (addr == addr_ptr));
        assert (!we || !cmd_r);
        assert (addr[15] == 1'b0);
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
