// =============================================================================
// 文件名     : tb_ad4080_chip_fixture.svh
// 功能       : 给真实单芯片 DUT 提供时钟、电源和一次 OTP 读取/核验应答。
// 设计说明   : 两笔原码沿用 E2E 模拟边界。本台架放宽 CNV 周期，独立检查
//              完成码经真实 CNV 窗口进入后处理器的值、标签和顺序。
// =============================================================================
    bit chip_aon, chip_io, chip_power_good, chip_tick, chip_analog_fault;
    bit chip_otp_rsp, chip_check_rsp;
    wire chip_otp_cancel, chip_otp_req, chip_otp_ready, chip_check_req, chip_check_ready;
    wire chip_allow, chip_timing_error, chip_retired, chip_fault;
    int chip_words;

    // 常开域必须先完整经历 Rev. B 的初始化。核心和 I/O 各跑自己的时钟。
    always #50 chip_aon = ~chip_aon;
    always #7 chip_io = ~chip_io;
    // 真实握手要求响应跨一个常开时钟采样沿，并见 ready 才释放。
    always @(posedge chip_aon) begin
        if (chip_otp_cancel) begin
            chip_otp_rsp <= 0;
            chip_check_rsp <= 0;
        end else begin
            if (chip_otp_rsp && chip_otp_ready) chip_otp_rsp <= 0;
            else if (chip_otp_req && !chip_otp_rsp) chip_otp_rsp <= 1;
            if (chip_check_rsp && chip_check_ready) chip_check_rsp <= 0;
            else if (chip_check_req && !chip_check_rsp) chip_check_rsp <= 1;
        end
    end

    // 从负沿预置 tick，DUT 只会在随后的一个核心上升沿消费它。
    task automatic next_chip_tick;
        @(negedge clk);
        chip_tick = 1;
        @(negedge clk);
        chip_tick = 0;
    endtask

    // 一次起点 CNV，然后每次真实重构完成后才发下一边沿。
    initial begin
        wait (cnt_en);
        next_chip_tick();
        for (int sample=0; sample<2; sample++) begin
            wait (out_valid);
            repeat (2) @(negedge clk);
            next_chip_tick();
            wait (!out_valid);
        end
    end
    // 等待上电的失败也必须退出，不能靠空日志判定通过。
    initial begin
        #1ms;
        $fatal(1, "chip E2E timeout");
    end

    always @(posedge clk) begin
        // 从实际 DSP 数值链接收端计数，发布脉冲本身不足以证明接线正确。
        if (chip_retired != dut.u_peripheral.u_dsp.chain_input_valid)
            $fatal(1, "chip retirement differed from DSP acceptance");
        if (dut.u_peripheral.u_dsp.chain_input_valid) begin
            // 从参考夹具两条固定的 offset-binary 期望码独立换算。
            case (chip_words)
                0: if (dut.u_peripheral.u_dsp.x != 20'h7FF00 ||
                       dut.u_peripheral.u_dsp.sample_tag_i != 32'hA1)
                       $fatal(1, "chip sample A wrong signed code/tag");
                1: if (dut.u_peripheral.u_dsp.x != 20'h00000 ||
                       dut.u_peripheral.u_dsp.sample_tag_i != 32'hB2)
                       $fatal(1, "chip sample B wrong signed code/tag");
                default: $fatal(1, "unexpected chip sample");
            endcase
            chip_words++;
        end
    end

    task automatic verify_chip_e2e;
        repeat (10) @(negedge clk);
        if (chip_words != 2 || chip_timing_error || chip_fault)
            $fatal(1, "chip timed handoff failed words=%0d timing=%b fault=%b",
                   chip_words, chip_timing_error, chip_fault);
        // 无码的宏故障也必须锁止，撤销故障后不可偷偷接着收新样本。
        chip_analog_fault = 1;
        @(negedge clk);
        chip_analog_fault = 0;
        @(negedge clk);
        if (!chip_fault || prepare_ready) $fatal(1, "chip fault failed to stop admissions");
        $display("AD4080_CHIP_E2E PASS dsp_words=2");
    endtask
