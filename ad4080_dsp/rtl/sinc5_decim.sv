//=============================================================================
// @file    sinc5_decim.sv
// @brief   五级流水积分/梳状 CIC 抽取器。
// @details D=2..256，超范围编码夹为 256；积分器按 valid 逐级寄存，抽取事件随样本
//          延迟。梳状器按抽取事件逐级寄存，再经加半码、移位和饱和流水，按 D^5 归一化。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；积分与抽取相位均按 valid 事件推进。
// 复位: rst_n 异步低有效；filter_sync 优先清积分、梳状、相位和输出流水。
// 延迟: 含五级输入样本流水与五级抽取事件流水，不能简化为 D 个输入加 2 clk。
// 吞吐: 每个 clk 最多一个输入，稳态每 D 个有效输入输出一个结果。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: IN_W=20 时积分/梳状各 61 bit，归一化/饱和使用 62 bit。
//     D 在抽取事件锁存；修改 D 须配合 filter_sync，避免旧流水与新尺度混合。
//     settled 采用输出计数门限，不是模拟物理建立证据。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sinc5_decim #(
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
    output logic                 settled       // 建立完成；按输出计数门限。
);

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
        // 归一化使用已锁存的 l_cur，使 5*log2(D) 与累加的 d_cur 属于同一块；
        // 若用 l_map，块中途改码会采用错误增益。
        shift_amt = {2'b0, l_cur, 2'b00} + {4'b0, l_cur};   // 5*l
    end

    logic signed [ACC_W-1:0] integ [0:4];
    logic [4:0]              dec_pipe;   // 积分器 5 级打拍后的抽取对齐（按 valid 推进）
    logic signed [ACC_W-1:0] comb_d [0:4];
    logic [10:0]             cnt;
    logic signed [ACC_W-1:0] data_ext;

    assign data_ext = $signed({{(ACC_W-IN_W){data_in[IN_W-1]}}, data_in});

    // 每级积分器包含一个寄存器，第 D 个样本在四个后续有效输入沿后到达
    // integ[4]；抽取事件须在有效样本域对齐，空闲 clk 不计入推进。
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
            // 每级流水只完成一次加法，避免同一级串联五个 61 bit 加法器。
            integ[0] <= integ[0] + data_ext;
            integ[1] <= integ[1] + integ[0];
            integ[2] <= integ[2] + integ[1];
            integ[3] <= integ[3] + integ[2];
            integ[4] <= integ[4] + integ[3];
        end
    end

    // 五级梳状器采用收缩式流水，每个 dec_evt 同时更新各级。第 i 级取上一
    // 抽取事件的第 i-1 级结果完成 ACC_W 位减法，避免串联五级减法器。
    logic signed [ACC_W-1:0]  comb_s [0:4];
    logic signed [ACC_WX-1:0] out_r;
    logic [7:0]               shift_r;
    logic                     out_v, norm_v, sat_v;
    logic signed [ACC_WX-1:0] norm_r;
    logic signed [ACC_WX-1:0] rnd_w;
    logic signed [ACC_WX-1:0] norm_w;
    logic signed [ACC_WX-1:0] shifted;

    // dec_pipe[4] 在有效样本速率下保持一个 valid 周期，与 valid_in 相与产生单拍
    // 事件。第 D 个样本占用五个有效输入沿传播到 integ[4]，每级一拍。
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

    // 建立门限以手册 p.69 的 2+5D+4 个 CNV 数值为起点。输出整体后移，
    // 因此本实现按 ceil(settling/D) 个已输出结果计数，避免仅按输入样本过早置 settled。
    // 该数字门限不证明模拟建立过程。
    localparam int SET_W = 16;
    logic [SET_W-1:0] out_cnt;
    logic [SET_W-1:0] n_out_settle;
    wire  [SET_W-1:0] settle_cnv = (SET_W'(d_cur) << 2) + SET_W'(d_cur) + 16'd6;
    wire  [SET_W-1:0] settle_out = (settle_cnv + SET_W'(d_cur) - 16'd1) >> l_cur;

    // 收缩式梳状器后移四个抽取点，另有两拍归一化；输出计数门限再加 5，
    // 用于本工程的流水建立判定。
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
