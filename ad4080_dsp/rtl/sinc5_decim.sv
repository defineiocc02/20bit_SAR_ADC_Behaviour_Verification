//=============================================================================
// @file    sinc5_decim.sv
// @brief   五级流水积分/梳状CIC抽取器
// @details D=2..256，超范围编码夹到256；积分器按valid逐级寄存，抽取事件随样本延迟。
//          梳状器按抽取事件逐级寄存，再经加半码/移位/饱和流水；归一化为D^5。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : clk；所有积分与抽取相位按valid事件推进
// 复位     : rst_n异步低有效；filter_sync优先清积分/梳状/相位和输出流水
// 延迟     : 含5级输入样本流水及5级抽取事件流水；不承诺D输入+2clk
// 吞吐     : 最多1输入/clk，稳态每D个有效输入1输出
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : 无
// 边界     : IN_W=20时积分/comb各61位，归一化/饱和使用62位。
//          D在抽取事件锁存；修改D应配合filter_sync，避免旧流水和新尺度混合。
//          settled采用输出计数门限，不是物理模拟建立证据。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc5_decim #(
    parameter int IN_W = 20
) (
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic                 filter_sync,
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      data_in,      // 补码
    input  logic [3:0]           dec_sel,
    output logic [IN_W-1:0]      data_out,     // 补码
    output logic                 valid_out,    // 输出有效，单拍
    output logic                 settled       // 建立完成；按输出计数门限。
);

    localparam int MAX_LOG2D = 8;
    localparam int ACC_W     = IN_W + 5*MAX_LOG2D + 1;   // 61
    localparam int ACC_WX    = ACC_W + 1;

    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));

    // 两个纯函数（Icarus 不支持 function 的 output 参数）
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
        // 必须用**已锁存**的 l_cur：累加用的是 d_cur，归一化就必须用
        // 同一个块对应的 5*log2(D)。用 l_map 会在块中途改码时把增益算错。
        shift_amt = {2'b0, l_cur, 2'b00} + {4'b0, l_cur};   // 5*l
    end

    logic signed [ACC_W-1:0] integ [0:4];
    logic [4:0]              dec_pipe;   // 积分器 5 级打拍后的抽取对齐（按 valid 推进）
    logic signed [ACC_W-1:0] comb_d [0:4];
    logic [10:0]             cnt;
    logic signed [ACC_W-1:0] data_ext;

    assign data_ext = $signed({{(ACC_W-IN_W){data_in[IN_W-1]}}, data_in});

    // 积分器每级一个寄存器，第D个样本在4个后续有效输入沿后到达integ[4]，
    // 因此抽取事件必须按有效样本域对齐，不能按空闲clk计数。
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
            // 标准流水 CIC：每级只做一次加法，链上不再串 5 个 61-bit 加法器
            integ[0] <= integ[0] + data_ext;
            integ[1] <= integ[1] + integ[0];
            integ[2] <= integ[2] + integ[1];
            integ[3] <= integ[3] + integ[2];
            integ[4] <= integ[4] + integ[3];
        end
    end

    // ---------------- 梳状器：收缩式（systolic）5 级 ----------------
    // 每个抽取事件（dec_evt）所有级同时更新一次：第 i 级用上一事件的
    // 第 i-1 级结果做一次 ACC_W 位减法。级间只差一个减法器，不再串 5 个。
    logic signed [ACC_W-1:0]  comb_s [0:4];
    logic signed [ACC_WX-1:0] out_r;
    logic [7:0]               shift_r;
    logic                     out_v, norm_v, sat_v;
    logic signed [ACC_WX-1:0] norm_r;
    logic signed [ACC_WX-1:0] rnd_w;
    logic signed [ACC_WX-1:0] norm_w;
    logic signed [ACC_WX-1:0] shifted;

    // dec_pipe[4] 在 valid 速率下保持一个 valid 周期；与 valid_in 相与得到单拍事件。
    // 第 D 个样本需要 5 个 valid 沿才传播到 integ[4]（每级 1 拍）。
    wire dec_evt = valid_in && dec_pipe[4];

    assign rnd_w  = $signed({{(ACC_WX-1){1'b0}}, 1'b1} << (shift_r - 8'd1));
    assign norm_w = norm_r >>> shift_r;
    // 移位结果先打拍，饱和/选择放下一拍（切断 62-bit 桶形移位 -> 比较器 的路径）

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

    // ---------------- 建立计数（按“输出个数”判定） ----------------
    // 手册 p.69：settling = 2 + 5D + 4 个 CNV。流水实现让输出整体后移，
    // 若仍按输入样本计数，settled 会提前。这里换算成“需要多少个已输出结果”：
    //   n_out = ceil(settling / D)
    localparam int SET_W = 16;
    logic [SET_W-1:0] out_cnt;
    logic [SET_W-1:0] n_out_settle;
    wire  [SET_W-1:0] settle_cnv = (SET_W'(d_cur) << 2) + SET_W'(d_cur) + 16'd6;
    wire  [SET_W-1:0] settle_out = (settle_cnv + SET_W'(d_cur) - 16'd1) >> l_cur;

    // 收缩式梳状器把输出整体后移 4 个抽取点（+ 2 拍归一化），
    // 因此“已输出个数”的门限还要加 5，才能覆盖流水延迟。
    assign n_out_settle = settle_out + 16'd5;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_cnt <= '0;
            settled <= 1'b0;
        end else if (filter_sync) begin
            out_cnt <= '0;
            settled <= 1'b0;
        end else if (valid_out && !settled) begin
            out_cnt <= out_cnt + 16'd1;
            if (out_cnt + 16'd1 >= n_out_settle) settled <= 1'b1;
        end
    end

endmodule

`default_nettype wire
