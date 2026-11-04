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

    // 两个纯函数（Icarus 不支持 function 的 output 参数）
    function automatic [10:0] dec_d(input [3:0] s);
        begin
            if (s > 4'd7) dec_d = 11'd256;
            else          dec_d = 11'd1 << (s + 4'd1);
        end
    endfunction

    function automatic [3:0] dec_l(input [3:0] s);
        begin
            if (s > 4'd7) dec_l = 4'd8;
            else          dec_l = s + 4'd1;
        end
    endfunction

    logic [10:0] d_map, d_cur;
    logic [3:0]  l_map, l_cur;
    logic [7:0]  shift_amt;

    always_comb begin
        d_map = dec_d(dec_sel);
        l_map = dec_l(dec_sel);
        // 必须用**已锁存**的 l_cur：累加用的是 d_cur，归一化就必须用
        // 同一个块对应的 5*log2(D)。用 l_map 会在块中途改码时把增益算错。
        shift_amt = {2'b0, l_cur, 2'b00} + {4'b0, l_cur};   // 5*l
    end

    logic signed [ACC_W-1:0] integ [0:4];
    logic [4:0]              dec_pipe;   // 积分器 5 级打拍后的抽取对齐（按 valid 推进）
    logic signed [ACC_W-1:0] comb_d [0:4];
    logic [10:0]             cnt;
    logic signed [ACC_W-1:0] data_ext;

    assign data_ext = $signed({{(ACC_W-IN_W){data_in[IN_W-1]}}, data_in});

    // 积分器现在是“每级一个寄存器”，第 D 个样本要 4 拍后才到达 integ[4]，
    // 因此抽取脉冲延 4 拍，再触发梳状器。
    wire dec_now = valid_in && (cnt == d_cur - 11'd1);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt      <= '0;
            dec_pipe <= '0;
        end else if (filter_sync) begin
            cnt      <= '0;
            dec_pipe <= '0;
        end else if (valid_in) begin
            cnt <= (cnt == d_cur - 11'd1) ? 11'd0 : cnt + 11'd1;
            dec_pipe <= {dec_pipe[3:0], dec_now};
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            integ[0] <= '0; integ[1] <= '0; integ[2] <= '0; integ[3] <= '0; integ[4] <= '0;
        end else if (filter_sync) begin
            integ[0] <= '0; integ[1] <= '0; integ[2] <= '0; integ[3] <= '0; integ[4] <= '0;
        end else if (valid_in) begin
            // 标准流水 CIC：每级只做一次加法，链上不再串 5 个 61-bit 加法器
            integ[0] <= integ[0] + data_ext;
            integ[1] <= integ[1] + integ[0];
            integ[2] <= integ[2] + integ[1];
            integ[3] <= integ[3] + integ[2];
            integ[4] <= integ[4] + integ[3];
        end
    end

    // ---------------- 梳状器：收缩式（systolic）5 级 ----------------
    // 每个抽取事件（dec_evt）所有级同时更新一次：第 i 级用上一事件的
    // 第 i-1 级结果做一次 ACC_W 位减法。级间只差一个减法器，不再串 5 个。
    logic signed [ACC_W-1:0]  comb_s [0:4];
    logic signed [ACC_WX-1:0] out_r;
    logic [7:0]               shift_r;
    logic                     out_v, norm_v, sat_v;
    logic signed [ACC_WX-1:0] norm_r;
    logic signed [ACC_WX-1:0] rnd_w;
    logic signed [ACC_WX-1:0] norm_w;
    logic signed [ACC_WX-1:0] shifted;

    // dec_pipe[4] 在 valid 速率下保持一个 valid 周期；与 valid_in 相与得到单拍事件。
    // 第 D 个样本需要 5 个 valid 沿才传播到 integ[4]（每级 1 拍）。
    wire dec_evt = valid_in && dec_pipe[4];

    assign rnd_w  = $signed({{(ACC_WX-1){1'b0}}, 1'b1} << (shift_r - 8'd1));
    assign norm_w = norm_r >>> shift_r;
    // 移位结果先打拍，饱和/选择放下一拍（切断 62-bit 桶形移位 -> 比较器 的路径）

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            comb_d[0] <= '0; comb_d[1] <= '0; comb_d[2] <= '0;
            comb_d[3] <= '0; comb_d[4] <= '0;
            comb_s[0] <= '0; comb_s[1] <= '0; comb_s[2] <= '0;
            comb_s[3] <= '0; comb_s[4] <= '0;
            out_r     <= '0;
            shift_r   <= '0;
            out_v     <= 1'b0;
            norm_r    <= '0;
            norm_v    <= 1'b0;
            sat_v     <= 1'b0;
            shifted   <= '0;
            data_out  <= '0;
            valid_out <= 1'b0;
            d_cur     <= 11'd2;
            l_cur     <= 4'd1;
        end else if (filter_sync) begin
            comb_d[0] <= '0; comb_d[1] <= '0; comb_d[2] <= '0;
            comb_d[3] <= '0; comb_d[4] <= '0;
            comb_s[0] <= '0; comb_s[1] <= '0; comb_s[2] <= '0;
            comb_s[3] <= '0; comb_s[4] <= '0;
            out_r     <= '0;
            shift_r   <= '0;
            out_v     <= 1'b0;
            norm_r    <= '0;
            norm_v    <= 1'b0;
            sat_v     <= 1'b0;
            shifted   <= '0;
            data_out  <= '0;
            valid_out <= 1'b0;
            d_cur     <= d_map;
            l_cur     <= l_map;
        end else begin
            valid_out <= 1'b0;
            out_v     <= dec_evt;
            norm_v    <= out_v;
            sat_v     <= norm_v;

            if (dec_evt) begin
                comb_s[0] <= integ[4] - comb_d[0];  comb_d[0] <= integ[4];
                comb_s[1] <= comb_s[0] - comb_d[1]; comb_d[1] <= comb_s[0];
                comb_s[2] <= comb_s[1] - comb_d[2]; comb_d[2] <= comb_s[1];
                comb_s[3] <= comb_s[2] - comb_d[3]; comb_d[3] <= comb_s[2];
                comb_s[4] <= comb_s[3] - comb_d[4]; comb_d[4] <= comb_s[3];
                out_r     <= $signed({{(ACC_WX-ACC_W){comb_s[4][ACC_W-1]}}, comb_s[4]});
                shift_r   <= shift_amt;
                d_cur     <= d_map;
                l_cur     <= l_map;
            end

            if (out_v) norm_r <= out_r + rnd_w;

            if (norm_v) shifted <= norm_w;

            if (sat_v) begin
                if (shifted > $signed({{(ACC_WX-IN_W){MAX_CODE[IN_W-1]}}, MAX_CODE}))
                    data_out <= {1'b0, {(IN_W-1){1'b1}}};
                else if (shifted < $signed({{(ACC_WX-IN_W){MIN_CODE[IN_W-1]}}, MIN_CODE}))
                    data_out <= {1'b1, {(IN_W-1){1'b0}}};
                else
                    data_out <= shifted[IN_W-1:0];
                valid_out <= 1'b1;
            end
        end
    end

    // ---------------- 建立计数（按“输出个数”判定） ----------------
    // 手册 p.69：settling = 2 + 5D + 4 个 CNV。流水实现让输出整体后移，
    // 若仍按输入样本计数，settled 会提前。这里换算成“需要多少个已输出结果”：
    //   n_out = ceil(settling / D)
    localparam int SET_W = 16;
    logic [SET_W-1:0] out_cnt;
    logic [SET_W-1:0] n_out_settle;
    wire  [SET_W-1:0] settle_cnv = (SET_W'(d_cur) << 2) + SET_W'(d_cur) + 16'd6;
    wire  [SET_W-1:0] settle_out = (settle_cnv + SET_W'(d_cur) - 16'd1) >> l_cur;

    // 收缩式梳状器把输出整体后移 4 个抽取点（+ 2 拍归一化），
    // 因此“已输出个数”的门限还要加 5，才能覆盖流水延迟。
    assign n_out_settle = settle_out + 16'd5;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_cnt <= '0;
            settled <= 1'b0;
        end else if (filter_sync) begin
            out_cnt <= '0;
            settled <= 1'b0;
        end else if (valid_out && !settled) begin
            out_cnt <= out_cnt + 16'd1;
            if (out_cnt + 16'd1 >= n_out_settle) settled <= 1'b1;
        end
    end

endmodule

`default_nettype wire
