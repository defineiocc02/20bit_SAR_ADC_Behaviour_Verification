// =============================================================================
// 文件名     : result_spi_tasks.svh
// 模块名     : 外围产品结果 SPI 引脚用例
// 版本       : v1.0
// 功能       : 真实捕获、四银行读回、图 87/88 的原始 DCLK 逐沿验收。
// 设计说明   : 主机只看实际 FIFO_FULL GPIO。第一下降沿不等内部载荷
//              邮箱，后续每个原始 DCLK 都比对实际引脚。没有按 valid 压缩。
//              两字用不同符号、非对称位形的独立物理序列，捕获真实路径。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

localparam logic [55:0] FIG87_WIRE = 56'hAA_F81234_012468;
localparam logic [79:0] FIG88_WIRE = 80'hF0F0F0F0_F184C2_084261;
// 单线期望在独立 SDOB 上，四线期望已按物理接线 A/B/C/D 固定排列。
// 两个真样本含不同符号和不同低位，不重用 RTL 的位移或 lane 映射。
bit spi_pin_observe = 0;
int spi_pin_falls, spi_pin_first_count, spi_pin_last_count, spi_pin_first_at, spi_pin_last_at;
int spi_pin_done_count, spi_pin_failed_count;
realtime spi_pin_last_store_time;

// =============================================================================
// 1. 实物接口替身的采样点与异步状态回读
// =============================================================================
// 在 DCLK 下降沿计数前台架更新其序号。状态事件由已经发出的物理位组
// 回到 IO 域，须仍对应那个真实边沿，而非前面更早的 SRAM 预取路径。
always @(posedge clk_io_i) begin
    if (spi_pin_observe && first_payload_word_load_io_o) begin
        spi_pin_first_count++;
        spi_pin_first_at = spi_pin_falls;
    end
    if (spi_pin_observe && last_payload_word_load_io_o) begin
        spi_pin_last_count++;
        spi_pin_last_at = spi_pin_falls;
    end
end

always @(posedge clk_core_i) begin
    // 完成和失败是核心单拍脉冲，可能发生在最后 DCLK 的高半周期或
    // 直到 DCS 已释放才到；持续监视比主机循环后再等待脉冲可靠。
    if (spi_pin_observe && dut.write_req && dut.write_ready) spi_pin_last_store_time = $realtime;
    if (spi_pin_observe && record_done_core_o) spi_pin_done_count++;
    if (spi_pin_observe && record_failed_core_o) spi_pin_failed_count++;
end

