`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_wsum_scan.sv
// 模块名     : tb_wsum_scan
// 版本       : v2.0
// 功能       : 权重表**任意取值**下逐笔输出 G/Wp/Wm，并给独立整数参考值。
//
// 设计说明   :
//   * v1.0 的问题（审查指出）：只写"成组随机权重"，不逐行逐列单点扫描，
//     也不给参考值，无法区分"接反"和"综合折叠"。
//
//   * v2.0 做三件事：
//       ① 18 行分别写**互不相同**的权重，逐行单点非零扫描
//          （每次只让一行一列非零，输出必然等于该权重 -> 能直接定位行/列错位）
//       ② 8 个互异合法片号，pre/post 设置有区分度的图案
//       ③ 每笔都由**独立整数参考模型**算期望值并比对，不符立即报错
//
//   * 参考模型与 RTL 语义一致：
//       G  = sum over s(0..7), j(0..15)   w_main[sid[s]][j]
//       Wp = sum over s, j(0..3) if pre[s*4 + (3-j)]   w_sub[sid[s]][j]
//       Wm = sum over s, j(0..15) if post[s*20+j]      w_main[sid[s]][j]
//            + sum over s, j(0..3) if post[s*20+16+j]  w_sub[sid[s]][j]
//
//   * 交易编号：每笔写 `#<idx>` 前缀，门级侧必须逐 idx 完整配对；
//     缺笔/重复/空交集一律判失败（由 check_wsum_equiv.sh 检查）。
//
// 变更记录   :
//   2026-11-16  v1.0 成组随机权重，无参考值。
//   2026-11-16  v2.0 逐行逐列单点扫描 + 独立参考 + 交易编号。
// =============================================================================
module tb_wsum_scan;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    logic         calc_valid = 1'b0;
    logic [39:0]  ids_i = 40'd0;
    logic [31:0]  pre_mask_i = 32'd0;
    logic [159:0] post_mask_i = 160'd0;
    // 权重表在本 TB 里**仍用本地数组**：它只用于参考模型算期望值。
    // DUT 侧已改成内部寄存器堆，通过下面 5 根配置线灌入（见 wt_load）。
    logic [39:0]  w_main_tbl [0:17][0:15];
    logic [39:0]  w_sub_tbl  [0:17][0:3];
    logic         cfg_we   = 1'b0;
    logic         cfg_sel  = 1'b0;
    logic [4:0]   cfg_sid  = 5'd0;
    logic [4:0]   cfg_idx  = 5'd0;
    logic [39:0]  cfg_data = 40'd0;

    // 把本地权重数组整表灌进 DUT 的寄存器堆（360 个字，每字一拍）
    task automatic wt_load();
        begin
            for (int s = 0; s < 18; s++) begin
                for (int j = 0; j < 16; j++) begin
                    @(negedge clk);
                    cfg_we = 1'b1; cfg_sel = 1'b0;
                    cfg_sid = 5'(s); cfg_idx = 5'(j);
                    cfg_data = w_main_tbl[s][j];
                    @(posedge clk);
                end
                for (int j = 0; j < 4; j++) begin
                    @(negedge clk);
                    cfg_we = 1'b1; cfg_sel = 1'b1;
                    cfg_sid = 5'(s); cfg_idx = 5'(j);
                    cfg_data = w_sub_tbl[s][j];
                    @(posedge clk);
                end
            end
            @(negedge clk); cfg_we = 1'b0;
        end
    endtask

    logic [63:0] G_q30, Wp_q30, Wm_q30;
    logic        sum_valid;

    wsum_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .calc_valid(calc_valid),
        .ids_i(ids_i), .pre_mask_i(pre_mask_i), .post_mask_i(post_mask_i),
        .cfg_we(cfg_we), .cfg_sel(cfg_sel), .cfg_sid(cfg_sid),
        .cfg_idx(cfg_idx), .cfg_data(cfg_data),
        .sum_valid(sum_valid),
        .G_q30(G_q30), .Wp_q30(Wp_q30), .Wm_q30(Wm_q30));

    int fd;
    int errors = 0;
    int txn_idx = 0;

    // ---- 独立参考模型（与 RTL 语义一致，但用普通整数重算）----
    function automatic logic [63:0] ref_G();
        logic [63:0] a;
        begin
            a = 64'd0;
            for (int s = 0; s < 8; s++)
                for (int j = 0; j < 16; j++)
                    a = a + {24'd0, w_main_tbl[ids_i[s*5 +: 5]][j]};
            ref_G = a;
        end
    endfunction

    function automatic logic [63:0] ref_Wp();
        logic [63:0] a;
        begin
            a = 64'd0;
            for (int s = 0; s < 8; s++)
                for (int j = 0; j < 4; j++)
                    if (pre_mask_i[s*4 + (3-j)])
                        a = a + {24'd0, w_sub_tbl[ids_i[s*5 +: 5]][j]};
            ref_Wp = a;
        end
    endfunction

    function automatic logic [63:0] ref_Wm();
        logic [63:0] a;
        begin
            a = 64'd0;
            for (int s = 0; s < 8; s++) begin
                for (int j = 0; j < 16; j++)
                    if (post_mask_i[s*20 + j])
                        a = a + {24'd0, w_main_tbl[ids_i[s*5 +: 5]][j]};
                for (int j = 0; j < 4; j++)
                    if (post_mask_i[s*20 + 16 + j])
                        a = a + {24'd0, w_sub_tbl[ids_i[s*5 +: 5]][j]};
            end
            ref_Wm = a;
        end
    endfunction

    // 跑一笔：驱动一组输入，等 valid，比对参考，写轨迹
    task automatic run_one(input string tag);
        logic [63:0] eG, eWp, eWm;
        begin
            // 权重可能在上一笔之后被改过，先整表同步到 DUT 寄存器堆。
            // 360 字 x 364 笔 ≈ 131k 拍，仿真约几秒，可接受。
            wt_load();
            txn_idx++;
            eG = ref_G(); eWp = ref_Wp(); eWm = ref_Wm();
            calc_valid = 1'b1;
            @(posedge clk); #1;
            @(negedge clk);
            calc_valid = 1'b0;
            @(posedge clk); #1;      // sum_valid 打一拍
            @(negedge clk);
            // 轨迹：交易号 + 完整输入 + 输出 + 参考
            $fdisplay(fd, "#%0d %0s ids=%010h pre=%08h post=%040h G=%016h Wp=%016h Wm=%016h rG=%016h rWp=%016h rWm=%016h",
                      txn_idx, tag, ids_i, pre_mask_i, post_mask_i,
                      G_q30, Wp_q30, Wm_q30, eG, eWp, eWm);
            if ((^G_q30) === 1'bx || (^Wp_q30) === 1'bx || (^Wm_q30) === 1'bx) begin
                errors++;
                $display("  ERROR txn#%0d %0s: 输出含 X/Z", txn_idx, tag);
            end else if (G_q30 !== eG || Wp_q30 !== eWp || Wm_q30 !== eWm) begin
                errors++;
                $display("  ERROR txn#%0d %0s: G=%0d(exp %0d) Wp=%0d(exp %0d) Wm=%0d(exp %0d)",
                         txn_idx, tag, G_q30, eG, Wp_q30, eWp, Wm_q30, eWm);
            end
        end
    endtask

    initial begin
        reg [1023:0] opath;
        if (!$value$plusargs("OUT=%s", opath)) opath = "wsum_scan.txt";
        fd = $fopen(opath, "w");
        if (fd == 0) begin $display("FATAL: cannot open %0s", opath); $finish; end

        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 16; j++) w_main_tbl[s][j] = 40'd0;
            for (int j = 0; j < 4;  j++) w_sub_tbl[s][j]  = 40'd0;
        end

        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(negedge clk);

        // ---------- 阶段 1：18 行分别写互不相同的权重 ----------
        // 权重取 base + s*16 + j，保证每个 (行,列) 的值唯一，
        // 任何行/列错位都会让输出不匹配。
        for (int s = 0; s < 18; s++)
            for (int j = 0; j < 16; j++)
                w_main_tbl[s][j] = 40'd1000 + 40'(s*16 + j);
        for (int s = 0; s < 18; s++)
            for (int j = 0; j < 4; j++)
                w_sub_tbl[s][j] = 40'd50000 + 40'(s*4 + j);

        // 8 个互异合法片号：0,1,2,3,4,5,6,7
        ids_i = 40'd0;
        for (int s = 0; s < 8; s++) ids_i[s*5 +: 5] = 5'(s);
        pre_mask_i  = 32'hFFFF_FFFF;
        post_mask_i = {160{1'b1}};
        run_one("all-ones-all-slots");

        // ---------- 阶段 2：逐行单点非零扫描 ----------
        // 每次只让**一行**的**一列**非零，其余全 0：
        //   G 必须等于该权重 * 该行被 8 个 slot 命中的次数
        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 16; j++) begin
                for (int a = 0; a < 18; a++)
                    for (int b = 0; b < 16; b++)
                        w_main_tbl[a][b] = 40'd0;
                for (int a = 0; a < 18; a++)
                    for (int b = 0; b < 4; b++)
                        w_sub_tbl[a][b] = 40'd0;
                w_main_tbl[s][j] = 40'd777777 + 40'(s*16 + j);
                // 8 个 slot 全指向该行 -> G = 8 * 权重
                ids_i = 40'd0;
                for (int k = 0; k < 8; k++) ids_i[k*5 +: 5] = 5'(s);
                pre_mask_i  = 32'd0;
                post_mask_i = 160'd0;
                run_one($sformatf("row%0d_col%0d", s, j));
            end
        end

        // ---------- 阶段 3：子阵列逐行逐列扫描 ----------
        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 4; j++) begin
                for (int a = 0; a < 18; a++) begin
                    for (int b = 0; b < 16; b++) w_main_tbl[a][b] = 40'd0;
                    for (int b = 0; b < 4;  b++) w_sub_tbl[a][b]  = 40'd0;
                end
                w_sub_tbl[s][j] = 40'd888888 + 40'(s*4 + j);
                ids_i = 40'd0;
                for (int k = 0; k < 8; k++) ids_i[k*5 +: 5] = 5'(s);
                // pre: j 对应 pre[s*4 + (3-j)]；post: j 对应 post[s*20+16+j]
                pre_mask_i  = 32'd0;
                post_mask_i = 160'd0;
                for (int k = 0; k < 8; k++) begin
                    pre_mask_i[k*4 + (3-j)]      = 1'b1;
                    post_mask_i[k*20 + 16 + j]   = 1'b1;
                end
                run_one($sformatf("sub_row%0d_col%0d", s, j));
            end
        end

        // ---------- 阶段 4：有区分度的 pre/post 图案 ----------
        for (int a = 0; a < 18; a++) begin
            for (int b = 0; b < 16; b++) w_main_tbl[a][b] = 40'd2000 + 40'(a*16 + b);
            for (int b = 0; b < 4;  b++) w_sub_tbl[a][b]  = 40'd90000 + 40'(a*4 + b);
        end
        // 8 个互异片号，且不连续，覆盖 17
        ids_i = 40'd0;
        ids_i[0*5 +: 5] = 5'd0;
        ids_i[1*5 +: 5] = 5'd3;
        ids_i[2*5 +: 5] = 5'd17;
        ids_i[3*5 +: 5] = 5'd5;
        ids_i[4*5 +: 5] = 5'd11;
        ids_i[5*5 +: 5] = 5'd1;
        ids_i[6*5 +: 5] = 5'd16;
        ids_i[7*5 +: 5] = 5'd8;
        pre_mask_i  = 32'hA5A5_5A5A;
        post_mask_i = 160'hFFFF_0000_FFFF_0000_FFFF_0000_FFFF_0000_FFFF_0000;
        run_one("mixed-ids-pattern1");
        pre_mask_i  = 32'h0F0F_F0F0;
        post_mask_i = 160'h0000_FFFF_0000_FFFF_0000_FFFF_0000_FFFF_0000_FFFF;
        run_one("mixed-ids-pattern2");
        pre_mask_i  = 32'h1234_5678;
        post_mask_i = {160{1'b1}};
        run_one("mixed-ids-pattern3");

        repeat (3) @(negedge clk);
        $fclose(fd);
        $display("WSUM_SCAN_DONE txns=%0d errors=%0d", txn_idx, errors);
        if (errors != 0) $display("RESULT: FAIL (%0d errors)", errors);
        else             $display("RESULT: PASS (%0d txns)", txn_idx);
        $finish;
    end

endmodule
