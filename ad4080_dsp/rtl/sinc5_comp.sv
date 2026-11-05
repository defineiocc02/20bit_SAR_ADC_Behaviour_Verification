//=============================================================================
// @file    sinc5_comp.sv
// @brief   SINC5 后接显式三抽头 FIR 与二倍抽取。
// @details 三个系数均为缩放 2^15 的 17 bit 有符号整数，启用时必须明确提供。
//          缺少系数绑定时拒绝处理，不用单位系数代替补偿滤波器。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.64–70/94 的外部模式与抽取率；三抽头系数/顺序由调用方绑定。
// 时钟域: clk；valid、配置与 filter_sync 均同步。
// 复位: rst_n 低有效、异步置位；filter_sync 取消 CIC/FIR 在途结果。
// 延迟: CIC 流水加两个 FIR 历史样本与寄存归一化。
// 吞吐: FIR 历史填充后，每 2N 个输入最多输出一次。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: sinc5_decim。
// 边界: 加半步舍入后饱和到 20 bit 补码；频响符合性需要独立验证的系数组。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc5_comp #(
    parameter int IN_W = 20,  // IN_W: 仅支持 20 bit 补码输入/输出。
    parameter bit P_COMP_COEFF_VALID = 1'b0,  // P_COMP_COEFF_VALID: 三个系数已明确绑定。
    parameter int C0 = 32'h40000000,  // C0: 当前样本系数，缩放 2^15；绑定有效时须在 -65536..65535。
    parameter int C1 = 32'h40000000,  // C1: 前一样本系数，缩放 2^15；绑定有效时须在 -65536..65535。
    parameter int C2 = 32'h40000000  // C2: 前两样本系数，缩放 2^15；绑定有效时须在 -65536..65535。
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out,
    output logic                 processing_reject
);

    localparam bit COEFF_BINDING_VALID=P_COMP_COEFF_VALID &&
        C0>=-65536 && C0<=65535 && C1>=-65536 && C1<=65535 && C2>=-65536 && C2<=65535;

    initial begin
        if (IN_W != 20) $fatal(1, "sinc5_comp: only signed20 is supported");
        if (P_COMP_COEFF_VALID &&
            (C0 < -65536 || C0 > 65535 || C1 < -65536 || C1 > 65535 ||
             C2 < -65536 || C2 > 65535))
            $fatal(1, "sinc5_comp: all three explicit signed17 Q15 coefficients are required");
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) processing_reject <= 1'b0;
        else if (filter_sync) processing_reject <= 1'b0;
        else processing_reject <= valid_in && !COEFF_BINDING_VALID;
    end

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

    sinc5_decim #(.IN_W(IN_W)) u_sinc5 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(valid_in && COEFF_BINDING_VALID), .data_in(data_in), .dec_sel(dec_sel),
        .data_out(s5_data), .valid_out(s5_valid)
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
                    // 当前 SINC5 样本为 x[2m]；历史填充完整后才允许输出。
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

endmodule

`default_nettype wire
