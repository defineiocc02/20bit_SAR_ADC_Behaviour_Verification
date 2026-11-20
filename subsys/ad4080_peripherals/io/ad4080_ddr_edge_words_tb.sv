`timescale 1ns/1ps

// =============================================================================
// 文件名     : ad4080_ddr_edge_words_tb.sv
// 模块名     : ad4080_ddr_edge_words_tb
// 版本       : v1.0
// 功能       : 按图 74/75 的接收顺序检查边沿映射、测试字覆盖和闲置槽。
// 设计说明   : 固定模式用手推实值，其余输入用接收端逐边沿拼接作 oracle。
//              此处的时间步仅让组合传播稳定，不能用来量取 PHY 时延。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module ad4080_ddr_edge_words_tb;
    // =========================================================================
    // 1. 组合 DUT 接线
    // =========================================================================
    logic [19:0] sample;          // 待测 20 位物理字
    logic        dual_lane;       // 两种 lane 配置逐样本遍历
    logic        intf_check;      // 比较正常状态、覆盖与退出检查模式
    logic [9:0]  da_fall, da_rise; // 槽由低位按时间递增
    logic [9:0]  db_fall, db_rise; // 空槽也在测试域内
    logic [3:0]  slot_count;      // 独立检查，重组器用固定规格长度
    int unsigned checks = 0;      // 本次成功完成的整字检查数

    ad4080_ddr_edge_words dut (
        .sample_i(sample),
        .dual_lane_i(dual_lane),
        .intf_check_i(intf_check),
        .da_fall_o(da_fall),
        .da_rise_o(da_rise),
        .db_fall_o(db_fall),
        .db_rise_o(db_rise),
        .slot_count_o(slot_count)
    );

    // =========================================================================
    // 2. 按线时序重组和检查空槽
    // =========================================================================
    // [验收] 接收机顺序由手册定义，双 lane 每沿先收 A 后收 B。
    // 槽数不能用 DUT 自己的输出循环，否则少发一整槽可能悄悄漏验。
    task automatic check_word(
        input logic [19:0] input_word,  // 帧源词，检查模式下仍随机或定向改变
        input logic        use_dual,    // 独立选择接收重组的宽度
        input logic        use_check    // 固定字优先于任意输入
    );
        logic [19:0] received_word;
        logic [19:0] expected_word;

        sample = input_word;
        dual_lane = use_dual;
        intf_check = use_check;
        #1;

        received_word = '0;
        if (use_dual) begin
            for (int slot = 0; slot < 5; slot++) begin
                received_word = {received_word[15:0], da_fall[slot], db_fall[slot],
                                 da_rise[slot], db_rise[slot]};
            end
            if (slot_count !== 4'd5 ||
                {da_fall[9:5], da_rise[9:5], db_fall[9:5], db_rise[9:5]} !== 20'b0) begin
                $fatal(1, "dual mode bad slot count or unused edge bits");
            end
        end else begin
            for (int slot = 0; slot < 10; slot++) begin
                received_word = {received_word[17:0], da_fall[slot], da_rise[slot]};
            end
            if (slot_count !== 4'd10 || {db_fall, db_rise} !== 20'b0) begin
                $fatal(1, "single mode bad slot count or DB bits");
            end
        end

        expected_word = use_check ? 20'hAC5D6 : input_word;
        if (received_word !== expected_word) begin
            $fatal(1, "mode dual=%0d check=%0d word=%05h received=%05h expected=%05h",
                   use_dual, use_check, input_word, received_word, expected_word);
        end
        checks++;
    endtask

    // =========================================================================
    // 3. 固定、walking 位和可重放伪随机词
    // =========================================================================
    // [验收] 固定码的十六进制边沿字即使有另一种重组规则也不会消掉 lane
    // 或首沿弄反的错误。walking 位使最高位、最低位、中间每一位单独可观测。
    initial begin
        logic [31:0] prng_state;
        logic [19:0] one_hot;

        check_word(20'h00000, 1'b0, 1'b0);
        check_word(20'hFFFFF, 1'b1, 1'b0);
        check_word(20'h00000, 1'b0, 1'b1);
        if ({da_fall, da_rise} !== {10'h247, 10'h1F4}) begin
            $fatal(1, "single fixed pattern edges differ from Rev. B order");
        end

        check_word(20'hFFFFF, 1'b1, 1'b1);
        if ({da_fall, da_rise, db_fall, db_rise} !==
            {10'h00B, 10'h011, 10'h01E, 10'h00C}) begin
            $fatal(1, "dual fixed pattern edges differ from Rev. B order");
        end
        check_word(20'h1359C, 1'b1, 1'b0);

        for (int bit_index = 0; bit_index < 20; bit_index++) begin
            one_hot = 20'h1 << bit_index;
            check_word(one_hot, 1'b0, 1'b0);
            check_word(~one_hot, 1'b0, 1'b0);
            check_word(one_hot, 1'b1, 1'b0);
            check_word(~one_hot, 1'b1, 1'b0);
        end

        // [可复现] 固定 xorshift32 种子，无仿真器私有随机状态依赖。
        prng_state = 32'h40800001;
        for (int vector_num = 0; vector_num < 256; vector_num++) begin
            prng_state ^= prng_state << 13;
            prng_state ^= prng_state >> 17;
            prng_state ^= prng_state << 5;
            check_word(prng_state[19:0], 1'b0, 1'b0);
            check_word(prng_state[19:0], 1'b0, 1'b1);
            check_word(prng_state[19:0], 1'b1, 1'b0);
            check_word(prng_state[19:0], 1'b1, 1'b1);
        end

        $display("PASS IO edge words: %0d frame-word checks and both fixed edge literals", checks);
        $finish;
    end
endmodule
