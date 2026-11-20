`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : control_review_tb.sv
// 模块名     : control_review_tb
// 版本       : v1.0
// 功能       : 用真实 SPI 引脚对比冻结 DSP 顶层的默认行为和有据绑定行为。
// 设计说明   :
//   * 同一主机波形驱动原参数实例和绑定实例。所有写入均经 CS/SCLK/SDI。
//     绑定实例仅提供 Rev. B 和 ADI no-OS 可核实的控制参数。
//   * 事件刺激为已完成的并行码。等待处理充分退出后核对 GPIO 和 CSR，
//     以逻辑结果为观测点，转换 CNV 到 GPIO 的器件时延仍需集成实测。
//   * 缺失器件功能的测试复放当前基线行为。反例日志与一致性断言分列。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module control_review_tb;
    // =========================================================================
    // 1. 共同主机和两个冻结顶层实例
    // =========================================================================
    logic clk = 0, rst_n = 0;
    logic cs_n = 1, sclk = 1, sdi = 0;     // 配置 SPI Mode 3
    logic interface_ready = 1, adc_idle = 1;
    logic valid_in = 0;
    logic [19:0] x = 0;                     // 并行、有符号转换码
    logic [3:0] gpio_i = '1;
    wire [1:0] sdo, sdo_oe, cfg_reject, processing_reject;
    wire [1:0][3:0] cfg_reason, gpio_o, gpio_oe;
    wire [1:0][7:0] status;
    int checks = 0;
    int bound_bank_resets = 0;
    int bound_arm_writes = 0;
    int rejected[2];
    logic [3:0] last_reason[2];

    always #5 clk = ~clk;

    ad4080_top dut_default (
        // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
        .pwr_cfg_full_allow_i(1'b1), .pwr_cmd_ready_i(1'b0), .pwr_cmd_valid_o(),
        .pwr_cmd_op_o(), .pwr_cmd_value_o(), .pwr_rsp_valid_i(1'b0),
        .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
        .pwr_ldo_pd_i(2'b00), .rom_crc_error_i(1'b0), .por_ana_event_i(1'b0),

        .clk(clk), .rst_n(rst_n), .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        // 此套基线只验证配置契约。器件 CNV 与 PHY 时序能力均保持未绑定。
        .cnv_tick_i(1'b0),.conversion_timing_error_i(1'b0),
        .phy_fifo_full_i(1'b0),.phy_fifo_read_done_i(1'b0),
        .csr_fifo_full_i(1'b0),.csr_fifo_read_done_i(1'b0),.cnv_data_timing_error_o(),
        .data_config_ready_i(1'b1),.sample_tag_i(32'd0),
        .data_intf_config_a_o(),.data_intf_config_b_o(), .data_intf_config_c_o(),
        .continuous_bypass_o(), .continuous_enable_o(), .continuous_flush_o(), .filter_config_o(),.continuous_word_valid_o(),.continuous_word_o(),.continuous_word_tag_o(),
        .sdo(sdo[0]), .sdo_oe(sdo_oe[0]), .interface_ready(interface_ready), .adc_idle(adc_idle),
        .valid_in(valid_in), .x(x), .filter_sync(1'b0), .ext_event(1'b0), .arm(1'b0), .rd_en(1'b0),
        .gpio_i(gpio_i), .gpio_o(gpio_o[0]), .gpio_oe(gpio_oe[0]), .status(status[0]),
        .dout(), .dout_valid(), .correction_clip_low(), .correction_clip_high(),
        .cfg_reject(cfg_reject[0]), .cfg_reject_reason(cfg_reason[0]), .processing_reject(processing_reject[0])
    );

    ad4080_top #(
        .P_MULTIBYTE_ORDER(0),              // 左右字节按 ADDR_ASCENSION 选择线序
        .P_OFFSET_LSB_ADDR(32'h25),         // Rev. B 第 74 页、表 60、ADI no-OS
        .P_PRODUCT_ID_L(32'h50),            // Rev. B 表 36，ADI no-OS AD4080_PRODUCT_ID
        .P_SHORT_ADDR_BITS(7),              // Rev. B 表 33
        .P_WRITE_SDO_DRIVE(1)               // Rev. B 第 35 页，写数据段驱动零
    ) dut_bound (
        .clk(clk), .rst_n(rst_n), .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        // 此套基线只验证配置契约。器件 CNV 与 PHY 时序能力均保持未绑定。
        .cnv_tick_i(1'b0),.conversion_timing_error_i(1'b0),
        .phy_fifo_full_i(1'b0),.phy_fifo_read_done_i(1'b0),
        .csr_fifo_full_i(1'b0),.csr_fifo_read_done_i(1'b0),.cnv_data_timing_error_o(),
        .data_config_ready_i(1'b1),.sample_tag_i(32'd0),
        .data_intf_config_a_o(),.data_intf_config_b_o(), .data_intf_config_c_o(),
        .continuous_bypass_o(), .continuous_enable_o(), .continuous_flush_o(), .filter_config_o(),.continuous_word_valid_o(),.continuous_word_o(),.continuous_word_tag_o(),
        .sdo(sdo[1]), .sdo_oe(sdo_oe[1]), .interface_ready(interface_ready), .adc_idle(adc_idle),
        .valid_in(valid_in), .x(x), .filter_sync(1'b0), .ext_event(1'b0), .arm(1'b0), .rd_en(1'b0),
        .gpio_i(gpio_i), .gpio_o(gpio_o[1]), .gpio_oe(gpio_oe[1]), .status(status[1]),
        .dout(), .dout_valid(), .correction_clip_low(), .correction_clip_high(),
        .cfg_reject(cfg_reject[1]), .cfg_reject_reason(cfg_reason[1]), .processing_reject(processing_reject[1])
    );

    always @(negedge clk) begin
        // 拒绝是单 clk 脉冲，等待 SPI 帧结束才采样会漏掉原始原因。
        for (int i = 0; i < 2; i++) begin
            if (rst_n && cfg_reject[i]) begin
                rejected[i]++;
                last_reason[i] = cfg_reason[i];
            end
        end
        // 只观察原顶层内部脉冲，生产端口未把它接到电源服务壳。
        if (rst_n && dut_bound.unused_sw_reset_pulse) bound_bank_resets++;
        // 记录真实 SPI 译码出的非零 FIFO_MODE 写，便于证明主机坏读回分支未发 arm。
        if (rst_n && dut_bound.we && dut_bound.addr == 16'h1c && dut_bound.wdata[1:0] != 0)
            bound_arm_writes++;
    end

    // =========================================================================
    // 2. SPI 主机与断言
    // =========================================================================
    task automatic check(input bit ok, input string label);
        checks++;
        if (!ok) $fatal(1, "CONTROL_REVIEW_ASSERT %s", label);
    endtask

    task automatic spi_bit(input bit tx, output logic [1:0] rx, output logic [1:0] drive);
        sdi = tx;
        sclk = 0;
        #65;
        sclk = 1;         // 上升沿抽取，给下降沿和双级同步足够的模型时钟
        rx = sdo;
        drive = sdo_oe;
        #65;
    endtask

    task automatic spi_frame(
        input bit rd, input logic [14:0] address, input int octets,
        input logic [15:0] sent,
        output logic [1:0][15:0] received, output logic [1:0][15:0] header,
        output logic [1:0] data_driven
    );
        logic [15:0] command_word;
        logic [1:0] b, oe;
        received = '0;
        header = '0;
        data_driven = '1;
        command_word = {rd, address};
        cs_n = 0;
        #80;
        for (int i = 15; i >= 0; i--) begin
            spi_bit(command_word[i], b, oe);
            header[0][i] = b[0];
            header[1][i] = b[1];
        end
        // 线序的第一字节总放在 sent[15:8]，不隐含寄存器大小端。
        for (int i = 15; i >= 16 - octets*8; i--) begin
            spi_bit(sent[i], b, oe);
            received[0][i] = b[0];
            received[1][i] = b[1];
            data_driven &= oe;
        end
        cs_n = 1;
        #80;
        check(sdo_oe == 2'b00, "CS high releases both SDO drivers");
    endtask

    task automatic write8(input logic [14:0] a, input logic [7:0] v);
        logic [1:0][15:0] rx, header;
        logic [1:0] oe;
        spi_frame(0, a, 1, {v, 8'b0}, rx, header, oe);
    endtask

    task automatic write16(input logic [14:0] a, input logic [15:0] on_wire);
        logic [1:0][15:0] rx, header;
        logic [1:0] oe;
        spi_frame(0, a, 2, on_wire, rx, header, oe);
    endtask

    task automatic read_bound(input logic [14:0] a, input int n, input logic [15:0] on_wire);
        logic [1:0][15:0] rx, header;
        logic [1:0] oe;
        spi_frame(1, a, n, 16'b0, rx, header, oe);
        check(oe[1] && rx[1] === on_wire,
              $sformatf("read %04h got %04h oe %b expected %04h", a, rx[1], oe[1], on_wire));
    endtask

    task automatic try_watermark_arm(
        input bit program_entity, input logic [15:0] watermark, input logic [1:0] fifo_mode,
        output bit armed, output logic [15:0] observed
    );
        logic [1:0][15:0] rx, header;
        logic [1:0] oe;
        logic [7:0] general;
        logic [14:0] start_addr;
        logic [15:0] payload;
        bit ascend;
        armed = 0;
        observed = '0;
        // 模式 11 要求四样本粒度。主机锁住配置序列，先确认严格实体访问及未 arm。
        if (watermark == 0 || watermark > 16'd16384 || fifo_mode == 0 ||
            (fifo_mode == 3 && watermark[1:0] != 0)) return;
        // 主机收单字节时放在 rx[15:8]。因此两个控制寄存器的 bit5 均落在
        // rx[13]，这里用实际回读选择实体的起始地址及线序。
        spi_frame(1, 15'h10, 1, 0, rx, header, oe);
        if (!oe[1] || !rx[1][13]) return;
        spi_frame(1, 15'h00, 1, 0, rx, header, oe);
        if (!oe[1]) return;
        ascend = rx[1][13];
        spi_frame(1, 15'h1c, 1, 0, rx, header, oe);
        if (!oe[1] || rx[1][9:8] != 0) return;
        general = rx[1][15:8];
        start_addr = ascend ? 15'h1d : 15'h1e;
        payload = ascend ? {watermark[7:0], watermark[15:8]} : watermark;
        // program_entity=0 仅供负例，检查用旧主机方式配置后守卫能拦住错误。
        // 正常主机以单 CS 帧提交完整值，再独立读取同一完整实体。
        if (program_entity) write16(start_addr, payload);
        spi_frame(1, start_addr, 2, 0, rx, header, oe);
        if (!oe[1]) return;
        observed = ascend ? {rx[1][7:0], rx[1][15:8]} : rx[1];
        // 两字节解码后才比较目标水位。对比失败路径还没发 0x1C 写入。
        if (observed !== watermark) return;
        write8(15'h1c, {general[7:2], fifo_mode});
        spi_frame(1, 15'h1c, 1, 0, rx, header, oe);
        armed = oe[1] && (rx[1][15:8] === {general[7:2], fifo_mode});
    endtask

    task automatic check_reject(input int which, input int old_count, input logic [3:0] why);
        check(rejected[which] == old_count + 1 && last_reason[which] == why,
              $sformatf("dut %0d reject count %0d prior %0d reason %0d expected %0d",
                        which, rejected[which], old_count, last_reason[which], why));
    endtask

    task automatic drive_sample(input int high_12bit);
        // Rev. B 事件阈值一档对应 ADC 并行补码 256 LSB。这里选整档值。
        @(negedge clk);
        adc_idle = 0;
        x = 20'(high_12bit <<< 8);
        valid_in = 1;
        @(negedge clk);
        valid_in = 0;
        repeat (10) @(negedge clk);
        adc_idle = 1;
    endtask

    // =========================================================================
    // 3. 定向事务
    // =========================================================================
    initial begin
        int before_default, before_bound, before_arm;
        logic [1:0][15:0] rx, header;
        logic [1:0] oe;
        logic [15:0] observed_watermark;
        bit armed;
        repeat (5) @(negedge clk);
        rst_n = 1;
        #80;

        // [启动] 用同一帧证明默认实例连无关 SCRATCH 写入也被拒绝。
        before_default = rejected[0];
        before_bound = rejected[1];
        spi_frame(0, 15'h0a, 1, 16'h5a00, rx, header, oe);
        check_reject(0, before_default, 1);
        check(rejected[1] == before_bound && oe == 2'b10 && rx[1] == 0,
              "bound write SDO drives zero, default rejects");
        spi_frame(1, 15'h0a, 1, 0, rx, header, oe);
        check(oe == 2'b11 && rx[0] == 16'h0000 && rx[1] == 16'h5a00, "scratch write contrast");
        $display("OBS default_boot reject=1 SCRATCH=0x%02h bound=0x%02h", rx[0][15:8], rx[1][15:8]);
        spi_frame(1, 15'h11, 1, 0, rx, header, oe);
        check(oe == 2'b11 && rx[0] == 0 && rx[1] == 0, "engineering rejection has no device status");
        read_bound(15'h04, 1, 16'h5000);
        read_bound(15'h14, 1, 16'h0900);
        write8(15'h14, 8'h08);
        read_bound(15'h14, 1, 16'h0100);
        write8(15'h14, 8'h01);
        read_bound(15'h14, 1, 16'h0000);
        // [服务缺口] 0x02=2 在 Rev. B 是待机。基线 bank 明确拒绝非正常模式。
        before_bound = rejected[1];
        write8(15'h02, 8'h02);
        check_reject(1, before_bound, 2);
        read_bound(15'h02, 1, 0);
        read_bound(15'h11, 1, 0);
        $display("COUNTEREXAMPLE device_config request=0x02 read=0x00 reject=2");
        // SPI 就绪能观察到接口故障，不能替代独立的模拟电源事件输入。
        interface_ready = 0;
        #120;
        interface_ready = 1;
        read_bound(15'h14, 1, 0);
        $display("OBS baseline_POR after_W1C=0x00 no_separate_analog_event_input");

        // [多字节] 默认下降寻址要求先从高地址发 MSB，0x24 仍属 LO。
        write16(15'h26, 16'h0fed);
        read_bound(15'h26, 2, 16'h0fed);
        write16(15'h24, 16'h0345);
        read_bound(15'h24, 2, 16'h0345);
        read_bound(15'h26, 2, 16'h0fed);
        before_bound = rejected[1];
        write8(15'h26, 8'h07);
        check_reject(1, before_bound, 5);
        read_bound(15'h26, 2, 16'h0fed);
        read_bound(15'h11, 1, 16'h1200);
        write8(15'h11, 8'h02);
        read_bound(15'h11, 1, 16'h1000);
        write8(15'h11, 8'h10);
        read_bound(15'h11, 1, 0);
        before_bound = rejected[1];
        write16(15'h25, 16'h078e);        // 下降模式误用低地址，整实体不提交
        check_reject(1, before_bound, 5);
        read_bound(15'h11, 1, 16'h0200);
        read_bound(15'h26, 2, 16'h0fed);
        write8(15'h11, 8'h02);
        write8(15'h00, 8'h20);
        write16(15'h25, 16'h7a03);
        read_bound(15'h25, 2, 16'h7a03);
        $display("OBS offset descending=0x0fed ascending=0x037a lo_alias=0x0345 partial_reject=5");

        // [绑定] 线协议已通但增益算法参数未绑定。写后从 MISO 读到原 unity 码。
        before_bound = rejected[1];
        write16(15'h27, 16'h0102);
        check_reject(1, before_bound, 1);
        read_bound(15'h27, 2, 16'h0002);
        read_bound(15'h11, 1, 0);
        check(!processing_reject[1], "bad gain never reaches processing chain");
        $display("OBS gain request=0x0201 readback=0x0200 reject=1 IF_STATUS_A=0");
        write16(15'h25, 16'h0000);        // 恢复校正零点后再独立刺激阈值

        // [驱动交叉] no-OS 的水位 helper 走两个普通字节帧，从默认
        // 0x4000 写 0x01AA 时，中间值 0x40AA 被逐笔 clamp，低字节丢失。
        // 相同最终目标用严格实体帧提交，验证不是 host 的字节翻转。
        write8(15'h10, 8'h03);           // 选择普通访问，保留 CRC 禁用编码
        read_bound(15'h1e, 1, 16'h4000);
        read_bound(15'h1d, 1, 16'h0000);
        write8(15'h1d, 8'haa);
        write8(15'h1e, 8'h01);
        read_bound(15'h1d, 1, 16'h0000);
        read_bound(15'h1e, 1, 16'h0100);
        write8(15'h10, 8'h23);           // 重开严格访问，低地址起步的 ASC 保留
        before_arm = bound_arm_writes;
        try_watermark_arm(0, 16'h01aa, 2'd1, armed, observed_watermark);
        check(!armed && observed_watermark == 16'h0100 && bound_arm_writes == before_arm,
              "legacy low-byte mismatch blocked before FIFO arm");
        // 把低字节恢复为期望值并故意改错高字节，独立验证完整回读守卫。
        write16(15'h1d, 16'haa02);
        try_watermark_arm(0, 16'h01aa, 2'd1, armed, observed_watermark);
        check(!armed && observed_watermark == 16'h02aa && bound_arm_writes == before_arm,
              "high-byte mismatch blocked before FIFO arm");
        try_watermark_arm(1, 16'h01aa, 2'd3, armed, observed_watermark);
        check(!armed && bound_arm_writes == before_arm, "mode 11 bad granularity blocked");
        try_watermark_arm(1, 16'h01aa, 2'd1, armed, observed_watermark);
        check(armed && observed_watermark == 16'h01aa && bound_arm_writes == before_arm + 1,
              "ascending strict full readback then actual FIFO mode 01 arm");
        $display("COUNTEREXAMPLE no_os_watermark requested=0x01aa bytewise=0x0100 strict=0x01aa");
        write8(15'h1c, 0);
        write8(15'h00, 0);
        try_watermark_arm(1, 16'h01aa, 2'd1, armed, observed_watermark);
        check(armed && observed_watermark == 16'h01aa && bound_arm_writes == before_arm + 2,
              "descending strict full readback then actual FIFO mode 01 arm");
        write8(15'h1c, 0);
        write8(15'h00, 8'h20);           // 其余控制测试沿用 ASC 帧
        $display("OBS watermark_host low_mismatch=blocked high_mismatch=blocked mode11_granularity=blocked ASC=0x01aa DESC=0x01aa FIFO_MODE=01");

        // [可见性] 三类真实 SPI 协议故障置厂商状态位，样例核对附带状态的位序。
        before_bound = rejected[1];
        write8(15'h2a, 8'h55);
        check_reject(1, before_bound, 3);
        before_bound = rejected[1];
        write8(15'h03, 8'h56);
        check_reject(1, before_bound, 4);
        before_bound = rejected[1];
        interface_ready = 0;
        spi_frame(1, 15'h0a, 1, 0, rx, header, oe);
        check_reject(1, before_bound, 8);
        check(!oe[1], "not-ready frame does not return data");
        interface_ready = 1;
        read_bound(15'h11, 1, 16'h8500);
        write8(15'h10, 8'h33);           // 维持 strict，启用长指令附带状态
        spi_frame(1, 15'h14, 1, 0, rx, header, oe);
        check(oe[1] && header[1] == 16'h0025, "long status maps not-ready, RO and address");
        write8(15'h11, 8'h04);
        read_bound(15'h11, 1, 16'h8100);
        write8(15'h11, 8'h81);
        read_bound(15'h11, 1, 0);
        write8(15'h10, 8'h23);
        $display("OBS protocol_sticky=0x85 long_header=0x0025 W1C=0x81->0x00");

        // [GPIO/事件] 高低阈值分别置位，ALERT 反相。持续跨阈期间的 W1C
        // 只清状态，重新越过迟滞边界后才允许下一次粘滞置位。
        write16(15'h1f, 16'h0200);      // HYST = 2
        write16(15'h21, 16'h1000);      // HI = +16
        write16(15'h23, 16'hf00f);      // LO = -16，12-bit 补码 0xff0
        write8(15'h1a, 8'h40);          // GPIO1 = HI_DTCT，GPIO0 保留 SDO
        write8(15'h1b, 8'h56);          // GPIO3 = LO_DTCT，GPIO2 = ALERT
        write8(15'h19, 8'h0f);          // 所选 4 个输出使能
        write8(15'h1c, 8'he0);          // 内部事件，HI/LO 都路由，FIFO bypass
        drive_sample(18);
        check(gpio_oe[1][3:1] == 3'b111 && gpio_o[1][3:1] == 3'b001,
              "GPIO high detection, ALERT low");
        read_bound(15'h14, 1, 16'h2000);
        drive_sample(0);
        check(gpio_o[1][3:1] == 3'b010, "known baseline HI_DTCT returns low after hysteresis");
        read_bound(15'h14, 1, 16'h2000);
        $display("OBS event_without_CNV_timing hi_dtct_at_return=0 alert_n=1 HI_STATUS=1");
        drive_sample(-18);
        check(gpio_o[1][3:1] == 3'b100, "GPIO low detection, ALERT low");
        read_bound(15'h14, 1, 16'h3000);
        drive_sample(18);
        write8(15'h14, 8'h20);
        read_bound(15'h14, 1, 16'h1000);
        check(gpio_o[1][1] && !gpio_o[1][2], "W1C leaves high GPIO and ALERT level active");
        drive_sample(18);
        read_bound(15'h14, 1, 16'h1000);
        drive_sample(0);
        drive_sample(18);
        read_bound(15'h14, 1, 16'h3000);
        $display("OBS event_w1c HI+LO=0x30 clear_HI=0x10 persistent=0x10 recross=0x30");

        // [GPIO 限制] 含不可用功能的双选择字节原子失败，板级 GPIO 保持原用途。
        before_bound = rejected[1];
        write8(15'h1a, 8'h30);
        check_reject(1, before_bound, 2);
        read_bound(15'h1a, 1, 16'h4000);
        read_bound(15'h11, 1, 0);
        write8(15'h1b, 8'h86);
        check(!gpio_oe[1][3] && !dut_bound.gpio_fsync, "FSYNC input overruns output-enable");
        gpio_i[3] = 0;
        #80;
        check(!gpio_oe[1][3] && dut_bound.gpio_fsync, "active-low held FSYNC");
        gpio_i[3] = 1;
        #80;
        check(!dut_bound.gpio_fsync, "release FSYNC");
        $display("OBS GPIO FILTER_RDY reject=2 unchanged_select=0x40 FILTER_SYNC=low_level");
        // 表 54 指定 1010 勿用。再搭配另一个有效变化验证整字节原子拒绝。
        before_bound = rejected[1];
        write8(15'h1b, 8'ha7);
        check_reject(1, before_bound, 2);
        read_bound(15'h1b, 1, 16'h8600);
        check(!gpio_oe[1][3], "GPIO3 retains FILTER_SYNC input after rejected write");
        before_bound = rejected[1];
        write8(15'h1b, 8'h7a);
        check_reject(1, before_bound, 2);
        read_bound(15'h1b, 1, 16'h8600);
        // 0x86 同时保留 GPIO2 的 ALERT 和 GPIO3 的 FILTER_SYNC，确认未提交同字节的合法替换。
        $display("OBS gpio_sel_reserved code=0xa reject=2 both_nibbles_readback=0x86");

        // [复位] 两次写分别要求 ADC 已空闲。拒绝第二步不会清 Scratch 或补 POR。
        before_bound = rejected[1];
        adc_idle = 0;
        write8(15'h00, 8'h81);
        check_reject(1, before_bound, 8);
        read_bound(15'h0a, 1, 16'h5a00);
        adc_idle = 1;
        write8(15'h00, 8'h81);
        read_bound(15'h0a, 1, 16'h5a00);
        before_bound = rejected[1];
        adc_idle = 0;
        write8(15'h00, 8'h00);
        check_reject(1, before_bound, 8);
        adc_idle = 1;
        read_bound(15'h0a, 1, 16'h5a00);
        read_bound(15'h14, 1, 16'h3000);
        write8(15'h00, 8'h81);
        write8(15'h00, 8'h00);
        check(bound_bank_resets == 1, "only the eligible two-write sequence reaches bank reset");
        read_bound(15'h0a, 1, 0);
        read_bound(15'h00, 1, 16'h3000);  // 复位保留 ADDR_ASC，SDO_ENABLE 仍为 1
        read_bound(15'h14, 1, 16'h0900);
        $display("OBS software_reset illegal_steps_rejected=2 valid_two_writes POR=0x09 ADDR_ASC=1");
        $display("OBS original_bank_reset_pulses=%0d unused_by_service", bound_bank_resets);
        $display("CONTROL_REVIEW_COMPLETE checks=%0d default_rejections=%0d bound_rejections=%0d",
                 checks, rejected[0], rejected[1]);
        $finish;
    end

    initial begin
        #4_000_000;
        $fatal(1, "control review watchdog");
    end
endmodule
`default_nettype wire
