`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : sinc5_comp.sv
// 模块名     : sinc5_comp
// 版本       : v1.0
// 功能       : 一份原速率 SINC5 和按公开响应设计的对称 FIR，末级抽取二倍。
// 设计说明   :
//   * 选中 SINC5 时直接取共用 CIC 的数值。选中 PF1 才更新 FIR 的历史及乘积。
//   * 系数来自本仓可复现公共规格拟合，以基率 N 选择。每对对称输入先扩大到
//     21 位再相加，一对仅一次乘法。取样间隔由有效字控制，不由空拍推进。
//   * FILTER_SYNC 或复位优先，清历史、偶相及在途有效。第一帧零填启动历史。
//     偶数个 CIC 数值各产生一个真实 FIR 字，器件 CNV 公布在外围择完整历史。
//   * 舍入以有符号全精度和加正半步后算术右移实现，恰半值向正无穷。
// -----------------------------------------------------------------------------
// 变更记录   : 初次加入固定公开响应系数表和共用 CIC，移去旧手填三抽头入口。
// =============================================================================
module sinc5_comp #(
    parameter int IN_W = 20  // 固定 AD4080 码宽，所有历史及系数点积按二补码处理。
) (
    // --- 输入侧，共用 CIC ---
    input  logic                 clk,              // 与原数值链同步，停样时仍可排空已有结果。
    input  logic                 rst_n,            // 异步置位，释放由所属域负责。
    input  logic                 filter_sync,      // 与档位同时同步采纳，取消旧批次。
    input  logic                 valid_in,         // 共用 CIC 的原始采样有效。
    input  logic [IN_W-1:0]      data_in,          // 原采样二补码。
    input  logic [3:0]           dec_sel,           // 表 62 基础 N，超范围夹到 N=256。
    input  logic                 pf1_enable_i,      // 由同一配置和 FILTER_SYNC 建立，非逐字复位。

    // --- 后端选择与数值状态 ---
    output logic [IN_W-1:0]      sinc5_data_o,     // 共用 CIC 的原速率真值。
    output logic                 sinc5_valid_o,    // 每 N 个输入一个结果。
    output logic [IN_W-1:0]      data_out,         // 舍入并限幅后的二补码。
    output logic                 valid_out,        // 每 2N 个输入一个数值结果。
    output logic                 processing_reject // 旧诊断接口，所有档位都有系数故固定为零。
);
    `include "sinc5_comp_coeff_lut.svh"

    localparam int PAIRS = (PMODE_COMP_TAPS - 1) / 2;
    localparam int TERMS = PAIRS + 1;                // 含不成对的中心项。
    localparam int PAIR_W = IN_W + 1;
    localparam int PRODUCT_W = PAIR_W + PMODE_COMP_COEF_W;
    localparam int LEVELS = $clog2(TERMS);
    localparam int LEAVES = 1 << LEVELS;             // 补零的平衡加法树，深度受抽头数控制。
    localparam int ACC_W = PRODUCT_W + LEVELS + 1;  // 最后再为舍入半步保留一位。
    localparam logic signed [ACC_W-1:0] ROUND_HALF = ACC_W'(1) <<< (PMODE_COMP_COEF_FRAC - 1);
    localparam logic signed [ACC_W-1:0] MAX_CODE = (ACC_W'(1) <<< (IN_W - 1)) - ACC_W'(1);
    localparam logic signed [ACC_W-1:0] MIN_CODE = -(ACC_W'(1) <<< (IN_W - 1));

    initial begin
        if (IN_W != 20 || PMODE_COMP_TAPS < 3 || PMODE_COMP_TAPS > 65 || PMODE_COMP_TAPS % 2 != 1)
            $fatal(1,"sinc5_comp: expected signed20 and 3..65 odd taps");
    end

    // =========================================================================
    // 1. 共用 SINC5 和按有效推进的对称窗口
    // =========================================================================
    sinc5_decim #(.IN_W(IN_W)) u_sinc5 (
        .clk,.rst_n,.filter_sync,.valid_in,.data_in,.dec_sel,
        .data_out(sinc5_data_o),.valid_out(sinc5_valid_o)
    );

    logic [2:0] rate_q;                              // SYNC 时一次冻结整组系数选择。
    logic phase_q;                                  // 复位为零，第二次 CIC 有效时乘积进入流水。
    logic product_valid_q;
    logic signed [IN_W-1:0] history_q [0:PMODE_COMP_TAPS-2];
    wire signed [IN_W-1:0] window [0:PMODE_COMP_TAPS-1];
    logic signed [PRODUCT_W-1:0] products_q [0:TERMS-1];
    wire signed [PAIR_W-1:0] pairs [0:TERMS-1];
    wire do_product = pf1_enable_i && sinc5_valid_o && phase_q;
    // 直接叶级绑定对非法 PF1 档位报配置诊断。CIC 与补偿表都在同一
    // SYNC 沿钳到档 7，保留表62 要求的最大抽取数值行为。
    assign processing_reject = pf1_enable_i && filter_sync && dec_sel > 4'd7;
    assign window[0] = $signed(sinc5_data_o);
    for (genvar sample_index=1; sample_index<PMODE_COMP_TAPS; sample_index++) begin : g_input
        assign window[sample_index] = history_q[sample_index-1];
    end

    for (genvar tap=0; tap<PAIRS; tap++) begin : g_pairs
        // 保留加法的额外一位。尤其两个最小补码相加不能回绕成零。
        assign pairs[tap] = PAIR_W'(window[tap]) + PAIR_W'(window[PMODE_COMP_TAPS-1-tap]);
    end
    assign pairs[PAIRS] = PAIR_W'(window[PAIRS]);

    // -------------------------------------------------------------------------
    // 系数和输入窗口都属于当前偶相。乘积只在采纳该字时登记，后一核心沿
    // 退役点积结果。SYNC 可越过一个待退役的乘积，旧数值不会串入新档。
    // -------------------------------------------------------------------------
    for (genvar tap=0; tap<TERMS; tap++) begin : g_product
        wire signed [PMODE_COMP_COEF_W-1:0] coefficient = sinc5_comp_coeff(rate_q,tap);
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) products_q[tap] <= '0;
            else if (!filter_sync && do_product) products_q[tap] <= pairs[tap] * coefficient;
        end
    end

    // =========================================================================
    // 2. 平衡归约、舍入和唯一结果
    // =========================================================================
    // 单向逐级相加，每一层只增加求和必需的一位，不把整个数组误作组合反馈。
    for (genvar level=0; level<=LEVELS; level++) begin : g_sum
        localparam int WIDTH = PRODUCT_W + level;
        localparam int NODES = LEAVES >> level;
        wire signed [WIDTH-1:0] value [0:NODES-1];
        for (genvar term=0; term<NODES; term++) begin : g_term
            if (level == 0) begin : g_leaf
                if (term < TERMS) begin : g_nonzero
                    assign value[term] = products_q[term];
                end else begin : g_zero
                    assign value[term] = '0;
                end
            end else begin : g_add
                assign value[term] = WIDTH'(g_sum[level-1].value[2*term]) +
                                     WIDTH'(g_sum[level-1].value[2*term+1]);
            end
        end
    end
    wire signed [ACC_W-1:0] normalized =
        (ACC_W'(g_sum[LEVELS].value[0]) + ROUND_HALF) >>> PMODE_COMP_COEF_FRAC;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rate_q <= '0;
            phase_q <= 1'b0;
            product_valid_q <= 1'b0;
            valid_out <= 1'b0;
            data_out <= '0;
            for (int sample_index=0; sample_index<PMODE_COMP_TAPS-1; sample_index++) history_q[sample_index] <= '0;
        end else if (filter_sync) begin
            rate_q <= dec_sel > 4'd7 ? 3'd7 : dec_sel[2:0];
            phase_q <= 1'b0;
            product_valid_q <= 1'b0;
            valid_out <= 1'b0;
            data_out <= '0;
            for (int sample_index=0; sample_index<PMODE_COMP_TAPS-1; sample_index++) history_q[sample_index] <= '0;
        end else begin
            product_valid_q <= do_product;
            valid_out <= product_valid_q;
            if (pf1_enable_i && sinc5_valid_o) begin
                phase_q <= ~phase_q;
                history_q[0] <= $signed(sinc5_data_o);
                for (int sample_index=1; sample_index<PMODE_COMP_TAPS-1; sample_index++)
                    history_q[sample_index] <= history_q[sample_index-1];
            end
            if (product_valid_q) begin
                if (normalized > MAX_CODE) data_out <= MAX_CODE[IN_W-1:0];
                else if (normalized < MIN_CODE) data_out <= MIN_CODE[IN_W-1:0];
                else data_out <= normalized[IN_W-1:0];
            end
        end
    end
endmodule
`default_nettype wire
