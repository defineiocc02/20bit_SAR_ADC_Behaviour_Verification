`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : sinc5_decim.sv
// 模块名     : sinc5_decim
// 版本       : v1.0
// 功能       : 五阶 CIC 抽取与按 D^5 归一化，支持 20 位补码和 D=2…256。
//
// 设计说明   :
//   * 每个有效输入向积分链发出令牌。四个后续核心时钟使对应的抽取点
//     到达最终级，暂停输入时积分链按令牌排空。
//   * 抽取后的五阶梳状差分写成转置 FIR，阵列存各阶历史贡献。每次
//     抽取直接完成当前数值，避免按五组抽取事件串行转发。
//   * FILTER_SYNC 同沿优先，清积分、梳状及在途输出并锁存抽取率。
//     valid_out 包含零填充启动结果，器件 CNV 就绪由外层另行判定。
//
// 变更记录   : 2026-11-20 将积分和抽取对齐迁至连续核心时钟，去除跨组延迟。
// @file    sinc5_decim.sv
// @brief   五级积分、五阶差分的 CIC 数值通路。
// @details 同组梳状输出，抽取样本到有效数值相隔四个核心时钟间隔。
// @author  Project maintainers
// @date    2026-11-20
// @version v1.0
// 手册依据: AD4080 Rev. B p.64–69、94。核心时钟延迟与舍入为工程合同。
// 时钟域: clk。复位: rst_n 低有效异步，filter_sync 为同域同步清空。
// 可综合性: 定宽整数结构，目标映射与 STA 独立验收。
// =============================================================================
module sinc5_decim #(
    parameter int IN_W = 20
) (
    // --- 样本与配置 ---
    input  logic            clk,
    input  logic            rst_n,       // 清除在途令牌和历史
    input  logic            filter_sync, // 同沿丢弃输入，锁存新抽取率
    input  logic            valid_in,    // 未停止的核心时钟上接受新样本
    input  logic [IN_W-1:0] data_in,     // 20 位补码
    input  logic [3:0]      dec_sel,     // 0…7, 对应抽取 2…256；其余沿用 256
    // --- 滤波数值 ---
    output logic [IN_W-1:0] data_out,    // 20 位补码，未建立时为部分窗口结果
    output logic            valid_out    // 工程数值有效，依抽取令牌提交
);
    // =========================================================================
    // 1. 抽取配置和定宽数值
    // =========================================================================
    initial begin
        if (IN_W != 20) $fatal(1, "numerical SINC: only the signed20 contract is supported");
    end

    localparam int MAX_LOG2D = 8;
    localparam int ACC_W = IN_W + 5*MAX_LOG2D + 1;
    localparam logic signed [IN_W-1:0] MAX_CODE = (IN_W'(1) <<< (IN_W-1)) - IN_W'(1);
    localparam logic signed [IN_W-1:0] MIN_CODE = -(IN_W'(1) <<< (IN_W-1));
    localparam logic signed [ACC_W-1:0] RESET_HALF = ACC_W'(16); // D=2，D^5/2=16

    // 抽取率及归一化移位一起锁存，禁止同一窗口同时使用两个倍率。
    wire [3:0] log2_map = dec_sel > 4'd7 ? 4'd8 : dec_sel + 4'd1;
    wire [10:0] d_map = 11'd1 << log2_map;
    wire [7:0] shift_map = {2'b0, log2_map, 2'b00} + {4'b0, log2_map};
    wire signed [ACC_W-1:0] half_map = ACC_W'(1) <<< (shift_map - 8'd1);
    logic [10:0] d_cur, count_q;
    logic [7:0] shift_cur;
    logic signed [ACC_W-1:0] half_cur;

    // =========================================================================
    // 2. 积分与抽取令牌
    // =========================================================================
    logic signed [ACC_W-1:0] integ [0:4];
    logic [3:0] valid_pipe_q, dec_pipe_q;
    wire dec_now = valid_in && count_q == d_cur - 11'd1;
    wire dec_evt = dec_pipe_q[3];
    wire signed [ACC_W-1:0] data_ext = ACC_W'($signed(data_in));
    wire signed [ACC_W-1:0] integral5_next = integ[4] + integ[3];

    // 每级只在属于该级的令牌到达时更新，同一份样本经历五阶积分。
    // 令牌和抽取标志随核心拍移动，输入的空白间隔无需由外部补虚拟样本。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count_q <= '0;
            valid_pipe_q <= '0;
            dec_pipe_q <= '0;
            for (int stage = 0; stage < 5; stage++) integ[stage] <= '0;
        end else if (filter_sync) begin
            count_q <= '0;
            valid_pipe_q <= '0;
            dec_pipe_q <= '0;
            for (int stage = 0; stage < 5; stage++) integ[stage] <= '0;
        end else begin
            valid_pipe_q <= {valid_pipe_q[2:0], valid_in};
            dec_pipe_q <= {dec_pipe_q[2:0], dec_now};
            if (valid_in) begin
                count_q <= dec_now ? 11'd0 : count_q + 11'd1;
                integ[0] <= integ[0] + data_ext;
            end
            for (int stage = 1; stage < 4; stage++) begin
                if (valid_pipe_q[stage-1]) integ[stage] <= integ[stage] + integ[stage-1];
            end
            if (valid_pipe_q[3]) integ[4] <= integral5_next;
        end
    end

    // =========================================================================
    // 3. 当前抽取点的梳状、舍入和输出
    // =========================================================================
    logic signed [ACC_W-1:0] comb_tail [0:4];
    wire signed [ACC_W-1:0] integral_times_five = integral5_next + (integral5_next <<< 2);
    wire signed [ACC_W-1:0] integral_times_ten = integral_times_five <<< 1;
    wire signed [ACC_W-1:0] rounded_comb = integral5_next + comb_tail[0];
    wire signed [ACC_W-1:0] normalized = rounded_comb >>> shift_cur;

    // (1-z^-1)^5 的抽取域系数为 [1,-5,10,-10,5,-1]。每槽预存后续
    // 抽取时的历史贡献，并携带一份半步。积分与历史均模 2^ACC_W；
    // 完整 CIC 和连同半步的绝对值低于 2^60，模运算后的有符号值可直接归一。
    // 当前抽取使用末级积分的同拍新值，避免额外等待一次输入或抽取。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            d_cur <= 11'd2;
            shift_cur <= 8'd5;
            half_cur <= RESET_HALF;
            for (int stage = 0; stage < 5; stage++) comb_tail[stage] <= RESET_HALF;
            data_out <= '0;
            valid_out <= 1'b0;
        end else if (filter_sync) begin
            d_cur <= d_map;
            shift_cur <= shift_map;
            half_cur <= half_map;
            for (int stage = 0; stage < 5; stage++) comb_tail[stage] <= half_map;
            data_out <= '0;
            valid_out <= 1'b0;
        end else begin
            valid_out <= dec_evt;
            if (dec_evt) begin
                comb_tail[0] <= comb_tail[1] - integral_times_five;
                comb_tail[1] <= comb_tail[2] + integral_times_ten;
                comb_tail[2] <= comb_tail[3] - integral_times_ten;
                comb_tail[3] <= comb_tail[4] + integral_times_five;
                comb_tail[4] <= half_cur - integral5_next;
                if (normalized > ACC_W'(MAX_CODE)) data_out <= MAX_CODE;
                else if (normalized < ACC_W'(MIN_CODE)) data_out <= MIN_CODE;
                else data_out <= normalized[IN_W-1:0];
            end
        end
    end
endmodule
`default_nettype wire
