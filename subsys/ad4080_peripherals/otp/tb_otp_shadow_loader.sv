`timescale 1ns/1ps

// =============================================================================
// 文件名     : tb_otp_shadow_loader.sv
// 模块名     : tb_otp_shadow_loader
// 版本       : v1.0
// 功能       : 以合成测试字扰动只读与校验握手，逐拍检查影子原子发布。
// 设计说明   :
//   * 测试字、错误和判决均由测试台控制。选三字是为触发非 2 的幂边界，
//     另实例化一字一位、时限一拍配置；两组尺寸均与器件无关。
//   * 上升沿完成受理，等待 1 ns 后检查寄存器与组合输出，避免竞态。
//   * 校验拒绝、超时、brownout 和复位都在能暴露旧响应或部分字的时点触发。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 审查后增加提前出现的读回、校验判决与合法同沿返回的对照。
// =============================================================================
module tb_otp_shadow_loader;

    // =========================================================================
    // 1. 三字场景的接口与定值
    // =========================================================================
    // 非二次幂尺寸在末字后仍有编码余量，可抓到按地址回绕而非逻辑字数结束。
    // 三个字均不为零且互不相同，末次整像比较同时检查漏字、重字和打包次序。
    localparam int WORD_WIDTH     = 8;
    localparam int IMAGE_WORDS    = 3;
    localparam int TIMEOUT_CYCLES = 4;
    localparam int ADDR_WIDTH     = $clog2(IMAGE_WORDS);
    localparam int IMAGE_WIDTH    = IMAGE_WORDS * WORD_WIDTH;
    localparam logic [IMAGE_WIDTH-1:0] EXPECTED_IMAGE = 24'ha7_56_9c;

    // --- 主实例供电与会话控制 ---
    logic clk = 1'b0;
    logic rst_n, power_good, cancel, quiescent;
    // --- 测试台驱动的宏读适配器 ---
    logic req_valid, req_ready, rsp_valid, rsp_ready, rsp_error;
    logic [ADDR_WIDTH-1:0] req_addr;
    logic [WORD_WIDTH-1:0] rsp_data;
    // --- 测试台判决与镜像观察 ---
    logic verify_req_valid, verify_req_ready, verify_rsp_valid, verify_rsp_ready, verify_pass;
    logic [IMAGE_WIDTH-1:0] verify_image, shadow_image;
    // --- 对下游可见的会话终态 ---
    logic busy, shadow_valid;
    logic [3:0] error_code;

    otp_shadow_loader #(
        .IMAGE_WORDS(IMAGE_WORDS),
        .WORD_WIDTH(WORD_WIDTH),
        .TIMEOUT_CYCLES(TIMEOUT_CYCLES)
    ) dut (
        // --- 供电与冲刷，故意允许静默确认和旧响应做冲突激励 ---
        .clk_i(clk),
        .rst_ni(rst_n),
        .power_good_i(power_good),
        .adapters_cancel_o(cancel),
        .adapters_quiescent_i(quiescent),
        // --- 只驱动受理后的逻辑字，不需任何原厂宏数据 ---
        .otp_req_valid_o(req_valid),
        .otp_req_ready_i(req_ready),
        .otp_req_addr_o(req_addr),
        .otp_rsp_valid_i(rsp_valid),
        .otp_rsp_ready_o(rsp_ready),
        .otp_rsp_data_i(rsp_data),
        .otp_rsp_error_i(rsp_error),
        // --- 校验判决可正可负，分别打到发布与封闭两条路径 ---
        .verify_req_valid_o(verify_req_valid),
        .verify_req_ready_i(verify_req_ready),
        .verify_image_o(verify_image),
        .verify_rsp_valid_i(verify_rsp_valid),
        .verify_rsp_ready_o(verify_rsp_ready),
        .verify_rsp_pass_i(verify_pass),
        // --- 影子值和有效标志同时比较，避免仅验证状态位 ---
        .busy_o(busy),
        .error_code_o(error_code),
        .shadow_valid_o(shadow_valid),
        .shadow_image_o(shadow_image)
    );

    always #5ns clk <= ~clk;

    // =========================================================================
    // 2. 驱动与可见性检查
    // =========================================================================
    // [时序] 检查状态在非阻塞赋值生效后执行，下一笔输入留到之后的周期。
    // 各用例共用此采样点，超时计数按受理沿衡量，与时钟的纳秒绝对值无关。
    task automatic tick;
        @(posedge clk);
        #1ns;
    endtask

    // [判据] 四态未知也判失败，避免握手信号未驱动时被当成测试通过。
    task automatic require_ok(input logic condition, input string why);
        if (condition !== 1'b1) $fatal(1, "%s", why);
    endtask

    // [验收] 即使调用方误读了无效周期，镜像端也不能泄露先读出的非零字。
    task automatic require_hidden;
        require_ok(!shadow_valid && shadow_image == '0, "uncommitted shadow exposed");
        if (!verify_req_valid)
            require_ok(verify_image == '0, "checker image exposed outside its request");
    endtask

    // [判据] 粘滞故障必须同时取消读/校验、终止 busy、隐藏全像；只对错误码会漏检。
    task automatic expect_failure(input logic [3:0] fault_code);
        require_hidden();
        require_ok(cancel && !busy && error_code == fault_code, "wrong terminal fault/cancel");
        require_ok(!req_valid && !rsp_ready && !verify_req_valid && !verify_rsp_ready,
                   "terminal fault left a handshake active");
    endtask

    // [隔离] 通用夹具重置时都撤掉所有适配器输入，故障用例自行注入目标故障。
    // 旧返回横跨复位的特例在主流程单独构造，不能被这段夹具清理掉。
    task automatic reset_loader;
        rst_n = 1'b0;
        power_good = 1'b0;
        quiescent = 1'b0;
        req_ready = 1'b0;
        rsp_valid = 1'b0;
        rsp_data = '0;
        rsp_error = 1'b0;
        verify_req_ready = 1'b0;
        verify_rsp_valid = 1'b0;
        verify_pass = 1'b0;
        tick();
        require_hidden();
        require_ok(cancel && !busy && error_code == 0, "reset did not clear boot session");
        rst_n = 1'b1;
    endtask

    // [冲刷] 先给电源资格，再检查 cancel 覆盖第一个清理周期。
    task automatic enter_drain;
        power_good = 1'b1;
        tick();
        require_ok(cancel && !req_valid, "adapter drain was skipped");
    endtask

    // [计时] 把每个分段反例推进到首个请求边界，避免把等电与冲刷计进读时限。
    task automatic boot_to_read;
        reset_loader();
        enter_drain();
        quiescent = 1'b1;
        tick();
        require_hidden();
        require_ok(!cancel && busy && req_valid && req_addr == '0, "boot did not start at word 0");
    endtask

    // [握手] 首先核地址与应答门控，然后只在一沿给 ready，隔离请求和应答预算。
    task automatic accept_read(input logic [ADDR_WIDTH-1:0] address);
        require_ok(req_valid && req_addr == address && !rsp_ready,
                   "wrong address or more than one request outstanding");
        req_ready = 1'b1;
        tick();
        req_ready = 1'b0;
        require_ok(rsp_ready && !req_valid, "response was not enabled after read acceptance");
        require_hidden();
    endtask

    // [判据] 正常和读错的返回都带实际非零字，能检出错误数据被误入暂存或影子的路径。
    task automatic return_word(input int address, input logic abort_word);
        rsp_valid = 1'b1;
        rsp_data = EXPECTED_IMAGE[address * WORD_WIDTH +: WORD_WIDTH];
        rsp_error = abort_word;
        tick();
        rsp_valid = 1'b0;
        rsp_error = 1'b0;
        require_hidden();
    endtask

    // [地址] 首字可由延迟应答场景自行处理，后续仍逐字核递增并要求恰在末字转校验。
    task automatic read_remaining_words(input int first_address);
        for (int address = first_address; address < IMAGE_WORDS; address++) begin
            accept_read(ADDR_WIDTH'(address));
            return_word(address, 1'b0);
        end
        require_ok(!req_valid && verify_req_valid && verify_image == EXPECTED_IMAGE,
                   "short/extra read or wrong image word order");
    endtask

    // [时序] 接收校验镜像和收到判决是两拍契约，验收端须在请求有效时锁定数据。
    task automatic accept_verify;
        require_ok(verify_req_valid && verify_image == EXPECTED_IMAGE && !verify_rsp_ready,
                   "checker was offered an incomplete image");
        verify_req_ready = 1'b1;
        tick();
        verify_req_ready = 1'b0;
        require_hidden();
        require_ok(verify_rsp_ready && !verify_req_valid, "checker response phase missing");
    endtask

    task automatic await_timeout(input logic [3:0] fault_code);
        // [时限] 逐沿核对不会提前失败，最后一沿等待仍无握手才退出。
        repeat (TIMEOUT_CYCLES - 1) begin
            tick();
            require_hidden();
            require_ok(busy && error_code == 0, "timeout fired too early");
        end
        tick();
        expect_failure(fault_code);
    endtask

    // [复现] 静默确认已经受理，首读尚未受理。此时看到的 valid 无请求
    // 可归属，若后续请求受理后直接拿走它，旧会话的字就会混入新镜像。
    task automatic reject_unarmed_read;
        logic [3:0] early_error;
        boot_to_read();
        rsp_valid = 1'b1;
        rsp_data = 8'he7;
        tick();
        early_error = error_code;
        req_ready = 1'b1;
        tick();
        req_ready = 1'b0;
        tick();
        // [判据] 旧版会把持续的旧 valid 当成字零并前进到下一地址。
        // 检查对外地址进度，避免只复现诊断码缺失而遗漏实际误受理。
        require_ok(!req_valid || req_addr == '0, "old macro response was consumed as the first word");
        require_ok(early_error == 4'd10, "early OTP response was not rejected before request acceptance");
        expect_failure(4'd10);
        $display("PASS unrequested macro response closes the session with code 10");
    endtask

    // [复现] 镜像齐备后故意让校验请求背压，同时给出旧 PASS。校验器
    // 没有接收本轮镜像，旧判决即使通过也必须封闭整个会话。
    task automatic reject_unarmed_verdict;
        logic [3:0] early_error;
        boot_to_read();
        read_remaining_words(0);
        verify_rsp_valid = 1'b1;
        verify_pass = 1'b1;
        tick();
        early_error = error_code;
        verify_req_ready = 1'b1;
        tick();
        verify_req_ready = 1'b0;
        tick();
        // [判据] 旧 valid 一直保持，中间从未替换为针对本轮的判决。
        require_ok(!shadow_valid, "old checker PASS committed a new image without verifying it");
        require_ok(early_error == 4'd11, "early checker pass was not rejected before image acceptance");
        expect_failure(4'd11);
        $display("PASS unrequested checker pass closes the session with code 11");
    endtask

    // =========================================================================
    // 3. 三字用例与逐拍检查
    // =========================================================================
    initial begin
        // [可重放] 单独在备份 RTL 测试任一路，可以观察各自首次失败。
        if ($test$plusargs("otp_early_read")) begin
            reject_unarmed_read();
            $finish;
        end
        if ($test$plusargs("otp_early_verify")) begin
            reject_unarmed_verdict();
            $finish;
        end

        // [上电] 失去上游电源资格允许无限等待，单阶段 watchdog 尚未起算。
        reset_loader();
        repeat (TIMEOUT_CYCLES + 1) tick();
        require_ok(cancel && busy && !req_valid && error_code == 0,
                   "initial power wait consumed the phase watchdog");

        // [验收] 即使适配器过早给静默确认，挂着的旧响应也会阻断首读。
        enter_drain();
        quiescent = 1'b1;
        rsp_valid = 1'b1;
        verify_rsp_valid = 1'b1;
        tick();
        require_hidden();
        require_ok(cancel && !req_valid && !rsp_ready && !verify_rsp_ready,
                   "stale responses escaped the drain phase");
        rsp_valid = 1'b0;
        verify_rsp_valid = 1'b0;
        tick();

        // [握手] 在请求预算末沿才接受，结果从该沿起保持到应答 ready。
        repeat (TIMEOUT_CYCLES - 1) begin
            tick();
            require_ok(req_valid && req_addr == '0 && !rsp_ready, "read request was unstable");
            require_hidden();
        end
        rsp_valid = 1'b1;
        rsp_data = EXPECTED_IMAGE[WORD_WIDTH-1:0];
        accept_read(0);
        tick();
        rsp_valid = 1'b0;
        require_hidden();
        read_remaining_words(1);

        // [校验] 镜像在请求反压时保持完整稳定，末沿判决与超时冲突时可提交。
        repeat (TIMEOUT_CYCLES - 1) begin
            tick();
            require_ok(verify_req_valid && verify_image == EXPECTED_IMAGE,
                       "candidate image changed under verifier backpressure");
            require_hidden();
        end
        accept_verify();
        repeat (TIMEOUT_CYCLES - 1) begin
            tick();
            require_hidden();
        end
        verify_rsp_valid = 1'b1;
        verify_pass = 1'b1;
        tick();
        verify_rsp_valid = 1'b0;
        require_ok(shadow_valid && shadow_image == EXPECTED_IMAGE && !busy && error_code == 0,
                   "successful image was not published atomically");
        // [保持] 正常提交为终态，无调用方读确认时仍保存镜像且不再发宏请求。
        tick();
        require_ok(shadow_valid && shadow_image == EXPECTED_IMAGE && !req_valid,
                   "image did not remain stable after commit");
        $display("PASS power wait, stale drain, ordered reads, backpressure, deadline and commit");

        // [供电] 已提交快照也需要立即遮蔽，下一拍进入粘滞掉电故障。
        power_good = 1'b0;
        #1ns;
        require_hidden();
        require_ok(cancel, "power removal did not suppress the adapter interface");
        // [诊断] 组合遮蔽在采样沿之前生效，粘滞错误码要等本域采样才改变。
        tick();
        expect_failure(4'd1);
        power_good = 1'b1;
        tick();
        expect_failure(4'd1);
        $display("PASS committed shadow revoked on power loss; recovery requires reset");

        // [验收] 读第二字失败前已有一个非零字，错误数据或半张影子均不可用。
        boot_to_read();
        accept_read(0);
        return_word(0, 1'b0);
        accept_read(1);
        return_word(1, 1'b1);
        expect_failure(4'd2);
        $display("PASS mid-image read failure without partial commit");

        // [反例] 数据字均正常、仅判决为拒绝时退出，验证装满镜像不能自动发布。
        boot_to_read();
        read_remaining_words(0);
        accept_verify();
        verify_rsp_valid = 1'b1;
        verify_pass = 1'b0;
        tick();
        expect_failure(4'd3);
        $display("PASS complete image rejected by checker");

        // [验收] 等待点分别留空，以确认诊断能够定位断链而非一律报 CRC。
        reset_loader();
        enter_drain();
        await_timeout(4'd4);
        boot_to_read();
        await_timeout(4'd5);
        // [重入] 超时以后同时补到 ready 和迟来的 valid，首错保留且会话不能重开。
        req_ready = 1'b1;
        rsp_valid = 1'b1;
        tick();
        expect_failure(4'd5);
        boot_to_read();
        accept_read(0);
        await_timeout(4'd6);
        boot_to_read();
        read_remaining_words(0);
        await_timeout(4'd7);
        boot_to_read();
        read_remaining_words(0);
        accept_verify();
        await_timeout(4'd8);
        $display("PASS five independent timeout phases and sticky failure");

        // [优先级] 同一沿有足够信息发布，但掉电应优先，连一拍有效窗都没有。
        boot_to_read();
        read_remaining_words(0);
        accept_verify();
        power_good = 1'b0;
        verify_rsp_valid = 1'b1;
        verify_pass = 1'b1;
        #1ns;
        require_ok(!verify_rsp_ready && cancel, "brownout left the checker handshake open");
        tick();
        expect_failure(4'd1);
        $display("PASS brownout wins over coincident checker pass");

        // [重入] 第二字在途时异步复位，旧响应横跨复位并在冲刷阶段撤除。
        boot_to_read();
        accept_read(0);
        return_word(0, 1'b0);
        accept_read(1);
        rsp_data = 8'hff;
        rsp_valid = 1'b1;
        rst_n = 1'b0;
        #1ns;
        require_hidden();
        require_ok(cancel && !busy && !rsp_ready && error_code == 0,
                   "asynchronous reset did not close the in-flight response");
        // [时序] 电源保持有效，重启的冲刷仍必须等待旧应答落下。
        rst_n = 1'b1;
        tick();
        tick();
        require_ok(cancel && !req_valid && !rsp_ready, "stale response escaped reset drain");
        rsp_valid = 1'b0;
        tick();
        read_remaining_words(0);
        accept_verify();
        verify_rsp_valid = 1'b1;
        verify_pass = 1'b1;
        tick();
        // [隔离] 旧会话的 0xff 必须被新会话按地址读回的字替代。
        require_ok(shadow_valid && shadow_image == EXPECTED_IMAGE, "post-reset image was corrupted");
        $display("PASS async reset, stale-response drain and clean reload");

        reject_unarmed_read();
        reject_unarmed_verdict();

        // [同沿] 校验请求受理当沿若已给出应答，仍等到应答握手后提交。
        boot_to_read();
        read_remaining_words(0);
        verify_rsp_valid = 1'b1;
        verify_pass = 1'b1;
        accept_verify();
        tick();
        verify_rsp_valid = 1'b0;
        require_ok(shadow_valid && shadow_image == EXPECTED_IMAGE && error_code == 0,
                   "legal checker response coincident with its request was lost");
        $display("PASS checker response valid on the accepting request edge commits after response handshake");

        require_ok(single_done, "minimal-parameter case did not finish");
        $display("OTP SHADOW LOADER REGRESSION PASS");
        $finish;
    end

    // =========================================================================
    // 4. 一字一位与一拍时限的退化用例
    // =========================================================================
    // 独立实例只共享时钟，防止主用例多拍预算掩盖一拍边界。
    // --- 最小实例的供电、宏读和冲刷状态 ---
    logic single_rst_n, single_power, single_req_valid, single_rsp_ready;
    logic single_cancel, single_addr, single_busy;
    // --- 校验、发布和跨两个用例的完成标志 ---
    logic single_rsp_valid, single_verify_req_valid, single_verify_image;
    logic single_verify_rsp_valid, single_verify_rsp_ready;
    logic single_shadow_valid, single_shadow_image, single_done;
    logic [3:0] single_error;

    otp_shadow_loader #(
        .IMAGE_WORDS(1),
        .WORD_WIDTH(1),
        .TIMEOUT_CYCLES(1)
    ) single_word (
        // --- 冲刷可立即完成，单周期预算留给实际分段握手 ---
        .clk_i(clk),
        .rst_ni(single_rst_n),
        .power_good_i(single_power),
        .adapters_cancel_o(single_cancel),
        .adapters_quiescent_i(1'b1),
        // --- 只有一个非零字，确认最小位宽不会造成空切片 ---
        .otp_req_valid_o(single_req_valid),
        .otp_req_ready_i(1'b1),
        .otp_req_addr_o(single_addr),
        .otp_rsp_valid_i(single_rsp_valid),
        .otp_rsp_ready_o(single_rsp_ready),
        .otp_rsp_data_i(1'b1),
        .otp_rsp_error_i(1'b0),
        // --- 请求立即受理，判决仍要等应答阶段才给 ---
        .verify_req_valid_o(single_verify_req_valid),
        .verify_req_ready_i(1'b1),
        .verify_image_o(single_verify_image),
        .verify_rsp_valid_i(single_verify_rsp_valid),
        .verify_rsp_ready_o(single_verify_rsp_ready),
        .verify_rsp_pass_i(1'b1),
        // --- 用输出有效与数据一起判定成功 ---
        .busy_o(single_busy),
        .error_code_o(single_error),
        .shadow_valid_o(single_shadow_valid),
        .shadow_image_o(single_shadow_image)
    );

    // [参数] 最小时限允许每个阶段一次受理，专门捕获零位宽与终址比较错误。
    initial begin
        single_rst_n = 1'b0;
        single_power = 1'b0;
        single_rsp_valid = 1'b0;
        single_verify_rsp_valid = 1'b0;
        single_done = 1'b0;
        tick();
        require_ok(!single_busy && single_cancel, "minimal-parameter reset failed");
        single_rst_n = 1'b1;
        single_power = 1'b1;
        tick();
        tick();
        require_ok(single_req_valid && !single_addr, "single word address was not zero");
        tick();
        require_ok(single_rsp_ready, "single word response phase missing");
        single_rsp_valid = 1'b1;
        tick();
        single_rsp_valid = 1'b0;
        require_ok(single_verify_req_valid && single_verify_image && !single_shadow_valid,
                   "single word image was offered incorrectly");
        tick();
        require_ok(single_verify_rsp_ready, "single word checker response phase missing");
        single_verify_rsp_valid = 1'b1;
        tick();
        single_verify_rsp_valid = 1'b0;
        require_ok(single_shadow_valid && single_shadow_image && single_error == 0 && !single_busy,
                   "single word image did not pass the one-cycle deadline");
        single_done = 1'b1;
        $display("PASS one word, one bit and one-cycle deadlines");
    end

    // [验收] 若定向任务卡在等待而未到结束点，以独立仿真计时判失败。
    initial begin
        #10us;
        $fatal(1, "testbench did not finish");
    end
endmodule
