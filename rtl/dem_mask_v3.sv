// D3 DEM：3+3+3 -> 8 宏列 x 16 半单元 -> 8x20 物理 mask（合同 v2.1 §6）
//
// k = 64H + 8V + L (0..511)；guard 512..520 主阵列全选 + 子阵列
// 横向：[thermometer7(H), 0] 在 8 宏列上整体轮转，结构零位即保留列
// 纵向：8 位、恰好 V 个 1，按 v_phase 轮转
// 每个宏列展开成两个相同半单元 j=2u, 2u+1
// 子阵列 [L2,L1,L0,0]；bridge 交换按 §6.1
module dem_mask_v3 (
    input  logic [9:0]  k_i,          // 0..520
    input  logic [2:0]  h_phase_i,
    input  logic [2:0]  v_phase_i,
    input  logic        coin_i,
    input  logic [3:0]  acq_seq_i,    // bit3 决定 half
    output logic [19:0] mask_o [0:7]  // 8 片 x 20 单元
);
    logic [2:0] H, V, L;
    logic       guard;
    logic [2:0] res;
    logic [7:0] hw, yw;
    logic [3:0] j_sel;

    assign guard = (k_i >= 10'd512);
    assign H = guard ? 3'd0 : k_i[8:6];
    assign V = guard ? 3'd0 : k_i[5:3];
    assign L = k_i[2:0];
    wire [3:0] r4 = k_i[3:0];      // guard 时 = k-512 (0..8)
    assign res = (3'd7 + h_phase_i);          // (7+h) % 8，3 位自然回绕

    // 横向：[thermometer7(H), 0] 按 h_phase 整体轮转
    genvar i;
    generate
        for (i = 0; i < 8; i++) begin : g_h
            assign hw[i] = (((i - h_phase_i) & 3'd7) < H);
            assign yw[i] = (((i - v_phase_i) & 3'd7) < V);
        end
    endgenerate

    // 普通区 mask
    always_comb begin
        for (int s = 0; s < 8; s++) begin
            for (int u = 0; u < 8; u++) begin
                logic b;
                b = guard ? 1'b1 : ((u[2:0] == res) ? yw[s] : hw[u[2:0]]);
                mask_o[s][2*u]   = b;
                mask_o[s][2*u+1] = b;
            end
            // 子阵列
            if (guard) begin
                mask_o[s][16]=r4[2]; mask_o[s][17]=r4[1]; mask_o[s][18]=r4[0]; mask_o[s][19]=1'b0;
                if (r4 >= 4'd8) mask_o[s][19:16] = 4'b1111;
            end else begin
                mask_o[s][16]=L[2]; mask_o[s][17]=L[1]; mask_o[s][18]=L[0]; mask_o[s][19]=1'b0;
            end
        end
        // bridge 交换：slot=v_phase, macro=res, half=acq_seq[3]
        if (coin_i) begin
            j_sel = {res, acq_seq_i[3]};
            begin
                logic a, b4;
                a  = mask_o[v_phase_i][j_sel];
                b4 = mask_o[0][16];
                mask_o[v_phase_i][j_sel] = b4;
                for (int s = 0; s < 8; s++)
                    mask_o[s][16] = a;
            end
        end
    end
endmodule
