`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : pinned_fifo_contract_tb.sv
// 模块名     : pinned_fifo_contract_tb
// 版本       : v1.0
// 功能       : 在生产字数下验证冻结 FIFO 的有序读写和记录控制的单口独占。
// 设计说明   : 裸队列与记录控制各为一个独立试验对象，用来查证二者的接口差别。
//              商用单端口 SRAM 的口径是同一拍只读或只写。直接读写底层队列时
//              可出现不同的行为；产品在记录控制器处先完成仲裁。
// 变更记录   : 将固定 200 µs 小台架的看门狗和 16K 队列的实际遍历时间分开。
// =============================================================================
module pinned_fifo_contract_tb;
    localparam int DEPTH = 16384;
    logic clk=0, rst_n=0;
    always #5 clk=~clk;

    // --- 裸队列，普通读写与快照口是不同的接口 ---
    logic q_clear=0, q_write=0, q_read=0;
    logic [19:0] q_in=0, q_out;
    logic q_valid, q_full, q_empty;
    result_fifo #(.DATA_W(20), .DEPTH(DEPTH)) queue (
        .clk, .rst_n, .clear(q_clear), .wr_en(q_write), .wr_data(q_in),
        .rd_en(q_read), .overwrite_en(1'b0), .snapshot_rd_en(1'b0), .snapshot_addr(14'b0),
        .rd_data(q_out), .rd_valid(q_valid), .full(q_full), .empty(q_empty),
        .almost_full(), .almost_empty(), .overflow(), .underflow(), .access_reject()
    );

    // --- 通过记录控制器访问，写入只在 arm 后，读回只在 captured 之后 ---
    logic cap_arm=0, cap_read=0, cap_valid_in=0;
    logic [19:0] cap_in=0, cap_out;
    logic cap_valid, cap_done, captured, full_flag, cap_reject;
    fifo_capture #(.DATA_W(20), .DEPTH(DEPTH)) capture (
        .clk, .rst_n, .clear_capture(1'b0), .arm(cap_arm), .event_in(1'b0),
        .rd_en(cap_read), .mode(2'b01), .watermark(15'd16384),
        .valid_in(cap_valid_in), .data_in(cap_in), .rd_data(cap_out), .rd_valid(cap_valid),
        .full(), .empty(), .overflow(), .underflow(), .read_done(cap_done), .captured,
        .full_flag, .wm_wb_valid(), .wm_wb_data(), .capture_active(), .capture_incomplete(),
        .cfg_reject(cap_reject), .cfg_reject_reason()
    );
    int q_both=0, cap_writes=0, cap_reads=0;

    // 1. 数真实物理访问，避免把上游挂起的读申请当作单口的第二个事务。
    always @(posedge clk) if (rst_n) begin
        if (queue.write_fire && queue.read_fire) q_both++;
        if (capture.u_fifo.write_fire) cap_writes++;
        if (capture.u_fifo.read_fire || capture.u_fifo.snapshot_fire) cap_reads++;
        if (capture.u_fifo.write_fire && (capture.u_fifo.read_fire || capture.u_fifo.snapshot_fire))
            $fatal(1, "record_controller_issued_two_operations");
    end

    function automatic logic [19:0] word_at(input int addr);
        // 奇数乘数在 20 位环内使这段地址的样本互异，跨银行乱序不会被常量掩盖。
        return 20'((addr * 40503) ^ 20'h53a61);
    endfunction
    task automatic drive_queue(input bit wr, input bit rd, input logic [19:0] value);
        @(negedge clk); q_write=wr; q_read=rd; q_in=value;
        @(posedge clk); #1;
    endtask

    initial begin
        repeat (3) @(negedge clk);
        rst_n=1;
        // 2. 裸叶在非空且未满时可同沿退一个样本并写下一个。独立 1RW 宏并无双口承诺。
        drive_queue(1,0,20'h55555);
        drive_queue(1,1,20'habcde);
        if (!q_valid || q_out !== 20'h55555 || q_both != 1) $fatal(1,"queue_simultaneous");
        drive_queue(0,1,0);
        if (!q_empty || !q_valid || q_out !== 20'habcde) $fatal(1,"queue_after_simultaneous");
        @(negedge clk); q_clear=1; q_write=0; q_read=0;
        @(posedge clk); #1; @(negedge clk); q_clear=0;
        for (int i=0;i<DEPTH;i++) drive_queue(1,0,word_at(i));
        if (!q_full) $fatal(1,"queue_incomplete");
        for (int i=0;i<DEPTH;i++) begin
            drive_queue(0,1,0);
            if (!q_valid || q_out !== word_at(i)) $fatal(1,"queue_order addr=%0d",i);
        end
        if (!q_empty) $fatal(1,"queue_not_drained");
        drive_queue(0,0,0);

        // 3. 上游持续给反方向请求，核对控制器先写满，再依次只读；最后一字也不能丢失。
        @(negedge clk); cap_arm=1; @(posedge clk); #1;
        if (cap_reject) $fatal(1,"watermark_16384_rejected");
        for (int i=0;i<DEPTH;i++) begin
            @(negedge clk); cap_arm=0; cap_read=1; cap_valid_in=1; cap_in=word_at(i);
            @(posedge clk); #1;
            if (cap_valid || captured != (i==DEPTH-1)) $fatal(1,"capture_phase addr=%0d",i);
        end
        if (!full_flag || cap_writes!=DEPTH || cap_reads!=0) $fatal(1,"capture_full");
        // 捕获完成后继续改变功能输入，只有已冻结记录的读回可以到达存储阵列。
        for (int i=0;i<DEPTH;i++) begin
            @(negedge clk); cap_in=~word_at(i); @(posedge clk); #1;
            if (!cap_valid || cap_out !== word_at(i) || cap_done != (i==DEPTH-1))
                $fatal(1,"capture_order addr=%0d",i);
        end
        @(negedge clk); cap_valid_in=0; @(posedge clk); #1;
        if (cap_valid || cap_reads!=DEPTH || cap_writes!=DEPTH) $fatal(1,"capture_extra_transfer");
        $display("PINNED_FIFO_1RW_PASS depth=%0d queue_both=%0d captured_writes=%0d captured_reads=%0d",DEPTH,q_both,cap_writes,cap_reads);
        $finish;
    end
    // 两次完整写入和两次完整读出至少需要 4 × DEPTH 个 10 ns 周期。
    initial begin #(10*(4*DEPTH+100)); $fatal(1,"full_depth_watchdog"); end
endmodule
`default_nettype wire
