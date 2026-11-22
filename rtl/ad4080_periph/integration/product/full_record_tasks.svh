// =============================================================================
// 文件名     : full_record_tasks.svh
// 模块名     : 产品 FIFO 四模式和完整环形读回
// 版本       : v1.0
// 功能       : 比对实际写入、物理访问和连续记录，含事件前已有历史与早事件旧数据。
// 设计说明   : Rev.B p57/p63 给出样本数和读回序号。地址比较是附加的四银行工程契约。
//              只保存当前位对和一个输出字，不需要记录整帧波形。CRC 用台架工程参数。
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================

// =============================================================================
// 1. 从独立输入序号建立判据
// =============================================================================
// 两个高位标签跨记录保持不同，低位序号经过 SINC/校正旁路后仍可辨认。
// FULL_DEPTH 取实际 14 位数组长度，允许一圈后再走到同一物理槽。
localparam int FULL_DEPTH = 16384;
localparam logic [19:0] FULL_TAG_HISTORY = 20'h20000;
localparam logic [19:0] FULL_TAG_EARLY = 20'hE0000;

function automatic logic [19:0] full_source_word(input logic [19:0] tag, input int sequence_index);
    // 同一记录的输入逐字唯一。高位标签区分跨记录的已知旧内容和当次转换。
    return tag + 20'(sequence_index);
endfunction

function automatic int full_physical_slot(input int ordinal);
    // 早事件有负的相对历史序号。需要真正模深度，SV 的负数 % 仍会带符号。
    return (ordinal % FULL_DEPTH + FULL_DEPTH) % FULL_DEPTH;
endfunction

function automatic logic [19:0] full_expected_word(
    input int output_index, count, writes_this_record, writes_previous_record,
    input logic [19:0] tag, previous_tag
);
    int chronological_ordinal;
    int stale_slot, previous_oldest, offset_from_oldest;
    // 输出起点取冻结时刻向前数 count。序号非负时就是本次输入序列；
    // 早事件的负序号必须回到物理槽并查上一记录的最近一次覆盖。
    chronological_ordinal = writes_this_record - count + output_index;
    if (chronological_ordinal >= 0)
        return full_source_word(tag, chronological_ordinal);

    // p63 允许早事件的旧区保留上一记录内容。本用例此前已逐字验证整圈，旧值有来源。
    // 算出旧槽来自上一记录的哪个输入序号，物理槽仅用于映射旧值，不直接等同线上序号。
    if (writes_previous_record < FULL_DEPTH)
        $fatal(1, "Early-event oracle has no proven previous-memory contents");
    stale_slot = full_physical_slot(chronological_ordinal);
    previous_oldest = writes_previous_record - FULL_DEPTH;
    offset_from_oldest = full_physical_slot(stale_slot - full_physical_slot(previous_oldest));
    return full_source_word(previous_tag, previous_oldest + offset_from_oldest);
endfunction

