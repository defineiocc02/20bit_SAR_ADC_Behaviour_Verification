//=============================================================================
// @file    sinc1_decim.sv
// @brief   锁存抽取率的补码块平均。
// @details D=2^(code+1)，编码超过 9 时夹为 1024；在块边界更新 D，filter_sync
//          取消在途状态。末样本计入块和后加 D/2，再按锁存的 log2(D) 算术右移。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；输入、配置与 filter_sync 同步。
// 复位: rst_n 异步低有效；filter_sync 优先于 valid，清相位和流水。
// 延迟: 完成 D 个有效输入后再经过一个完整 clk 间隔输出；空闲 valid 不增加计数。
// 吞吐: 每个 clk 最多一个输入，平均每 D 个有效输入输出一个结果。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: 工程使用 IN_W=20，累加位宽为 IN_W+12，平均输出落在输入补码范围。
//     舍入半码向正无穷；settled 按有效输入计数，与 FIFO 输出标签无关。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc1_decim #(
    parameter int IN_W = 20  // IN_W: 补码输入/输出位宽，工程配置为 20 bit。
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
    logic signed [ACC_WXX-1:0] avg_pipe;   // 第二拍使用的移位结果。
    logic [4:0]               l_sum;       // 与 sum_r 属于同一块的 log2(D)。
    logic                     avg_v;

    // 显式符号扩展到累加位宽：避免工具对混合位宽的有符号加法做不同解释。
    assign data_ext = $signed({{(ACC_WX-IN_W){data_in[IN_W-1]}}, data_in});
    assign sum = acc + data_ext + $signed({{(ACC_WX-11){1'b0}}, (d_cur >> 1)});
    assign avg_pipe = sum_r >>> l_sum;

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
                    // 第一拍保存包含末样本与半步舍入项的块和。
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
            // 第二拍按同一块的移位量输出，valid 与结果同拍。
            if (avg_v) begin
                data_out  <= avg_pipe[IN_W-1:0];
                valid_out <= 1'b1;
            end
        end
    end

    // 建立计数采用手册 p.69 的 2+D+1 数值门限，但本实现按 valid_in 计数，
    // 不能将此门限当作已验证的物理 CNV 时序。
    localparam int SET_W = 16;
    logic [SET_W-1:0] settle_cnt;
    wire  [SET_W-1:0] settle_target = SET_W'(d_cur) + 16'd3;

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
