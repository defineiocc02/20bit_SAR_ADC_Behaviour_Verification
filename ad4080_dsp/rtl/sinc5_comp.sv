//=============================================================================
// @file    sinc5_comp.sv
// @brief   SINC5 后接三抽头 FIR 与额外二倍抽取。
// @details FIR 对 SINC5 输出的偶数相位计算，保存前两点；系数为缩放 2^15 的
//          17 bit 有符号整数。默认 (32768,0,0) 只保持结构与二倍抽取，不代表真实补偿频响。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；valid、配置与 filter_sync 同步。
// 复位: rst_n 异步低有效；filter_sync 取消 CIC/FIR 内部流水。
// 延迟: 含 CIC 样本域流水、FIR 启动前两点与输出寄存，不能简化为 N+2 clk。
// 吞吐: 每 2N 个有效输入最多一个结果，启动时还须填充 FIR 历史。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: sinc5_decim。
// 边界: C0/C1/C2 采用 17 bit 补码，整数范围 -65536..65535，缩放 2^15。
//     输出加半码后算术右移并饱和；settled 门限使用当前 dec_sel，改配置须配合 filter_sync。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc5_comp #(
    parameter int IN_W = 20,  // IN_W: 补码输入/输出位宽，工程配置为 20 bit。
    parameter int C0   = 32768,   // C0: 当前样本的系数整数，缩放 2^15，默认表示 1.0。
    parameter int C1   = 0,  // C1: 前一样本的系数整数，缩放 2^15。
    parameter int C2   = 0  // C2: 前两样本的系数整数，缩放 2^15。
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out,    // 输出有效，单拍
    output logic                 settled       // 建立完成；按有效输入计数。
);

    localparam int COEF_W = 17;                  // 17 bit 补码整数，缩放 2^15，范围 -65536..65535。
    localparam int PROD_W = IN_W + COEF_W + 2;   // 三项相加再留 2 位

    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));
    localparam logic signed [PROD_W+1:0] MAX_EXT =
        $signed({{(PROD_W+2-IN_W){1'b0}}, MAX_CODE});
    localparam logic signed [PROD_W+1:0] MIN_EXT =
        $signed({{(PROD_W+2-IN_W){1'b1}}, MIN_CODE});

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
    // 负系数（例如 -1 = 17'h1FFFF）须按补码切片，避免补零改变符号。
    assign c0_s = $signed(C0[COEF_W-1:0]);
    assign c1_s = $signed(C1[COEF_W-1:0]);
    assign c2_s = $signed(C2[COEF_W-1:0]);

    wire signed [PROD_W+1:0] acc_ext  = $signed({{2{acc_r[PROD_W-1]}}, acc_r});
    wire signed [PROD_W+1:0] rnd_ext  = $signed({{(PROD_W-13){1'b0}}, 15'd16384});
    assign q_w = (acc_ext + rnd_ext) >>> 15;

    always_comb begin
        acc = $signed(s5_data) * c0_s
            + $signed(x1)      * c1_s
            + $signed(x2)      * c2_s;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x1        <= '0;
            x2        <= '0;
            phase     <= 1'b0;
            primed    <= '0;
            acc_r     <= '0;
            v_r       <= 1'b0;
            data_out  <= '0;
            valid_out <= 1'b0;
        end else if (filter_sync) begin
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
                    // 当前 SINC5 样本为 x[2m]，历史填充完整后才允许输出。
                    acc_r <= acc;
                    v_r   <= (primed == 2'd2) || (primed == 2'd3);
                end
                x2    <= x1;
                x1    <= s5_data;
                phase <= ~phase;
                if (primed != 2'd3) primed <= primed + 2'd1;
            end

            if (v_r) begin
                if (q_w > MAX_EXT)
                    data_out <= {1'b0, {(IN_W-1){1'b1}}};
                else if (q_w < MIN_EXT)
                    data_out <= {1'b1, {(IN_W-1){1'b0}}};
                else
                    data_out <= q_w[IN_W-1:0];
                valid_out <= 1'b1;
            end
        end
    end

    // 建立计数采用手册 p.69 的 2+35N+10 数值门限，本实现按 valid_in 推进。
    // 该计数不是物理 CNV 建立证据。
    localparam int SET_W = 16;
    logic [SET_W-1:0] settle_cnt;
    logic [10:0]      n_d;
    wire  [SET_W-1:0] settle_target;

    // N 的编码映射与内部 SINC5 相同，超过 7 时采用最大抽取率 256。
    always_comb begin
        if (dec_sel > 4'd7) n_d = 11'd256;
        else                n_d = 11'd1 << (dec_sel + 4'd1);
    end
    // 35N + 12 = (N<<5) + (N<<1) + N + 12
    assign settle_target = (SET_W'(n_d) << 5) + (SET_W'(n_d) << 1) + SET_W'(n_d) + 16'd12;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            settle_cnt <= '0;
            settled    <= 1'b0;
        end else if (filter_sync) begin
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
