//=============================================================================
// @file    sinc5_decim.sv
// @brief   五级流水 CIC 抽取滤波器。
// @details N=2..256；五级积分和五级梳状差分后按 N^5 归一化。
//          抽取事件跟随已接受样本的流水，不按空闲时钟周期推进。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.64–69/94 的模式与抽取率；精确流水和舍入为工程约定。
// 时钟域: clk；积分与抽取按有效事件推进。
// 复位: rst_n 低有效、异步置位；filter_sync 清积分、梳状历史和输出流水。
// 延迟: 五级样本流水加梳状事件/归一化流水，不能简化为 N+2。
// 吞吐: 每个 clk 可输入一个样本；稳态每 N 个被接受样本输出一次。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 只支持 20 bit 补码；改变 N 须使用 filter_sync，不生成未绑定的物理稳定时序。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc5_decim #(
    parameter int IN_W = 20  // IN_W: 仅支持 20 bit 补码输入/输出。
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out     // 数值输出有效，不建模基于 CNV 的 ready。
);

    initial begin
        if (IN_W != 20) $fatal(1, "numerical SINC: only the signed20 contract is supported");
    end

    localparam int MAX_LOG2D = 8;
    localparam int ACC_W     = IN_W + 5*MAX_LOG2D + 1;   // 61
    localparam int ACC_WX    = ACC_W + 1;

    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));

    // 抽取率与移位量分开返回，以兼容不支持 function 输出参数的 Icarus 用法。
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
        // 归一化必须使用已锁存的 l_cur，使 5*log2(D) 与累加使用的 d_cur
        // 属于同一数据块；改用 l_map 会在块中途改码时使用错误增益。
        shift_amt = {2'b0, l_cur, 2'b00} + {4'b0, l_cur};   // 5*l
    end

    logic signed [ACC_W-1:0] integ [0:4];
    logic [4:0]              dec_pipe;   // 积分器 5 级打拍后的抽取对齐（按 valid 推进）
    logic signed [ACC_W-1:0] comb_d [0:4];
    logic [10:0]             cnt;
    logic signed [ACC_W-1:0] data_ext;

    assign data_ext = $signed({{(ACC_W-IN_W){data_in[IN_W-1]}}, data_in});

    // 每级积分器包含一个寄存器，第 D 个样本在四个后续有效输入沿后
    // 到达 integ[4]。抽取事件因此须在有效样本域对齐，空闲 clk 不计入推进。
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
            // 每级流水仅完成一次加法，避免在同一级串联五个 61 bit 加法器。
            integ[0] <= integ[0] + data_ext;
            integ[1] <= integ[1] + integ[0];
            integ[2] <= integ[2] + integ[1];
            integ[3] <= integ[3] + integ[2];
            integ[4] <= integ[4] + integ[3];
        end
    end

    // 五级梳状器采用收缩式流水，每个 dec_evt 同时更新各级。第 i 级
    // 使用上一抽取事件的第 i-1 级结果完成 ACC_W 位减法，避免串联五级减法器。
    logic signed [ACC_W-1:0]  comb_s [0:4];
    logic signed [ACC_WX-1:0] out_r;
    logic [7:0]               shift_r;
    logic                     out_v, norm_v, sat_v;
    logic signed [ACC_WX-1:0] norm_r;
    logic signed [ACC_WX-1:0] rnd_w;
    logic signed [ACC_WX-1:0] norm_w;
    logic signed [ACC_WX-1:0] shifted;

    // dec_pipe[4] 在有效样本速率下保持一个 valid 周期，与 valid_in 相与
    // 生成单拍事件。第 D 个样本占用五个有效输入沿传播到 integ[4]，每级一拍。
    wire dec_evt = valid_in && dec_pipe[4];

    assign rnd_w  = $signed({{(ACC_WX-1){1'b0}}, 1'b1} << (shift_r - 8'd1));
    assign norm_w = norm_r >>> shift_r;
    // 移位结果先寄存，下一拍再饱和和选择，以分开 62 bit 桶形移位与比较器路径。

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

endmodule

`default_nettype wire
