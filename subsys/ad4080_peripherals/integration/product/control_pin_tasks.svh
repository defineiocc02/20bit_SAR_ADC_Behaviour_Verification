// =============================================================================
// 文件名     : control_pin_tasks.svh
// 模块名     : 产品台架的配置 SPI 电源原生用例
// 版本       : v1.0
// 功能       : 通过真实 SPI 逐字段检查 0x02、两步 0x00 和 0x18。
// 设计说明   : 跟踪实际 AON 接受沿和数字 LDO 控制线。供电资格模拟器
//              独立驱动，结论仅为逻辑控制与状态和复位次序。
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================

// =============================================================================
// 1. 冷启动、读回与唤醒时基
// =============================================================================
// [初始化] 改内/外供模式只允许在硬 POR 会话之间。用同一 fixture
// 完成 OTP 协议、原 300 µs 等待及物理许可同步后再下指令。
task automatic control_pin_boot(input bit external_supply);
    @(negedge clk_aon_i);
    por_d_ok_async_i = 1'b0;
    external_io_i = external_supply;
    vdd33_ok_async_i = 1'b1;
    iovdd_ok_async_i = 1'b1;
    ref_ok_async_i = 1'b1;
    vdd11_ldo_pd_safe_aon_i = 1'b0;
    wake_recover_done_aon_i = 1'b0;
    mode_req_valid_aon_i = 1'b0;
    sw_reset_req_aon_i = 1'b0;
    sample_source_idle_core_i = 1'b1;
    repeat (3) @(negedge clk_aon_i);
    por_d_ok_async_i = 1'b1;
    wait (conversion_allow_core_o && record_ready_core_o);
    repeat (6) @(negedge clk_aon_i);
    $display("POWER_STAGE BOOT_READY external=%b t=%0t", external_supply, $time);
endtask

// 读取实际 SDO 字节才能区分暂存写、AON 受理、寄存器提交和写拒绝。
// 这里不绕开 SPI 访问内部影子，以免把接收了命令误当作主机可见。
task automatic control_pin_read(input logic [14:0] address, input logic [7:0] expected);
    logic [15:0] word;
    spi_access(1'b1, address, 1, 16'b0, word);
    if (word[7:0] !== expected)
        $fatal(1, "Power configuration readback address=%h got=%h expected=%h", address, word[7:0], expected);
endtask

// [唤醒] 只读内部接受沿作为时间戳，所有命令仍由原 SPI 引脚发送。
// 台架独立选择附加恢复完成时间，不能用它得出模拟 t_recover。
task automatic control_pin_wake(input realtime minimum_ns);
    realtime accepted_ns;
    fork
        write_reg(15'h0002, 1, 16'h0000);
        begin
            wait (dut.u_services.mode_accept_aon);
            @(posedge clk_aon_i);
            accepted_ns = $realtime;
            wait (wake_recover_req_aon_o);
            if ($realtime-accepted_ns < minimum_ns || conversion_allow_core_o)
                $fatal(1, "SPI wake command bypassed AON guard or analog recovery");
        end
    join
    repeat (3) @(negedge clk_aon_i);
    if (conversion_allow_core_o) $fatal(1, "Wake completed before external recovery qualification");
    // 读回已提交的 00，但 CNV 在新的恢复资格到达前仍不可用。
    control_pin_read(15'h0002, 8'h00);
    wake_recover_done_aon_i = 1'b1;
    wait (conversion_allow_core_o);
    @(negedge clk_aon_i);
    wake_recover_done_aon_i = 1'b0;
    control_pin_read(15'h0002, 8'h00);
endtask