task automatic spi_result_setup(input bit quad);
    logic [15:0] readback;
    // 让每个读窗口都来自一个真正的新记录。配置 CS 保持和结果 DCS 分离。
    write_reg(15'h001C, 1, 16'h0000);
    write_reg(15'h0029, 1, 16'h0000);
    configure_calibration(16'h0000, 16'h0200);
    write_reg(15'h0019, 1, 16'h000D);
    write_reg(15'h001B, 1, 16'h0012);           // GPIO3 FULL，GPIO2 READ_DONE
    write_reg(15'h0015, 1, quad ? 16'h0045 : 16'h0041);
    wait (continuous_config_ready_core_o);
    spi_access(1, 15'h0015, 1, 0, readback);
    if (readback !== (quad ? 16'h0045 : 16'h0041) || gpio_oe[3:2] !== 2'b11)
        $fatal(1, "Official result SPI format or status GPIO failed to apply");
    if (result_sdo_oe_o !== (quad ? 4'b0001 : 4'b0000))
        $fatal(1, "Unselected result OE must only retain SDOD in four-lane mode");
    write_reg(15'h001E, 2, 16'h0002);
    write_reg(15'h001C, 1, 16'h0001);
endtask

task automatic spi_result_capture_two();
    // E1 建批，再给两笔真实完成码，最后两次 CNV 使原 DSP/捕获尾部退场。
    begin_bypass();
    cnv_step(1'b1, 20'h81234);
    cnv_step(1'b1, 20'h12468);
    finish_cnv_batch();
endtask

task automatic spi_result_raw_group(input bit quad, input int index, input bit fig_check);
    logic [3:0] expected, expected_oe;
    longint edge_ps;
    expected_oe = quad ? 4'b1111 : 4'b0100;
    expected = quad ? 4'(FIG88_WIRE >> (4*(19-index))) :
                       {1'b0, FIG87_WIRE[55-index], 2'b00};
    spi_pin_falls = index+1;
    edge_ps = longint'($realtime * 1000.0);
    result_dclk_i = 0;
    #10ns;
    // Mode 3 主机只在本次下降沿后的上升沿查新组，不能取平台生成的预测位。
    result_dclk_i = 1;
    if (fig_check) begin
        expect_pin(quad ? "FIG88" : "FIG87", "DCLK", "WIRE_ABCD", index+1, edge_ps,
                   int'(result_sdo_o), int'(expected));
        expect_pin(quad ? "FIG88" : "FIG87", "DCLK", "SDO_OE", index+1, edge_ps,
                   int'(result_sdo_oe_o), int'(expected_oe));
        if (index < 8)
            expect_pin(quad ? "FIG88" : "FIG87", "DCLK", "FIFO_FULL_IN_HEADER", index+1, edge_ps, int'(gpio_o[3]), 1);
    end else if (result_sdo_o !== expected || result_sdo_oe_o !== expected_oe)
        $fatal(1, "Physical result prefix before interruption, edge %0d", index+1);
    #10ns;
endtask

// =============================================================================
// 2. 正常读取、未满拒绝和活动 DCS 退出
// =============================================================================
task automatic spi_result_try_empty(input bit quad, input bit hold_dcs_low = 1'b0);
    // 未满的物理下降沿不能启动假头，不因为事务内部握手未成功而跳过采样。
    // 四线的 D 仍按表6可驱动，但其它引脚必须没有资格。
    if (gpio_o[3] || record_available_core_o) $fatal(1, "Empty SPI fixture has a captured record");
    result_dcs_ni = 0;
    #1ps;
    result_dclk_i = 0;
    #10ns;
    result_dclk_i = 1;
    if (result_sdo_oe_o !== (quad ? 4'b0001 : 4'b0000) || record_done_core_o ||
        first_payload_word_load_io_o || last_payload_word_load_io_o)
        $fatal(1, "Unarmed physical DCLK incorrectly acquired output ownership");
    if (!hold_dcs_low) result_dcs_ni = 1;
    #20ns;
endtask

task automatic spi_result_transaction(input bit quad, input bit close_early, input bit lose_power,
                                      input bit standby_read = 1'b0, input bit early_dcs_held = 1'b0);
    int writes_before, reads_before, done_before, failures_before, expected_groups, observed_groups;
    bit standby_entered;
    realtime full_seen_time;
    logic held_sdod;
    writes_before = physical_writes;
    reads_before = physical_reads;
    done_before = spi_pin_done_count;
    failures_before = spi_pin_failed_count;
    spi_pin_first_count = 0;
    spi_pin_last_count = 0;
    spi_pin_first_at = 0;
    spi_pin_last_at = 0;
    spi_pin_falls = 0;
    expected_groups = quad ? 20 : 56;
    observed_groups = (close_early || lose_power) ? 9 : expected_groups;
    standby_entered = 1'b0;
    if (early_dcs_held) spi_result_try_empty(quad, 1'b1);

    fork
        begin
            spi_result_capture_two();
            if (standby_read) begin
                // 已冻结的物理银行保持原样，未选 DCS 不得预读锁住捕获。
                // 真配置 SPI 写表11操作模式，等许可的退出而非直接改电源状态。
                write_reg(15'h0002, 1, 16'h0002);
                repeat (40) @(negedge clk_core_i);
                // 待机时只能淘换已经存在的记录。若 SPI 模式本身触发了
                // 隐式读，FULL 后的 read 计数会在 DCS 前先暴露问题。
                if (conversion_allow_core_o || !gpio_o[3] || physical_reads != reads_before ||
                    continuous_da_oe_o || continuous_db_oe_o || continuous_dco_oe_o)
                    $fatal(1, "External standby: mode=%0d conversion=%b full=%b reads=%0d LVDS_oe=%b%b%b read_epoch=%b",
                           dut.pwr_mode, conversion_allow_core_o, gpio_o[3], physical_reads-reads_before,
                           continuous_da_oe_o, continuous_db_oe_o, continuous_dco_oe_o, io_record_epoch_o);
                standby_entered = 1'b1;
            end
        end
        begin
            // 并行等待真实 GPIO，从最后写的同一个时刻立刻发起主机访问。
            // 不调用调试 record command，也不等待内部载荷 ready。
            wait (gpio_o[3]);
            full_seen_time = $realtime;
            #1ps;
            if (gpio_o[3] !== 1'b1 || physical_writes != writes_before+2 ||
                full_seen_time != spi_pin_last_store_time)
                $fatal(1, "Visible FULL was not asserted on the final physical write");
            if (standby_read) wait (standby_entered);
            if (early_dcs_held) begin
                // 历史未满窗口跨过 FULL 也不能变成合法读。直接给它再来一个
                // 真实 DCLK，独立等核心也没见读受理，才用新 DCS 取得该记录。
                result_dclk_i = 0;
                #10ns;
                result_dclk_i = 1;
                // 以核心周期观察，覆盖跨域选择事件即使迟到也不能授权。
                repeat (10) @(negedge clk_core_i);
                if (physical_reads != reads_before || !gpio_o[3] ||
                    result_sdo_oe_o !== (quad ? 4'b0001 : 4'b0000))
                    $fatal(1, "Unselected old DCS acquired the newly complete record");
                result_dcs_ni = 1;
                #20ns;
                $display("PASS DCS asserted before FULL stayed unselected across real last write; no physical reads");
            end
            if (result_spi_ready_io_o)
                $fatal(1, "Earliest DCS test accidentally waited for payload prefetch");
            result_dcs_ni = 0;
            #1ps;
            $display("RESULT_SPI_DCS,lanes=%0d,full_ns=%0.3f,first_fall_ns=%0.3f,standby=%0d,old_window=%0d",
                     quad ? 4 : 1, full_seen_time, $realtime, standby_read, early_dcs_held);
            for (int idx = 0; idx < observed_groups; idx++) begin
                spi_result_raw_group(quad, idx, !close_early && !lose_power);
                // 在合法 DCS 内将 DCLK 保持在高电平，再从下一原序号继续。
                // 正常最大速率的图 87/88 用例没有该停顿，故障用例另验恢复时基。
                if (close_early && idx == 3) begin
                    held_sdod = result_sdo_o[0];
                    #200ns;
                    if (result_sdo_o[0] !== held_sdod || result_sdo_oe_o !== (quad ? 4'b1111 : 4'b0100))
                        $fatal(1, "DCLK pause mutated selected output bit or direction");
                end
            end
            held_sdod = result_sdo_o[0];
            if (lose_power) begin
                // 让 DCS 继续有效、DCLK 不再变，隔离必须由供电撤销本身
                // 产生，不能借后面的解除片选掩盖掉电时保留驱动的缺陷。
                iovdd_ok_async_i = 0;
                #1ps;
                if (result_sdo_oe_o !== 4'b0000) $fatal(1, "I/O power loss failed to isolate SDOD and other lanes");
            end
            result_dcs_ni = 1;
            #1ps;
            if (!lose_power && (result_sdo_oe_o !== (quad ? 4'b0001 : 4'b0000) ||
                (quad && result_sdo_o[0] !== held_sdod)))
                $fatal(1, "DCS release must disable A/B/C and retain powered quad SDOD drive/value");
        end
    join

    repeat (40) begin
        @(negedge clk_core_i);
        if (spi_pin_done_count != done_before || spi_pin_failed_count != failures_before) break;
    end
    // 因首末事件经时钟域返回，不绑定 GPIO 与外部 DCLK 的模拟传播时间。
    // 比较所得事件归属哪一个已采到的下降沿，并证明最后结果与端到端完成分开。
    if (close_early || lose_power) begin
        if (spi_pin_failed_count != failures_before+1 || spi_pin_done_count != done_before || spi_pin_last_count != 0)
            $fatal(1, "Aborted SPI was treated as a successful FIFO record or last sample launch");
        $display("PASS interrupted result SPI lanes=%0d actual_DCLK=%0d close=%0d loss=%0d no last/done",
                 quad ? 4 : 1, spi_pin_falls, close_early, lose_power);
    end else begin
        if (spi_pin_done_count != done_before+1 || spi_pin_failed_count != failures_before ||
            spi_pin_first_count != 1 || spi_pin_last_count != 1 || spi_pin_first_at != 9 ||
            spi_pin_last_at != (quad ? 15 : 33) || gpio_o[3] || !gpio_o[2])
            $fatal(1, "Physical first/last FIFO status boundary or successful prefix mismatch");
        $display("PASS result SPI lanes=%0d prefix=%0d real_DCLK_20ns first_load=%0d last_load=%0d DCS_release=complete",
                 quad ? 4 : 1, spi_pin_falls, spi_pin_first_at, spi_pin_last_at);
        if (standby_read) begin
            if (physical_writes != writes_before+2 || conversion_allow_core_o ||
                continuous_da_oe_o || continuous_db_oe_o || continuous_dco_oe_o)
                $fatal(1, "Standby SPI substituted fresh conversion data or enabled LVDS");
            $display("PASS externally powered standby used actual DCS/DCLK to read the two old SRAM words, no new write or LVDS");
        end
    end
    #40ns;
endtask

task automatic run_result_spi_pin_check();
    // 更改输入时基只在空闲边界。实际 DCLK 每周期 20ns，服务 CLK 2.5ns。
    @(negedge clk_io_i);
    io_half_period = 1.25ns;
    repeat (4) @(negedge clk_io_i);
    spi_pin_observe = 1'b1;
    spi_result_setup(1'b0);
    spi_result_try_empty(1'b0);
    spi_result_transaction(1'b0, 1'b0, 1'b0);
    spi_result_setup(1'b1);
    spi_result_try_empty(1'b1);
    spi_result_transaction(1'b1, 1'b0, 1'b0);
    spi_result_setup(1'b1);
    spi_result_transaction(1'b1, 1'b1, 1'b0);
    // 重新开始必须由原捕获生成新记录。中断后重新完成一次真实 SPI 回归。
    spi_result_setup(1'b1);
    spi_result_transaction(1'b1, 1'b0, 1'b0, 1'b0, 1'b1);
    spi_result_setup(1'b0);
    spi_result_transaction(1'b0, 1'b0, 1'b0, 1'b1);
    control_pin_wake(100_000.0);
    spi_result_setup(1'b1);
    spi_result_transaction(1'b1, 1'b0, 1'b1);
    iovdd_ok_async_i = 1;
    wait (io_record_epoch_o);
    write_reg(15'h001C, 1, 16'h0000);
    write_reg(15'h0015, 1, 16'h0040);
    wait (continuous_config_ready_core_o);
    spi_pin_observe = 1'b0;
    @(negedge clk_io_i);
    io_half_period = 11ns;
    $display("AD4080_RESULT_SPI_PINS PASS");
endtask