function automatic logic [23:0] full_crc_division(
    input logic [23:0] previous, input logic [19:0] field, input int width
);
    logic [43:0] dividend;
    // 本包外部工程余数校核，以长除进 header 与每个独立期望数据字。
    // 生产台架服务按实收位对累积，两个路径不共享同一组输入。
    dividend = (44'(previous) << width) ^ (44'(field) << 24);
    for (int bit_index = 43; bit_index >= 24; bit_index--)
        if (dividend[bit_index]) dividend ^= (44'h1864CFB << (bit_index-24));
    return dividend[23:0];
endfunction

// =============================================================================
// 2. 真实 CS 配置、CNV 样本和 SRAM 提交
// =============================================================================
// 模式重进先关闭采集再设置 W，否则前一帧冻结状态会屏蔽新写。
// CSR 在事件前读回 W，事件后再读则由 DSP 报出事件地址。
task automatic full_capture(
    input logic [1:0] mode,
    input int pre, post,
    input logic [19:0] tag
);
    int before_writes, span, before_event;
    logic [3:0] written_banks;
    logic [15:0] readback;
    write_reg(15'h001C, 1, 16'h0000);
    write_reg(15'h001E, 2, 16'(post));
    spi_access(1, 15'h001E, 2, 0, readback);
    if (readback != 16'(post)) $fatal(1, "Mode %0d configured W=%0d, CSR got %04h", mode, post, readback);
    before_writes = physical_writes;
    written_banks = '0;
    span = (mode == 2'd3 ? pre : 0) + post;
    before_event = mode == 2'd3 ? pre : 0;
    write_reg(15'h001C, 1, {14'b0, mode});
    wait (capture_incomplete_core_o);

    // 分开驱动刺激和监看每次实际提交。只看末址或指针数量，无法发现银行/地址写错。
    fork
        begin : observe_all_writes
            int slot;
            logic [19:0] word;
            for (int j = 0; j < span; j++) begin
                do @(posedge clk_core_i); while (!(dut.write_req && dut.write_ready));
                slot = full_physical_slot(j);
                word = full_source_word(tag, j);
                if (dut.write_addr !== 14'(slot) || dut.write_data !== word)
                    $fatal(1, "Mode %0d physical write %0d: addr=%04h data=%05h expected %04h/%05h",
                           mode, j, dut.write_addr, dut.write_data, slot, word);
                written_banks[dut.write_addr[13:12]] = 1'b1;
            end
        end
        begin : drive_samples_and_pulse
            begin_bypass();
            // Mode10 拒绝任何触发前写入。Mode11 则真正按环提交 pre 笔。
            for (int j = 0; j < pre; j++)
                cnv_step(1'b1, full_source_word(mode == 2'd2 ? 20'h5E000 : tag, j));
            if (mode == 2'd2 || mode == 2'd3) begin
                // 旁路从 raw 接受到 SRAM 写还隔两个 CNV。用首两笔后续码
                // 推进数值链，在其写窗口前脉冲；中途缺 raw 会终止这一批。
                for (int j = 0; j < 2; j++)
                    cnv_step(1'b1, full_source_word(tag, before_event + j));
                if (physical_writes != before_writes + before_event || record_available_core_o)
                    $fatal(1, "Mode %0d before event expected %0d actual writes, got %0d, frozen=%b",
                           mode, before_event, physical_writes-before_writes, record_available_core_o);
                // `ext_event` 按早于下一输入的真实 core 沿驱动，不用到达接收寄存器当作写入。
                ext_event_core_i = 1'b1;
                @(negedge clk_core_i);
                ext_event_core_i = 1'b0;
            end
            for (int j = mode[1] ? 2 : 0; j < post; j++)
                cnv_step(1'b1, full_source_word(tag, before_event + j));
            finish_cnv_batch();
        end
    join
    // 模式冻结和写计数必须同时成立。若错误提前冻结，观察进程
    // 等不到后续事务并触发统一 watchdog，不会打印通过标记。
    // W 笔后的逻辑 FULL 与计数达到 16K 的物理 full 是两种状态。
    if (physical_writes != before_writes + span || capture_incomplete_core_o ||
        !record_available_core_o || !dut.pin_fifo_full || fifo_full_core_o !== (span >= FULL_DEPTH) ||
        capture_write_loss_core_o)
        $fatal(1, "Mode %0d freeze: writes=%0d/%0d incomplete=%b available=%b logical_full=%b physical_full=%b loss=%b",
               mode, physical_writes-before_writes, span, capture_incomplete_core_o, record_available_core_o,
               dut.pin_fifo_full, fifo_full_core_o, capture_write_loss_core_o);
    if (span >= FULL_DEPTH && written_banks !== 4'b1111)
        $fatal(1, "Mode11 complete history lacks true writes in all banks: %b", written_banks);
    if (mode == 2'd3) begin
        // 这里读的是事件的物理 SRAM 位置。p63 的第一条事后输出是线上序号，另行验证。
        spi_access(1, 15'h001E, 2, 0, readback);
        if (readback != 16'(full_physical_slot(pre)) || readback == 0)
            $fatal(1, "Mode11 event address CSR got %04h expected nonzero %04h", readback, full_physical_slot(pre));
        $display("FULL_RECORD_CAPTURE mode11 event_addr=%04h W=%0d writes=%0d banks=%04b",
                 readback, post, span, written_banks);
    end
endtask

// =============================================================================
// 3. 逐物理事务校核和逐位对解码
// =============================================================================
// 并行消费者检查每次物理读、首尾标志以及载荷顺序。完整读退出还要
// 验证队尾已退休、FIFO 已空和写计数不变，防止重复消费同一记录。
task automatic full_read_record(
    input string label,
    input int count, writes_this_record, writes_previous_record, watermark,
    input logic [19:0] tag, previous_tag
);
    int first_slot, before_reads, before_writes, pairs;
    first_slot = full_physical_slot(writes_this_record - count);
    before_reads = physical_reads;
    before_writes = physical_writes;
    pairs = 4 + 10*count + 12;
    // 打开主台架已绑定的工程 CRC 服务，只作真正完整帧。时钟观察从首有效位对开始。
    @(negedge clk_io_i);
    format_bound_io_i = 1'b1;
    expected_record_words = count;
    check_golden = 1'b1;
    fork
        begin : issue_request
            request_record();
        end
        begin : observe_physical_reads
            int slot;
            for (int j = 0; j < count; j++) begin
                do @(posedge clk_core_i); while (!dut.read_taken);
                slot = full_physical_slot(first_slot + j);
                if (dut.read_addr !== 14'(slot) || (dut.write_req && dut.write_ready))
                    $fatal(1, "%s physical read %0d: addr=%04h expected %04h or concurrent write",
                           label, j, dut.read_addr, slot);
            end
        end
        begin : observe_complete_record
            logic [7:0] header;
            logic [19:0] word, expected;
            logic [23:0] tail, reference_crc;
            logic [1:0] kind;
            int output_index;
            header = '0;
            word = '0;
            tail = '0;
            reference_crc = 24'hB704CE;
            output_index = 0;
            do @(posedge clk_io_i); while (!beat_valid_io_o);
            for (int k = 0; k < pairs; k++) begin
                kind = k < 4 ? 2'd0 : k < 4+10*count ? 2'd1 : 2'd2;
                if (!beat_valid_io_o || !dut.beat_ready_io_i || beat_kind_io_o !== kind ||
                    beat_first_io_o !== (k == 0) || beat_last_io_o !== (k == pairs-1))
                    $fatal(1, "%s noncontiguous or malformed beat %0d", label, k);
                case (kind)
                    2'd0: begin
                        header = {header[5:0], beat_pair_io_o};
                        if (k == 3 && header !== 8'hAA) $fatal(1, "%s header got %02h", label, header);
                        if (k == 3) reference_crc = full_crc_division(reference_crc, 20'hAA, 8);
                    end
                    2'd1: begin
                        word = {word[17:0], beat_pair_io_o};
                        if ((k-4) % 10 == 9) begin
                            // 借用真实接口逐字成帧，但 oracle 按线上第 j 个字从输入历史取值。
                            // 不用 SRAM 读地址反推预期值，避免串序与同一寻址错误互相掩护。
                            expected = full_expected_word(output_index, count, writes_this_record,
                                                         writes_previous_record, tag, previous_tag);
                            if (word !== expected)
                                $fatal(1, "%s output word %0d (1-based %0d): %05h != %05h",
                                       label, output_index, output_index+1, word, expected);
                            if (output_index < 3 || output_index >= count-watermark)
                                $display("FULL_RECORD_SAMPLE case=%s position=%0d word=%05h",
                                         label, output_index+1, word);
                            reference_crc = full_crc_division(reference_crc, expected, 20);
                            output_index++;
                            word = '0;
                        end
                    end
                    2'd2: tail = {tail[21:0], beat_pair_io_o};
                    default: $fatal(1, "%s unexpected kind at %0d", label, k);
                endcase
                if (k < pairs-1) @(posedge clk_io_i);
            end
            if (output_index != count || tail !== reference_crc)
                $fatal(1, "%s word count/engineering CRC got %0d/%06h expected %0d/%06h",
                       label, output_index, tail, count, reference_crc);
        end
    join
    // 线上的末位与核心域记录完成之间有跨域延迟。等核心域完成
    // 再使用稳定的读数与状态位。模式11按冻结地址读，环写历史
    // 不移动队列占用，只需关闭本次读资格；短队列才检查物理 empty。
    wait (record_done_core_o);
    @(negedge clk_core_i);
    if (physical_reads != before_reads+count || physical_writes != before_writes || pair_count != pairs ||
        (count < FULL_DEPTH && !fifo_empty_core_o) || !status_o[6] ||
        record_available_core_o || record_failed_core_o)
        $fatal(1, "%s full physical count or completion bookkeeping", label);
    check_golden = 1'b0;
    expected_record_words = 3;
    $display("FULL_RECORD_CHECK case=%s read=%0d pairs=%0d first_phys=%04h first_post_one_based=%0d",
             label, count, pairs, first_slot, count-watermark+1);
endtask

task automatic run_full_record_check();
    int writes_before, reads_before;
    // 旁路滤波及校正；这组序列隔离 FIFO，尚不承担此处未配置的滤波和慢控制语义。
    write_reg(15'h001C, 1, 16'h0000);
    write_reg(15'h0026, 2, 16'h0000);
    write_reg(15'h0028, 2, 16'h0200);
    write_reg(15'h0029, 1, 16'h0000);
    writes_before = physical_writes;
    reads_before = physical_reads;
    begin_bypass();
    for (int i = 0; i < 4; i++) cnv_step(1'b1, 20'(i));
    finish_cnv_batch();
    request_record();
    if (physical_writes != writes_before || physical_reads != reads_before ||
        !record_rejected_core_o || record_available_core_o)
        $fatal(1, "Mode00 admitted capture or readback");
    $display("FULL_RECORD_CHECK case=mode00_disabled writes=0 reads=0 declined=1");

    full_capture(2'd1, 0, 4, 20'h10200);
    full_read_record("mode01_immediate", 4, 4, 0, 4, 20'h10200, '0);

    full_capture(2'd2, 6, 4, 20'h30400);
    full_read_record("mode10_latest", 4, 4, 0, 4, 20'h30400, '0);

    // 先真实写超过一圈。3FFB 是事件物理位置，冻结后的首物理地址是 3FFF，
    // 但线上的第一个事后字仍固定是 16381（0-based 16380），与上面的指针分开核。
    full_capture(2'd3, 2*FULL_DEPTH-5, 4, FULL_TAG_HISTORY);
    full_read_record("mode11_wrapped", FULL_DEPTH, 2*FULL_DEPTH-1, 0, 4, FULL_TAG_HISTORY, '0);

    // 早事件只有三笔当次前历史。在已证明有定义的旧 RAM 上复核其余位置。
    full_capture(2'd3, 3, 8, FULL_TAG_EARLY);
    full_read_record("mode11_early_known_previous", FULL_DEPTH, 11, 2*FULL_DEPTH-1, 8,
                     FULL_TAG_EARLY, FULL_TAG_HISTORY);
endtask
