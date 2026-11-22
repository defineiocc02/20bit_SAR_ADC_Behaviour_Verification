// =============================================================================
// 文件名     : dem_mask_v3.sv
// 模块名     : D3 分段 DEM 与桥接交换
// 版本       : 随 Git 源版本
// 功能       : 将 3+3+3 码展开为 8 片、每片 20 位的物理开关状态。
// 设计说明   : 水平与垂直相位按 8 位环转，保护码按完整输入饱和。
// 变更记录   : 使超域输入保持保护码终点，桥接临时位覆盖全部组合分支。
// =============================================================================
// D3 DEM：3+3+3 -> 8 宏列 x 16 半单元 -> 8x20 物理 mask（合同 v2.1 §6）
//
// k = 64H + 8V + L (0..511)；guard 512..520 主阵列全选 + 子阵列
// 横向：[thermometer7(H), 0] 在 8 宏列上整体轮转，结构零位即保留列
// 纵向：8 位、恰好 V 个 1，按 v_phase 轮转
// 每个宏列展开成两个相同半单元 j=2u, 2u+1
// 子阵列 [L2,L1,L0,0]；bridge 交换按 §6.1
//
// 端口打包约定（2026-11-15）
// -------------------------
// 原版 mask_o 是 `output logic [19:0] mask_o [0:7]`（非打包数组）。
// DC 2024.09 能综合它，但会把端口自动展平成 [159:0]，且**展平顺序与源码
// 下标相反**（slice 0 落在最高位段），这一点在门级仿真时会导致整片错位，
// 必须先试出来才知道。为了让这个约定显式化、可被约束和 STA 正常处理，
// 现在固定为打包向量：
//
//     mask_o[20*s + j]  = 第 s 片（0..7）、第 j 单元（0..19）
//
// 内部仍保留一份 2 维视图 mask_v[][] 便于按 (s, j) 读写，最后统一展开，
// 这样算法部分一行不用改，位序只有一处定义。
module dem_mask_v3 (
    input  logic [9:0]   k_i,          // 0..520
    input  logic [2:0]   h_phase_i,
    input  logic [2:0]   v_phase_i,
    input  logic         coin_i,
    input  logic [3:0]   acq_seq_i,    // bit3 决定 half
    output logic [159:0] mask_o        // 8 片 x 20 单元，bit = 20*s + j
);
    logic [2:0] H, V, L;
    logic       guard;
    logic [2:0] res;
    logic [7:0] hw, yw;
    logic [3:0] j_sel;
    logic       sw_a, sw_b4;

    assign guard = (k_i >= 10'd512);
    assign H = guard ? 3'd0 : k_i[8:6];
    assign V = guard ? 3'd0 : k_i[5:3];
    assign L = k_i[2:0];
    // 非饱和 guard 只有 512..519，低三位等于其子阵列余量。
    wire [2:0] guard_residue = k_i[2:0];
    assign res  = (3'd7 + h_phase_i);        // (7+h) % 8，3 位自然回绕
    assign j_sel = {res, acq_seq_i[3]};      // 连续赋值，不进 always_comb，避免锁存

    // 1. 三位模减法产生环绕；比较保持同宽，避免生成索引扩到整数。
    genvar i;
    generate
        for (i = 0; i < 8; i++) begin : g_h
            assign hw[i] = ((3'(i) - h_phase_i) < H);
            assign yw[i] = ((3'(i) - v_phase_i) < V);
        end
    endgenerate

    // 内部的二维视图：只在本模块内存在，不流出端口
    logic [19:0] mask_v [0:7];

    // 2. 主/子阵列选元与桥接
    always_comb begin
        // coin=0 时不读取交换临时位。仍给组合输出完整赋值以消除锁存。
        sw_a = 1'b0;
        sw_b4 = 1'b0;
        for (int s = 0; s < 8; s++) begin
            for (int u = 0; u < 8; u++) begin
                logic b;
                b = guard ? 1'b1 : ((u[2:0] == res) ? yw[s] : hw[u[2:0]]);
                mask_v[s][2*u]   = b;
                mask_v[s][2*u+1] = b;
            end
            // 子阵列
            if (guard) begin
                // 完整输入到达保护码终点后保持全接通，低位翻转不会再回绕。
                if (k_i >= 10'd520) mask_v[s][19:16] = 4'b1111;
                else mask_v[s][19:16] = {1'b0, guard_residue[0], guard_residue[1], guard_residue[2]};
            end else begin
                mask_v[s][16]=L[2]; mask_v[s][17]=L[1]; mask_v[s][18]=L[0]; mask_v[s][19]=1'b0;
            end
        end
        // bridge 交换：slot=v_phase, macro=res, half=acq_seq[3]
        if (coin_i) begin
            sw_a  = mask_v[v_phase_i][j_sel];
            sw_b4 = mask_v[0][16];
            mask_v[v_phase_i][j_sel] = sw_b4;
            for (int s = 0; s < 8; s++)
                mask_v[s][16] = sw_a;
        end
    end

    // 3. 唯一的位序定义处：bit = 20*s + j
    genvar gs, gj;
    generate
        for (gs = 0; gs < 8; gs++) begin : g_pack_s
            for (gj = 0; gj < 20; gj++) begin : g_pack_j
                assign mask_o[20*gs + gj] = mask_v[gs][gj];
            end
        end
    endgenerate

endmodule
