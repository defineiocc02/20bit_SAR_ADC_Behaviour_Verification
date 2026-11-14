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
    input  logic [3:0]  sar_index_i,  // 本次已决位在 9 位粗码中的位置 0..8
    input  logic        sar_qid_i,    // 量化器身份 0=A 1=B（每笔事务冻结）
    input  logic        flash_valid_i,// 3-bit Flash 结果有效（start 时采样）
    input  logic [2:0]  flash_code_i, // Flash 最高 3 位
    input  logic [2:0]  upper_i,      // 仅 start_i 那一拍采样
    output logic        ev_valid_o,
    output logic [3:0]  ev_idx_o,     // 0..8
    output logic [8:0]  k_ev_o,
    output logic        done_o,
    output logic        err_o,        // prefix < upper 时置位
    output logic [8:0]  prefix_o,     // 已累加的前缀（供顶层事件模式取用）
    output logic        last_ev_o,    // 本次事件是第 9 个（与 ev_valid_o 同沿）
    output logic        proto_err_o   // 协议违例：位序错/重复/缺位/qid 错/与 Flash 不符
);

    logic [8:0]  prefix_q;
    logic [2:0]  upper_q;             // frozen
    logic        qid_q;               // 本笔事务的量化器身份（冻结）
    logic        qid_seen;            // 是否已开始接收事件
    logic [3:0]  exp_idx;             // 期望的下一个位序（= 已收事件数）
    logic [3:0]  idx_q;       // 已收事件数（定位下一位）
    logic [3:0]  ev_idx_q;    // 本事件序号
    logic        done_q, ev_valid_q, err_q, proto_err_q, last_ev_q;
    logic [8:0]  k_q;

    // 按原位置累加：第 idx 个事件接的是原码第 (8-idx) 位
    // 位序由 sar_index_i 给出（0..8 = 原码 bit8..bit0），**不再内部推定**
    wire [3:0]  pos3   = 4'd8 - sar_index_i[3:0];
    wire [8:0]  bitpos = 9'd1 << pos3;
    wire [8:0]  acc    = prefix_q | (bit_i ? bitpos : 9'd0);
    // 协议检查：索引必须等于期望值、qid 必须与冻结值一致
    wire        idx_bad = (sar_index_i != exp_idx);
    wire        qid_bad = qid_seen && (sar_qid_i != qid_q);

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
            qid_q     <= 1'b0; qid_seen <= 1'b0; exp_idx <= 4'd0;
            proto_err_q <= 1'b0; last_ev_q <= 1'b0;
        end else if (start_i) begin
            prefix_q  <= 9'd0;
            idx_q     <= 4'd0;
            done_q    <= 1'b0;
            ev_valid_q<= 1'b0;
            err_q     <= 1'b0;
            k_q       <= 9'd0;
            ev_idx_q  <= 4'd0;
            upper_q   <= upper_i;
            qid_q     <= sar_qid_i;      // 每笔事务冻结量化器身份
            qid_seen  <= 1'b0;
            exp_idx   <= 4'd0;
            proto_err_q <= 1'b0; last_ev_q <= 1'b0;
            // Flash 通道：若有效，最高 3 位直接预置，后续事件须与之相符
            prefix_q  <= flash_valid_i ? {flash_code_i, 6'b0} : 9'd0;          // 冻结：事件期间不再读 upper_i
        end else if (bit_valid_i && !done_q) begin
            proto_err_q <= idx_bad || qid_bad;
            last_ev_q   <= (idx_q == 4'd8);      // 与 ev_valid_q 同沿寄存
            qid_seen    <= 1'b1;
            exp_idx     <= exp_idx + 4'd1;
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
    assign prefix_o   = prefix_q;
    assign proto_err_o = proto_err_q;
    assign last_ev_o   = last_ev_q;

endmodule