// =============================================================================
// 2. 从主机命令追踪电源状态和异常出口
// =============================================================================
task automatic run_control_pin_check();
    int rejects_before;
    realtime accepted_reset_ns, released_reset_ns;
    bit restricted_writes_done;
    // 先在双外供样本检查不可关电，再硬重启切换至内供资格。
    // 两组采用同一套真实 SPI，差别来自 supervisor 的供电模式输入。
    control_pin_boot(1'b1);
    if (vdd11_ldo_en_o || iovdd_ldo_en_o) $fatal(1, "External rails must not request either internal LDO");
    control_pin_read(15'h0002, 8'h00);
    control_pin_read(15'h0018, 8'h00);
    rejects_before = config_rejects;
    write_reg(15'h0018, 1, 16'h0001);
    repeat (8) @(negedge clk_aon_i);
    if (config_rejects != rejects_before + 1 || vdd11_ldo_en_o || iovdd_ldo_en_o)
        $fatal(1, "External rail PD write changed control or lacked denial");
    control_pin_read(15'h0018, 8'h00);

    // [空闲] 第二次写必须重新检查实际忙状态，首写接受不能预授权。
    write_reg(15'h000A, 1, 16'h006C);
    write_reg(15'h0000, 1, 16'h0081);
    sample_source_idle_core_i = 1'b0;
    rejects_before = config_rejects;
    write_reg(15'h0000, 1, 16'h0000);
    if (config_rejects != rejects_before + 1 || !conversion_allow_core_o)
        $fatal(1, "Non-idle second reset write was not blocked");
    control_pin_read(15'h000A, 8'h6C);
    sample_source_idle_core_i = 1'b1;
    $display("POWER_STAGE NONIDLE_RESET_REJECT t=%0t", $time);

    // [停钟] 一个完整 SPI 候选暂存至 AON 工作。读回仍为原模式，
    // 后一候选应有拒绝，不能覆盖尚未执行的命令。
    @(negedge clk_aon_i);
    aon_clock_enabled = 1'b0;
    write_reg(15'h0002, 1, 16'h0002);
    control_pin_read(15'h0002, 8'h00);
    rejects_before = config_rejects;
    write_reg(15'h0002, 1, 16'h0003);
    if (config_rejects != rejects_before + 1 || !conversion_allow_core_o)
        $fatal(1, "Stopped AON issued a fake mode success or overwrote the queued write");
    control_pin_read(15'h0002, 8'h00);
    aon_clock_enabled = 1'b1;
    repeat (8) @(negedge clk_aon_i);
    if (conversion_allow_core_o) $fatal(1, "Committed standby must stop new conversions");
    control_pin_read(15'h0002, 8'h02);
    rejects_before = config_rejects;
    write_reg(15'h0002, 1, 16'h0003);
    repeat (8) @(negedge clk_aon_i);
    if (config_rejects != rejects_before + 1) $fatal(1, "AON did not deny direct standby to sleep");
    control_pin_read(15'h0002, 8'h02);
    $display("POWER_STAGE STANDBY_COMMITTED t=%0t", $time);
    control_pin_wake(100_000.0);
    // 从 Normal 才能进入 Sleep。前一次直接从 Standby 下发相同字节
    // 已被 AON 拒绝，此处重用值，验证路径差异由已提交状态决定。
    write_reg(15'h0002, 1, 16'h0003);
    repeat (8) @(negedge clk_aon_i);
    control_pin_read(15'h0002, 8'h03);
    if (conversion_allow_core_o) $fatal(1, "Committed sleep must stop new conversions");
    control_pin_wake(180_000.0);
    $display("PASS SPI_AON_MODE stopped AON pending, busy reject, actual standby/sleep readback and fresh wake");

    // [软复位] AON 真正接到 SPI 第二写才撤许可，之后 bank 同步重置。
    write_reg(15'h0000, 1, 16'h0081);
    fork
        write_reg(15'h0000, 1, 16'h0000);
        begin
            wait (dut.u_services.sw_accept_aon);
            @(posedge clk_aon_i);
            accepted_reset_ns = $realtime;
            wait (!conversion_allow_core_o);
        end
    join
    // p.28 允许软初始化中纯读0x11。必须等 AON 已执行并由真实 bank
    // 消费回执，第一读和第二读都不能顺手累积 p.82 的非法访问状态。
    wait (dut.u_dsp.reset_poll_only);
    control_pin_read(15'h0011, 8'h80);
    control_pin_read(15'h0011, 8'h80);
    wait (conversion_allow_core_o);
    released_reset_ns = $realtime;
    if ($realtime-accepted_reset_ns < 300_000.0) $fatal(1, "AON software reset guard was shortened");
    control_pin_read(15'h0011, 8'h00);
    control_pin_read(15'h000A, 8'h00);
    control_pin_read(15'h0014, 8'h09);
    $display("PASS SPI_AON_SOFT_RESET two writes, AON guard elapsed %0.3f ns, scratch reset and POR defaults",
             released_reset_ns-accepted_reset_ns);

    control_pin_boot(1'b0);
    if (!vdd11_ldo_en_o || !iovdd_ldo_en_o) $fatal(1, "Internal rails require startup LDO requests");
    rejects_before = config_rejects;
    write_reg(15'h0018, 1, 16'h0002);
    repeat (8) @(negedge clk_aon_i);
    if (config_rejects != rejects_before + 1 || !vdd11_ldo_en_o)
        $fatal(1, "Unbound VDD11 shutdown permission was ignored");
    control_pin_read(15'h0018, 8'h00);

    // [真实控制线] 不改 PG 或模拟电压，直接见证控制位使 I/O 先隔离。
    fork
        write_reg(15'h0018, 1, 16'h0001);
        begin
            wait (dut.u_services.isolate_io_o);
            #1ps;
            if (!iovdd_ldo_en_o) $fatal(1, "LDO removal preceded I/O isolation");
            wait (!iovdd_ldo_en_o);
        end
    join
    if (!conversion_allow_core_o || !vdd11_ldo_en_o) $fatal(1, "I/O shutdown disabled the core rail");
    // 内供 I/O 关闭后仍允许 VDD33 合格的管理写。SDO 应为无驱动。
    // 连续监视 OE 覆盖地址、数据和事务间隔，抓主机只在数据尾采样
    // 时会漏掉的短时驱动。软复位须保留为停供后的实际恢复入口。
    restricted_writes_done = 1'b0;
    fork
        begin
            rejects_before = config_rejects;
            spi_write_without_sdo(15'h000A, 1, 16'h0022);
            if (config_rejects != rejects_before + 1) $fatal(1, "Normal CSR write accepted without IOVDD");
            spi_write_without_sdo(15'h0000, 1, 16'h0081);
            spi_write_without_sdo(15'h0000, 1, 16'h0000);
            restricted_writes_done = 1'b1;
        end
        begin
            while (!restricted_writes_done) begin
                @(negedge clk_core_i);
                if (sdo_oe) $fatal(1, "Restricted configuration drove SDO without IOVDD permission");
            end
        end
    join
    wait (iovdd_ldo_en_o && dut.u_dsp.reset_poll_only);
    // 接口块仍在复位，0x11 例外也须实时核供电。仅驱动监测逻辑，
    // 不在 VDD33 未合格时实际发送 SPI 输入波形。
    @(negedge clk_aon_i);
    vdd33_ok_async_i = 1'b0;
    #1ns;
    if (dut.u_dsp.reset_poll_only || sdo_oe) $fatal(1, "Polling remained enabled without VDD33");
    vdd33_ok_async_i = 1'b1;
    wait (dut.u_dsp.reset_poll_only);
    @(negedge clk_aon_i);
    iovdd_ok_async_i = 1'b0;
    #1ns;
    if (dut.u_dsp.reset_poll_only || sdo_oe) $fatal(1, "Polling remained enabled without IOVDD");
    iovdd_ok_async_i = 1'b1;
    wait (dut.u_dsp.reset_poll_only);
    // p.82 的真实非法访问在忙结束后仍留在 sticky。正在初始化的
    // 0x11 写不享受读例外，增加的诊断必须等待正常访问恢复才能 W1C。
    rejects_before = config_rejects;
    spi_write_without_sdo(15'h0011, 1, 16'h0080);
    if (config_rejects != rejects_before + 1) $fatal(1, "Polling incorrectly accepted a 0x11 write");
    control_pin_read(15'h0011, 8'h80);
    wait (conversion_allow_core_o);
    repeat (8) @(negedge clk_aon_i);
    control_pin_read(15'h0011, 8'h80);
    write_reg(15'h0011, 1, 16'h0080);
    control_pin_read(15'h0011, 8'h00);
    control_pin_read(15'h0018, 8'h00);
    $display("PASS SPI_INIT_POLL p28 pure read busy and auto-clear, p82 illegal write and W1C, no polling without IO/VDD33");

    // [核心关断] 此资格位由测试环境模拟独立执行方的授权。输出本身
    // 即为验据。物理掉电另施加 POR，以便同一台架恢复到外供模式。
    vdd11_ldo_pd_safe_aon_i = 1'b1;
    write_reg(15'h0018, 1, 16'h0002);
    wait (!vdd11_ldo_en_o);
    if (conversion_allow_core_o || iovdd_ldo_en_o || !dut.u_services.isolate_io_o)
        $fatal(1, "VDD11 LDO request dropped before data and I/O shutdown");
    control_pin_boot(1'b1);
    control_pin_read(15'h0018, 8'h00);
    $display("PASS SPI_AON_LDO external/unqualified reject, IO isolation first, restricted reset restores, qualified core request");
    $display("AD4080_PRODUCT_POWER_CONFIG PASS");
endtask
