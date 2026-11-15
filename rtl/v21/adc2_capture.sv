// =============================================================================
// 文件名     : adc2_capture.sv
// 模块名     : adc2_capture
// 版本       : v1.0
// 功能       : 13 位 ADC2 捕获归属与整字返回（方案 v1.4 §4.1/§9.2、§11.2 I3）。
// 设计说明   :
//   * 接口语义（§4.1）：
//       adc2_capture_arm   公共沿**之前**注册，告诉宏"下一沿要捕获余差"
//       adc2_hold_ack      宏回报 hold 完成（同域单拍），决定旧片组能否释放
//       adc2_valid/ready   valid && ready 的上升沿接收 raw[12:0] + range_err
//       遇反压时 raw 与 range_err 一起保持
//
//   * 为什么 owner 必须单独保存（§4.2）：
//     "不能把 qid 的生命周期延长到输出退休而阻塞下一次采样，
//      也不能因为 qid 已重用就覆盖旧 ADC2 的上下文。"
//     所以捕获时把本笔的 tag/qid/放行信息快照进 owner 记录，
//     raw 回来时按 owner 归属，而不是按"当前正在转换的那笔"。
//
//   * 同沿优先级（§11.2 I3 明确要求）：
//     "旧返回与新捕获同沿时先绑定旧 owner，再登记新 owner。"
//     实现上就是捕获登记与 raw 消费在同一个 always_ff 里，
//     raw 消费用**捕获前**的 owner_q，捕获写入用非阻塞赋值影响下一拍。
//
//   * 迟到返回隔离：
//     owner 记录带 valid；已消费（owner 已清）后到达的 raw 直接丢弃并置
//     late_err，绝不写进新的一笔。复位同样只清 owner，不产生假结果。
//
//   * 越窗：raw 落在 [0,8191] 之外不可能（13 位），所以 range 由宏的
//     adc2_range_err 提供，原样随 raw 一起锁存并上报。
// 变更记录   :
//   2026-11-15  初版。I3 阶段。
// =============================================================================
module adc2_capture (
    input  logic        clk,
    input  logic        rst_n,

    // ---------------------------------------------------------------- 捕获请求
    // 来自上层：某笔 CONV 即将在公共沿被捕获余差
    input  logic        capture_req,      // 本拍登记一次捕获（与 arm 同源）
    input  logic [31:0] capture_tag,
    input  logic        capture_qid,
    input  logic [2:0]  capture_phase_h,  // 本笔 DEM 相位（供追溯）
    input  logic [2:0]  capture_phase_v,

    // ---------------------------------------------------------------- 宏接口
    output logic        adc2_capture_arm_o,
    input  logic        adc2_hold_ack_i,
    input  logic        adc2_valid_i,
    output logic        adc2_ready_o,
    input  logic [12:0] adc2_raw_i,
    input  logic        adc2_range_err_i,

    // ---------------------------------------------------------------- 返回
    output logic        rsp_valid_o,      // 一次整字返回（tag/raw/range 同拍）
    output logic [31:0] rsp_tag_o,
    output logic        rsp_qid_o,
    output logic [12:0] rsp_raw_o,
    output logic        rsp_range_o,

    // ---------------------------------------------------------------- 状态
    output logic        owner_busy_o,     // 有一个已捕获待返回的 owner
    output logic        hold_done_o,      // 宏已回报 hold（供释放资格用）
    output logic        late_err_o        // 迟到返回被丢弃（粘滞）
);

    // ---------------------------------------------------------------- owner 记录
    logic        owner_valid_q;
    logic [31:0] owner_tag_q;
    logic        owner_qid_q;
    logic [2:0]  owner_h_q, owner_v_q;
    logic        hold_done_q;
    logic        late_err_q;

    // arm 在公共沿**之前**注册。这里用"有 owner 在等"作为 arm 的依据：
    // 捕获登记发生在公共沿（capture_req），arm 必须早于它，
    // 所以 arm 取"本拍有 capture_req 且尚未登记完成"之外的前瞻形式。
    //
    // 简化且可验证的做法：arm = 本拍 capture_req（上层保证它在沿前给出）。
    // 该极性与时序绑定属 §12 待确认项，此处按方案"arm 在沿前注册"实现。
    assign adc2_capture_arm_o = capture_req;

    // ready：只有在 owner 槽空、或本拍正好消费掉旧 owner 时才为 1。
    // 方案 §4.2 初版限制"同一时刻最多一个已捕获但尚未返回 raw 的 owner"。
    wire can_accept = ~owner_valid_q | adc2_valid_i;   // 本拍消费旧 owner 则空出
    assign adc2_ready_o = can_accept;

    wire take_raw = adc2_valid_i & adc2_ready_o;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            owner_valid_q <= 1'b0;
            owner_tag_q   <= 32'd0;
            owner_qid_q   <= 1'b0;
            owner_h_q     <= 3'd0;
            owner_v_q     <= 3'd0;
            hold_done_q   <= 1'b0;
            late_err_q    <= 1'b0;
        end else begin
            // ---- hold 回报：归属于当前 owner ----
            if (adc2_hold_ack_i && owner_valid_q) hold_done_q <= 1'b1;

            // ---- 同沿优先级：先消费旧 owner，再登记新 owner ----
            // raw 消费用的是**本拍之前**的 owner_q（非阻塞赋值不影响本拍的读），
            // 所以同一沿上"旧 raw 返回 + 新捕获登记"能正确各自归属。
            if (take_raw) begin
                if (!owner_valid_q)
                    late_err_q <= 1'b1;      // 无 owner 却收到 raw -> 迟到/越界
                else begin
                    owner_valid_q <= 1'b0;   // 旧 owner 完成
                    hold_done_q   <= 1'b0;
                end
            end

            // ---- 捕获登记：覆盖 owner（若本拍刚消费掉旧 owner）----
            if (capture_req) begin
                if (owner_valid_q && !take_raw) begin
                    // 旧 owner 既没返回又要登记新捕获 -> 违反"最多一个 owner"。
                    // 记错误但不丢旧 owner（旧数据不能让位给新数据）。
                    late_err_q <= 1'b1;
                end else begin
                    owner_valid_q <= 1'b1;
                    owner_tag_q   <= capture_tag;
                    owner_qid_q   <= capture_qid;
                    owner_h_q     <= capture_phase_h;
                    owner_v_q     <= capture_phase_v;
                    hold_done_q   <= adc2_hold_ack_i;   // 同拍 hold 也采入
                end
            end
        end
    end

    // ---------------------------------------------------------------- 返回
    // 整字返回：tag/qid/raw/range 必须同一拍有效 —— 由 take_raw 统一驱动。
    assign rsp_valid_o = take_raw & owner_valid_q;
    assign rsp_tag_o   = owner_tag_q;
    assign rsp_qid_o   = owner_qid_q;
    assign rsp_raw_o   = adc2_raw_i;
    assign rsp_range_o = adc2_range_err_i;

    assign owner_busy_o = owner_valid_q;
    assign hold_done_o  = hold_done_q;
    assign late_err_o   = late_err_q;

endmodule
