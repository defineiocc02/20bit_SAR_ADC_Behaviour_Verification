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

    // ---------------------------------------------------------------- 沿前授权 / 沿后登记
    //
    // **必须分开**（方案目标 I3 第 1 条）：
    //   capture_arm_req  沿**前**给出，表示"本笔余差已具备捕获条件，
    //                    下一个公共沿请捕获它"。它由本笔最后一次实际开关应用、
    //                    RA 就绪和可用捕获资源共同决定 —— 是一个**前瞻**信号。
    //   capture_reg_req  沿**后**给出，表示"公共沿已经发生，本拍登记这次
    //                    捕获"。它只在 sample_event & residue_captured 为真时有效。
    //
    // 早期版本把两者合成一个 capture_req，arm 等于登记信号，等于"事后才授权"，
    // 宏没有提前收到授权 —— 语义上不成立。
    input  logic        capture_arm_req,   // 沿前授权
    input  logic        capture_reg_req,   // 沿后登记（已发生的捕获）
    input  logic [31:0] capture_tag,
    input  logic        capture_qid,
    input  logic [2:0]  capture_phase_h,  // 本笔 DEM 相位（供追溯）
    input  logic [2:0]  capture_phase_v,
    // 本笔的权重和快照。**必须随 owner 一起存**，因为 raw 返回时
    // CONV 上下文可能已被下一笔替换（方案 §4.2："不能因为 qid 已重用就
    // 覆盖旧 ADC2 的上下文"）。D5 重构要求"用同笔的 raw/pre/post/ids"。
    input  logic [63:0] capture_G_q30,
    input  logic [63:0] capture_Wp_q30,
    input  logic [63:0] capture_Wm_q30,

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
    // ---------------------------------------------------------------- 配置代
    //
    // **epoch 必须随事务走**（外部审计 §3/R1）：
    // 捕获时把当时的 cfg_epoch 锁进 owner，随 rsp 一起送出，
    // 供 recon 用锁存值判定，而不是用实时端口 —— 否则
    // "旧 raw 返回与新 capture 同沿"时会错配。
    input  logic [7:0]   cfg_epoch,
    output logic [7:0]   rsp_epoch_o,
    output logic        owner_busy_o,     // 有一个已捕获待返回的 owner
    output logic        hold_done_o,      // 宏已回报 hold（供释放资格用）
    output logic        late_err_o,       // 迟到返回被丢弃（粘滞）
    // 旧组释放资格：只看 hold 与安静窗，**不看 raw 是否已返回**。
    // 方案目标 I3 第 1 条："旧组释放依据自己的 hold 和安静窗，
    // raw 返回不能提前释放旧组。"
    output logic        release_ok_o,

    // ---------------------------------------------------------------- 返回时携带
    // 与 rsp 同拍有效的本笔权重和（供 D5 直接用，不必再查上下文）
    output logic [63:0] rsp_G_q30_o,
    output logic [63:0] rsp_Wp_q30_o,
    output logic [63:0] rsp_Wm_q30_o
);

    // ---------------------------------------------------------------- owner 记录
    logic        owner_valid_q;
    logic [31:0] owner_tag_q;
    logic        owner_qid_q;
    logic [2:0]  owner_h_q, owner_v_q;
    logic        hold_done_q;
    logic [63:0] owner_G_q, owner_Wp_q, owner_Wm_q;
    logic [7:0]  owner_epoch_q;   // 捕获那一代的 epoch（随 raw 送出）
    logic        late_err_q;

    // arm 直接输出沿前授权，且只在 owner 槽可接收时才授权
    // （没有空位就别让宏白跑一次捕获）。
    assign rsp_epoch_o = owner_epoch_q;
    assign adc2_capture_arm_o = capture_arm_req & (~owner_valid_q | adc2_valid_i);

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
            owner_G_q     <= 64'd0;
            owner_Wp_q    <= 64'd0;
            owner_Wm_q    <= 64'd0;
            owner_epoch_q <= 8'd0;
            hold_done_q   <= 1'b0;
            late_err_q    <= 1'b0;
        end else begin
            // ---- hold 回报：**与 owner 解耦** ----
            //
            // hold 是"模拟资源已放行"的信号，它描述的是**片组**可以释放，
            // 与 raw 是否已经返回无关。早期版本写成 `&& owner_valid_q`，
            // 且 raw 返回时把 hold_done_q 清 0 —— 结果是"raw 先回、hold 后到"
            // 这个最常见顺序下 hold 永远记不上，旧组被永久卡住（实测）。
            //
            // 方案目标 I3 第 1 条："旧组释放依据自己的 hold 和安静窗，
            // raw 返回不能提前释放旧组。" 反过来同样成立：
            // raw 返回也不该抹掉 hold 记录。
            if (adc2_hold_ack_i) hold_done_q <= 1'b1;

            // ---- 同沿优先级：先消费旧 owner，再登记新 owner ----
            // raw 消费用的是**本拍之前**的 owner_q（非阻塞赋值不影响本拍的读），
            // 所以同一沿上"旧 raw 返回 + 新捕获登记"能正确各自归属。
            if (take_raw) begin
                if (!owner_valid_q)
                    late_err_q <= 1'b1;      // 无 owner 却收到 raw -> 迟到/越界
                else begin
                    owner_valid_q <= 1'b0;   // 旧 owner 完成
                    // hold_done_q **不清** —— 它属于片组释放资格，
                    // 由上层在换笔时复位（见 use_hint 里的 !conv_valid 分支）。
                end
            end

            // ---- 捕获登记：覆盖 owner（若本拍刚消费掉旧 owner）----
            // 用沿后的 capture_reg_req，不是沿前的授权。
            if (capture_reg_req) begin
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
                    owner_G_q     <= capture_G_q30;
                    owner_Wp_q    <= capture_Wp_q30;
                    owner_Wm_q    <= capture_Wm_q30;
                    // **epoch 随事务锁存**：用捕获那一拍的 cfg_epoch，
                    // 不用实时端口。这样"旧 raw 返回 + 新 capture 同沿"时，
                    // 返回的是旧 owner 那一代，不会错配。
                    owner_epoch_q <= cfg_epoch;
                    // 新捕获开始时清掉上一笔的 hold 记录
                    hold_done_q   <= adc2_hold_ack_i;
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
    assign rsp_range_o  = adc2_range_err_i;
    assign rsp_G_q30_o  = owner_G_q;
    assign rsp_Wp_q30_o = owner_Wp_q;
    assign rsp_Wm_q30_o = owner_Wm_q;

    // release_ok 与 owner/raw 完全解耦：hold 到了就允许释放，
    // 不必等 raw。反之 raw 提前回来也不会让 release 提前。
    assign release_ok_o = hold_done_q;
    assign owner_busy_o = owner_valid_q;
    assign hold_done_o  = hold_done_q;
    assign late_err_o   = late_err_q;

endmodule
