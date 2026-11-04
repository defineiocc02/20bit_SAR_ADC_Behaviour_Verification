//=============================================================================
// @file    cal_two_point.sv
// @brief   固定横坐标的两点仿射拟合工具
// @details 由固定X1/X2与输入y1/y2拟合y=m*x+c，再编码gain/offset并钳位。
//          若X是已知输入而y是测得原始码，结果为正向传递系数；本模块不自动求逆校正。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : clk；start、y1、y2必须同域且在接受沿稳定
// 复位     : rst_n异步低有效；每级valid独立传播
// 延迟     : 接受start沿到done为2个完整clk间隔（含接受沿共3个寄存沿）
// 吞吐     : 可连续每clk接受一次start，无busy/反压
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : 无；未连接到顶层自动学习控制器
// 边界     : 默认IN_W=20补码、固定测试点及预计算倒数必须相互一致；非默认组合需单独验证。
//          输出gain为0..1023，offset为-2048..2047；舍入为加半码后算术右移。
//          error当前恒0，不表示运行时已检查单调性、分母或参数一致性。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module cal_two_point #(
    parameter int IN_W      = 20,
    parameter int X1_CODE   = -419430,
    parameter int X2_CODE   = 419430,
    parameter int INV_DX_K  = 40961,
    parameter int SHIFT_K   = 20,
    parameter int INV_DX    = 1280,
    parameter int SHIFT_O   = 30
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

    // 显式定宽常数：避免 int/无符号把乘法结果截到 32 位
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

    // Stage 1: dy = y2 - y1; num_g = dy - DX
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

    // Stage 2: constant-multiply the two-point formulas
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

    // Stage 3: clamp and output; one new transaction may arrive every clk.
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
