//=============================================================================
// @file    sinc1_decim.sv
// @brief   有符号块平均抽取滤波器。
// @details N=2..1024；按 Table 62，超出有效码范围的抽取码选择最大 N。
//          先将末样本计入块和，再加半个量化步长并算术右移归一化。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.64–69/94 的模式与抽取率；舍入和流水为工程约定。
// 时钟域: clk；valid、配置与 filter_sync 均同步。
// 复位: rst_n 低有效、异步置位；filter_sync 清相位并取消待输出结果。
// 延迟: 接受 N 个样本后，从末样本沿再经过一个完整 clk 间隔输出。
// 吞吐: 每个 clk 可输入一个样本；每 N 个被接受样本输出一次。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 只支持 20 bit 补码；修改配置须使用 filter_sync，不生成基于 CNV 的稳定输出指示。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc1_decim #(
    parameter int IN_W = 20  // IN_W: 仅支持 20 bit 补码输入/输出。
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out     // 数值输出有效，不隐藏启动瞬态。
);

    initial begin
        if (IN_W != 20) $fatal(1, "numerical SINC: only the signed20 contract is supported");
    end

    localparam int ACC_W  = IN_W + 11;
    localparam int ACC_WX = ACC_W + 1;
    localparam int ACC_WXX = ACC_WX + 1;

    // 抽取率与移位量分开返回，以兼容不支持 function 输出参数的 Icarus 用法。
    function automatic [10:0] dec_d(input [3:0] s);
        begin
            if (s > 4'd9) dec_d = 11'd1024;
            else          dec_d = 11'd1 << (s + 4'd1);
        end
    endfunction

    function automatic [4:0] dec_l(input [3:0] s);
        begin
            if (s > 4'd9) dec_l = 5'd10;
            else          dec_l = {1'b0, s} + 5'd1;
        end
    endfunction

    logic [10:0] d_map, d_cur;
    logic [4:0]  l_map, l_cur;

    always_comb begin
        d_map = dec_d(dec_sel);
        l_map = dec_l(dec_sel);
    end

    logic signed [ACC_WX-1:0] acc;
    logic [10:0]              cnt;
    logic signed [ACC_WX-1:0] sum;
    logic signed [ACC_WX-1:0] data_ext;
    logic signed [ACC_WXX-1:0] sum_r;      // 寄存块和，分开加法与桶形移位路径。
    logic signed [IN_W-1:0] avg_pipe;   // 第二拍使用的移位结果。
    logic [4:0]               l_sum;       // 与 sum_r 属于同一块的 log2(D)。
    logic                     avg_v;

    // 显式符号扩展到累加位宽：避免工具对混合位宽的有符号加法做不同解释。
    assign data_ext = $signed({{(ACC_WX-IN_W){data_in[IN_W-1]}}, data_in});
    assign sum = acc + data_ext + $signed({{(ACC_WX-11){1'b0}}, (d_cur >> 1)});
    assign avg_pipe = IN_W'(sum_r >>> l_sum);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc       <= '0;
            cnt       <= '0;
            d_cur     <= 11'd2;
            l_cur     <= 5'd1;
            data_out  <= '0;
            valid_out <= 1'b0;
            sum_r     <= '0;
            l_sum     <= 5'd1;
            avg_v     <= 1'b0;
        end else if (filter_sync) begin
            acc       <= '0;
            cnt       <= '0;
            d_cur     <= d_map;
            l_cur     <= l_map;
            valid_out <= 1'b0;
            sum_r     <= '0;
            l_sum     <= 5'd1;
            avg_v     <= 1'b0;
        end else begin
            valid_out <= 1'b0;
            avg_v     <= 1'b0;
            if (valid_in) begin
                if (cnt == d_cur - 11'd1) begin
                    // 第一拍保存包含末样本及半步舍入项的块和。
                    sum_r     <= $signed({{(ACC_WXX-ACC_WX){sum[ACC_WX-1]}}, sum});
                    l_sum     <= l_cur;
                    avg_v     <= 1'b1;
                    acc       <= '0;
                    cnt       <= '0;
                    d_cur     <= d_map;
                    l_cur     <= l_map;
                end else begin
                    acc <= acc + data_ext;
                    cnt <= cnt + 11'd1;
                end
            end
            // 第二拍对前一拍的块和移位，输出与 valid 同拍。
            if (avg_v) begin
                data_out  <= avg_pipe[IN_W-1:0];
                valid_out <= 1'b1;
            end
        end
    end

endmodule

`default_nettype wire
