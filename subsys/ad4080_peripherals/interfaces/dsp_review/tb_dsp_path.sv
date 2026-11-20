`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : tb_dsp_path.sv
// 模块名     : tb_dsp_path
// 版本       : v1.0
// 功能       : 从配置 SPI 验证固定 DSP 的校正、滤波、事件、单口 FIFO 和结果 SPI 组帧。
// 设计说明   :
//   * 测试台独立编写，DUT 从指定 Git 对象临时导出。只驱动顶层端口，层级探针
//     仅用于区分校正输出、滤波输出、阈值事件和实际 SRAM 写入。
//   * clk 为数值处理域；主机 SPI 用确定的慢边沿让两级同步器稳定。输入码为
//     20 位补码，valid 是已知完成码入口，adc_idle 是台架排空后显式给出的资格。
//   * 软件写的 FILTER_CONFIG 和低有效 GPIO FILTER_SYNC 均可清掉在途滤波；
//     比较启动流前从 GPIO 发一个完整低脉冲。比较首码与稳态码时单独计数。
//   * BOUND=1 使用可手算的工程增益，BOUND=0 验证未绑定增益的拒绝。
//     PF1 使用现有唯一系数表。此处相位所用 CNV 和产品 CNV 发布分开验收。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 2026-11-20 增加新校正、SINC5 启动态及逐档起录的独立整式对照。
// =============================================================================
module tb_dsp_path #(
    parameter bit BOUND = 1'b1  // 只控制非单位增益的工程绑定，保持 SPI 绑定一致。
);
    // =========================================================================
    // 1. 台架接口及观察量
    // =========================================================================
    // --- 系统节拍与配置 SPI ---
    logic clk = 0;       // 10 ns 周期，给慢 SPI 保留同步后建立时间。
    logic rst_n = 0;     // 测试开始保持低，随后在 clk 下降沿解除。
    logic cs_n = 1;
    logic sclk = 1;
    logic sdi = 0;
    wire sdo, sdo_oe;
    logic adc_idle = 0;  // 仅已停新输入且流水检查清空后，才置高。

    // --- 处理输入和已完成记录读口 ---
    logic valid_in = 0;
    logic [19:0] x = 0;          // 有符号补码；与 v21 的 offset-binary 边界分开。
    logic manual_rd_en = 0;      // 独立的 W=1 启动码反例直接核对并行 FIFO 读回。
    wire rd_en;                  // 完成记录由一字握手适配器或单笔验证消费。
    logic [3:0] gpio_i = 4'hf;   // GPIO1 如选为 FILTER_SYNC，初始置高以避免误复位。
    wire [3:0] gpio_o, gpio_oe;
    wire [19:0] dout;
    wire dout_valid;
    wire [7:0] status;
    wire cfg_reject;
    wire [3:0] cfg_reject_reason;

    // --- 起录保护试验外部命令源与物理 CNV 见证 ---
    logic guard_enabled = 0, profile_valid = 1;  // 同一 DUT 先测无保护反例，再实接保护端。
    logic oscillator_run = 0, oscillator = 0, stream_claim = 0;  // 分开禁止 CNV 和误报仍在运行。
    wire cnv_pin = oscillator_run && oscillator;
    logic cnv_meta = 0, cnv_sync = 0, cnv_prev = 0;
    wire cnv_tick = cnv_sync && !cnv_prev;
    logic epoch_mark = 0, sync_req = 0;  // 同步相位经独立针脚沿登记，空拍期间不推进批次。
    wire epoch_start = epoch_mark && cnv_tick;
    logic live_input = 0;
    logic [19:0] cnv_code = 20'(-1024);  // 只由独立 CNV 模型喂入，改测试向量不改可用节拍。
    int cnv_in_epoch = 0;
    logic command = 0, frame_end = 0, frame_ok = 0, session_end = 0;
    logic [1:0] operation = 0;
    logic guarded_event = 0;
    wire grant, spi_permit, guard_rejected, locked, qualified, managed_sync, event_to_dsp, event_blocked;
    wire [2:0] guard_reason;
    wire dsp_input_valid = guard_enabled ? live_input : valid_in;

    // 固定 K=1、SHIFT=14，GAIN=768 时 x 的增量正好是 x/64；非器件传递绑定。
    // 唯一 PF1 系数源由与 DUT 一起固定的源码提供。
    ad4080_top #(
        .FIFO_DEPTH(16), .P_WRITE_SDO_DRIVE(0), .P_MULTIBYTE_ORDER(0), .P_OFFSET_LSB_ADDR(37),
        .P_GAIN_MAP_VALID(BOUND), .K_GAIN(1), .SHIFT(14)
    ) dut (
        // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
        .pwr_cfg_full_allow_i(1'b1), .pwr_cmd_ready_i(1'b0), .pwr_cmd_valid_o(),
        .pwr_cmd_op_o(), .pwr_cmd_value_o(), .pwr_rsp_valid_i(1'b0),
        .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
        .pwr_ldo_pd_i(2'b00), .rom_crc_error_i(1'b0),

        .clk(clk), .rst_n(rst_n), .cs_n(cs_n), .sclk(sclk), .sdi(sdi), .sdo(sdo), .sdo_oe(sdo_oe),
        .interface_ready(!guard_enabled || spi_permit), .adc_idle(adc_idle),
        .valid_in(dsp_input_valid), .x(guard_enabled ? cnv_code : x),
        .filter_sync(managed_sync), .ext_event(event_to_dsp), .arm(1'b0), .rd_en(rd_en),
        .gpio_i(gpio_i), .gpio_o(gpio_o), .gpio_oe(gpio_oe), .dout(dout), .dout_valid(dout_valid),
        .status(status), .correction_clip_low(), .correction_clip_high(), .cfg_reject(cfg_reject),
        .cfg_reject_reason(cfg_reject_reason), .cnv_tick_i(1'b0), .conversion_timing_error_i(1'b0),
        .phy_fifo_full_i(1'b0), .phy_fifo_read_done_i(1'b0), .csr_fifo_full_i(1'b0), .csr_fifo_read_done_i(1'b0),
        .cnv_data_timing_error_o(), .data_config_ready_i(1'b0), .sample_tag_i(32'd0), .por_ana_event_i(1'b0),
        .data_intf_config_a_o(), .data_intf_config_b_o(), .data_intf_config_c_o(), .continuous_bypass_o(), .continuous_enable_o(), .continuous_flush_o(), .filter_config_o(),
        .continuous_word_valid_o(), .continuous_word_o(), .continuous_word_tag_o(), .processing_reject()
    );
    always #5 clk = ~clk;
    always #25 oscillator = ~oscillator;

    // [时间对应] 激励的真实 CNV 针脚独立于样本 valid，经过同步和边沿检测。
    // E1 锚点后，E3 起每个物理触发各交付一个已完成的 -1024 码；中间有空拍。
    // [参考] 这只绑定台架的转换时序，未验证实际模拟或产品引脚 CNV 建立/偏斜。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnv_meta     <= 0;
            cnv_sync     <= 0;
            cnv_prev     <= 0;
            cnv_in_epoch <= 0;
            live_input   <= 0;
        end else begin
            cnv_meta <= cnv_pin;
            cnv_sync <= cnv_meta;
            cnv_prev <= cnv_sync;
            live_input <= 0;
            if (epoch_start) cnv_in_epoch <= 1;
            else if (cnv_tick) begin
                cnv_in_epoch <= cnv_in_epoch + 1;
                live_input <= (cnv_in_epoch >= 2);
            end
        end
    end

    // 选择和抽取编码只取真实 SPI 被寄存后的影子，台架不代写寄存器。
    // FIFO_MODE=00 才观察 dout_valid 数值脉冲，防止误把完成记录的读回当成建立样本。
    ad4080_record_guard #(.MAX_CNV_GAP_CYCLES(12)) u_guard (
        .clk_i(clk), .rst_ni(rst_n && guard_enabled), .cnv_stream_i(stream_claim), .cnv_tick_i(cnv_tick),
        .epoch_start_i(epoch_start), .profile_valid_i(profile_valid), .filter_mode_i(dut.filter_sel),
        .dec_code_i(dut.dec_sel), .profile_min_cnv_i(16'd0), .selected_valid_i(dut.fifo_mode==0 && dout_valid),
        .filter_sync_req_i(sync_req), .filter_sync_o(managed_sync), .command_i(command), .operation_i(operation),
        .adc_idle_i(adc_idle), .frame_end_i(frame_end), .frame_ok_i(frame_ok), .session_end_i(session_end),
        .grant_o(grant), .spi_permit_o(spi_permit), .rejected_o(guard_rejected), .reject_reason_o(guard_reason),
        .record_locked_o(locked), .capture_qualified_o(qualified), .event_i(guarded_event),
        .event_o(event_to_dsp), .event_blocked_o(event_blocked)
    );

    bit observing;
    int cycle, accepted, first_input_cycle, words_seen, rejections;
    logic [3:0] last_reason;
    int seen_word [0:127];
    int seen_cycle [0:127];
    int seen_input_count [0:127];
    int seen_cnv [0:127];              // 抓同步后的物理 CNV 序号，和核心 clk 序号分别记账。
    typedef struct packed { int cnv; int word; } qualification_t;
    qualification_t first_qualified;  // 两个字段在同一观察点原子登记。
    int rate_words_checked = 0;        // 启动和完整窗各字都进入精确参考计数。

    // --- 同步结果 SPI 组帧及接收统计 ---
    localparam int RECORD_LEN = 3;
    localparam int RECEIVE_BITS = 8 + 24*RECORD_LEN;
    logic frame_start = 0;
    wire frame_start_ready, frame_start_accept, frame_busy, frame_done, frame_error;
    wire frame_sample_ready, frame_bit_valid, frame_first, frame_last;
    wire [2:0] frame_beat_width;
    wire [3:0] frame_beat;
    logic read_pending = 0, frame_sample_valid = 0;
    logic [19:0] frame_sample = 0;
    int reads_issued = 0, sample_count = 0, receive_count = 0, frame_done_count = 0;
    logic [RECEIVE_BITS-1:0] received_spi = 0;

    wire frame_read = frame_busy && frame_sample_ready && !read_pending && !frame_sample_valid &&
                      reads_issued < RECORD_LEN;
    assign rd_en = manual_rd_en || frame_read;

    ad4080_fifo_record_framer u_framer (
        .clk_i(clk), .rst_ni(rst_n), .power_good_i(1'b1), .abort_i(1'b0),
        .start_valid_i(frame_start), .start_ready_o(frame_start_ready), .start_accept_o(frame_start_accept),
        .mode_i(2'd1), .sample_count_i(15'(RECORD_LEN)), .format_bound_i(1'b1), .crc_service_ready_i(1'b0),
        .payload_prefetch_words_i(2'd0), .payload_pair_valid_i(1'b0), .payload_pair_i(2'b00), .payload_pair_first_i(1'b0),
        .payload_pair_last_i(1'b0), .payload_start_allow_o(),
        .sample_valid_i(frame_sample_valid), .sample_ready_o(frame_sample_ready),
        .sample_i(frame_sample), .sample_last_i(sample_count == RECORD_LEN - 1),
        .crc_valid_i(1'b0), .crc_ready_o(), .crc_i(24'd0), .crc_error_i(1'b0), .crc_cancel_o(),
        .beat_valid_o(frame_bit_valid), .beat_ready_i(1'b1), .beat_o(frame_beat),
        .spi_pin_beat_taken_i(1'b0), .sync_header_o(), .payload_word_start_o(),
        .beat_width_o(frame_beat_width), .beat_first_o(frame_first), .beat_last_o(frame_last),
        .beat_kind_o(), .busy_o(frame_busy), .done_o(frame_done), .error_o(frame_error), .error_code_o()
    );

    // -------------------------------------------------------------------------
    // 组帧器有空才向唯一 FIFO 发读，只保留一个在途读及一个 20 位应答槽。
    // 本槽唯一的数据来源是实际 DSP 的 dout。响应捕获和握手分沿，组帧停顿
    // 时不发第二个请求，也不会把整个记录复制到另一个存储器。
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n || frame_start_accept) begin
            read_pending       <= 0;
            frame_sample_valid <= 0;
            frame_sample       <= 0;
            reads_issued       <= 0;
            sample_count       <= 0;
        end else begin
            if (frame_read) begin
                reads_issued <= reads_issued + 1;
                read_pending <= 1;
            end
            if (read_pending && dout_valid) begin
                read_pending       <= 0;
                frame_sample       <= dout;
                frame_sample_valid <= 1;
            end
            if (frame_sample_valid && frame_sample_ready) begin
                frame_sample_valid <= 0;
                sample_count       <= sample_count + 1;
            end
        end
    end

    // -------------------------------------------------------------------------
    // 接收端只读取实际交付的单线 bit 和边界，独立左移拼出 80 位采集流。
    // 样本数据比较使用固定线上字面量，避免与发送端共用码制转换逻辑。
    // -------------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst_n && frame_bit_valid) begin
            verify(frame_beat_width==1 && frame_first==(receive_count==0) &&
                   frame_last==(receive_count==RECEIVE_BITS-1),"SPI result frame lane or boundary");
            received_spi = {received_spi[RECEIVE_BITS-2:0],frame_beat[0]};
            receive_count++;
        end
        if (frame_done) frame_done_count++;
        if (frame_error) $fatal(1,"SPI result framer aborted/rejected capture record");
    end

    // -------------------------------------------------------------------------
    // 接受发生在 clk 边沿，#1 后读寄存输出。持续送样时，日志同时保留总周期
    // 和输入个数。FIFO 模式的 chain_valid 只做观测，最终数据仍靠顶层读口检查。
    // -------------------------------------------------------------------------
    always @(posedge clk) begin
        cycle++;
        if (observing && dsp_input_valid) begin
            if (accepted == 0) first_input_cycle = cycle;
            accepted++;
        end
        #1;
        if (rst_n && cfg_reject) begin
            rejections++;
            last_reason = cfg_reject_reason;
        end
        // 逐档扫描走原顶层直通结果口，事件捕获试验则观察同一结果在 FIFO 接缝的时刻。
        if (observing && (guard_enabled ? dout_valid : dut.chain_valid)) begin
            if (words_seen >= 128) $fatal(1, "unexpected filter output burst");
            seen_word[words_seen] = int'($signed(guard_enabled ? dout : dut.chain_data));
            seen_cycle[words_seen] = cycle - first_input_cycle + 1;
            seen_input_count[words_seen] = accepted;
            seen_cnv[words_seen] = cnv_in_epoch;
            words_seen++;
        end
        if (observing && qualified && first_qualified == '0) begin
            // 捕获的是守卫实际消费的 ordinal，跟独立 raw/CNV 相序算出的 guard_ord 对照。
            first_qualified = {cnv_in_epoch,int'(u_guard.numeric_count_q)};
        end
    end

    task automatic verify(input bit ok, input string why);
        if (!ok) $fatal(1,"%s at t=%0t cycle=%0d",why,$time,cycle);
    endtask

    // =========================================================================
    // 2. 只走 SPI 引脚的寄存器读写和样本驱动
    // =========================================================================
    task automatic shift_byte(input logic [7:0] tx, output logic [7:0] rx, input int oe);
        // 主机在采样沿前预置 SDI；数据阶段同步完成后才读 SDO 及其驱动资格。
        for (int bit_index=7; bit_index>=0; bit_index--) begin
            sdi = tx[bit_index];
            #80; sclk = 0;
            #80; sclk = 1;
            rx[bit_index] = sdo;
            if (oe >= 0) verify(sdo_oe == 1'(oe),"configuration SPI SDO ownership");
            #80;
        end
    endtask

    task automatic spi_entity(input bit rd, input logic [14:0] addr,
                              input int nbytes, input logic [15:0] value,
                              output logic [15:0] response);
        logic [7:0] received;
        // 复位默认长指令、降序、严格多字节。两字节实体从高地址开始；若起始
        // 字节或实体提交有误，后续读回/数值检查会失败，不走 DUT 并行写口。
        response = 0;
        @(negedge clk);
        cs_n = 0;
        repeat (8) @(negedge clk);
        shift_byte({rd, addr[14:8]},received,-1);
        shift_byte(addr[7:0],received,-1);
        for (int j=nbytes-1; j>=0; j--) begin
            shift_byte(value[8*j+:8],received,int'(rd));
            response[8*j+:8] = received;
        end
        cs_n = 1;
        repeat (8) @(negedge clk);
        verify(!sdo_oe,"configuration SPI relinquishes SDO at end of frame");
    endtask

    task automatic write_reg(input logic [14:0] addr,input int nbytes,input logic [15:0] value);
        logic [15:0] unused_response;
        spi_entity(0,addr,nbytes,value,unused_response);
    endtask

    task automatic read_reg(input logic [14:0] addr,input int nbytes,input logic [15:0] value);
        logic [15:0] received;
        spi_entity(1,addr,nbytes,0,received);
        verify(received == value,$sformatf("SPI readback at %h got %h expected %h",addr,received,value));
    endtask

    task automatic quiesce;
        @(negedge clk);
        valid_in = 0;
        // 这里不给虚假的连续样本推进积分器，只检查无新输入后的出码通路。
        repeat (20) @(negedge clk);
        verify(!dut.chain_valid,"output still in flight during testbench stop");
        adc_idle = 1;
    endtask

    task automatic observing_begin;
        @(negedge clk);
        accepted = 0;
        first_input_cycle = 0;
        words_seen = 0;
        first_qualified = '0;
        observing = 1;
    endtask

    task automatic sample(input int code);
        @(negedge clk);
        adc_idle = 0;
        valid_in = 1;
        x = 20'(code);
    endtask

    task automatic filter_gpio_sync;
        @(negedge clk);
        gpio_i[1] = 0;
        repeat (5) @(negedge clk);
        verify(dut.fsync_all && !gpio_oe[1],"GPIO1 low FILTER_SYNC did not reach selected filter");
        gpio_i[1] = 1;
        repeat (5) @(negedge clk);
        verify(!dut.fsync_all,"GPIO1 FILTER_SYNC did not release");
    endtask

    task automatic read_word(input int expected);
        @(negedge clk);
        manual_rd_en = 1;
        @(posedge clk);
        #2;
        verify(dout_valid && int'($signed(dout))==expected,
               $sformatf("FIFO read got valid=%0b word=%0d expected=%0d",dout_valid,$signed(dout),expected));
        @(negedge clk);
        manual_rd_en = 0;
        @(posedge clk);
        #2;
        verify(!dout_valid,"FIFO repeated the read response");
    endtask

    task automatic drain_frame;
        // 每一位须实际发出；把读完整记录、FIFO 清 FULL 和线上总位数钉在同一用例。
        @(negedge clk);
        verify(frame_start_ready,"SPI result framer should accept an idle command");
        frame_start = 1;
        @(negedge clk);
        frame_start = 0;
        verify(frame_busy,"SPI result framer did not start");
        wait (reads_issued==1);
        @(negedge clk);
        verify(!status[7] && !status[6],"protocol FULL should clear on first read");
        wait (frame_done_count==1);
        @(negedge clk);
        verify(reads_issued==RECORD_LEN && sample_count==RECORD_LEN && status[6],
               "READ_DONE or actual SRAM count disagrees with SPI record");
        verify(receive_count==RECEIVE_BITS && received_spi==80'haa00075d0009e7fff30d,
               $sformatf("SPI result serial bits: count=%0d wire=%h",receive_count,received_spi));
        $display("PASS result SPI SDOB: beats=%0d wire=%h (0xAA + three signed24 words)",
                 receive_count,received_spi);
    endtask

    // =========================================================================
    // 3. 实际配置 SPI 的外围许可、同批锚点与状态回收
    // =========================================================================
    task automatic request_frame(input logic [1:0] action, input logic [2:0] expected);
        // 同步申请和准许相位分开采样。grant 在受理沿前有效，拒绝粘滞码在沿后核对。
        // 未授权波形另在真实 SPI 场景验证，不能仅以控制层拒绝代替电路线拦截。
        @(negedge clk);
        operation = action;
        command = 1;
        #1;
        verify(grant == (expected==0),$sformatf("guard result: op=%d expected reason=%d",action,expected));
        @(negedge clk);
        command = 0;
        if (expected == 0) verify(spi_permit,"granted command did not allow actual configuration SPI");
        else verify(guard_rejected && guard_reason==expected && !spi_permit,"guard reject reason or frame protection");
    endtask

    task automatic close_frame(input bit accepted_frame);
        // 发送端只有等 CS 高通过同步链以后才能交还许可，不能在最后 SDI 采样沿撤权。
        @(negedge clk);
        frame_ok = accepted_frame;
        frame_end = 1;
        @(negedge clk);
        frame_end = 0;
        frame_ok = 0;
        verify(!spi_permit,"SPI authority leaked past CS synchronization");
    endtask

    task automatic guarded_write(input logic [1:0] action, input logic [14:0] addr,
                                 input int bytes, input logic [15:0] value);
        // 原 DSP 的 cfg_reject 在 SPI 内部时钟脉冲出现，采用监视累计量收整帧结果。
        // 本任务的已允许起录均实跑到原 DSP，用 FIFO 读回检验实际提交。
        int reject_before;
        request_frame(action,0);
        reject_before = rejections;
        write_reg(addr,bytes,value);
        verify(rejections==reject_before,"frozen SPI/CSR refused admitted transaction");
        close_frame(1);
    endtask

    task automatic start_cnv_batch(input bit filtered);
        @(negedge clk);
        // 物理同步在首个 CNV 前建立一个完整的低相位以上，首沿过同步后尽早释放。
        adc_idle = 0;
        stream_claim = 1;
        sync_req = filtered;
        epoch_mark = 1;
        repeat (4) @(negedge clk);
        @(negedge oscillator);
        oscillator_run = 1;
        wait (epoch_start);
        @(posedge clk);
        @(negedge clk);
        epoch_mark = 0;
        sync_req = 0;
    endtask

    task automatic stop_cnv(input bit silent);
        @(negedge clk);
        oscillator_run = 0;
        if (!silent) stream_claim = 0;
        // 冲完同步器和样本流水，并留出超过 12 周期的独立 CNV 看门窗口。
        repeat (24) @(negedge clk);
        verify(!qualified,"lost/stopped CNV must invalidate entire batch");
        adc_idle = 1;
    endtask

    task automatic finish_session;
        // 锁覆盖写后和读出，只有真正 READ_DONE 到来才退本批。
        verify(status[6] && locked,"guarded session must stay locked through its final actual read");
        @(negedge clk);
        session_end = 1;
        @(negedge clk);
        session_end = 0;
        verify(!locked && !qualified,"record completion must consume the qualification");
    endtask

    task automatic guarded_cases;
        int reject_before;
        @(negedge clk);
        guard_enabled = 1;
        guarded_write(1,15'h0029,1,16'h000a);
        guarded_write(1,15'h001e,2,16'h0001);
        request_frame(3,4);
        start_cnv_batch(1);
        request_frame(3,4);  // 守候中 CNV 可动，早期有效仍不能批准捕获。
        // 阈值模式尚未开，单个预热事件必须明确丢弃，后续不能挂起重播。
        @(negedge clk);
        guarded_event = 1;
        #1;
        verify(event_blocked && !event_to_dsp && dut.fifo_mode==0,"early event escaped unqualified epoch");
        @(negedge clk);
        guarded_event = 0;
        wait (qualified);
        @(negedge clk);
        // 核心时钟梳状链无额外抽取组延迟。本独立执行器采用真实数值到达后才判断
        // 资格。第五个数值早于手册 CNV 底线，下一真实有效使资格在 E27 成立。
        verify(u_guard.numeric_count_q==6 && u_guard.actual_cnv_q==27 && int'($signed(dout))==-1105,
               "SINC5 guard must first qualify at the true stable result after the CNV floor");
        $display("GUARD accepted history: SINC5 N=4 observed CNV=%0d outputs=%0d latest=%0d",
                 u_guard.actual_cnv_q,u_guard.numeric_count_q,$signed(dout));
        stop_cnv(1);
        request_frame(3,4);  // 源信号保持运行声明，只有实际脉冲丢失导致拒绝。
        // 只恢复针脚振荡，保持旧批锚点；若硬件把计数保留并自动重开，此处应失败。
        @(negedge oscillator);
        oscillator_run = 1;
        repeat (25) @(negedge clk);
        request_frame(3,4);  // 恢复脉冲不得借用先前批次资格。
        stop_cnv(0);

        start_cnv_batch(1);
        wait (qualified);
        guarded_write(3,15'h001c,1,16'h0001);
        wait (status[7]);
        // FULL 后仍要保护未读记录。软件复位和参数修改均在该窗口有风险。
        request_frame(1,1);
        request_frame(2,1);
        // 请求被拒后即使继续送原始引脚波形，interface_ready 也会阻止实体写入。
        reject_before = rejections;
        write_reg(15'h0029,1,16'h0000);
        verify(rejections==reject_before+1 && last_reason==8 && dut.filter_sel==2,
               "disallowed physical SPI wave changed active filter");
        @(negedge clk);
        sync_req = 1;
        // 保持实际滤波复位线稳定，冻结源对复位本身的清数行为由既有链路提供。
        #1;
        verify(!managed_sync,"filter reset leaked during locked record");
        @(negedge clk);
        verify(guard_rejected && guard_reason==1,"filter reset denial not observable");
        sync_req = 0;
        request_frame(0,0);
        // 安全读仍穿过真实 SPI，0x29 回读验证误发写事务没有落入底层寄存器。
        read_reg(15'h0029,1,16'h000a);
        close_frame(1);
        read_word(-1105);
        finish_session();
        request_frame(3,4);
        $display("PASS guarded SINC5 W=1: no early arm/event, lost CNV invalidated, record read -1105");

        stop_cnv(0);
        guarded_write(1,15'h001c,1,16'h0000);
        guarded_write(1,15'h0029,1,16'h0000);
        request_frame(3,4);  // 模式切换后即使旁路也需要本批真实 CNV 与新数值。
        start_cnv_batch(0);
        @(negedge clk);
        guarded_event = 1;  // 提前的外部触发不可留到后来批准的记录。
        #1;
        verify(event_blocked,"pre-arm bypass event escaped");
        @(negedge clk);
        guarded_event = 0;
        wait (qualified);
        guarded_write(3,15'h001c,1,16'h0002);
        verify(dut.capture_active && !status[7] && !dut.u_capture.event_seen,
               "early event incorrectly started waiting mode 10 record");
        request_frame(1,1);  // 捕获已 arm 而未写满，与 SINC5 的 FULL/待读阶段分别覆盖。
        request_frame(2,1);
        @(negedge clk);
        guarded_event = 1;
        #1;
        verify(event_to_dsp && !event_blocked,"qualified same-batch trigger did not reach actual FIFO");
        @(negedge clk);
        guarded_event = 0;
        wait (status[7]);
        read_word(-1105);
        finish_session();
        stop_cnv(0);
        guarded_write(1,15'h001c,1,16'h0000);
        // 未绑定 profile 和已绑定但未建立新批是两种独立拒绝。
        profile_valid = 0;
        request_frame(3,3);
        guarded_write(1,15'h0029,1,16'h000b);
        request_frame(3,3);  // PF1 有系数仍需证明 CNV 和数值绑定。
        profile_valid = 1;
        request_frame(3,4);
        $display("PASS guarded mode change/bypass/PF1 profile and write/reset interlock");
    endtask

    // =========================================================================
    // 4. 正常停机换档与各档数据路径实证
    // =========================================================================
    // 阶跃参考由 run.sh 用闭式窗口和全点积计算，再逐字观察真实 RTL。
    // 前置表避免在有长 SPI 协程的测试台里反复内联宽整数运算。
    localparam int PF1_TAPS = 35, PF1_FRAC = 22;
    localparam int REF_WORDS = 40;
    int step_table [2:3][0:7][0:REF_WORDS-1];
    int direct_pf1_n4 [0:24];

    task automatic load_step_table;
        int fd, taps, fraction;
        fd = $fopen("filter_step_reference.txt", "r");
        if (fd == 0 || $fscanf(fd, "%d %d", taps, fraction) != 2 ||
            taps != PF1_TAPS || fraction != PF1_FRAC)
            $fatal(1,"complete filter reference was not provided by the pinned runner");
        for (int mode=2; mode<=3; mode++) begin
            for (int rate=0; rate<8; rate++) begin
                for (int word=0; word<REF_WORDS; word++) begin
                    if ($fscanf(fd, "%d", step_table[mode][rate][word]) != 1)
                        $fatal(1,"incomplete filter reference mode=%0d rate=%0d word=%0d",mode,rate,word);
                end
            end
        end
        foreach (direct_pf1_n4[word]) begin
            if ($fscanf(fd,"%d",direct_pf1_n4[word]) != 1)
                $fatal(1,"incomplete PF1 direct-input reference word %0d",word);
        end
        $fclose(fd);
    endtask

    task automatic scan_rate(input logic [1:0] selected_mode, input int code, input int n_rate);
        // 同一真实 CSR、校正和选择链；表 62 的非法编码测试显式传入钳位后的 N。
        // 输入 -2^18 先加 -64 LSB 再用工程增益，结果为 -266305，远离饱和。采用较大的
        // 定值阶跃，可分辨稳态前的量化误差。期望来自闭式卷积及外部相序。
        localparam int signed FIRST_CODE = -266305;
        logic [15:0] filter_cfg;
        int d_rate, s5_full, stable_ord, ds_floor, plateau;
        int first_input, numeric_latency, first_cnv, guard_ord, required_words;
        int expected_word, source_sample;
        filter_cfg = (16'(code) << 3) | 16'(selected_mode);
        d_rate = selected_mode == 2'd3 ? 2*n_rate : n_rate;
        s5_full = (6*n_rate-5)/n_rate;      // ceil((5N-4)/N)，不额外延迟抽取组。
        stable_ord = 1;
        ds_floor = 1;
        first_input = 1;
        numeric_latency = 2;
        // 数学完整历史的序号与手册起录下界分别计算。此处只测试独立守卫。
        case (selected_mode)
            2'd1: begin
                ds_floor = n_rate+3;
                first_input = n_rate;
                numeric_latency = 4;
            end
            2'd2: begin
                ds_floor = 5*n_rate+6;
                stable_ord = s5_full;
                first_input = n_rate;
                numeric_latency = 7;
            end
            2'd3: begin
                stable_ord = (s5_full+PF1_TAPS)/2; // 首个完整前级字再加 34 历史，向上取偶相。
                ds_floor = 35*d_rate+12;
                first_input = 2*n_rate;
                numeric_latency = 9;
            end
            default: begin end
        endcase
        // E3 起每个同步 CNV 的下一核心拍接受原码。相邻 CNV 相隔五核心拍。
        first_cnv = first_input+2+(1+numeric_latency)/5;
        guard_ord = selected_mode == 2'd3 ? 35 : (selected_mode == 2'd2 ? 5 : 1);
        while (first_cnv+(guard_ord-1)*d_rate < ds_floor) guard_ord++;
        // D=2 的 SINC5 数学稳态较早，原固定扫描六字到 E15，尚未越过 E16。
        // 等到第七字才能确认资格，同时保留稳态后连续三个字的数值检查。
        required_words = stable_ord+3;
        if (guard_ord > required_words) required_words = guard_ord;
        guarded_write(1,15'h0029,1,filter_cfg);
        request_frame(0,0);
        read_reg(15'h0029,1,filter_cfg);
        close_frame(1);
        cnv_code = 20'(-262144);
        observing_begin();
        start_cnv_batch(selected_mode!=2'd0);
        wait (words_seen >= required_words);
        repeat (2) @(negedge clk);  // 所选 valid 后一沿由守卫消费，随后才观察资格。
        for (int k=0; k<required_words; k++) begin
            source_sample = first_input+k*d_rate;
            verify(seen_cnv[k]==first_cnv+k*d_rate && seen_cycle[k]==1+5*(source_sample-1)+numeric_latency,
                   $sformatf("rate scan phase: mode=%0d code=%0d N=%0d k=%0d CNV=%0d core=%0d",
                             selected_mode,code,n_rate,k,seen_cnv[k],seen_cycle[k]));
            case (selected_mode)
                2'd2, 2'd3: expected_word = step_table[selected_mode][code>7 ? 7 : code][k];
                default: expected_word = FIRST_CODE;
            endcase
            verify(seen_word[k]==expected_word,
                   $sformatf("step oracle: mode=%0d N=%0d k=%0d wanted=%0d got=%0d",
                             selected_mode,n_rate,k,expected_word,seen_word[k]));
            rate_words_checked++;
        end
        plateau = stable_ord-1;
        // 数值可能因舍入提前等于 DC。记录连续同码的起点和完整历史的证明点。
        // 只有后者用于证明前史已填满，两个计数不可替换。
        while (plateau>0 && seen_word[plateau-1]==FIRST_CODE) plateau--;
        verify(qualified,"rate-scan eligibility missing after real samples and bound phase");
        verify(first_qualified.cnv==first_cnv+(guard_ord-1)*d_rate && first_qualified.word==guard_ord,
               $sformatf("guard first qualification: mode=%0d N=%0d CNV=%0d word=%0d",
                         selected_mode,n_rate,first_qualified.cnv,first_qualified.word));
        if (selected_mode==2'd1) verify(seen_cnv[0]==ds_floor,"SINC1 numeric count vs Figure 99 anchor");
        if (selected_mode==2'd2) verify(seen_cnv[stable_ord-1]<ds_floor,"SINC5 arithmetic history is separate from the device CNV floor");
        $display("RATE_SCAN mode=%0d code=%0d N=%0d D=%0d first_CNV=%0d first=%0d steady_CNV=%0d full_CNV=%0d period=%0d guard_CNV=%0d guard_word=%0d",
                 selected_mode,code,n_rate,d_rate,seen_cnv[0],seen_word[0],seen_cnv[plateau],
                 seen_cnv[stable_ord-1],seen_cnv[1]-seen_cnv[0],first_qualified.cnv,first_qualified.word);
        observing = 0;
        stop_cnv(0);
    endtask

    task automatic scan_pause_and_switch;
        // 同一次受控同步后分别停止生产端和改写寄存器，验证停流只保留旧历史，
        // 改档才建立新的输入块。组合模拟不把 GPIO 声明到达当成硬件 CNV 脉冲。
        guarded_write(1,15'h0029,1,16'h0011);  // SINC1 N=8，预填小于完整组的三个不同幅度字。
        cnv_code = 20'd640;
        observing_begin();
        start_cnv_batch(1);
        wait (accepted==3);
        stop_cnv(1);
        verify(accepted==3 && words_seen==0,"CNV pause fabricated a result");
        // 源继续同一批数据可推进原数值历史，但保护层已经撤销其起录资格。
        cnv_code = 20'(-1024);
        @(negedge oscillator);
        oscillator_run = 1;
        wait (words_seen>=1);
        @(negedge clk);
        verify(seen_word[0]==-471 && !qualified,"paused history/state or stale session qualification");
        wait (accepted>=10);
        stop_cnv(0);
        observing = 0;
        // 留有未成块的旧速率输入后改 0x29。故意省 GPIO 同步以观察原 CSR 的内部
        // 清流行为，保护层仍须拒绝用无真实 FILTER_SYNC 的新批出数启动捕获。
        guarded_write(1,15'h0029,1,16'h0001);
        observing_begin();
        start_cnv_batch(0);
        wait (words_seen>=3);
        @(negedge clk);
        verify(seen_word[0]==-1105 && seen_cnv[0]==5 && seen_cnv[1]-seen_cnv[0]==2 &&
               !qualified,"FILTER_CONFIG did not discard old block, or old epoch leaked");
        $display("PASS stop/switch via SPI: paused mixed average=-471, first new N=2=-1105, missing physical sync denies arm");
        observing = 0;
        stop_cnv(0);
    endtask

    task automatic scan_all_rates;
        // Table 29 的 27 档与 Table 62 的三个越界编码共用同一 DUT 及 SPI 帧任务。
        // 共用一个调用点，Verilator 不必为每个滤波模式展开完整的 SPI 回放任务。
        int highest_code, code, n_rate;
        for (int mode=0; mode<4; mode++) begin
            case (mode)
                0: highest_code = 0;
                1: highest_code = 9;
                default: highest_code = 7;
            endcase
            for (int index=0; index<=highest_code+int'(mode!=0); index++) begin
                code = index > highest_code ? 15 : index;
                n_rate = mode == 0 ? 1 : (2 << (index > highest_code ? highest_code : index));
                scan_rate(2'(mode),code,n_rate);
            end
        end
        scan_pause_and_switch();
        verify(rate_words_checked==443,"rate step-oracle coverage count");
        $display("AD4080_DSP_STEP_ORACLE_COMPLETE words=%0d",rate_words_checked);
        $display("AD4080_DSP_RATE_SCAN_COMPLETE legal=27 clamp=3 pause_switch=1");
    endtask

    // =========================================================================
    // 5. SINC1 事件/读回与 SINC5/COMP 启动观察
    // =========================================================================
    initial begin
        int reject_before;
        load_step_table();
        repeat (5) @(negedge clk);
        rst_n = 1;
        quiesce();
        read_reg(15'h0028,2,16'h0200);       // p.71：GAIN=0x200 为单位斜率。
        write_reg(15'h0026,2,16'h0fc0);      // 12 位补码 -64 LSB，高地址绑定 p.92。
        read_reg(15'h0026,2,16'h0fc0);

        if (!BOUND) begin
            // 失败路径必须拒绝且“不更改原配置”；读回单位增益和先前直通模式。
            reject_before = rejections;
            write_reg(15'h0028,2,16'h0300);
            verify(rejections == reject_before+1 && last_reason == 1,"unbound gain was accepted");
            read_reg(15'h0028,2,16'h0200);
            observing_begin();
            sample(-1024);
            quiesce();
            verify(words_seen==1 && seen_word[0]==-1088,"unity/negative offset on accepted input");
            $display("PASS unbound numerical features: gain rejected, unity with -64 offset -1024 -> %0d",seen_word[0]);
            $finish;
        end else begin
            write_reg(15'h0028,2,16'h0300);
            read_reg(15'h0028,2,16'h0300);
            observing_begin();
            sample(-1024);
            quiesce();
            verify(words_seen==1 && seen_word[0]==-1105 && seen_cycle[0]==3,
                   "bound gain+offset or correction cadence");
            $display("PASS config SPI -> correction: -1024 -> %0d, input-relative clk=%0d",seen_word[0],seen_cycle[0]);
            observing = 0;

            // SINC1 N=4、阈值为高 4×256 码、W=3。第一组含一个越阈值的原始
            // 样本，过滤后的 195 码仍低于阈值。第二组才触发，后面三组入 FIFO。
            write_reg(15'h001a,1,16'h0086);  // GPIO1 FILTER_SYNC，GPIO0 ALERT。
            write_reg(15'h0029,1,16'h0009);
            read_reg(15'h0029,1,16'h0009);
            write_reg(15'h0022,2,16'h0004);
            write_reg(15'h001e,2,16'h0003);
            write_reg(15'h001c,1,16'h00c2);
            filter_gpio_sync();
            observing_begin();
            sample(1280); sample(-256); sample(0); sample(0);
            quiesce();
            verify(words_seen==1 && seen_word[0]==195 && seen_cycle[0]==8 && seen_input_count[0]==4,
                   "SINC1 N=4 must average after the fourth input");
            verify(!status[5] && gpio_oe[0] && gpio_o[0] && !dut.u_capture.event_seen &&
                   int'(dut.u_capture.u_fifo.count)==0,"individual nonfiltered samples erroneously triggered FIFO");
            sample(960); sample(1088); sample(1216); sample(1344);
            quiesce();
            verify(words_seen==2 && seen_word[1]==1105 && status[5] && !gpio_o[0] &&
                   dut.u_capture.event_seen && int'(dut.u_capture.u_fifo.count)==0,
                   "threshold crossing did not route to ALERT and arm mode 10");
            for (int i=3; i<=4; i++) begin
                sample(640*i-192); sample(640*i-64); sample(640*i+64); sample(640*i+192);
            end
            sample(-3392); sample(-3264); sample(-3136); sample(-3008);
            quiesce();
            verify(words_seen==5 && seen_word[2]==1885 && seen_word[3]==2535 && seen_word[4]==-3315,
                   "SINC1 post-trigger words or FILT_CONFIG rate");
            verify(status[7] && !status[6],"FIFO record must complete at the watermark");
            verify(status[5] && gpio_o[0],"negative word releases ALERT while sticky HI persists");
            drain_frame();
            $display("PASS SINC1 N=4 -> internal HI + GPIO ALERT -> FIFO: avg %0d then trigger %0d, words [%0d,%0d,%0d]",
                     seen_word[0],seen_word[1],seen_word[2],seen_word[3],seen_word[4]);
            observing = 0;
            write_reg(15'h001c,1,16'h0000);

            // 80 个连续 -1024 输入，对应常值 -1105。比较从五级滑动和
            // 权重 [1,5,15,35,...,1]/1024 手算的首段响应和抽取节拍。
            @(negedge clk);
            adc_idle = 0;
            write_reg(15'h0029,1,16'h000a);
            read_reg(15'h0029,1,16'h000a);
            $display("OBS FILTER_CONFIG write accepts with adc_idle=0 even with no input valid");
            filter_gpio_sync();
            observing_begin();
            repeat (80) sample(-1024);
            quiesce();
            verify(words_seen >= 12 && seen_cycle[0]==11,"SINC5 missing first result or changed timing");
            for (int i=1; i<12; i++) verify(seen_cycle[i]-seen_cycle[i-1]==4,"SINC5 N=4 result cadence");
            verify(seen_word[0]==-60 && seen_word[1]==-552 && seen_word[2]==-1045 && seen_word[3]==-1105,
                   "SINC5 step response or signed gain");
            $display("OBS SINC5 N=4: first clk=%0d word=%0d, steady clk=%0d word=%0d",
                     seen_cycle[0],seen_word[0],seen_cycle[3],seen_word[3]);
            observing = 0;

            // 以 W=1 明确暴露未定相启动 valid 的后果，直接布防会读到部分窗。
            write_reg(15'h001e,2,16'h0001);
            write_reg(15'h001c,1,16'h0001);
            filter_gpio_sync();
            repeat (20) sample(-1024);
            quiesce();
            verify(status[7],"SINC5 early valid did not reach FIFO");
            read_word(-60);
            $display("OBS SINC5 N=4 immediate W=1 captures partial-window -60 for corrected input -1105");
            write_reg(15'h001c,1,16'h0000);

            // 工程试验先清滤波器，保持 FIFO 未 arm 送 48 笔，然后只改变 FIFO
            // 模式。保留全程相同的转换设置与相位历史，验证外部延后 arm 的可行性。
            filter_gpio_sync();
            repeat (48) sample(-1024);
            quiesce();
            write_reg(15'h001c,1,16'h0001);
            repeat (12) sample(-1024);
            quiesce();
            verify(status[7],"warm SINC5 did not fill W=1 FIFO");
            read_word(-1105);
            $display("PASS SINC5 N=4 after 48 warmup inputs: delayed arm W=1 reads -1105");
            write_reg(15'h001c,1,16'h0000);

            // PF1 使用同一路 SINC5。基础 N=4 时从前级第 2 组开始每 8 个输入
            // 发一次整数码。每个样本按资料表及完整 35 项点积验证。
            write_reg(15'h0029,1,16'h000b);
            read_reg(15'h0029,1,16'h000b);
            filter_gpio_sync();
            observing_begin();
            repeat (200) sample(-1024);
            quiesce();
            verify(words_seen==25 && seen_cycle[0]==17,"PF1 first result or stopped-input tail");
            for (int i=0; i<25; i++) begin
                verify(seen_cycle[i]==17+8*i,"PF1 additional decimation by 2");
                verify(seen_word[i]==direct_pf1_n4[i],"PF1 signed Q22 complete integer oracle");
            end
            verify(seen_cycle[18]==161 && seen_word[18]==-1105,"PF1 complete history at 19th numeric output");
            $display("OBS PF1 N=4: first clk=%0d word=%0d, steady clk=%0d word=%0d, interval=8",
                     seen_cycle[0],seen_word[0],seen_cycle[18],seen_word[18]);
            $display("PASS bound DSP path and numerical-valid startup cases");
            observing = 0;
            guarded_cases();
            scan_all_rates();
            $finish;
        end
    end
    initial begin
        #8000000;
        $fatal(1,"DSP path watchdog");
    end
// 所有完成计数来自真实 SPI 与数值比较，捕获守卫和直接引脚定相分开验证。
// 此处使用宿主生成的独立期望码，避免 RTL 和仿真判据共享同一个乘法结果。
endmodule
`default_nettype wire
