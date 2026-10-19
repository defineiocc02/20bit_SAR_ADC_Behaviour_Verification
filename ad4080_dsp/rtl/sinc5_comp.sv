//=============================================================================
// @file    sinc5_comp.sv
// @brief   SINC5 + 补偿滤波器（总抽取 2N，补码）
// @details sinc5(N) 后接 2 倍抽取的 3 抽头 FIR（Q1.15）；
//          系数手册未给出，默认 (1,0,0)，结构上已实现额外 2 倍抽取。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.64-66 Table 28/29/62（系数未公开）
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效
// 延迟     : N 个输入 + 2 clk（再 2 倍抽取）
// 吞吐     : 1 sample/clk 输入，1/(2N) 输出
// 可综合性 : 是；3 个乘法器（默认系数下被常量折叠）
// 依赖     : sinc5_decim
// 修订记录 :
//   1.0.0 2026-10-19 初版（缺 2 倍抽取，负系数零扩展）
//   2.0.0 2026-10-19 增加 2 倍抽取；系数改补码切片
//   3.0.0 2026-10-19 增加 settled（settling = 2+35N+10）
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.64-66 Table 28/29/62
//     FILTER_SEL=11 -> SINC5 + compensation
//     总抽取 = N x 2，N = 2^(code+1)，code 0..7；code>7 -> N=256
//     允许的总抽取率 4..512；settling = 2 + 35N + 10 个 CNV
//
//   结构：sinc5_decim(N) -> 2 倍抽取的 3 抽头 FIR（Q1.15）
//     y[m] = c0*x[2m] + c1*x[2m-1] + c2*x[2m-2]
//
//   系数：手册只给幅频响应（Fig.105-107），**没有给系数**。因此 C0/C1/C2
//     作为参数暴露，默认 (1,0,0)。真实系数必须来自 ADI 资料，不臆造。
//     即使默认值，"补偿块额外的 2 倍抽取"这一结构事实已按手册实现。
//
//   符号：20-bit 补码输入，全链路 signed；输出饱和到 20-bit 补码。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module sinc5_comp #(
    parameter int IN_W = 20,
    parameter int C0   = 32768,   // Q1.15, 1.0
    parameter int C1   = 0,
    parameter int C2   = 0
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out,    // 输出有效，单拍
    output logic                 settled       // 建立完成（2+35N+10 个样本后）                // 输出有效，单拍
);

    localparam int COEF_W = 17;                  // 有符号 Q1.15：-65536..+32767 足够
    localparam int PROD_W = IN_W + COEF_W + 2;   // 三项相加再留 2 位

    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));

    logic [IN_W-1:0] s5_data;
    logic            s5_valid;
    logic            s5_settled_unused;   // 建立计数在本层独立实现，内部实例不消费

    sinc5_decim #(.IN_W(IN_W)) u_sinc5 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(valid_in), .data_in(data_in), .dec_sel(dec_sel),
        .data_out(s5_data), .valid_out(s5_valid), .settled(s5_settled_unused)
    );

    logic [IN_W-1:0] x1, x2;
    logic            phase;      // 0 = 偶样本（输出点），1 = 奇样本
    logic [1:0]      primed;
    logic signed [PROD_W-1:0] acc;
    logic signed [PROD_W-1:0] acc_r;
    logic signed [PROD_W+1:0] q_w;
    logic                     v_r;

    logic signed [COEF_W-1:0] c0_s, c1_s, c2_s;
    // 负系数（如 -1 = 17'h1FFFF）必须按补码切片，不能补 0 零扩展。
    assign c0_s = $signed(C0[COEF_W-1:0]);
    assign c1_s = $signed(C1[COEF_W-1:0]);
    assign c2_s = $signed(C2[COEF_W-1:0]);

    assign q_w = (acc_r + (1 << 14)) >>> 15;

    always_comb begin
        acc = $signed(s5_data) * c0_s
            + $signed(x1)      * c1_s
            + $signed(x2)      * c2_s;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || filter_sync) begin
            x1        <= '0;
            x2        <= '0;
            phase     <= 1'b0;
            primed    <= '0;
            acc_r     <= '0;
            v_r       <= 1'b0;
            data_out  <= '0;
            valid_out <= 1'b0;
        end else begin
            v_r       <= 1'b0;
            valid_out <= 1'b0;

            if (s5_valid) begin
                if (phase == 1'b0) begin
                    acc_r <= acc;                 // 当前样本即 x[2m]
                    v_r   <= (primed == 2'd2) || (primed == 2'd3);
                end
                x2    <= x1;
                x1    <= s5_data;
                phase <= ~phase;
                if (primed != 2'd3) primed <= primed + 2'd1;
            end

            if (v_r) begin
                if (q_w > MAX_CODE)
                    data_out <= {1'b0, {(IN_W-1){1'b1}}};
                else if (q_w < MIN_CODE)
                    data_out <= {1'b1, {(IN_W-1){1'b0}}};
                else
                    data_out <= q_w[IN_W-1:0];
                valid_out <= 1'b1;
            end
        end
    end

    // ---------------- 建立计数（手册 p.69：settling = 2 + 35N + 10 个 CNV） ----------------
    localparam int SET_W = 16;
    logic [SET_W-1:0] settle_cnt;
    logic [10:0]      n_d;
    logic [3:0]       n_l;
    wire  [SET_W-1:0] settle_target;

    // 内部 SINC5 的 N：与 sinc5_decim.dec_map 一致
    always_comb begin
        if (dec_sel > 4'd7) begin
            n_d = 11'd256;
            n_l = 4'd8;
        end else begin
            n_d = 11'd1 << (dec_sel + 4'd1);
            n_l = dec_sel + 4'd1;
        end
    end
    // 35N + 12 = (N<<5) + (N<<1) + N + 12
    assign settle_target = (SET_W'(n_d) << 5) + (SET_W'(n_d) << 1) + SET_W'(n_d) + 16'd12;

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
