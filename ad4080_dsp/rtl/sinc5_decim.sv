//=============================================================================
// @file    sinc5_decim.sv
// @brief   SINC5（5 级 CIC）抽取滤波器（运行时抽取率，补码）
// @details 5 级积分 -> 抽取 D -> 5 级梳状；H(z)=((1-z^-D)/(1-z^-1))^5；
//          ACC_W=61（D=256），输出右移 5log2D 并饱和。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.20 Table 7, p.64-66 Table 28/29/62
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效；filter_sync 清积分/梳状
// 延迟     : D 个输入 + 2 clk
// 吞吐     : 1 sample/clk 输入，1/D 输出
// 可综合性 : 是；5 级积分加法链是关键路径（见 synthesis_review.md）
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版（编译期 DECIM，无符号）
//   2.0.0 2026-10-19 运行时 dec_sel；改补码；归一化用锁存码
//   3.0.0 2026-10-19 增加 settled（settling = 2+5D+4）
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.20 Table 7（20-bit 补码）、
//     p.64-66 Table 28/29/62：
//     FILTER_SEL=10 -> SINC5，D = 2..256
//     SINC_DEC_RATE: D = 2^(code+1)，code>7 -> 256
//     settling = 2 + 5D + 4 个 CNV
//
//   结构：5 级积分器 -> 抽取 D -> 5 级梳状器
//     H(z) = ((1 - z^-D)/(1 - z^-1))^5，直流增益 D^5
//     输出右移 5*log2(D) 归一化（D 为 2 的幂，移位精确）
//
//   位宽：ACC_W = IN_W + 5*8 + 1 = 61。CIC 固有位增长，少一位就溢出。
//   符号：全链路 signed；补码输入直接参与运算（CIC 对补码是线性的，
//         前提是每一步都做符号扩展——旧版按无符号处理会把负半轴算反）。
//   输出：20-bit 补码，四舍五入 + 饱和。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module sinc5_decim #(
    parameter int IN_W = 20
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out,    // 输出有效，单拍
    output logic                 settled       // 建立完成（2+5D+4 个样本后）                // 输出有效，单拍
);

    localparam int MAX_LOG2D = 8;
    localparam int ACC_W     = IN_W + 5*MAX_LOG2D + 1;   // 61
    localparam int ACC_WX    = ACC_W + 1;

    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));

    function automatic void dec_map (
        input  logic [3:0]  s,
        output logic [10:0] d,
        output logic [3:0]  l
    );
        if (s > 4'd7) begin
            d = 11'd256;
            l = 4'd8;
        end else begin
            d = 11'd1 << (s + 4'd1);
            l = s + 4'd1;
        end
    endfunction

    logic [10:0] d_map, d_cur;
    logic [3:0]  l_map, l_cur;
    logic [7:0]  shift_amt;

    always_comb begin
        dec_map(dec_sel, d_map, l_map);
        // 必须用**已锁存**的 l_cur：累加用的是 d_cur，归一化就必须用
        // 同一个块对应的 5*log2(D)。用 l_map 会在块中途改码时把增益算错。
        shift_amt = {2'b0, l_cur, 2'b00} + {4'b0, l_cur};   // 5*l
    end

    logic signed [ACC_W-1:0] integ [0:4];
    logic signed [ACC_W-1:0] integ_nxt [0:4];
    logic signed [ACC_W-1:0] comb_d [0:4];
    logic signed [ACC_W-1:0] comb_in [0:5];
    logic signed [ACC_W-1:0] comb_nxt [0:4];
    logic [10:0]             cnt;
    logic                    dec_en;
    logic signed [ACC_W-1:0] data_ext;

    assign data_ext = $signed({{(ACC_W-IN_W){data_in[IN_W-1]}}, data_in});

    always_comb begin
        integ_nxt[0] = integ[0] + data_ext;
        integ_nxt[1] = integ[1] + integ_nxt[0];
        integ_nxt[2] = integ[2] + integ_nxt[1];
        integ_nxt[3] = integ[3] + integ_nxt[2];
        integ_nxt[4] = integ[4] + integ_nxt[3];
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt    <= '0;
            dec_en <= 1'b0;
        end else if (filter_sync) begin
            cnt    <= '0;
            dec_en <= 1'b0;
        end else if (valid_in) begin
            if (cnt == d_cur - 11'd1) begin
                cnt    <= '0;
                dec_en <= 1'b1;
            end else begin
                cnt    <= cnt + 11'd1;
                dec_en <= 1'b0;
            end
        end else begin
            dec_en <= 1'b0;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || filter_sync) begin
            for (int i=0;i<5;i++) integ[i] <= '0;
        end else if (valid_in) begin
            for (int i=0;i<5;i++) integ[i] <= integ_nxt[i];
        end
    end

    always_comb begin
        comb_in[0] = integ[4];
        for (int i=0;i<5;i++) begin
            comb_nxt[i] = comb_in[i] - comb_d[i];
            comb_in[i+1] = comb_nxt[i];
        end
    end

    logic signed [ACC_WX-1:0] round_const;
    logic signed [ACC_WX-1:0] comb_ext;
    logic signed [ACC_WX-1:0] norm;

    assign comb_ext    = $signed({{(ACC_WX-ACC_W){comb_nxt[4][ACC_W-1]}}, comb_nxt[4]});
    assign round_const = $signed({{(ACC_WX-1){1'b0}}, 1'b1} << (shift_amt - 8'd1));
    assign norm        = (comb_ext + round_const) >>> shift_amt;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || filter_sync) begin
            for (int i=0;i<5;i++) comb_d[i] <= '0;
            data_out  <= '0;
            valid_out <= 1'b0;
            d_cur     <= filter_sync ? d_map : 11'd2;
            l_cur     <= filter_sync ? l_map : 4'd1;
        end else begin
            valid_out <= 1'b0;
            if (dec_en) begin
                for (int i=0;i<5;i++) comb_d[i] <= comb_in[i];
                if (norm > $signed({{(ACC_WX-IN_W){MAX_CODE[IN_W-1]}}, MAX_CODE}))
                    data_out <= {1'b0, {(IN_W-1){1'b1}}};
                else if (norm < $signed({{(ACC_WX-IN_W){MIN_CODE[IN_W-1]}}, MIN_CODE}))
                    data_out <= {1'b1, {(IN_W-1){1'b0}}};
                else
                    data_out <= norm[IN_W-1:0];
                valid_out <= 1'b1;
                d_cur     <= d_map;
                l_cur     <= l_map;
            end
        end
    end

    // ---------------- 建立计数（手册 p.69：settling = 2 + 5D + 4 个 CNV） ----------------
    localparam int SET_W = 16;
    logic [SET_W-1:0] settle_cnt;
    wire  [SET_W-1:0] settle_target = (SET_W'(d_cur) << 2) + SET_W'(d_cur) + 16'd6;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || filter_sync) begin
            settle_cnt <= '0;
            settled    <= 1'b0;
        end else if (valid_in && !settled) begin
            if (settle_cnt == settle_target - 16'd1) begin
                settled <= 1'b1;
            end else begin
                settle_cnt <= settle_cnt + 16'd1;
            end
        end
    end

endmodule

`default_nettype wire
