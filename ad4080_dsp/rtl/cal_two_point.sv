//=============================================================================
// @file    cal_two_point.sv
// @brief   固定横坐标的两点仿射拟合工具。
// @details 由固定 X1/X2 与输入 y1/y2 拟合 y=m*x+c，再编码并钳位 gain/offset。
//          X 为已知输入、y 为测得原始码时，输出是正向传递系数，不自动求逆校正。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；start、y1、y2 须同域，并在接受沿稳定。
// 复位: rst_n 异步低有效；各级 valid 独立传播。
// 延迟: 接受 start 沿到 done 为两个完整 clk 间隔，含接受沿共三个寄存沿。
// 吞吐: 每个 clk 可接受一次 start，无 busy 或反压。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块，未连接顶层自动学习控制器。
// 边界: 默认 IN_W=20 补码；固定测试点须与预计算倒数一致，非默认组合需另验。
//     gain 范围 0..1023，offset 范围 -2048..2047；加半码后算术右移舍入。
//     error 当前恒为 0，不表示已检查运行时单调性、分母或参数一致性。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module cal_two_point #(
    parameter int IN_W      = 20,  // IN_W: 补码端点响应位宽，默认 20 bit。
    parameter int X1_CODE   = -419430,  // X1_CODE: 固定第一横坐标，单位为 ADC 码。
    parameter int X2_CODE   = 419430,  // X2_CODE: 固定第二横坐标，单位为 ADC 码。
    parameter int INV_DX_K  = 40961,  // INV_DX_K: 增益拟合的预计算倒数分子。
    parameter int SHIFT_K   = 20,  // SHIFT_K: 增益拟合常数乘积的右移位数。
    parameter int INV_DX    = 1280,  // INV_DX: 偏移拟合使用的预计算倒数分子。
    parameter int SHIFT_O   = 30  // SHIFT_O: 偏移拟合常数乘积的右移位数。
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     start,
    input  logic [IN_W-1:0]          y1,
    input  logic [IN_W-1:0]          y2,
    output logic [9:0]               gain_code,
    output logic signed [11:0]       offset_code,
    output logic                     done,
    output logic                     error                // 保留输出，恒0；当前不检查运行时或参数合法性
);

    localparam int DX = X2_CODE - X1_CODE;

    // 显式定义常数位宽与符号，避免 int/无符号混算将乘法结果截到 32 bit。
    localparam logic signed [IN_W:0]       DX_S        = (IN_W+1)'(DX);
    localparam logic signed [IN_W:0]       X1_S        = (IN_W+1)'(X1_CODE);
    localparam logic signed [17:0]         INV_DX_K_S  = 18'(INV_DX_K);
    localparam logic signed [11:0]         INV_DX_S    = 12'(INV_DX);

    localparam int PROD_G_W  = (IN_W + 2) + 18;   // 22 + 18 = 40
    localparam int OFF_NUM_W = (IN_W + 2) + (IN_W + 1); // 42
    localparam int OFF_PROD_W= OFF_NUM_W + 12;    // 54

    logic signed [IN_W:0]         dy_s1;
    logic signed [IN_W+1:0]       num_g_s1;
    logic [IN_W-1:0]              y1_s1;
    logic                         v_s1;

    logic signed [PROD_G_W-1:0]   gprod_s2;
    logic signed [OFF_NUM_W-1:0]  offnum_s2;
    logic signed [OFF_PROD_W-1:0] offprod_s2;
    logic                         v_s2;

    wire signed [PROD_G_W-1:0]    gd_s2  = (gprod_s2 + (1 << (SHIFT_K-1))) >>> SHIFT_K;
    wire signed [OFF_PROD_W-1:0]  oc_s2  = (offprod_s2 + (1 << (SHIFT_O-1))) >>> SHIFT_O;
    wire signed [PROD_G_W-1:0]    gain_calc = PROD_G_W'(512) + gd_s2;
    wire signed [IN_W+1:0] y1_gain_ext = $signed({{2{y1[IN_W-1]}}, y1});
    wire signed [IN_W+1:0] y2_gain_ext = $signed({{2{y2[IN_W-1]}}, y2});
    wire signed [IN_W+1:0] dx_gain_ext = $signed({DX_S[IN_W], DX_S});

    logic [9:0]                   gain_s3;
    logic signed [11:0]           off_s3;
    logic                         v_s3;

    // 第一级计算 dy=y2-y1、num_g=dy-DX，并与接受的 y1 一起保存。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dy_s1    <= '0;
            num_g_s1 <= '0;
            y1_s1    <= '0;
            v_s1     <= 1'b0;
        end else if (start) begin
            dy_s1    <= $signed(y2) - $signed(y1);
            num_g_s1 <= (y2_gain_ext - y1_gain_ext) - dx_gain_ext;
            y1_s1    <= y1;
            v_s1     <= 1'b1;
        end else begin
            v_s1 <= 1'b0;
        end
    end

    // 第二级以预计算倒数的常数乘法实现两点拟合公式，不包含运行时除法。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gprod_s2  <= '0;
            offnum_s2 <= '0;
            offprod_s2<= '0;
            v_s2      <= 1'b0;
        end else begin
            v_s2 <= v_s1;
            if (v_s1) begin
                gprod_s2  <= num_g_s1 * INV_DX_K_S;
                offnum_s2 <= $signed(y1_s1) * DX_S
                             - dy_s1 * X1_S;
                offprod_s2<= ($signed(y1_s1) * DX_S
                              - dy_s1 * X1_S) * INV_DX_S;
            end
        end
    end

    // 第三级钳位系数并输出；流水可每个 clk 接受一个新事务。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gain_s3  <= 10'd512;
            off_s3   <= 12'sd0;
            v_s3     <= 1'b0;
            error    <= 1'b0;
        end else begin
            v_s3 <= v_s2;
            if (v_s2) begin
                if (gain_calc < PROD_G_W'(0))         gain_s3 <= 10'd0;
                else if (gain_calc > PROD_G_W'(1023)) gain_s3 <= 10'd1023;
                else                              gain_s3 <= gain_calc[9:0];

                if (oc_s2 < -2048)      off_s3 <= -12'sd2048;
                else if (oc_s2 > 2047)  off_s3 <= 12'sd2047;
                else                    off_s3 <= $signed(oc_s2[11:0]);
                error <= 1'b0;
            end
        end
    end

    assign gain_code   = gain_s3;
    assign offset_code = off_s3;
    assign done        = v_s3;

endmodule

`default_nettype wire
