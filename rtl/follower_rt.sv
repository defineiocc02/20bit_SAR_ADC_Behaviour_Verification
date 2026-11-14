// D2 Follower（实时版，事件协议 v2）
//
// 协议：
//   - start_i 拉高一拍：清空前缀、冻结 upper、ev_idx 归零
//   - 每个 bit_valid_i 拍接收一位已决码，按其在原码中的位置累加
//   - ev_valid_o / ev_idx_o / k_ev_o **同一时钟沿寄存输出**，三者同拍一致
//   - 9 个事件后 done_o 置位，之后的 bit_valid_i 被忽略（不再产生事件）
//
// 码修正：k = prefix - upper。凡 prefix < upper 时 9 位无符号减法会回绕，
// 这里用 10 位带符号判定并置 err_o，k 钳到 0，不回绕。
module follower_rt (
    input  logic        clk,
    input  logic        reset_n,
    input  logic        start_i,
    input  logic        bit_valid_i,
    input  logic        bit_i,
    input  logic [2:0]  upper_i,      // 仅 start_i 那一拍采样
    output logic        ev_valid_o,
    output logic [3:0]  ev_idx_o,     // 0..8
    output logic [8:0]  k_ev_o,
    output logic        done_o,
    output logic        err_o         // prefix < upper 时置位
);

    logic [8:0]  prefix_q;
    logic [3:0]  idx_q;       // 已收事件数（定位下一位）
    logic [3:0]  ev_idx_q;    // 本事件序号
    logic        done_q, ev_valid_q, err_q;
    logic [8:0]  k_q;
    logic [2:0]  upper_q;

    // 按原位置累加：第 idx 个事件接的是原码第 (8-idx) 位
    wire [3:0]  pos    = 4'd8 - idx_q;
    wire [8:0]  bitpos = 9'd1 << pos;
    wire [8:0]  acc    = prefix_q | (bit_i ? bitpos : 9'd0);

    // 带符号判定，避免 9 位无符号回绕
    wire signed [10:0] acc_s   = {2'b00, acc};
    wire signed [10:0] up_s    = {8'b0, upper_q};
    wire signed [10:0] diff_s  = acc_s - up_s;
    wire               neg     = diff_s[10];

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            prefix_q  <= 9'd0;
            idx_q     <= 4'd0;
            done_q    <= 1'b0;
            ev_valid_q<= 1'b0;
            err_q     <= 1'b0;
            k_q       <= 9'd0;
            ev_idx_q  <= 4'd0;
            upper_q   <= 3'd0;
        end else if (start_i) begin
            prefix_q  <= 9'd0;
            idx_q     <= 4'd0;
            done_q    <= 1'b0;
            ev_valid_q<= 1'b0;
            err_q     <= 1'b0;
            k_q       <= 9'd0;
            ev_idx_q  <= 4'd0;
            upper_q   <= upper_i;          // 冻结：事件期间不再读 upper_i
        end else if (bit_valid_i && !done_q) begin
            prefix_q  <= acc;
            idx_q     <= idx_q + 4'd1;
            ev_valid_q<= 1'b1;
            k_q       <= neg ? 9'd0 : diff_s[8:0];
            ev_idx_q  <= idx_q;
            err_q     <= neg;
            done_q    <= (idx_q == 4'd8);
        end else begin
            ev_valid_q<= 1'b0;             // 无事件周期：不产生事件
        end
    end

    assign ev_valid_o = ev_valid_q;
    assign ev_idx_o   = ev_idx_q;
    assign k_ev_o     = k_q;
    assign done_o     = done_q;
    assign err_o      = err_q;

endmodule
