// =============================================================================
// 文件名     : core_safety.sv
// 模块名     : DEM 与片组调度独立形式化属性
// 版本       : 初始验证
// 功能       : 证明保护码饱和、片权不交叠及采集片数。
// 设计说明   : 调度首沿置复位，此后种子、推进和复位均可任意变化。
// =============================================================================
module dem_safety(input [9:0] command, input [2:0] horizontal, vertical,
                  input coin, input [3:0] acquisition);
    wire [159:0] mask;
    dem_mask_v3 dut(.k_i(command), .h_phase_i(horizontal), .v_phase_i(vertical),
                    .coin_i(coin), .acq_seq_i(acquisition), .mask_o(mask));
    // 超出数字发令范围的所有码也约束至终点。
    always @* if (command >= 10'd520) assert(mask == {160{1'b1}});
endmodule

module sched_safety(input clk, reset_n, advance, seed_we, input [31:0] seed);
    wire [17:0] acq, conv, idle;
    wire [39:0] ids, next_ids;
    wire [31:0] rng;
    reg checked_initial_reset = 0;
    sched18 dut(.clk(clk),.reset_n(reset_n),.adv(advance),.seed_we(seed_we),.seed_i(seed),
        .acq_mask_o(acq),.conv_mask_o(conv),.idle_mask_o(idle),.ids_o(ids),.ids_next_o(next_ids),.rng_o(rng));

    // 独立计算掩码基数，避免使用 DUT 的候选前缀或索引。
    function automatic [4:0] count_slots(input [17:0] mask);
        integer slot;
        begin
            count_slots=0;
            for (slot=0; slot<18; slot=slot+1) count_slots=count_slots+{4'd0,mask[slot]};
        end
    endfunction

    // 第一沿建立硬复位前提。之后允许任意空拍、播种和重新复位。
    always @(posedge clk) begin
        checked_initial_reset <= 1;
        if (!checked_initial_reset) assume(!reset_n);
        else begin
            assert((acq & conv) == 18'd0);
            assert(count_slots(acq) == 5'd8);
        end
    end
endmodule
