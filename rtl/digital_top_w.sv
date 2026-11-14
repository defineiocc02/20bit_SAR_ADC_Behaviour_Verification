// D6 数字顶层 · 权重路径（w2）集成版
//
//   c_i ─┬─ dither_split ─ upper ─┐
//        └──────────────────────── k = c − upper
//   k + DEM 相位 ─ dem_shuffle ─ h[15:0] / y[7:0]
//        └─ mask[s][j] = (j==reserved) ? y[s] : h[j]   （j=16..19 取 L 子阵列位）
//
//   片组由顶层内部 round-robin 产生：第 n 笔（reset 后从 0 起）
//     sid[s] = (8·(n+1) + s) mod 18,  s = 0..7
//   与 digital_chain.run_transaction 的 slice_ids 完全一致（见 slice_pool.sv）。
//
//   系数表 w_tab[sid][unit] / g_tab[sid] / inv_g 由 cfg 口预写，
//   **只允许在流水空闲时写**（busy_o == 0）；reserved 校准状态不随 reset 清零。
//     g_tab[sid] = Σ_{unit<16} w_tab[sid][unit]   （写口增量维护）
//   冻结语义：一笔事务一旦被接收，它用到的权重/倒数在整个流水里保持不变。
//
// 流水（实测延迟见 docs/TOP_INTEGRATION.md）：
//   E0  valid_i 当拍：k、mask、片 ID
//   E1  mask/片 ID/adc2/dither/tag 打拍
//   E2  Wm/G 组合累加后打拍
//   E3  recon_d5_w2 S1（d_q24_r 打拍）
//   E4  recon_d5_w2 S2（x_q24 打拍）→ S3 组合出 code_o
//   ⇒ 从 valid_i 到 valid_o = 4 拍（实测，不以函数名假定）
//
// 背压只做在入口（stall_i → in_ready_o=0），流水内部不反压；
// reset_n 清掉在途 valid（这些事务由外层记分板计为 cancelled）。
module digital_top_w (
    input  logic        clk,
    input  logic        reset_n,

    // ---- 事务入口 ----
    input  logic        valid_i,
    input  logic        stall_i,        // 上游停顿：只在入口生效
    input  logic [8:0]  c_i,            // 量化器粗码
    input  logic [11:0] adc2_i,         // ADC2 原始码
    input  logic [5:0]  dither_i,       // D6..D1
    input  logic [3:0]  reserved_i,     // 保留列（固定零）
    input  logic [3:0]  h_phase_i,
    input  logic [2:0]  v_phase_i,
    input  logic [23:0] tag_i,          // 事务唯一标签，随流水透传
    output logic        in_ready_o,

    // ---- 冻结系数加载口（仅 idle 有效）----
    input  logic        cfg_we,
    input  logic [4:0]  cfg_slice,      // 0..17
    input  logic [4:0]  cfg_unit,       // 0..19
    input  logic [43:0] cfg_w,          // Q30 权重
    input  logic        cfg_inv_we,
    input  logic [3:0]  cfg_inv_grp,    // 0..8，该倒数对应的活动片组
    input  logic [63:0] cfg_inv,        // round(2^60 / G)，冻结

    // ---- 状态 ----
    output logic        busy_o,         // 流水内有在途事务
    output logic [39:0] slice_ids_o,    // 当拍入口事务选中的 8 个物理片 ID

    // ---- 结果 ----
    output logic [19:0] code_o,
    output logic        overflow_o,
    // ---- D2 实时 Follower 事件口（可选）----
    input  logic        ev_start_i,     // 一次转换开始
    input  logic        ev_valid_i,     // 该拍有一位已决码
    input  logic        ev_bit_i,       // 已决码位
    output logic        ev_done_o,      // 9 个事件收完
    output logic [3:0]  ev_idx_o,       // 0..8
    output logic [8:0]  ev_k_o,         // 该事件产出的 RDAC 码
    output logic        ev_err_o,       // prefix < Upper（不回绕）
    output logic        ev_mask_valid_o,
    output logic [159:0] ev_mask_o,     // 8 片 × 20 单元，每事件一份
    output logic [39:0] ev_ids_o,       // 该笔事务的 8 个物理片 ID
    output logic        err_o,          // 输入合同违例（k 越界 / G=0 / inv 越界）
    output logic        valid_o,
    output logic [23:0] tag_o
);

    localparam int N_SLICE  = 18;
    localparam int N_ACTIVE = 8;
    localparam int N_UNIT   = 20;
    localparam int N_COL    = 16;

    // ================================================================ E0
    logic [2:0]  upper;
    logic [4:0]  lower_q3;
    logic [5:0]  total_q3;
    logic [8:0]  k;
    logic        k_bad;

    dither_split u_dither(
        .word_i(dither_i),
        .upper_o(upper),
        .lower_q3_o(lower_q3),
        .total_q3_o(total_q3));

    assign k     = c_i - {6'b0, upper};
    assign k_bad = (c_i < {6'b0, upper});       // k < 0，mask/片选无定义

    // round-robin 片组：只在接收事务时前进
    logic [15:0] sample_cnt;
    logic [31:0] sid_base;
    assign sid_base = (({16'd0, sample_cnt} + 32'd1) * 32'd8) % 32'd18;

    genvar gi;
    generate
        for (gi = 0; gi < N_ACTIVE; gi++) begin : g_sid
            assign slice_ids_o[gi*5 +: 5] = (sid_base[4:0] + gi) % 5'd18;
        end
    endgenerate

    logic [15:0] h;
    logic [7:0]  y;
    dem_shuffle u_dem(
        .k_i(k), .reserved_i(reserved_i),
        .h_phase_i(h_phase_i), .v_phase_i(v_phase_i),
        .h_o(h), .y_o(y));

    // 物理单元 mask：主 16 列 + 子 4 位。子阵列只用 L=k[1:0]（与数字链一致）
    logic [19:0] mask_c [0:N_ACTIVE-1];
    always_comb begin
        for (int s = 0; s < N_ACTIVE; s++) begin
            for (int j = 0; j < N_COL; j++)
                mask_c[s][j] = (j == int'(reserved_i)) ? y[s] : h[j];
            mask_c[s][16] = 1'b0;
            mask_c[s][17] = 1'b0;
            mask_c[s][18] = k[1];
            mask_c[s][19] = k[0];
        end
    end

    // ================================================================ E1
    logic        valid_a;
    logic [19:0] mask_a [0:N_ACTIVE-1];
    logic [4:0]  slice_a [0:N_ACTIVE-1];
    logic [3:0]  grp_a;
    logic [11:0] adc2_a;
    logic [5:0]  dither_a;
    logic [23:0] tag_a;
    logic        kbad_a;

    // ================================================================ E2
    logic        valid_b;
    logic [43:0] wm_b, g_b;
    logic [43:0] wm_c, g_c;
    logic [3:0]  grp_b;
    logic [63:0] inv_sel;
    logic        inv_missing;
    logic [11:0] adc2_b;
    logic [5:0]  dither_b;
    logic [23:0] tag_b;
    logic        kbad_b;

    // ================================================================ E3/E4
    logic        valid_s1, valid_s2;
    logic        err_s1, err_s2;
    logic [23:0] tag_s1, tag_s2;
    logic        recon_err;

    // ---------------------------------------------------------------- 冻结系数表
    logic [43:0] w_tab [0:N_SLICE-1][0:N_UNIT-1];
    logic [43:0] g_tab [0:N_SLICE-1];
    logic [63:0] inv_tab [0:8];     // 每组 27 位有效，存 64 位便于零扩展
    logic [8:0]  inv_loaded;        // 该组倒数是否已装载
    // 片组号：与 slice_ids 同一 n 推导，组 = (8(n+1))%18 的逆映射，等价于 n%9
    function automatic logic [3:0] grp_of(input logic [15:0] n);
        return 4'(n % 16'd9);
    endfunction

    // 校准状态：靠 cfg 口建立，不随 reset_n 清零
    initial begin
        for (int s = 0; s < N_SLICE; s++) begin
            g_tab[s] = 44'd0;
            for (int u = 0; u < N_UNIT; u++) w_tab[s][u] = 44'd0;
        end
        inv_loaded = 9'd0;
        for (int g = 0; g < 9; g++) inv_tab[g] = 64'd0;
    end

    // 在途事务数：用于判定 idle（cfg 口只在 idle 时可写）
    assign busy_o = valid_a | valid_b | valid_s1 | valid_s2;
    // 入口只在 reset/stall/校准写时停；流水本身不反压
    assign in_ready_o = reset_n & ~stall_i & ~cfg_we & ~cfg_inv_we;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            sample_cnt <= 16'd0;
        end else if (valid_i && in_ready_o) begin
            sample_cnt <= sample_cnt + 16'd1;
        end
    end

    // 写口：busy 时整笔丢弃（外层在 idle 才写；若违反则这里不静默改表）
    always @(posedge clk) begin
        if (cfg_we && !busy_o && (cfg_slice < N_SLICE) && (cfg_unit < N_UNIT)) begin
            // 系数更新 -> 旧倒数全部失效，未重载前转换必须报错
            inv_loaded <= 9'd0;
            // 主列增量维护行和，重写同址也正确
            if (cfg_unit < N_COL)
                g_tab[cfg_slice] <= g_tab[cfg_slice] - w_tab[cfg_slice][cfg_unit] + cfg_w;
            w_tab[cfg_slice][cfg_unit] <= cfg_w;
        end
        if (cfg_inv_we && !busy_o && (cfg_inv_grp < 4'd9)) begin
            inv_tab[cfg_inv_grp]    <= cfg_inv;
            inv_loaded[cfg_inv_grp] <= 1'b1;
        end
    end

    // ---------------------------------------------------------------- D2 实时 Follower
    logic [62:0] fk_ev;
    logic        ev_valid_o;   // follower 事件有效（内部用）
    logic [2:0]  dith_upper;      // U = 4*D6 + D4（与 _dither_terms 一致）
    assign dith_upper = (dither_i[5] ? 3'd4 : 3'd0) + (dither_i[3] ? 3'd1 : 3'd0);
    follower_rt u_fol (
        .clk(clk), .reset_n(reset_n), .start_i(ev_start_i),
        .bit_valid_i(ev_valid_i), .bit_i(ev_bit_i),
        .upper_i(dith_upper),
        .ev_valid_o(ev_valid_o), .ev_idx_o(ev_idx_o), .k_ev_o(ev_k_o),
        .done_o(ev_done_o), .err_o(ev_err_o));

    // 生产路径不再用最终码 c_i 做事件比对；期望值由 TB 记分板独立计算

    // ---- 逐事件 DEM + 物理 mask ----
    // 每笔事务在 ev_start_i 冻结（片组/保留列/横纵相位/Upper），事件期间不得再读当拍值。
    logic [4:0]  ev_sid0_q;
    logic [3:0]  ev_res_q, ev_hp_q;
    logic [2:0]  ev_vp_q;
    logic        ev_active_q;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            ev_sid0_q <= 5'd0; ev_res_q <= 4'd0; ev_hp_q <= 4'd0;
            ev_vp_q <= 3'd0; ev_active_q <= 1'b0;
        end else if (ev_start_i) begin
            ev_sid0_q   <= slice_ids_o[4:0];   // 该笔事务首片 ID
            ev_res_q    <= reserved_i;
            ev_hp_q     <= h_phase_i;
            ev_vp_q     <= v_phase_i;
            ev_active_q <= 1'b1;
        end else if (ev_done_o) begin
            ev_active_q <= 1'b0;
        end
    end

    logic [15:0] ev_h;
    logic [7:0]  ev_y;
    dem_shuffle u_dem_ev (
        .k_i(ev_k_o), .reserved_i(ev_res_q), .h_phase_i(ev_hp_q), .v_phase_i(ev_vp_q),
        .h_o(ev_h), .y_o(ev_y));

    logic [159:0] ev_mask_c;
    always_comb begin
        for (int s_ = 0; s_ < 8; s_++) begin
            for (int j = 0; j < N_COL; j++)
                ev_mask_c[s_*20 + j] = (j == int'(ev_res_q)) ? ev_y[s_] : ev_h[j];
            ev_mask_c[s_*20 + 16] = 1'b0;
            ev_mask_c[s_*20 + 17] = 1'b0;
            ev_mask_c[s_*20 + 18] = ev_k_o[1];
            ev_mask_c[s_*20 + 19] = ev_k_o[0];
        end
    end

    // 越界策略：prefix < Upper 时不输出该事件命令，**保持上一次合法命令**，
    // 同时由 ev_err_o 报告。不把钳到 0 的码当作有效命令。
    // mask 与 ev_k_o 同拍组合输出，保证"事件码 ↔ 该码的物理 mask"一一对齐。
    // 越界事件不输出命令（valid 拉低），也不钳码后当合法命令使用。
    assign ev_mask_o       = ev_mask_c;
    assign ev_mask_valid_o = ev_valid_o && !ev_err_o;
    assign ev_ids_o        = slice_ids_o;

    // ---------------------------------------------------------------- E1 打拍
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            valid_a <= 1'b0;
            kbad_a  <= 1'b0;
        end else begin
            valid_a <= valid_i && in_ready_o;
            kbad_a  <= k_bad;
            for (int s = 0; s < N_ACTIVE; s++) begin
                mask_a[s]  <= mask_c[s];
                slice_a[s] <= slice_ids_o[s*5 +: 5];
            end
            grp_a    <= grp_of(sample_cnt);   // 与 slice_ids 用同一个 n
            adc2_a   <= adc2_i;
            dither_a <= dither_i;
            tag_a    <= tag_i;
        end
    end

    // ---------------------------------------------------------------- E2 累加
    // Wm = Σ_s Σ_j mask[s][j]·w[sid_s][j]（主 + 子，Q30）
    // G  = Σ_s Σ_{j<16} w[sid_s][j]        （只算 16 个采样主列）
    always_comb begin
        wm_c = 44'd0;
        g_c  = 44'd0;
        for (int s = 0; s < N_ACTIVE; s++) begin
            g_c = g_c + g_tab[slice_a[s]];
            for (int j = 0; j < N_UNIT; j++)
                if (mask_a[s][j]) wm_c = wm_c + w_tab[slice_a[s]][j];
        end
    end

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            valid_b <= 1'b0;
            kbad_b  <= 1'b0;
        end else begin
            valid_b <= valid_a;
            kbad_b  <= kbad_a;
            wm_b    <= wm_c;
            g_b     <= g_c;
            grp_b   <= grp_a;
            adc2_b  <= adc2_a;
            dither_b<= dither_a;
            tag_b   <= tag_a;
        end
    end

    assign inv_sel     = inv_tab[grp_b];
    assign inv_missing = ~inv_loaded[grp_b];   // 未装载 -> 明确报错，不静默用旧值

    // ---------------------------------------------------------------- E3/E4：w2 重构
    recon_d5_w2 u_recon(
        .clk(clk), .reset_n(reset_n),
        .wm_q30(wm_b), .g_q30(g_b), .inv_g_q60(inv_sel),
        .adc2_i(adc2_b), .dither_i(dither_b),
        .code_o(code_o), .overflow_o(overflow_o), .err_o(recon_err));

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            valid_s1 <= 1'b0;
            valid_s2 <= 1'b0;
            err_s1   <= 1'b0;
            err_s2   <= 1'b0;
            tag_s1   <= 24'd0;
            tag_s2   <= 24'd0;
        end else begin
            valid_s1 <= valid_b;
            valid_s2 <= valid_s1;
            // recon 的 err 与 wm_b/g_b/inv 同拍；k 越界随事务一起对齐
            err_s1   <= recon_err | kbad_b | inv_missing;
            err_s2   <= err_s1;
            tag_s1   <= tag_b;
            tag_s2   <= tag_s1;
        end
    end

    assign valid_o = valid_s2;
    assign err_o   = err_s2;
    assign tag_o   = tag_s2;

endmodule
