// =============================================================================
// 文件名     : tb_core_p0.sv
// 模块名     : 保护码与片组同沿事务的四态台架
// 版本       : 随 Git 源版本
// 功能       : 校验完整 10 位保护尾窗，及推进与播种同时发生时的片权。
// 设计说明   : 期望掩码和 seed 来自接口合同，未使用 DUT 的内部组合信号。
// 变更记录   : 增加两种历史硬件反例的独立检查。
// =============================================================================
`timescale 1ns/1ps
module tb_core_p0;
    logic clk = 0;
    always #5 clk = !clk;
    logic rst_n = 1, seed_we = 0, adv = 0;
    logic [31:0] seed = 32'h12345678;
    wire [31:0] rng;
    wire [17:0] acq, conv, idle;
    wire [39:0] ids, next_ids;
    logic [9:0] k;
    logic coin;
    logic [159:0] mask;
    int seed_errors, dem_errors;
    sched18 sched(.clk(clk), .reset_n(rst_n), .seed_we(seed_we), .seed_i(seed),
        .adv(adv), .acq_mask_o(acq), .conv_mask_o(conv), .idle_mask_o(idle),
        .ids_o(ids), .ids_next_o(next_ids), .rng_o(rng));
    dem_mask_v3 dem(.k_i(k), .h_phase_i(3'd3), .v_phase_i(3'd5),
        .coin_i(coin), .acq_seq_i(4'h8), .mask_o(mask));
    initial begin
        #1 rst_n=0;
        repeat (2) @(negedge clk);
        rst_n=1;
        seed_we=1;
        adv=1;
        @(posedge clk); #1;
        if (rng !== seed || conv !== 18'h000ff || $countones(acq) != 8 || (acq & conv) != 0) begin
            $display("SEED_ADV FAIL rng=%08x expected=%08x conv=%05x acq=%05x", rng,seed,conv,acq);
            seed_errors++;
        end
        for (int val=520; val<=535; val++) begin
            k=10'(val);
            for (int phase=0; phase<2; phase++) begin
                coin=1'(phase);
                #1;
                if (mask !== {160{1'b1}}) begin
                    dem_errors++;
                    if (dem_errors <= 2) $display("DEM_RANGE FAIL k=%0d coin=%b mask=%h", val,coin,mask);
                end
            end
        end
        if (dem_errors || seed_errors) $fatal(1,"CORE_P0 FAIL dem=%0d seed=%0d",dem_errors,seed_errors);
        $display("CORE_P0 PASS dem_vectors=32 seed_adv=1");
        $finish;
    end
endmodule
