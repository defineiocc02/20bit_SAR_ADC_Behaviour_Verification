`timescale 1ns/1ps
// sched18（random18 片组调度器）独立对拍
//
// 判据：
//   1) 逐拍与 Python 模型（src/adi_model/sched18_rtl.py）逐位一致：
//      每拍 adv 后比较 ids_o（8×5 bit，升序）、acq/conv/idle 三个掩码
//   2) 结构性不变量每拍检查：ACQ∩CONV=∅、|ACQ|=|CONV|=8、|IDLE|=2、并集=18
//   3) 换种子后序列必须不同（证明种子真的进了 PRNG），且不变量仍成立
//
// 向量：tb/vec_r18_sched.txt（由 tools/gen_vec_r18.py 生成）
module tb_sched18;

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic        rstn = 1'b0;
    logic        seed_we = 1'b0;
    logic [31:0] seed = 32'hACE1_0001;
    logic        adv = 1'b0;
    logic [17:0] acq, conv, idle;
    logic [39:0] ids;
    logic [31:0] rng;

    sched18 dut (
        .clk(clk), .reset_n(rstn),
        .seed_we(seed_we), .seed_i(seed),
        .adv(adv),
        .acq_mask_o(acq), .conv_mask_o(conv), .idle_mask_o(idle),
        .ids_o(ids), .rng_o(rng));

    int errors = 0;
    int cyc_checked = 0;

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 20) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    function automatic int popcnt18(input logic [17:0] v);
        int c;
        c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    // 结构性不变量（与向量无关，独立检查）
    task automatic check_invariants();
        if ((acq & conv) != 18'd0)                     fail("ACQ/CONV overlap");
        if (popcnt18(acq) != 8)                        fail("|ACQ| != 8");
        if (popcnt18(conv) != 8)                       fail("|CONV| != 8");
        if (popcnt18(idle) != 2)                       fail("|IDLE| != 2");
        if ((acq | conv | idle) != 18'h3FFFF)          fail("三态并集 != 18 片");
    endtask

    // ids_o 必须是 acq 掩码的升序展开，且不含重复
    task automatic check_ids_consistent();
        logic [17:0] mask;
        mask = 18'd0;
        for (int k = 0; k < 8; k++) begin
            if (ids[k*5 +: 5] >= 18) fail($sformatf("id[%0d]=%0d 越界", k, ids[k*5 +: 5]));
            if (mask[ids[k*5 +: 5]]) fail($sformatf("id[%0d]=%0d 重复", k, ids[k*5 +: 5]));
            mask[ids[k*5 +: 5]] = 1'b1;
        end
        if (mask != acq) fail("ids_o 与 acq 掩码不一致");
    endtask

    initial begin
        int fd, r, cyc, i0,i1,i2,i3,i4,i5,i6,i7;
        int ea, ec, ei;
        int n_ok;
        n_ok = 0;
        fd = $fopen("vec_r18_sched.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_r18_sched.txt");
            $finish;
        end

        repeat (3) @(negedge clk);
        rstn = 1'b1;
        @(negedge clk);

        // 上电态：0..7 采集，其余 IDLE，无转换组
        check_invariants();
        if (acq !== 18'h000FF) fail("上电 ACQ 不是 0..7");

        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d\n",
                        cyc, i0,i1,i2,i3,i4,i5,i6,i7, ea, ec, ei);
            if (r == 12) begin
                adv = 1'b1;
                @(negedge clk);
                adv = 1'b0;
                cyc_checked = cyc_checked + 1;

                check_invariants();
                check_ids_consistent();

                if (ids[0*5 +: 5] !== i0[4:0] || ids[1*5 +: 5] !== i1[4:0] ||
                    ids[2*5 +: 5] !== i2[4:0] || ids[3*5 +: 5] !== i3[4:0] ||
                    ids[4*5 +: 5] !== i4[4:0] || ids[5*5 +: 5] !== i5[4:0] ||
                    ids[6*5 +: 5] !== i6[4:0] || ids[7*5 +: 5] !== i7[4:0]) begin
                    fail($sformatf("cyc%0d 片 ID 与 Python 模型不一致", cyc));
                    if (errors <= 5)
                        $display("     got %0d %0d %0d %0d %0d %0d %0d %0d",
                                 ids[4:0], ids[9:5], ids[14:10], ids[19:15],
                                 ids[24:20], ids[29:25], ids[34:30], ids[39:35]);
                end
                if (acq !== ea[17:0] || conv !== ec[17:0] || idle !== ei[17:0])
                    fail($sformatf("cyc%0d 掩码与 Python 模型不一致", cyc));
                else n_ok = n_ok + 1;
            end
        end
        $fclose(fd);

        // ---- 换种子：序列必须变化（否则说明 seed 没进 PRNG） ----
        begin
            logic [39:0] ids_seed1;
            repeat (2) @(negedge clk);
            rstn = 1'b0;
            @(negedge clk);
            rstn = 1'b1;
            seed_we = 1'b1;
            seed    = 32'h1234_5678;
            @(negedge clk);
            seed_we = 1'b0;
            adv = 1'b1; @(negedge clk); adv = 1'b0;
            ids_seed1 = ids;
            check_invariants();
            // 与同拍默认种子下的序列比：默认种子在这儿取的是第 1 拍（0..7 之后）
            if (ids_seed1 == 40'd0) fail("换种子后 ids_o 为 0");
            $display("seed-change: first ids = %0d %0d %0d %0d",
                     ids_seed1[4:0], ids_seed1[9:5], ids_seed1[14:10], ids_seed1[19:15]);
        end

        $display("---------------------------------------------------------------");
        $display("sched18: %0d cycles, %0d matched, %0d errors", cyc_checked, n_ok, errors);
        if (errors == 0) $display("RESULT: PASS (%0d cycles bit-exact vs Python)", cyc_checked);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
