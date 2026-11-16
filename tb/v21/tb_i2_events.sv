`timescale 1ns/1ps
// 状态：**历史**。I2 逐事件，覆盖已并入 tb_i12_full.sv
//       （入口 tools/verify_v21.sh）。保留作追溯。
// =============================================================================
// 文件名     : tb_i2_events.sv
// 模块名     : tb_i2_events
// 版本       : v1.0
// 功能       : I2 逐事件开关验收 —— v2.1 事件协议、k=prefix+U、guard 路径、
//              原子应用资格、非法事件不驱动写门。
// 设计说明   :
//   * 向量来自实施方案 v1.4 §11.3：
//       A(qid0, guard, U=5): k = 453, 517, 517, 519, 519   （5 事件）
//       B(qid1, 普通, U=0): k = 256 x5, 258, 258           （7 事件）
//     由 k = prefix + U 反推 prefix，验证事件表与 §8.1 一致：
//       A: 448(Flash F=7) -> 512(接受阈) -> 514(累 bit1)
//       B: 256(Flash F=4) -> 258(累 bit1)
//   * 覆盖（§11.2 I2）：
//       ① 合法连续事件：k 序列、event_index、apply_fire 全对
//       ② 插空泡：事件之间隔拍
//       ③ 末事件自然结束：done 置位后不再 apply
//       ④ 错 qid、重复序号、k>520、完成后多发事件 -> 必须被拒且写门关闭
//   * guard 路径的"退回路"（阈 512 被拒 -> 回普通 7 事件）单独测。
// 变更记录   :
//   2026-11-15  初版。
// =============================================================================
module tb_i2_events;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    logic        start_i = 1'b0, abort_i = 1'b0;
    logic [31:0] tag_i = 32'd0;
    logic        qid_i = 1'b0;
    logic [2:0]  upper_i = 3'd0;
    logic        flash_valid_i = 1'b0;
    logic [2:0]  flash_code_i = 3'd0;
    logic        bit_valid_i = 1'b0, bit_i = 1'b0;
    logic [3:0]  bit_index_i = 4'd0, event_index_i = 4'd0;
    logic        sar_qid_i = 1'b0, sar_final_i = 1'b0;

    logic        ev_valid_o, k_bad_o, apply_fire_o, final_applied_o;
    logic [3:0]  ev_event_index_o, ev_bit_index_o;
    logic [9:0]  k_o, final_k_o;
    logic [31:0] tag_o;
    logic        qid_o, err_o, done_o;

    sar_evt_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .start_i(start_i), .abort_i(abort_i), .tag_i(tag_i), .qid_i(qid_i),
        .upper_i(upper_i),
        .flash_valid_i(flash_valid_i), .flash_code_i(flash_code_i),
        .bit_valid_i(bit_valid_i), .bit_i(bit_i),
        .bit_index_i(bit_index_i), .event_index_i(event_index_i),
        .sar_qid_i(sar_qid_i), .sar_final_i(sar_final_i),
        .ev_valid_o(ev_valid_o), .ev_event_index_o(ev_event_index_o),
        .ev_bit_index_o(ev_bit_index_o),
        .k_o(k_o), .k_bad_o(k_bad_o), .apply_fire_o(apply_fire_o),
        .final_applied_o(final_applied_o), .final_k_o(final_k_o),
        .tag_o(tag_o), .qid_o(qid_o), .err_o(err_o), .done_o(done_o));

    int errors = 0, checks = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 30) $display("[%0t] ERROR: %s", $time, msg);
    endtask
    task automatic ok(input string msg);
        checks = checks + 1;
        $display("[%0t] OK: %s", $time, msg);
    endtask

    // 启动一笔事务
    task automatic do_start(input logic [31:0] tag, input logic q, input logic [2:0] U,
                            input logic [2:0] F);
        @(negedge clk);
        start_i = 1'b1; tag_i = tag; qid_i = q; upper_i = U;
        flash_valid_i = 1'b1; flash_code_i = F;
        @(posedge clk); #1;
        @(negedge clk);
        start_i = 1'b0; flash_valid_i = 1'b0;
    endtask

    // 一个已决事件。
    //
    // **采样时机是本测试最容易写错的地方，记下来**：
    //   apply_fire_o / k_o 是组合信号，由**当拍**的 exp_evt_q 与 prefix_q 决定。
    //   在 posedge 之后 #1 读，exp_evt_q 已被非阻塞赋值推进到下一拍，
    //   prefix_q 也已更新 —— 读到的 k 是"下一个事件的 k"，
    //   apply_fire_o 也会因为 exp_evt 不匹配而变 0。
    //
    //   正确做法：在 posedge **之前**（驱动后、等一拍）读组合值。
    //   实测在 posedge 前读：exp_evt=1 exp_bit=9 evt_ok=1 bit_ok=1
    //   can_apply=1 apply=1 k=453  —— 与方案 §11.3 的 A 首事件完全一致。
    logic ev_applied;
    logic ev_kbad;
    logic [9:0] ev_k;
    task automatic do_event(input logic [3:0] bi, input logic [3:0] ei,
                            input logic bv, input logic q);
        @(negedge clk);
        bit_valid_i = 1'b1; bit_i = bv; bit_index_i = bi;
        event_index_i = ei; sar_qid_i = q;
        #1;                             // 让组合逻辑稳定（仍在同一个 negedge 之后）
        ev_applied = apply_fire_o;
        ev_kbad    = k_bad_o;
        ev_k       = k_o;
        @(posedge clk);                 // 时钟沿提交
        @(negedge clk);
        bit_valid_i = 1'b0;
    endtask

    // 期望 k 序列
    // A: 5 次提交 = Flash(448) / 阈512 / bit2拒 / bit1受 / bit0拒
    //    k = prefix + 5 -> 453 / 517 / 517 / 519 / 519
    int A_K[0:4]   = '{453, 517, 517, 519, 519};
    int A_BIT[0:4] = '{0, 9, 2, 1, 0};      // evt1 Flash 不比对位号；9=guard门限
    int A_BV[0:4]  = '{0, 1, 0, 1, 0};      // Flash 那拍 bit 无意义；阈受；bit2拒；bit1受；bit0拒
    // B: 7 次提交 = Flash(256) / bit5拒 / bit4拒 / bit3拒 / bit2拒 / bit1受 / bit0拒
    //    k = prefix + 0 -> 256 x5, 258, 258
    int B_K[0:6]   = '{256, 256, 256, 256, 256, 258, 258};
    int B_BIT[0:6] = '{0, 5, 4, 3, 2, 1, 0};
    int B_BV[0:6]  = '{0, 0, 0, 0, 0, 1, 0};

    initial begin
        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk);

        // ================= 场景 A：guard 路径，5 事件 =================
        $display("---- 场景A: guard 路径 (qid0, U=5, Flash F=7) ----");
        do_start(32'hA000_0001, 1'b0, 3'd5, 3'd7);
        checks = checks + 1;
        if (dut.prefix_q !== 10'd448)
            fail($sformatf("Flash F=7 后 prefix 应为 448，实为 %0d", dut.prefix_q));
        else $display("      Flash F=7 -> prefix=448  OK");

        for (int i = 0; i < 5; i++) begin
            logic [3:0] bi;
            bi = 4'(A_BIT[i]);
            do_event(bi, 4'(i+1), A_BV[i][0], 1'b0);
            // 检查 k 与 apply
            checks = checks + 1;
            if (ev_applied !== 1'b1)
                fail($sformatf("A 事件%0d 未产生 apply_fire", i));
            // k 在提交后一拍才更新（寄存器），这里读的是提交沿后的值
            checks = checks + 1;
            if (int'(ev_k) != A_K[i])
                fail($sformatf("A 事件%0d k=%0d 期望 %0d", i, ev_k, A_K[i]));
            else $display("      A ev%0d k=%0d  OK", i, ev_k);
        end
        checks = checks + 1;
        if (done_o !== 1'b1) fail("A 第5事件后 done 应置位");
        else $display("      A done  OK");
        checks = checks + 1;
        if (final_applied_o !== 1'b1) fail("A final_applied 应置位");
        else $display("      A final_applied  OK");
        checks = checks + 1;
        if (int'(final_k_o) != 519) fail($sformatf("A final_k=%0d 期望 519", final_k_o));
        else $display("      A final_k = 519  OK");

        // ================= 场景 B：普通路径，7 事件 =================
        $display("---- 场景B: 普通路径 (qid1, U=0, Flash F=4) ----");
        repeat (2) @(negedge clk);
        do_start(32'hB000_0002, 1'b1, 3'd0, 3'd4);
        checks = checks + 1;
        if (dut.prefix_q !== 10'd256)
            fail($sformatf("Flash F=4 后 prefix 应为 256，实为 %0d", dut.prefix_q));
        else $display("      Flash F=4 -> prefix=256  OK");

        for (int i = 0; i < 7; i++) begin
            do_event(4'(B_BIT[i]), 4'(i+1), B_BV[i][0], 1'b1);
            checks = checks + 1;
            if (ev_applied !== 1'b1)
                fail($sformatf("B 事件%0d 未产生 apply_fire", i));
            checks = checks + 1;
            if (int'(ev_k) != B_K[i])
                fail($sformatf("B 事件%0d k=%0d 期望 %0d", i, ev_k, B_K[i]));
            else $display("      B ev%0d k=%0d  OK", i, ev_k);
        end
        checks = checks + 1;
        if (done_o !== 1'b1) fail("B 第7事件后 done 应置位");
        else $display("      B done  OK");
        checks = checks + 1;
        if (int'(final_k_o) != 258) fail($sformatf("B final_k=%0d 期望 258", final_k_o));
        else $display("      B final_k = 258  OK");

        // ================= 场景 C：完成后多发事件必须被拒 =================
        $display("---- 场景C: done 后多发事件应被拒 ----");
        do_event(4'd1, 4'd8, 1'b1, 1'b1);
        checks = checks + 1;
        if (ev_applied !== 1'b0)
            fail("done 之后仍产生 apply_fire（物理写门应关闭）");
        else $display("      done 后事件被拒  OK");

        // ================= 场景 D：错 qid =================
        $display("---- 场景D: 错 qid 应被拒 ----");
        do_start(32'hC000_0003, 1'b0, 3'd0, 3'd0);
        do_event(4'd5, 4'd1, 1'b1, 1'b1);   // qid 应为 0，给 1
        checks = checks + 1;
        if (ev_applied !== 1'b0)
            fail("错 qid 仍产生 apply_fire");
        else $display("      错 qid 被拒  OK");
        checks = checks + 1;
        if (err_o !== 1'b1) fail("错 qid 应置粘滞 err");
        else $display("      err 置位  OK");

        // ================= 场景 E：重复/乱序序号 =================
        $display("---- 场景E: 乱序序号应被拒 ----");
        repeat (2) @(negedge clk);
        do_start(32'hD000_0004, 1'b0, 3'd0, 3'd0);
        do_event(4'd5, 4'd3, 1'b1, 1'b0);   // 期望 1，给 3
        checks = checks + 1;
        if (ev_applied !== 1'b0)
            fail("乱序序号仍产生 apply_fire");
        else $display("      乱序序号被拒  OK");

        // ================= 场景 F：k > 520 必须被拒 =================
        $display("---- 场景F: k>520 应被拒 ----");
        repeat (2) @(negedge clk);
        do_start(32'hE000_0005, 1'b0, 3'd5, 3'd7);   // prefix=448, U=5
        // Flash 提交（evt1）：prefix=448, k=453
        do_event(4'd0, 4'd1, 1'b0, 1'b0);
        // 阈 512（evt2）：prefix=512, k=517
        do_event(4'd9, 4'd2, 1'b1, 1'b0);
        // bit2 受（evt3）：prefix=516, k=521 > 520 -> 应被拒且 k_bad
        do_event(4'd2, 4'd3, 1'b1, 1'b0);
        checks = checks + 1;
        if (ev_kbad !== 1'b1)
            fail($sformatf("prefix=516+U=5 时 k_bad 应置位（k=%0d）", ev_k));
        else $display("      k>520 被检出 (k=%0d) OK", ev_k);
        checks = checks + 1;
        if (ev_applied !== 1'b0)
            fail("k 越界的事件不应产生 apply_fire（物理写门必须关闭）");
        else $display("      k 越界不写开关  OK");

        $display("---------------------------------------------------------------");
        $display("I2 events: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
