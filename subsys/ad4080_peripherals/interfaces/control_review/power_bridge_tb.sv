`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : power_bridge_tb.sv
// 模块名     : power_bridge_tb
// 版本       : v1.0
// 功能       : 在 SPI 引脚到真实 AON 服务壳间检查三项控制事务。
// 设计说明   :
//   * 使用冻结 spi_slave 和 ad4080_regs，测试刺激从实际串行 IO 进入。
//     电源监督器按交付 RTL 运行，REFIN 故障由监督器的异步宏输入触发。
//   * 在同一 AON 时钟上核对合法软复位的服务受理和 bank 提交。保留
//     FIFO/事件状态仅用已知位作比对，避免宣称此夹具覆盖完整数据通路。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module power_bridge_tb;
    // =========================================================================
    // 1. 真实器件组成及待测桥接
    // =========================================================================
    logic clk = 0, por_d_ok = 0, ref_ok = 1;
    logic cs_n = 1, sclk = 1, sdi = 0;      // Mode 3，AON/配置执行时钟 100 MHz
    logic clients_idle = 1, recover_done = 0;
    wire rst_n, cfg_control, cfg_full, new_por, ana_reset_n, conv_allow;
    wire [1:0] mode_req, mode_active;
    wire mode_valid, mode_accept, wake_busy, recover_req;
    wire sw_req, sw_accept, bank_idle, bank_sw_reset;
    wire write_entity, bank_we, address_valid, read_only, access_supported;
    wire [1:0] write_bytes, entity_bytes;
    wire [15:0] addr, wdata, bank_rdata, spi_rdata, bank_long, spi_long;
    wire [7:0] bank_status, visible_status, status_short, if_status;
    wire sdo_data, sdo_drive;
    wire sdo_pin_enabled = sdo_drive && cfg_full;
    wire crc_en, send_status, single_inst, short_instr, addr_asc, strict_access;
    wire crc_err, clocks_err, partial_err, addr_err, ro_err, init_err;
    wire spi_reject, bank_reject, bridge_reject;
    wire [3:0] access_reason, spi_reason, bank_reason, bridge_reason;
    int checks = 0;
    int por_events = 0, simultaneous = 0, resets = 0;
    int rejected_bank = 0, rejected_bridge = 0;
    logic [3:0] last_bank, last_bridge;

    always #5 clk = ~clk;

    ad4080_pwr_supervisor #(.AON_MAX_CLK_HZ(100_000_000)) service (
        .clk_aon_i(clk), .clk_core_i(clk), .clk_io_i(clk), .external_io_i(1'b1),
        .por_d_ok_async_i(por_d_ok), .vdd33_ok_async_i(1'b1), .ref_ok_async_i(ref_ok),
        .iovdd_ok_async_i(1'b1), .ana_ready_async_i(1'b1),
        .clients_idle_aon_i(clients_idle), .sw_reset_req_aon_i(sw_req),
        .mode_req_valid_aon_i(mode_valid), .mode_req_aon_i(mode_req), .wake_recover_done_aon_i(recover_done),
        .cfg_hw_reset_no(rst_n), .core_reset_no(), .fifo_reset_no(), .io_reset_no(), .ana_reset_no(ana_reset_n),
        .cfg_init_done_aon_o(), .cfg_control_write_allow_aon_o(cfg_control),
        .cfg_full_access_allow_aon_o(cfg_full), .fifo_read_allow_aon_o(), .lvds_allow_aon_o(),
        .conversion_allow_aon_o(conv_allow), .operating_mode_aon_o(mode_active),
        .wake_busy_aon_o(wake_busy), .wake_recover_req_aon_o(recover_req),
        .mode_req_accept_aon_o(mode_accept), .sw_reset_accept_aon_o(sw_accept),
        .por_ana_event_aon_o(new_por), .isolate_io_o(), .iovdd_ldo_en_o(), .ref_cmo_en_o()
    );

    spi_slave #(.P_SHORT_ADDR_BITS(7), .P_WRITE_SDO_DRIVE(1)) spi (
        .clk(clk), .rst_n(rst_n), .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        .sdo(sdo_data), .sdo_oe(sdo_drive), .interface_ready(cfg_control),
        .crc_en(crc_en), .send_status(send_status), .single_inst(single_inst), .short_instr(short_instr),
        .addr_asc(addr_asc), .strict_access(strict_access), .status_long(spi_long), .status_short(status_short),
        .addr(addr), .wdata(wdata), .write_bytes(write_bytes), .we(write_entity),
        .rdata(spi_rdata), .entity_bytes(entity_bytes), .address_valid(address_valid), .read_only(read_only),
        .access_supported(access_supported), .access_reject_reason(access_reason),
        .crc_err_pulse(crc_err), .clk_cnt_err_pulse(clocks_err), .partial_err_pulse(partial_err),
        .addr_err_pulse(addr_err), .readonly_err_pulse(ro_err), .not_ready_err_pulse(init_err),
        .cfg_reject(spi_reject), .cfg_reject_reason(spi_reason)
    );

    // [对照] 冻结 bank 的状态位及解码保留。FIFO 和 HI_STATUS 两个可见位
    // 置高，验证桥覆盖 bit3 后仍能从 SPI 返回它们。
    ad4080_regs #(.P_MULTIBYTE_ORDER(0), .P_OFFSET_LSB_ADDR(32'h25),
                  .P_PRODUCT_ID_L(32'h50), .P_SHORT_ADDR_BITS(7)) bank (
        .clk(clk), .rst_n(rst_n), .addr(addr), .wdata(wdata), .write_bytes(write_bytes), .we(bank_we),
        .rdata(bank_rdata), .entity_bytes(entity_bytes), .address_valid(address_valid),
        .read_only(read_only), .access_supported(access_supported), .access_reject_reason(access_reason),
        .adc_idle(bank_idle), .gpio_levels(4'b0), .capture_active(1'b0),
        .capture_incomplete(1'b0), .capture_io_active(1'b0),
        .st_fifo_full(1'b1), .st_fifo_read_done(1'b0), .st_hi_flag(1'b1), .st_lo_flag(1'b0),
        .crc_err_pulse(crc_err), .clk_cnt_err_pulse(clocks_err), .partial_err_pulse(partial_err),
        .addr_err_pulse(addr_err), .readonly_err_pulse(ro_err), .not_ready_err_pulse(init_err),
        .hi_clr(), .lo_clr(), .offset_code(), .gain_code(), .filter_sel(), .dec_sel(), .fsync_pulse(),
        .fifo_mode(), .fifo_watermark(), .fifo_arm(), .fifo_clear(), .wm_wb_valid(1'b0), .wm_wb_data(15'd0),
        .event_hyst(), .event_hi(), .event_lo(), .event_en(), .hi_route(), .lo_route(),
        .gpio_dir(), .gpio_reg(), .gpio_sel(), .crc_en(crc_en), .send_status(send_status), .addr_asc(addr_asc),
        .single_inst(single_inst), .short_instr(short_instr), .strict_access(strict_access),
        .sw_reset_pulse(bank_sw_reset), .status(bank_status), .interface_status(if_status),
        .status_long(bank_long), .status_short(status_short), .cfg_reject(bank_reject), .cfg_reject_reason(bank_reason)
    );

    ad4080_csr_power_bridge bridge (
        .clk_i(clk), .rst_ni(rst_n), .spi_write_i(write_entity), .spi_addr_i(addr),
        .spi_wdata_i(wdata), .spi_bytes_i(write_bytes), .bank_rdata_i(bank_rdata),
        .bank_status_i(bank_status), .bank_status_long_i(bank_long), .spi_rdata_o(spi_rdata),
        .device_status_o(visible_status), .spi_status_long_o(spi_long),
        .bank_we_o(bank_we), .bank_adc_idle_o(bank_idle), .adc_idle_i(1'b1),
        .capture_incomplete_i(1'b0), .capture_io_active_i(1'b0), .clients_idle_i(clients_idle),
        .cfg_control_allow_i(cfg_control), .cfg_full_allow_i(cfg_full), .por_ana_event_i(new_por),
        .mode_active_i(mode_active), .mode_accept_i(mode_accept), .mode_req_valid_o(mode_valid),
        .mode_req_o(mode_req), .sw_reset_accept_i(sw_accept), .sw_reset_req_o(sw_req),
        .reject_o(bridge_reject), .reject_reason_o(bridge_reason)
    );

    // [同沿] 与真实 CSR 的复位脉冲逐拍对照。服务拒绝时同时要求 bank 保留。
    always @(posedge clk) begin
        bit accepted;
        accepted = rst_n && sw_accept;
        if (rst_n && new_por) begin
            por_events++;
            // 按 AON 提交沿统计碰撞。SPI 末边沿与参考输入的下降都先经过同步链。
            // 须看到 bank_we 和实际 bit3 写 1，才把本次新故障计为与清位同沿。
            if (bank_we && addr == 16'h14 && wdata[3]) simultaneous++;
        end
        if (accepted) resets++;
        #1;
        if (rst_n && bank_sw_reset !== accepted) $fatal(1, "bank/service reset edge diverged");
        if (rst_n && bank_reject) begin
            rejected_bank++;
            last_bank = bank_reason;
        end
        if (rst_n && bridge_reject) begin
            rejected_bridge++;
            last_bridge = bridge_reason;
        end
    end

    // =========================================================================
    // 2. 真实引脚 SPI 刺激
    // =========================================================================
    task automatic check(input bit ok, input string meaning);
        checks++;
        if (!ok) $fatal(1, "POWER_BRIDGE_ASSERT %s", meaning);
    endtask

    task automatic config_frame(
        input bit read_access, input logic [14:0] reg_address, input logic [7:0] data,
        input bit fault_at_last_bit,
        output logic [15:0] rx_header, output logic [7:0] rx_data
    );
        logic [23:0] tx_word, rx_word;
        tx_word = {read_access, reg_address, data};
        rx_word = '0;
        cs_n = 0;
        #80;
        for (int n = 23; n >= 0; n--) begin
            sdi = tx_word[n];
            sclk = 0;
            #65;
            sclk = 1;
            // REFIN 经监督器的事件同步链，和最后一位的 SPI 提交同时到达。
            // 同沿计数器确定碰撞实际发生，避免测试只依赖预计延迟。
            if (fault_at_last_bit && n == 0) ref_ok = 0;
            if (read_access && n < 8) check(sdo_pin_enabled, "read SDO owned with live IOVDD");
            rx_word[n] = sdo_data;
            #65;
        end
        cs_n = 1;
        #80;
        check(!sdo_pin_enabled, "CS high releases SDO");
        {rx_header, rx_data} = rx_word;
    endtask

    task automatic wr(input logic [14:0] a, input logic [7:0] val, input bit fault_last = 0);
        logic [15:0] header;
        logic [7:0] payload;
        config_frame(0, a, val, fault_last, header, payload);
    endtask

    task automatic rd(input logic [14:0] a, input logic [7:0] want);
        logic [15:0] header;
        logic [7:0] payload;
        config_frame(1, a, 0, 0, header, payload);
        check(payload === want, $sformatf("read %02h got %02h expected %02h", a, payload, want));
    endtask

    task automatic finish_wake;
        wait (recover_req);
        @(negedge clk);
        recover_done = 1;
        wait (!recover_req);
        @(negedge clk);
        recover_done = 0;
        wait (conv_allow);
        #80;
    endtask

    task automatic check_gpio_mux_register(input logic [14:0] address, input logic [7:0] prior);
        int old_bridge, old_bank;
        // 预装非复位的选择组合，先证明这个寄存器有可用的引脚写回路径。
        // 0x1A 的低/高字段对应 GPIO0/1，0x1B 对应 GPIO2/3。前值要让每个
        // 字段都能与候选有效值 7 区分，观察半个字节误提交时完整回读会失败。
        wr(address, prior);
        rd(address, prior);
        // 每个坏 nibble 与另一个有效且不同的 nibble 搭配，逐位验证整个字节原子保留。
        old_bridge = rejected_bridge;
        old_bank = rejected_bank;
        for (int sel = 10; sel <= 15; sel++) begin
            // 共用同一个配置地址改变两种坏码位置，另一字段的 7 有合法 GPIO 数据语义。
            // 0xA 覆盖基线会静默接受的禁用码。0xB–0xF 核对桥也会截住未定义值。
            // 两帧各自从同一预装值出发，对比每帧后完整的选择寄存器值。
            wr(address, {4'h7, 4'(sel)});
            check(rejected_bridge == old_bridge + 1 && last_bridge == 2, "low mux nibble rejected");
            rd(address, prior);
            wr(address, {4'(sel), 4'h7});
            check(rejected_bridge == old_bridge + 2 && last_bridge == 2, "high mux nibble rejected");
            rd(address, prior);
            old_bridge += 2;
        end
        // 原 bank 对部分未定义编码也能报错，只看最终寄存器值会漏验拦截层级。
        check(rejected_bank == old_bank, "blocked entity never dispatched to bank");
        // 合法但时序尚未绑定的编码 3 留给冻结 bank，桥诊断不应冒充其来源。
        // 通过高字段送入 3，低字段仍给 7。bank 若误提交低字段，末尾回读可检测。
        wr(address, 8'h37);
        check(rejected_bank == old_bank + 1 && last_bank == 2 && rejected_bridge == old_bridge,
              "FILTER_RDY remains a bank capability rejection");
        rd(address, prior);
    endtask

    // =========================================================================
    // 3. 原 CSR 与真实服务联合验收
    // =========================================================================
    initial begin
        int old_reject;
        logic [15:0] header;
        logic [7:0] payload;
        repeat (5) @(negedge clk);
        por_d_ok = 1;
        wait (cfg_full && conv_allow);
        #80;

        // [POR] 只使参考电压监测失效，核心 POR 和其他设备状态保留。
        rd(15'h14, 8'ha9);
        wr(15'h14, 8'h09);
        rd(15'h14, 8'ha0);
        wr(15'h0a, 8'h7b);
        // 本夹具保留数字电源，只有参考检测信号下降。用已清过 bit0 的状态
        // 和有内容的 Scratch 检查模拟故障没有引起配置硬复位。
        ref_ok = 0;
        #37;
        ref_ok = 1;
        #200;
        rd(15'h14, 8'ha8);
        rd(15'h0a, 8'h7b);
        check(bank_status == 8'ha0 && visible_status == 8'ha8 && por_events == 1,
              "frozen bank lacks REFIN input; bridge adds only POR_ANA");
        wr(15'h10, 8'h33);
        config_frame(1, 15'h14, 0, 0, header, payload);
        check(header == 16'h2880 && payload == 8'ha8, "long status bit7 and 0x14 agree");
        $display("OBS bridge_ref_event original=0x%02h SPI=0x%02h long=0x%04h",
                 bank_status, payload, header);
        // 夹具已经消费过前一次参考事件。这里再制造新事件并清旧 bit3，
        // 用碰撞计数和后续读回一起验收新故障优先。
        wr(15'h14, 8'h08, 1'b1);
        check(simultaneous == 1 && por_events == 2, "actual new POR and W1C coincide");
        ref_ok = 1;
        rd(15'h14, 8'ha8);
        wr(15'h14, 8'h08);
        rd(15'h14, 8'ha0);
        $display("OBS bridge_ref_w1c_collision count=%0d event_wins=1", simultaneous);

        old_reject = rejected_bridge;
        check_gpio_mux_register(15'h1a, 8'h40);
        check_gpio_mux_register(15'h1b, 8'h56);
        $display("OBS bridge_gpio addresses=0x1a,0x1b blocked=%0d reject=2 atomic=1 filter_rdy_bank=2",
                 rejected_bridge - old_reject);

        // [模式] 01 保留，未排空也拒绝。读回应当反映服务模式而非待提交写码。
        // 0x02 的模式码属于电源域。同数值在 FIFO 的 0x1C 有独立语义。
        old_reject = rejected_bridge;
        wr(15'h02, 8'h01);
        check(rejected_bridge == old_reject + 1 && last_bridge == 9, "mode 01 invalid");
        rd(15'h02, 0);
        old_reject = rejected_bridge;
        // 待机码 10 本身合法。拉低客户空闲复现服务拒绝，并区分于上面的保留码诊断。
        clients_idle = 0;
        wr(15'h02, 8'h02);
        check(rejected_bridge == old_reject + 1 && last_bridge == 8, "busy standby rejected");
        rd(15'h02, 0);
        clients_idle = 1;
        wr(15'h02, 8'h02);
        rd(15'h02, 8'h02);
        check(mode_active == 2 && !ana_reset_n && !conv_allow, "standby reaches power shell");
        // 退出请求一旦受理，寄存器显示 00。附加恢复握手完成前转换许可仍须为零。
        wr(15'h02, 8'h00);
        rd(15'h02, 0);
        check(wake_busy && !conv_allow, "mode readback 00 before conversion recovery");
        finish_wake();
        wr(15'h02, 8'h03);
        rd(15'h02, 8'h03);
        check(mode_active == 3 && !conv_allow, "sleep reaches power shell");
        wr(15'h02, 0);
        finish_wake();
        rd(15'h02, 0);
        // 在待机和睡眠遍历前写好的 Scratch 验证模式事务走保持路径。
        rd(15'h0a, 8'h7b);
        $display("OBS bridge_modes 00->10->00->11->00 reserved_and_busy_blocked=1");

        // [复位] 先制造在两次写之间有客户变忙的真实竞争。只有服务同沿
        // 接纳时 bank 才清寄存器。第二次成功后，走真实初始化窗口再通 SPI。
        wr(15'h00, 8'h20);
        wr(15'h00, 8'h81);
        old_reject = rejected_bank;
        clients_idle = 0;
        wr(15'h00, 0);
        check(rejected_bank == old_reject + 1 && last_bank == 8 && resets == 0,
              "service denies second reset, bank must retain");
        clients_idle = 1;
        rd(15'h0a, 8'h7b);
        rd(15'h14, 8'ha0);
        wr(15'h00, 8'h81);
        wr(15'h00, 0);
        check(resets == 1 && !cfg_control && !cfg_full, "same-edge reset enters initialization guard");
        wait (cfg_full);
        #80;
        rd(15'h0a, 0);
        rd(15'h00, 8'h30);
        rd(15'h14, 8'ha9);
        rd(15'h11, 8'h00);             // 当前已提交的复位帧没被自触发成 NOT_READY
        $display("OBS bridge_software_reset rejected=1 accepted=%0d SCRATCH=0 POR=0x09 ADDR_ASC=1", resets);
        $display("POWER_BRIDGE_COMPLETE checks=%0d por_events=%0d collisions=%0d resets=%0d",
                 checks, por_events, simultaneous, resets);
        $finish;
    end

    initial begin
        #2_000_000;
        $fatal(1, "power bridge watchdog");
    end
endmodule
`default_nettype wire
