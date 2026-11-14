`timescale 1ns/1ps
// 复现 NO_TX c=3：第一笔事件模式转换，逐拍打印握手相关信号
module tb_notx_trace;
    logic clk = 0, rstn = 0, valid_i = 0, stall_i = 0;
    logic [8:0] c_i = 0; logic [11:0] adc2_i = 0; logic [5:0] dither_i = 0;
    logic [3:0] reserved_i = 0, h_phase_i = 0; logic [2:0] v_phase_i = 0;
    logic [23:0] tag_i = 0;
    logic in_ready, busy, ovf_o, err_o, valid_o, perr, rdy, done;
    logic [39:0] slice_ids; logic [19:0] code_o; logic [23:0] tag_o;
    logic ev_start = 0, ev_valid = 0, ev_bit = 0;
    logic [2:0] evi; logic [8:0] evk;
    logic cfg_we = 0, cfg_inv_we = 0;
    logic [4:0] cfg_slice = 0, cfg_unit = 0; logic [43:0] cfg_w = 0;
    logic [63:0] cfg_inv = 0; logic [3:0] cfg_grp = 0;

    digital_top_w dut (.clk(clk), .reset_n(rstn), .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i), .reserved_i(reserved_i),
        .h_phase_i(h_phase_i), .v_phase_i(v_phase_i), .tag_i(tag_i), .in_ready_o(in_ready),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b1), .ev_start_i(ev_start), .ev_valid_i(ev_valid), .ev_bit_i(ev_bit),
        .sar_index_i(evi), .sar_qid_i(1'b0), .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(done), .ev_idx_o(), .ev_k_o(evk), .ev_err_o(),
        .ev_proto_err_o(perr), .ev_ready_o(rdy),
        .ev_mask_valid_o(), .ev_mask_o(), .ev_ids_o(),
        .busy_o(busy), .slice_ids_o(slice_ids), .code_o(code_o),
        .overflow_o(ovf_o), .err_o(err_o), .valid_o(valid_o), .tag_o(tag_o));
    always #5 clk = ~clk;

    integer n; integer cyc = 0;
    always @(posedge clk) begin
        cyc = cyc + 1;
        if (cyc < 40)
          $display("cyc %0d | start_i=%b fire=%b rdy=%b busy=%b active=%b done=%b final=%b valid_a=%b valid_o=%b tag_o=%0d",
                   cyc, ev_start, dut.ev_start_fire, rdy, busy, dut.ev_active_q,
                   done, dut.ev_final_valid_q, dut.valid_a, valid_o, tag_o);
    end

    initial begin
        repeat (4) @(negedge clk); rstn = 1;
        @(negedge clk);
        c_i = 9'd3; adc2_i = 12'd0; tag_i = 24'd900;
        while (!rdy) @(negedge clk);
        ev_start = 1; @(negedge clk); ev_start = 0;
        for (n = 0; n < 9; n = n + 1) begin
            @(negedge clk); ev_valid = 1; ev_bit = c_i[8-n]; evi = n[3:0];
        end
        @(negedge clk); ev_valid = 0;
        repeat (8) @(posedge clk);
        $display("FINAL: valid_o=%b tag_o=%0d (期望 900)", valid_o, tag_o);
        $finish;
    end
endmodule
