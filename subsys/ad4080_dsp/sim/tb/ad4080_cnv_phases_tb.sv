`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_cnv_phases_tb.sv
// 模块名     : ad4080_cnv_phases_tb
// 版本       : v1.0
// 功能       : 真 SPI 布防后，逐 CNV 检查原 DSP 的 GPIO、原 FIFO 写、阈值和 W1C。
// 设计说明   : 核心域标记代表已同步的真实 CNV 上升沿。正常用例为原码、校正和
//              滤波留足时；故障用例有意提前、漏送或压缩间隔，不注入内部数据。
//              仅按公开图文检查沿序与真实值，物理 GPIO 建立/传播需另验。
// 变更记录   : 补充 Direct LVDS 的 FILTER_SYNC 多 CNV 保持与恢复用例。
// =============================================================================
module ad4080_cnv_phases_tb;
    // =========================================================================
    // 1. 同域 CNV、实际 SPI 引脚和观测资格
    // =========================================================================
    logic clk = 0, rst_n = 0;
    logic cs_n = 1, sclk = 1, sdi = 0;           // 配置写只由实际 SPI 接受。
    logic input_valid = 0, cnv_tick_i = 0;       // 原码与消费它的 CNV 同沿采入。
    logic pin_mode = 0, cnv_pin = 0;             // 原始针脚试验经过与 GPIO 匹配的两级同步。
    logic cnv_meta, cnv_sync, cnv_previous;
    wire pin_tick = cnv_sync && !cnv_previous;
    wire effective_tick = pin_mode ? pin_tick : cnv_tick_i;
    logic conversion_timing_error_i = 0;         // 外部间隔监测同步脉冲，独立于错拍诊断。
    logic [19:0] x = 0;                          // 补码数值，OFFSET=0、GAIN=0x200。
    logic [3:0] gpio_i = 4'hf;                   // 拍点测试的 SYNC 由 GPIO1 本身路由。
    logic rd_en = 0;
    wire sdo, sdo_oe, dout_valid, cfg_reject, cnv_data_timing_error_o;
    wire [19:0] dout;
    wire [3:0] gpio_o, gpio_oe, cfg_reject_reason;
    wire [7:0] status;
    int writes, rejects, direct_words, pin_edges;
    int checks = 0;                             // 整次回归的实际断言数，跨工况复位保留。
    logic [1:0] pin_sync_seen;
    logic [3:0] reason;
    always #5 clk = ~clk;

    ad4080_top #(
        .FIFO_DEPTH(16), .P_OFFSET_LSB_ADDR(37), .P_MULTIBYTE_ORDER(0), .P_WRITE_SDO_DRIVE(0),
        .P_CNV_TIMING_BOUND(1'b1), .P_CNV_ERROR_BOUND(1'b1)
    ) dut (
    // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
    .pwr_cfg_full_allow_i(1'b1), .pwr_cmd_ready_i(1'b0), .pwr_rsp_valid_i(1'b0),
    .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
    .pwr_ldo_pd_i(2'b00), .rom_crc_error_i(1'b0),

        .clk, .rst_n, .cs_n, .sclk, .sdi, .sdo, .sdo_oe,
        .interface_ready(1'b1), .adc_idle(1'b1), .valid_in(input_valid && (!pin_mode || pin_tick)), .x,
        .filter_sync(1'b0), .ext_event(1'b0), .arm(1'b0), .rd_en, .gpio_i, .gpio_o, .gpio_oe,
        .dout, .dout_valid, .status, .correction_clip_low(), .correction_clip_high(),
        .cfg_reject, .cfg_reject_reason, .cnv_tick_i(effective_tick), .conversion_timing_error_i,
        .phy_fifo_full_i(1'b0), .phy_fifo_read_done_i(1'b0),
        .csr_fifo_full_i(1'b0), .csr_fifo_read_done_i(1'b0),
        .data_config_ready_i(1'b0), .sample_tag_i(32'd0), .por_ana_event_i(1'b0),
        .data_intf_config_a_o(), .data_intf_config_b_o(), .data_intf_config_c_o(),
        .continuous_bypass_o(), .continuous_enable_o(), .continuous_flush_o(), .filter_config_o(), .continuous_word_valid_o(), .continuous_word_o(), .continuous_word_tag_o(),
        .pwr_cmd_valid_o(), .pwr_cmd_op_o(), .pwr_cmd_value_o(),
        .cnv_data_timing_error_o,.processing_reject()
    );

    // 这三只寄存器与实际 GPIO 两级同步的输出配对。第三只只保存上次状态
    // 用于判沿，不让 E1 的 GPIO 状态错移到下一笔。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnv_meta <= 1'b0;
            cnv_sync <= 1'b0;
            cnv_previous <= 1'b0;
        end else begin
            cnv_meta <= cnv_pin;
            cnv_sync <= cnv_meta;
            cnv_previous <= cnv_sync;
        end
    end

    task automatic check(input bit good, input string detail);
        checks++;
        if (!good) $fatal(1,"%s at %0t",detail,$time);
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin writes=0; rejects=0; reason=0; direct_words=0; pin_edges=0; pin_sync_seen=0; end
        else begin
            // 数的是原捕获的真实写沿。状态和 GPIO 独立在沿后校核。
            if (dut.u_capture.write_fire) writes++;
            if (cfg_reject) begin rejects++; reason=cfg_reject_reason; end
            if (pin_mode && pin_tick) begin
                if (pin_edges < 2) pin_sync_seen[pin_edges] = dut.pin_sync;
                pin_edges++;
            end
            #1; if (dout_valid && dut.fifo_mode==0) direct_words++;
        end
    end

    // =========================================================================
    // 2. 配置实体及带数据的 CNV 沿
    // =========================================================================
    task automatic send_byte(input logic [7:0] value);
        // SDI 提前稳定，配置 SPI 的两级同步器有完整核心间隔。
        for (int k=7; k>=0; k--) begin
            sdi=value[k];
            #80; sclk=0;
            #80; sclk=1;
            #80;
        end
    endtask

    task automatic write_csr(input logic [14:0] address, input int length, input logic [15:0] value);
        @(negedge clk);
        cs_n=0;
        repeat (8) @(negedge clk);
        send_byte({1'b0,address[14:8]});
        send_byte(address[7:0]);
        if (length == 2) send_byte(value[15:8]); // 严格降序从多字实体的高地址写。
        send_byte(value[7:0]);
        cs_n=1;
        repeat (8) @(negedge clk);
    endtask

    task automatic reset_top;
        @(negedge clk);
        rst_n=0;
        cs_n=1; sclk=1; cnv_tick_i=0; input_valid=0; conversion_timing_error_i=0; gpio_i=4'hf;
        pin_mode=0; cnv_pin=0;
        repeat (6) @(negedge clk);
        rst_n=1;
        repeat (5) @(negedge clk);
    endtask

    task automatic drive_tick(input bit has_word, input int code, input int gap);
        // E_n 的原码在核心采样沿之前满足 setup。gap=0 专用于故意让数值来不及。
        @(negedge clk);
        input_valid=has_word; x=20'(code);
        if (pin_mode) begin cnv_pin=1; wait (pin_tick); end
        else cnv_tick_i=1;
        @(posedge clk); #2;
        @(negedge clk);
        cnv_tick_i=0; cnv_pin=0; input_valid=0;
        repeat (gap) @(negedge clk);
    endtask

    task automatic raw_sync_start(input time setup_ns, input time release_ns);
        // p.68 FIFO 正文的原始脚相对 CNV 的两项距离独立验证，较晚相位使
        // 5 ns 释放的 GPIO 和 E2 实际落到同一第一同步采样沿上。
        @(negedge clk); #6ns;
        pin_mode=1; gpio_i[1]=0;
        #(setup_ns) cnv_pin=1;
        #20ns; cnv_pin=0;
        #(60ns-release_ns) gpio_i[1]=1;
        #(release_ns) cnv_pin=1;
        #20ns; cnv_pin=0;
        repeat (7) @(negedge clk);
        check(pin_edges==2 && pin_sync_seen==2'b01 && !cnv_data_timing_error_o,
              "p68 raw GPIO setup/release must sample low E1 and high E2 without moving E1");
    endtask

    task automatic filtered_start;
        @(negedge clk); gpio_i[1]=0;
        repeat (5) @(negedge clk);  // 实际 GPIO 同步时序，与 CSR 清流分开。
        drive_tick(0,0,0);         // E1
        gpio_i[1]=1;
        repeat (5) @(negedge clk);
    endtask

    task automatic cfg_filter(input logic [7:0] mode);
        write_csr(15'h0019,1,16'h000d);  // GPIO1 作为 SYNC，GPIO0/2/3 给外部观察。
        write_csr(15'h001a,1,16'h0083);  // GPIO0 RDY、GPIO1 FILTER_SYNC。
        write_csr(15'h001b,1,16'h0061);  // GPIO2 FULL、GPIO3 ALERT。
        write_csr(15'h0029,1,{8'h0,mode});
        check(gpio_oe[0] && gpio_o[0] && rejects==0,"SEL=3 must drive inactive-high RDY");
    endtask

    task automatic read_sample(input int expected);
        @(negedge clk); rd_en=1;
        @(posedge clk); #2;
        check(dout_valid && $signed(dout)==20'(expected),"stored ADC sample read value");
        @(negedge clk); rd_en=0;
    endtask

    // =========================================================================
    // 3. D=2 FIFO、D=4 直接读取、真实比较及错拍退场
    // =========================================================================
    task automatic run_fig99(input time raw_setup_ns, input time raw_release_ns);
        reset_top(); cfg_filter(8'h01);
        write_csr(15'h001e,2,16'd3);
        write_csr(15'h001c,1,16'd1);
        raw_sync_start(raw_setup_ns,raw_release_ns);  // E1/E2 直接来自实际两针脚。
        drive_tick(1,6,6); drive_tick(1,10,6);        // E3/E4：第一笔平均 = 8。
        check(gpio_o[0] && writes==0,"E4 incomplete first output must never assert RDY/write");
        drive_tick(1,14,6);                          // E5：仅公布、尚未写。
        check(!gpio_o[0] && writes==0 && !gpio_o[2],"Fig99 E5 RDY only");
        // CNV 真停，写和 GPIO 高低状态均保持，恢复后依旧只在原定 E6 提交一次。
        repeat (20) @(negedge clk);
        check(!gpio_o[0] && writes==0,"stopped CNV must keep pending sample and RDY");
        drive_tick(1,18,6);
        check(gpio_o[0] && writes==1,"Fig99 E6 first actual write");
        drive_tick(1,22,6);
        check(!gpio_o[0] && writes==1,"Fig99 E7 RDY second");
        drive_tick(1,26,6);
        check(gpio_o[0] && writes==2,"Fig99 E8 second actual write");
        // 末组的实际码已全部交付。E9/E10 只消费旧两级队列，无新假样本。
        drive_tick(0,0,6);
        check(!gpio_o[0] && writes==2 && !gpio_o[2],"Fig99 E9 RDY third, FULL remains low");
        @(negedge clk); cnv_pin=1; wait (pin_tick);
        @(posedge clk); #2;
        check(gpio_o[0] && writes==3 && gpio_o[2] && status[7] && !cnv_data_timing_error_o,
              "Fig99 E10 same-edge last actual write and GPIO/CSR FULL");
        @(negedge clk); cnv_pin=0;
        read_sample(8); read_sample(16); read_sample(24);
        $display("PASS FIG99 CNV RDY=5,7,9 WRITE=6,8,10 FULL=10 data=8,16,24");
        $display("PASS FIG99 raw GPIO_SYNC lead_setup=%0d ns lead_release=%0d ns sampled E1/E2=10",raw_setup_ns,raw_release_ns);
    endtask

    task automatic run_fig97;
        reset_top(); cfg_filter(8'h09); filtered_start();
        drive_tick(0,0,6);
        for (int k=1; k<=4; k++) drive_tick(1,k*8,6);  // E3..E6 成组，均值 20。
        // E7 公布，并以新的下半组值启动下一笔。GPIO 低宽随 CNV 停走。
        @(negedge clk); cnv_tick_i=1; input_valid=1; x=20'd40;
        @(posedge clk); #2;
        check(!gpio_o[0] && dout_valid && $signed(dout)==20'sd20,"Fig97 E7 selected result");
        @(negedge clk); cnv_tick_i=0; input_valid=0;
        repeat (20) @(negedge clk);
        check(!gpio_o[0] && !dout_valid && dout==20'd20,"direct word held while CNV paused, not republished");
        drive_tick(1,48,6); check(!gpio_o[0],"Fig97 E8 RDY low");
        drive_tick(1,56,6); check(!gpio_o[0],"Fig97 E9 RDY low");
        drive_tick(1,64,6); check(gpio_o[0] && dout==20'd20,"Fig97 E10 RDY high, previous word held");
        @(negedge clk); cnv_tick_i=1;
        @(posedge clk); #2;
        check(!gpio_o[0] && dout_valid && $signed(dout)==20'sd52,"Fig97 E11 second real word");
        @(negedge clk); cnv_tick_i=0;
        $display("PASS FIG97 D4 GPIO RDY low=E7,E8,E9 high=E10 next=E11 pause holds actual word");

        // p.67 Direct LVDS 允许同值写 0x29 单独重新起批。上一批真字保持，
        // 但 RDY 和数值有效撤回。这里 GPIO1 一直为高，不提供第二次 GPIO SYNC。
        write_csr(15'h0029,1,16'h0009);
        check(gpio_i[1] && gpio_o[0] && !dout_valid && dout==20'd52 && direct_words==2,
              "same-value direct 0x29 clears old validity while retaining previous data");
        drive_tick(0,0,6); drive_tick(0,0,6);  // 0x29 已提交后的 E1、E2。
        for (int k=0; k<4; k++) begin
            drive_tick(1,80+8*k,6);
            check(gpio_o[0] && dout==20'd52 && direct_words==2,"no stale output in new D4 window");
        end
        drive_tick(0,0,6);
        check(!gpio_o[0] && dout==20'd92 && direct_words==3 && !cnv_data_timing_error_o,
              "direct write-only restart publishes new D4 data at its own E7");
        $display("PASS FIG97 same 0x29 direct restart without GPIO, retained=52 next_E7=92");
    endtask

    task automatic run_direct_sync_hold;
        reset_top(); cfg_filter(8'h01);
        // Rev. B 表 25：低有效 SYNC 持续时应保持滤波复位。直读在图 98
        // 的同步释放后从最新一段原码重算，复位期间的完成码不得进入平均值。
        @(negedge clk); gpio_i[1]=0;
        repeat (5) @(negedge clk);
        drive_tick(0,0,6);
        for (int edge_no=2; edge_no<=3; edge_no++) begin
            drive_tick(1,4096,6);
            $display("DIRECT_SYNC_HOLD low_edge=%0d rdy_n=%0b fault=%0b results=%0d",
                     edge_no,gpio_o[0],cnv_data_timing_error_o,direct_words);
            check(gpio_o[0] && !cnv_data_timing_error_o && direct_words==0,
                  "Direct LVDS FILTER_SYNC held low must hold reset across CNVs without fault");
        end
        gpio_i[1]=1;
        repeat (5) @(negedge clk);
        drive_tick(0,0,6);
        drive_tick(1,8,6); drive_tick(1,16,6);
        check(gpio_o[0] && direct_words==0,"only the new pair is collected after SYNC releases");
        drive_tick(0,0,6);
        check(!gpio_o[0] && dout==20'd12 && direct_words==1 && !cnv_data_timing_error_o,
              "Direct LVDS publishes the first clean SINC1 result after multi-CNV SYNC");
        $display("PASS Direct LVDS FILTER_SYNC held for three CNVs then clean result 12");
    endtask

    task automatic run_fig83;
        reset_top();
        write_csr(15'h0019,1,16'h000d);
        write_csr(15'h001a,1,16'h0084);  // HI_DTCT 直接引脚，不由 W1C 截短。
        write_csr(15'h001b,1,16'h0065);  // LO_DTCT 和低有效 ALERT。
        write_csr(15'h0020,2,16'd1);     // HYST=1，释放等号保持。
        write_csr(15'h0022,2,16'h0004); write_csr(15'h0024,2,16'h0ffc);
        write_csr(15'h001c,1,16'h00e0);  // 两阈路由、旁路、无 CNV 错误路由。
        drive_tick(0,0,6);              // E1 模拟越阈，交付会在 E2。
        drive_tick(1,1024,6);           // E2：首字恰等于高阈值。
        check(gpio_o[3] && !status[5],"Fig83 E2 still pending real data comparison");
        drive_tick(1,768,6);            // E3：真实 E1 的结果在 E+2 断言。
        check(!gpio_o[3] && gpio_o[0] && status[5],"Fig83 E3 HI/ALERT with exact tie");
        drive_tick(1,512,6);
        check(!gpio_o[3],"at threshold minus hysteresis, HI remains asserted");
        drive_tick(1,-1024,6);
        check(gpio_o[3] && !gpio_o[0] && status[5],"ALERT recovers, HI flag remains sticky");
        drive_tick(1,-512,6);
        check(!gpio_o[3] && gpio_o[2] && status[4],"LO uses true selected sample and signed tie");
        drive_tick(1,1024,6);
        check(gpio_o[3] && status[4],"LO level releases, sticky remains");
        // SPI 已收齐 W1C 的真实实体后，下一核心沿给出 hi_clr。把公布沿放到
        // 事件检测器消费清除脉冲的同一沿，电平变化和触发保留，sticky 清除优先。
        fork
            write_csr(15'h0014,1,16'h0020);
            begin
                wait (dut.hi_clr);
                @(negedge clk); cnv_tick_i=1; input_valid=1; x=20'd0;
                @(posedge clk); #2;
                check(!status[5] && status[4] && gpio_o[0] && !gpio_o[3] && dut.hi_pulse,
                      "HI W1C wins sticky only on real crossing CNV");
                @(negedge clk); cnv_tick_i=0; input_valid=0;
            end
        join
        $display("PASS FIG83 real CNV +2 assertion and release, signed HI/LO ties and HI W1C collision");
    endtask

    task automatic run_event_capture;
        reset_top(); cfg_filter(8'h01);
        write_csr(15'h0022,2,16'h0004);
        write_csr(15'h001e,2,16'd1);
        write_csr(15'h001c,1,16'h00c2);
        filtered_start(); drive_tick(0,0,6);
        drive_tick(1,1024,6); drive_tick(1,1024,6);
        drive_tick(1,1280,6);
        check(!gpio_o[3] && status[5] && writes==0,"true trigger word already asserts ALERT at E5");
        drive_tick(1,1280,6);
        // E6 的写入资格在比较时钟中未被提前打开，触发原码不会计入事件后 W=1。
        check(writes==0 && dut.u_capture.event_seen,"triggering word must not enter post-event mode 10");
        drive_tick(0,0,6); drive_tick(0,0,6);
        check(writes==1 && gpio_o[2],"only the actual next selected code completes W=1");
        read_sample(1280);
        $display("PASS timed HI captures following word and preserves triggering GPIO phase");
    endtask

    task automatic run_clock_error;
        reset_top();
        write_csr(15'h0019,1,16'h0009); write_csr(15'h001b,1,16'h0060);
        @(negedge clk); conversion_timing_error_i=1;
        @(negedge clk); conversion_timing_error_i=0;
        check(!status[2] && gpio_o[3],"masked ADC_CNV_ERR cannot assert ALERT");
        write_csr(15'h001c,1,16'h0010);
        check(status[2] && !gpio_o[3],"route stored conversion error into CSR and alert GPIO");
        // 新错误和原 CSR W1C 同沿。与 HI/LO 不同，新短间隔必须留待观察。
        fork
            write_csr(15'h0014,1,16'h0004);
            begin
                wait (dut.we && dut.addr==16'h0014);
                @(negedge clk); conversion_timing_error_i=1;
                @(negedge clk); conversion_timing_error_i=0;
            end
        join
        check(status[2] && !gpio_o[3],"new ADC_CNV_ERR survives same-edge W1C");
        write_csr(15'h0014,1,16'h0004);
        check(!status[2] && gpio_o[3],"standalone ADC_CNV_ERR W1C clears CSR/GPIO");
        $display("PASS ADC_CNV_ERR synchronous event mask route sticky W1C event priority");
    endtask

    task automatic run_bad_timing;
        reset_top(); cfg_filter(8'h01); filtered_start();
        drive_tick(1,6,6); check(cnv_data_timing_error_o && gpio_o[0],"E2 early raw refused");
        reset_top(); cfg_filter(8'h01); filtered_start();
        drive_tick(0,0,6); drive_tick(1,6,6); drive_tick(0,0,6); drive_tick(1,10,6);
        check(cnv_data_timing_error_o && gpio_o[0] && writes==0,"missing group raw and late data cannot assert RDY");
        reset_top(); cfg_filter(8'h01); filtered_start();
        drive_tick(0,0,6); drive_tick(1,6,6); drive_tick(1,10,0); drive_tick(0,0,6);
        check(cnv_data_timing_error_o && gpio_o[0] && writes==0,"late real number rejected at E5 deadline");
        // 物理同步恢复是新 E1，不复用故障批中的输入。
        filtered_start();
        drive_tick(0,0,6); drive_tick(1,6,6); drive_tick(1,10,6); drive_tick(0,0,6);
        check(!cnv_data_timing_error_o && !gpio_o[0] && dout==20'd8,"new sync recovers exact pair");
        write_csr(15'h0029,1,16'h000b);
        check(rejects==0 && dut.filter_sel==3,"PF1 selects without any caller coefficient parameters");
        filtered_start(); drive_tick(0,0,6); drive_tick(0,0,6);
        check(cnv_data_timing_error_o && gpio_o[0],"PF1 missing initial raw fails closed");
        $display("PASS early/missing/late raw, late number, sync restart and PF1 missing first input");
    endtask

    task automatic run_sinc_rate(input int mode, input int code, input int decimation);
        int first_due, second_due, first_group, order, seen, out_rate, delay2;
        first_group=(mode==3) ? 35 : (mode==2 ? 5 : 1);
        order=(mode==1) ? 1 : 5;
        out_rate=(mode==3) ? 2*decimation : decimation;
        first_due=first_group*out_rate+(mode==3 ? 12 : (mode==2 ? 6 : 3));
        second_due=first_due+out_rate;
        seen=0;
        reset_top(); cfg_filter(8'((code<<3)|mode)); filtered_start(); drive_tick(0,0,10);
        // 斜坡 x[j]=800+2j。秩为 m 的 N 点矩形归一卷积均值位置后移
        // m(N−1)/2 个输入位置，因此第 k 组末码为 800+2kN−m(N−1)。
        // PF1 再平移 (TAPS−1)/2 个 CIC 字。对称单位 DC 增益不改变斜坡的斜率。
        // 非恒定输入可检出误将前一组或后一组伪作 p.69 指定公布拍。
        delay2=order*(decimation-1);
        if (mode==3) delay2=delay2+(dut.u_chain.u_comp.PMODE_COMP_TAPS-1)*decimation;
        for (int e=3; e<=second_due; e++) begin
            drive_tick(1,800+2*(e-2),10);
            if (mode==3 && e==first_due-1) begin
                // 手册的计数只限定公布拍，核心空拍不能补做缺失的 CNV。
                repeat (30) @(negedge clk);
                check(direct_words==0 && gpio_o[0],"PF1 staged true word cannot publish while CNV paused");
            end
            if (e==first_due || e==second_due) begin
                seen++;
                check($signed(dout)==20'(800+2*(seen+first_group-1)*out_rate-delay2),
                      "real selected group matches complete ramp window");
            end
            // 输出保持、RDY 引脚以及数值有效计数分别检查。单靠 data 相等
            // 会漏掉提前/重复发同一字的问题。
            check(!cnv_data_timing_error_o && direct_words==seen &&
                  gpio_o[0]==(e<first_due || e==second_due-1),"no early RDY and exact recurring window");
        end
        $display("PASS filter mode=%0d CNV code=%0d N=%0d actual group%0d/%0d RDY E%0d/E%0d",
                 mode,code,decimation,first_group,first_group+1,first_due,second_due);
    endtask

    task automatic run_pf1_record;
        // 使用原 FIFO 写口验证 p.69 之外的后一拍提交。不把内部直接读
        // 公布当成存储完成，且要求尾部空原码期间不生成伪输入。
        reset_top(); cfg_filter(8'h03);
        write_csr(15'h001e,2,16'd1); write_csr(15'h001c,1,16'd1);
        filtered_start(); drive_tick(0,0,10);
        for (int j=1; j<=140; j++) drive_tick(1,900+2*j,10);
        // 末组 SINC5 k70 已喂完。再无新增原码，只允许真实待提交的完整字
        // 经 p.69 定相公布 E152，再于下一 CNV E153 经原 capture 实写。
        for (int e=143; e<=151; e++) begin
            drive_tick(0,0,10);
            check(gpio_o[0] && writes==0,"PF1 record cannot consume any startup code");
        end
        drive_tick(0,0,10);
        check(!gpio_o[0] && writes==0,"PF1 E152 true ready before storage");
        drive_tick(0,0,10);
        check(writes==1 && status[7] && gpio_o[2] && !cnv_data_timing_error_o,"PF1 E153 true word and FULL");
        read_sample(900+2*140-5-(dut.u_chain.u_comp.PMODE_COMP_TAPS-1)*2);
        $display("PASS PF1 N2 W1 actual final stored true word RDY=E152 WRITE/FULL=E153");
    endtask

    task automatic run_pf1_switch;
        reset_top(); cfg_filter(8'h03); filtered_start(); drive_tick(0,0,10);
        // N=2 的第 35 个补偿字进入延迟槽，但还未到 E152。暂停并换档必须
        // 清除该真字及其标签，不能因其已计算而在旧公布沿出现。
        for (int j=1; j<=140; j++) drive_tick(1,900+2*j,10);
        drive_tick(0,0,10);  // E143 截止采纳旧字。
        repeat (30) @(negedge clk);
        check(direct_words==0 && gpio_o[0],"PF1 staged old word not yet published");
        write_csr(15'h0029,1,16'h000b);  // Direct PF1 N=4，新一组系数和历史。
        drive_tick(0,0,10); drive_tick(0,0,10); // 写后第一真实 CNV 为 E1，不用 GPIO。
        for (int j=1; j<=280; j++) begin
            drive_tick(1,-2000+2*j,10);
            check(gpio_o[0] && direct_words==0,"PF1 old staged result did not survive rate write");
        end
        for (int e=283; e<292; e++) begin
            drive_tick(0,0,10);
            check(direct_words==0 && gpio_o[0],"new rate cannot publish before its own p69 boundary");
        end
        drive_tick(0,0,10);
        check(direct_words==1 && !gpio_o[0] &&
              $signed(dout)==20'(-2000+2*280-15-(dut.u_chain.u_comp.PMODE_COMP_TAPS-1)*4),
              "PF1 resync publishes only the new fully formed word");
        $display("PASS PF1 paused old token flushed, direct 0x29 restart publishes first true word E292");
    endtask

    task automatic run_fifo_needs_sync;
        reset_top(); cfg_filter(8'h03);
        write_csr(15'h001e,2,16'd1); write_csr(15'h001c,1,16'd1);
        // 0x29 先提供了直读重启资格，随后启用 FIFO 应清除此资格。没有实际
        // GPIO SYNC 的 CNV 不能被误作滤波捕获的 E1。
        drive_tick(0,0,6); drive_tick(0,0,6); drive_tick(1,64,6);
        check(cnv_data_timing_error_o && writes==0 && gpio_o[0] && !status[7],
              "PF1 FIFO rearm requires physical FILTER_SYNC");
        $display("PASS PF1 FIFO write-only restart rejected, real FILTER_SYNC still required");
    endtask

    initial begin
        run_fig99(15ns,5ns); run_fig99(20ns,10ns); run_fig97(); run_direct_sync_hold(); run_fig83();
        run_event_capture(); run_clock_error(); run_bad_timing();
        for (int code=0; code<10; code++) run_sinc_rate(1,code,2<<code);
        run_sinc_rate(1,15,1024);
        for (int code=0; code<8; code++) begin
            run_sinc_rate(2,code,2<<code);
            run_sinc_rate(3,code,2<<code);
        end
        run_sinc_rate(2,15,256);
        run_sinc_rate(3,15,256);
        run_pf1_record(); run_pf1_switch(); run_fifo_needs_sync();
        $display("AD4080_CNV_PHASES_COMPLETE checks=%0d",checks);
        $finish;
    end
    initial begin #15000000; $fatal(1,"CNV phase watchdog"); end
endmodule
`default_nettype wire
