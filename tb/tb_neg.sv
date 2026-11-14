`timescale 1ns/1ps
// 8 类负向场景，独立复现；每场景报告 acc/comp/abort/canc/rej/pend
module tb_neg;
    logic clk=0, rstn=0, valid_i=0, stall_i=0;
    logic [8:0] c_i=0; logic [11:0] adc2_i=0; logic [5:0] dither_i=0;
    logic [3:0] reserved_i=0, h_phase_i=0; logic [2:0] v_phase_i=0;
    logic [23:0] tag_i=0;
    logic in_ready, busy, ovf_o, err_o, valid_o, perr, rdy, abort_o, done;
    logic [23:0] abort_tag;
    logic [39:0] slice_ids; logic [19:0] code_o; logic [23:0] tag_o;
    logic ev_start=0, ev_valid=0, ev_bit=0; logic [3:0] ev_idx=0; logic ev_qid=0;
    logic evk; logic [8:0] kev;
    logic cfg_we=0, cfg_inv_we=0; logic [4:0] cfg_slice=0, cfg_unit=0;
    logic [43:0] cfg_w=0; logic [63:0] cfg_inv=0; logic [3:0] cfg_grp=0;

    digital_top_w dut(.clk(clk), .reset_n(rstn), .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i), .reserved_i(reserved_i),
        .h_phase_i(h_phase_i), .v_phase_i(v_phase_i), .tag_i(tag_i), .in_ready_o(in_ready),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b1), .ev_start_i(ev_start), .ev_valid_i(ev_valid), .ev_bit_i(ev_bit),
        .sar_index_i(ev_idx), .sar_qid_i(ev_qid), .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(done), .ev_idx_o(), .ev_k_o(kev), .ev_err_o(),
        .ev_proto_err_o(perr), .ev_ready_o(rdy), .ev_abort_o(abort_o),
        .ev_abort_tag_o(abort_tag),
        .ev_mask_valid_o(), .ev_mask_o(), .ev_ids_o(),
        .busy_o(busy), .slice_ids_o(slice_ids), .code_o(code_o),
        .overflow_o(ovf_o), .err_o(err_o), .valid_o(valid_o), .tag_o(tag_o));
    always #5 clk = ~clk;

    integer n_comp=0, n_abort=0, n_canc=0;
    always @(posedge clk) begin #1;
        if (valid_o) n_comp = n_comp + 1;
        if (abort_o) n_abort = n_abort + 1;
    end

    integer fails = 0;
    integer c0,a0,b0;

    // 送 9 个事件；idx_ovr>=0 时把第 idx_ovr 个事件的索引故意改错
    task automatic send_events(input [8:0] c, input [2:0] up, input integer idx_ovr,
                              input integer badq_at, input integer stop_at);
        integer n;
        begin
            for (n = 0; n < 9; n = n + 1) begin
                if (stop_at >= 0 && n == stop_at) disable send_events;
                @(negedge clk);
                ev_valid = 1; ev_bit = c[8-n];
                ev_idx = (n == idx_ovr) ? 4'd9 : n[3:0];     // 9 = 非法索引
                ev_qid = (n >= badq_at) ? 1'b1 : 1'b0;       // 中途换 qid
            end
            @(negedge clk); ev_valid = 0; ev_idx = 0; ev_qid = 0;
        end
    endtask

    task automatic do_start(input [8:0] c, input [2:0] up, input [23:0] tg,
                            output logic ok);
        begin
            ok = 0;
            while (!rdy) @(negedge clk);
            c_i = c; tag_i = tg;                 // 下降沿驱动
            dither_i = {up[2],1'b0,up[0],3'b0};
            ev_start = 1;
            @(negedge clk);                      // 越过一个 posedge（DUT 采样沿）
            ok = dut.ev_active_q;                // 读寄存器判定
            ev_start = 0;
        end
    endtask

    task automatic drain();
        integer w;
        begin
            for (w = 0; w < 50; w = w + 1) begin
                if (!busy) begin w = 99; end else @(posedge clk);
            end
        end
    endtask

    integer rc, ra, rn;
    logic sok;
    logic [8:0] c_code60 = 9'd60;
    logic [8:0] c_code70 = 9'd70;

    initial begin
        repeat (4) @(negedge clk); rstn = 1; @(negedge clk);

        // S1 正常完成
        n_comp=0; n_abort=0; do_start(9'd100, 3'd0, 24'd1, sok); if(!sok) $display("S1 START_REJECTED"); else send_events(9'd100, 3'd0, -1, 99, -1); drain();
        $display("S1 正常        comp=%0d abort=%0d %s", n_comp, n_abort,
                 (n_comp==1 && n_abort==0) ? "PASS" : "FAIL"); if (!(n_comp==1&&n_abort==0)) fails=fails+1;

        // S2 拒绝 Start：忙时再发 start
        n_comp=0; n_abort=0;
        do_start(9'd50, 3'd0, 24'd2, sok);
        @(negedge clk); ev_start = 1; @(negedge clk);
        $display("S2 拒绝Start   rdy=%b active=%b %s", rdy, dut.ev_active_q,
                 (!dut.ev_start_fire && dut.ev_active_q) ? "PASS" : "FAIL");
        if (!(!dut.ev_start_fire && dut.ev_active_q)) fails=fails+1;
        ev_start = 0; send_events(9'd50, 3'd0, -1, 99, -1); drain();

        // S3 重复 Start：事件中途再发 start
        n_comp=0; n_abort=0;
        do_start(9'd60, 3'd0, 24'd3, sok); if(!sok) $display("S3 START_REJECTED");
        for (rc = 0; rc < 3; rc = rc + 1) begin
            @(negedge clk); ev_valid=1; ev_bit=(c_code60 >> (8-rc)) & 1; ev_idx=rc[3:0];
        end
        @(negedge clk); ev_start = 1; @(negedge clk);
        $display("S3 重复Start   fire=%b %s", dut.ev_start_fire, dut.ev_start_fire ? "FAIL" : "PASS");
        if (dut.ev_start_fire) fails=fails+1;
        ev_start = 0; ev_valid = 0; rstn = 0; @(negedge clk); rstn = 1;   // 取消在途
        n_canc = n_canc + 1; drain();

        // S4 Reset 中断：中途 reset
        n_comp=0; n_abort=0;
        do_start(9'd70, 3'd0, 24'd4, sok); if(!sok) $display("S4 START_REJECTED");
        for (rc = 0; rc < 4; rc = rc + 1) begin
            @(negedge clk); ev_valid=1; ev_bit=(c_code70 >> (8-rc)) & 1; ev_idx=rc[3:0];
        end
        @(negedge clk); ev_valid=0; rstn = 0; @(negedge clk); rstn = 1; @(negedge clk);
        $display("S4 Reset中断   active=%b busy=%b %s", dut.ev_active_q, busy,
                 (dut.ev_active_q===1'b0) ? "PASS" : "FAIL");
        if (dut.ev_active_q !== 1'b0) fails=fails+1;
        drain();

        // S5 错误 SAR 索引
        n_comp=0; n_abort=0;
        do_start(9'd80, 3'd0, 24'd5, sok); if(!sok) $display("S5 START_REJECTED");
        send_events(9'd80, 3'd0, 3, 99, -1); drain();
        $display("S5 错索引      comp=%0d abort=%0d %s", n_comp, n_abort,
                 (n_comp==0 && n_abort==1) ? "PASS" : "FAIL");
        if (!(n_comp==0 && n_abort==1)) fails=fails+1;

        // ---- S5->S6 交界 trace ----
        $display("--- S6 交界 trace ---");
        for (rc = 0; rc < 6; rc = rc + 1) begin
            @(negedge clk);
            $display("  cyc%0d start=%b rdy=%b fire=%b active=%b qid_q=%b qid_seen=%b qid_i=%b qid_bad=%b perr=%b evv=%b idx=%0d done=%b abt=%b atag=%0d",
                     rc, ev_start, rdy, dut.ev_start_fire, dut.ev_active_q,
                     dut.u_fol.qid_q, dut.u_fol.qid_seen, ev_qid,
                     (ev_qid != dut.u_fol.qid_q), dut.ev_proto_err_o, dut.ev_valid_o,
                     dut.ev_idx_o, done, abort_o, abort_tag);
        end

        // S6 错误 qid（中途换）
        n_comp=0; n_abort=0;
        do_start(9'd90, 3'd0, 24'd6, sok); if(!sok) $display("S6 START_REJECTED");
        send_events(9'd90, 3'd0, -1, 4, -1); drain();
        $display("S6 错qid       comp=%0d abort=%0d %s", n_comp, n_abort,
                 (n_comp==0 && n_abort==1) ? "PASS" : "FAIL");
        if (!(n_comp==0 && n_abort==1)) fails=fails+1;

        // S7 双入口冲突：同拍 valid_i 与 start
        n_comp=0;
        while (!rdy) @(negedge clk);
        @(negedge clk); valid_i = 1; ev_start = 1; c_i = 9'd10; tag_i = 24'd7;
        @(negedge clk);
        $display("S7 双入口      fire=%b in_ready=%b ev_mode=%b %s",
                 dut.ev_start_fire, in_ready, dut.ev_mode_q,
                 (!dut.ev_start_fire && !in_ready) ? "PASS(事件拒且整码亦不 ready)" :
                 (dut.ev_start_fire ? "FAIL(事件被接受)" : "PASS"));
        if (dut.ev_start_fire) fails=fails+1;
        valid_i = 0; ev_start = 0; drain();

        // S8 系数写入冲突：事件进行中 cfg_we
        n_comp=0;
        do_start(9'd30, 3'd0, 24'd8, sok); if(!sok) $display("S8 START_REJECTED");
        @(negedge clk); cfg_we = 1; cfg_slice = 5'd0; cfg_unit = 5'd0; cfg_w = 44'd1;
        @(negedge clk);
        $display("S8 系数冲突    busy=%b w_tab[0][0]=%0d %s", busy, dut.w_tab[0][0],
                 (busy && dut.w_tab[0][0] !== 44'd1) ? "PASS(写入被挡)" : "FAIL");
        if (!busy) fails=fails+1;
        cfg_we = 0; send_events(9'd30, 3'd0, -1, 99, -1); drain();

        $display("NEG_RESULT: %0d fails", fails);
        if (fails==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
