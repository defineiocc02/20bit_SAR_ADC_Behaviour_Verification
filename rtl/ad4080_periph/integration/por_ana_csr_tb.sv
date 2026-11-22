`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : por_ana_csr_tb.sv
// 模块名     : por_ana_csr_tb
// 版本       : v1.0
// 功能       : 对实际 ad4080_regs 验收 POR_ANA_FLAG 和 W1C 同沿优先关系。
// 设计说明   : 两实例只在事件绑定参数上不同，其他写入和事件沿完全相同。
//              地址和写长度取真正 CSR 提交端。产品 SPI 位级另由集成层验收。
// 变更记录   : 初始版本，新增能力开关、硬/软复位、W1C 与 ROM 位独立。
// =============================================================================
module por_ana_csr_tb;
    logic clk = 1'b0, rst_n = 1'b0;
    logic [15:0] addr = 16'h0014, wdata = '0;
    logic [1:0] write_bytes = 2'd1;
    logic we = 1'b0, por_ana_event_i = 1'b0, rom_crc_error_i = 1'b0;
    logic [1:0][7:0] status;
    logic [1:0][15:0] rdata, status_long;

    always #5ns clk = ~clk;

    // 只比较两个真实寄存器节点。参数为零时即使输入有事件也保留原行为。
    for (genvar view = 0; view < 2; view++) begin : g_bank
        ad4080_regs #(.P_POR_ANA_EVENT_ENABLE(view == 1), .P_ROM_CRC_ERROR_BOUND(view == 1)) dut (
        // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
        .pwr_cfg_full_allow_i(1'b1), .pwr_cmd_ready_i(1'b0), .pwr_cmd_valid_o(),
        .pwr_cmd_op_o(), .pwr_cmd_value_o(), .pwr_rsp_valid_i(1'b0),
        .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
        .pwr_ldo_pd_i(2'b00), .rom_crc_error_i,

            .clk, .rst_n, .addr, .wdata, .write_bytes, .we, .por_ana_event_i,
            .adc_idle(1'b1), .data_config_ready_i(1'b0), .gpio_levels(4'b0),
            .capture_active(1'b0), .capture_incomplete(1'b0), .capture_io_active(1'b0),
            .st_fifo_full(1'b0), .st_fifo_read_done(1'b0), .st_hi_flag(1'b0), .st_lo_flag(1'b0),
            .conversion_timing_error_i(1'b0), .crc_err_pulse(1'b0), .clk_cnt_err_pulse(1'b0),
            .partial_err_pulse(1'b0), .addr_err_pulse(1'b0), .readonly_err_pulse(1'b0), .not_ready_err_pulse(1'b0),
            .wm_wb_valid(1'b0), .wm_wb_data(15'b0),
            .status(status[view]), .rdata(rdata[view]), .status_long(status_long[view])
        );
    end

    // 读回与长指令状态都来自同一个 sticky。ROM 从独立只读输入取得。
    task automatic expect_flags(input logic [1:0] expected);
        if ({status[1][3], status[0][3]} !== expected ||
            {rdata[1][3], rdata[0][3]} !== expected ||
            {status_long[1][7], status_long[0][7]} !== expected ||
            {status[1][1], status[0][1]} !== {rst_n && rom_crc_error_i, 1'b0} ||
            {status_long[1][8], status_long[0][8]} !== {rst_n && rom_crc_error_i, 1'b0})
            $fatal(1, "POR_ANA or ROM bit mismatch enabled=%h disabled=%h expected=%b", status[1], status[0], expected);
    endtask

    // 在实际寄存器采纳沿呈现事件和写入，不能用连续两次事务代替同沿测试。
    task automatic commit(
        input logic [15:0] address, value, input logic [1:0] size,
        input bit write_enable, reference_event
    );
        @(negedge clk);
        addr = address;
        wdata = value;
        write_bytes = size;
        we = write_enable;
        por_ana_event_i = reference_event;
        @(posedge clk);
        #2ps;
        we = 1'b0;
        por_ana_event_i = 1'b0;
        addr = 16'h0014;
        #2ps;
    endtask

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1'b1;
        #2ps;
        expect_flags(2'b11);
        // 非法实体长度与写零均不能清 W1C，之后用合法的 byte 清除两实例。
        commit(16'h0014, 16'h0008, 2'd2, 1'b1, 1'b0);
        expect_flags(2'b11);
        commit(16'h0014, 16'h0000, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b11);
        commit(16'h0014, 16'h0008, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b00); // 两种绑定均通过原有清位路径。
        commit(16'h0014, 16'h0000, 2'd1, 1'b0, 1'b1);
        expect_flags(2'b10);
        // 新故障与 W1C 同沿胜出。没有新故障后，下一次 W1C 应能真正清掉。
        commit(16'h0014, 16'h0008, 2'd1, 1'b1, 1'b1);
        expect_flags(2'b10);
        commit(16'h0014, 16'h0008, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b00);
        $display("PASS POR_ANA_CSR default disabled, real event, same-edge W1C priority, later clear, ROM bit stays 0");

        // 此输入激励只检查适配口。写 bit1 不改变已报告的独立 ROM 故障。
        rom_crc_error_i = 1'b1;
        commit(16'h0014, 16'h0002, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b00);
        rom_crc_error_i = 1'b0;
        #2ps;
        expect_flags(2'b00);
        $display("PASS external ROM level, bit1 writes have no effect, independent of POR");

        // 按手册两次写触发软件复位，也不改变新事件的硬复位初值语义。
        commit(16'h0000, 16'h0081, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b00);
        commit(16'h0000, 16'h0000, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b11);
        commit(16'h0014, 16'h0008, 2'd1, 1'b1, 1'b0);
        expect_flags(2'b00);
        #1ns; // 在时钟沿之间验证硬复位使 bit3 恢复 1。
        rst_n = 1'b0;
        #2ps;
        expect_flags(2'b11);
        $display("PASS POR_ANA_CSR software and hard POR defaults at 0x14 and long status");
        $display("POR_ANA_CSR PASS");
        $finish;
    end
endmodule

`default_nettype wire
