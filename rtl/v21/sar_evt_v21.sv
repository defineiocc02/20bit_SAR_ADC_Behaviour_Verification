// =============================================================================
// 文件名     : sar_evt_v21.sv
// 模块名     : sar_evt_v21
// 版本       : v1.0
// 功能       : v2.1 已决事件协议 + 原子应用资格（方案 v1.4 §8.1/§8.2）。
//              普通区 Flash+6 SAR = 7 事件；guard 区 5 事件，
//              k = prefix + U（正向），10 位容器，覆盖 0..520。
// 设计说明   :
//   * 为什么新写而不是改 follower_rt：`follower_rt` 是旧协议（9 事件、
//     `prefix−U`、9 位 k、guard 不可达），且旧链 8509 笔回归与 6246 事件
//     回归都绑在它上面。原地改会同时破坏旧证据与新协议，所以另立一份。
//     旧的保留原样，作为"旧协议兼容性"资产。
//
//   * 事件表（§8.1，[E] 工程拟定的明确次序，不是原文公开的事件数）：
//       普通 F<7：Flash 初始化 prefix = F<<6，依次试 bit5..bit0 -> 7 次提交
//       guard   ：prefix = 448（Flash 满量程），先接受阈 512，再试 bit2..bit0
//                 -> 448、512、三个低位，共 5 次提交
//     两条路径都逐事件提交（"loaded as they develop"，论文 p.182）。
//
//   * event_index 与 bit_index 分开：event_index 是 4 位提交序号，
//     bit_index 是物理位号（0..8 对应 9 位粗码的位）。方案明确要求
//     不要用一个 sar_index 同时表示两者 —— 旧实现正是这么做的，
//     导致"序号对不对"和"位号对不对"混在一个比较里。
//
//   * k 用 11 位中间加法再判范围：`k = prefix + U`，合法范围 0..520，
//     超出即 k_bad（不静默压位）。方案要求"至少 11 位中间加法检查"。
//
//   * apply_fire（§8.2）的完整资格：
//       事务 active、qid/序号正确、本次为已决提交、k 合法、
//       无粘滞错误、无 reset/abort/start 冲突。
//     任一不满足则物理写门关闭 —— 非法事件不得驱动开关。
// 变更记录   :
//   2026-11-15  初版。I2 阶段。
// =============================================================================
module sar_evt_v21 (
    input  logic        clk,
    input  logic        rst_n,

    // ---------------------------------------------------------------- 事务起止
    input  logic        start_i,       // 分配上下文（不与同沿 bit_valid 混用）
    input  logic        abort_i,       // 放弃本笔
    input  logic [31:0] tag_i,
    input  logic        qid_i,         // 0/1，本笔冻结
    input  logic [2:0]  upper_i,       // 本笔 dither U（start 时采样）

    // ---------------------------------------------------------------- Flash
    input  logic        flash_valid_i,
    input  logic [2:0]  flash_code_i,  // F（0..7），可在首事件沿返回

    // ---------------------------------------------------------------- 已决事件
    input  logic        bit_valid_i,
    input  logic        bit_i,         // 本次试探结果（1=接受）
    input  logic [3:0]  bit_index_i,   // 物理位号 0..8
    input  logic [3:0]  event_index_i, // 提交序号 0..10（本笔期望值自增）
    input  logic        sar_qid_i,     // 事件所属 qid
    input  logic        sar_final_i,   // 该事件后前级已决码完成

    // ---------------------------------------------------------------- 输出
    output logic        ev_valid_o,    // 本拍产生一次已决提交
    output logic [3:0]  ev_event_index_o,
    output logic [3:0]  ev_bit_index_o,
    output logic [9:0]  k_o,           // prefix + U，已判范围
    output logic        k_bad_o,       // k 越界（>520）
    output logic        apply_fire_o,  // 合法已决事件，物理写门有效
    output logic        final_applied_o,
    output logic [9:0]  final_k_o,
    output logic [31:0] tag_o,
    output logic        qid_o,
    output logic        err_o,         // 协议/范围错误（粘滞到本笔结束）
    output logic        done_o,        // 本笔最后一个提交已应用
    output logic        last_evt_o     // **本拍应用的就是最后一个事件**（组合）
);

    // ---------------------------------------------------------------- 状态
    logic        active_q;
    logic [31:0] tag_q;
    logic        qid_q;
    logic [2:0]  upper_q;
    logic [9:0]  prefix_q;      // 已决前缀（含 Flash 初始化与位累加）
    logic [3:0]  exp_evt_q;     // 期望的提交序号
    logic [2:0]  guard_lvl_q;   // 已接受的 guard 试探数（0..3）
    logic        flash_done_q;
    logic        guard_q;       // 本笔走 guard 路径
    logic        err_q;
    logic        done_q;
    logic        final_applied_q;
    logic [9:0]  final_k_q;

    // ---------------------------------------------------------------- 期望位号
    //
    // 事件表（方案 §8.1 [E] 明确次序）。**关键：Flash 本身算第 1 次提交**，
    // 之后每个试探位各提交一次。两条路径都逐事件提交（"loaded as they
    // develop"，论文 p.182）。
    //
    //   普通 F<7（7 次）：evt1 = Flash 提交，evt2..7 = bit5..bit0
    //   guard  F=7（5 次）：evt1 = Flash 提交 448，
    //                        evt2 = 阈 512 试探（用 bit_index=9 特指），
    //                        evt3..5 = bit2..bit0
    //
    // 之所以把 Flash 也编进事件序号，是因为方案的 A/B 向量里第 1 个 k
    // 就是 Flash 提交后的值（A: 448+5=453，B: 256+0=256）。
    // 早期版本把 Flash 放在 start 里不计序号，导致整条序列错位一个事件。
    localparam logic [3:0] BI_GUARD_TH = 4'd9;   // guard 门限试探的专用 bit_index

    logic [3:0] exp_bit;
    always_comb begin
        exp_bit = 4'd0;
        if (!guard_q) begin
            // 普通：evt1 = Flash（不比对位号），evt2..7 = bit5..bit0
            if (exp_evt_q >= 4'd2 && exp_evt_q <= 4'd7)
                exp_bit = 4'd7 - exp_evt_q;      // evt2->5, evt7->0
            else
                exp_bit = 4'd0;
        end else begin
            // guard：evt1 = Flash，evt2 = 阈512，evt3..5 = bit2..bit0
            if (exp_evt_q == 4'd2)       exp_bit = BI_GUARD_TH;
            else if (exp_evt_q >= 4'd3 && exp_evt_q <= 4'd5)
                exp_bit = 4'd5 - exp_evt_q;      // evt3->2, evt5->0
            else exp_bit = 4'd0;
        end
    end

    // 同一量化器的 Flash 可在启动后、第一次提交前到达。若两者同沿，
    // 写入 RDAC 的是此沿真实判决的 F。首事件过后再来的 Flash 不改已决前缀。
    wire fresh_flash = active_q && !done_q && !err_q && (exp_evt_q == 4'd1)
                       && flash_valid_i && (sar_qid_i == qid_q);
    logic [9:0] prefix_accept;
    always_comb begin
        prefix_accept = prefix_q;
        if (exp_evt_q == 4'd1) begin
            prefix_accept = fresh_flash ? {1'b0, flash_code_i, 6'b0} : prefix_q;
        end else if (!guard_q) begin
            // 普通：evt2..7 依次试 bit5..bit0
            if (bit_i) prefix_accept = prefix_q | (10'd1 << exp_bit);
        end else begin
            // guard：evt2 = 阈 512；evt3..5 = bit2..bit0
            if (exp_evt_q == 4'd2) begin
                if (bit_i) prefix_accept = 10'd512;
            end else if (bit_i) begin
                prefix_accept = prefix_q | (10'd1 << exp_bit);
            end
        end
    end

    // ---------------------------------------------------------------- 本拍 k
    //
    // 用 **prefix_accept**（本次提交后的已决前缀）而不是 prefix_q：
    // 方案 §8.2 要求"每个合法已决事件应用对应的物理开关"，
    // 那次应用用的就是本事件提交后的 k。若用 prefix_q，k 会滞后一个事件
    // （实测 evt2 期望 517 却读到 453 —— 512 要到 evt2 提交后才写进寄存器）。
    //
    // 11 位中间加法：避免 10 位容器在 prefix+U 越界时静默回绕。
    wire [10:0] k_wide = {1'b0, prefix_accept} + {8'b0, upper_q};
    wire        k_over = (k_wide > 11'd520);

    // ---------------------------------------------------------------- 资格判定
    wire qid_ok   = (sar_qid_i == qid_q);
    wire evt_ok   = (event_index_i == exp_evt_q);
    // 第 1 次提交（Flash）不比对物理位号 —— 那次没有"试探位"，
    // bit_index 无意义。evt>=2 才比对。
    wire bit_ok   = (exp_evt_q == 4'd1) || (bit_index_i == exp_bit);
    wire can_apply = active_q & !err_q & !done_q & !abort_i & !start_i
                   & (flash_done_q | fresh_flash)
                   & qid_ok & evt_ok & bit_ok & !k_over;

    // ---------------------------------------------------------------- 拒绝 512 的那次不 apply
    //
    // 方案 §8.1："512 试探**不提交**；仍 7 次，实际多一次比较"。
    //   · 多一次比较  -> 比较器确实工作了一次
    //   · 不提交      -> **不写 RDAC**，apply_fire 必须为 0
    //
    // 审计反例（0977e14）：guard 路径先接受 Flash(448)，随后拒绝 512 阈，
    // 旧实现让这次拒绝也 apply 了（applied=1、cmd=453），于是整笔写了 8 次
    // 而不是 7 次 —— 虽然最终 k 仍然对（485），但物理开关多写一次，
    // 在硅片上就是多一次无谓的 RDAC 转移。
    wire guard_reject_th = guard_q & (exp_evt_q == 4'd2) & ~bit_i;

    assign apply_fire_o = bit_valid_i & can_apply & ~guard_reject_th;

    // ---------------------------------------------------------------- 提交推进
    // 接受则保留候选（置位），拒绝则保留旧 prefix；两种都提交已决前缀。
    // 本笔在哪个提交序号结束
    wire last_evt = guard_q ? (exp_evt_q == 4'd5) : (exp_evt_q == 4'd7);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            active_q <= 1'b0; tag_q <= 32'd0; qid_q <= 1'b0; upper_q <= 3'd0;
            prefix_q <= 10'd0; exp_evt_q <= 4'd0; guard_lvl_q <= 3'd0;
            flash_done_q <= 1'b0; guard_q <= 1'b0; err_q <= 1'b0; done_q <= 1'b0;
            final_applied_q <= 1'b0; final_k_q <= 10'd0;
        end else if (start_i) begin
            // start 只分配上下文，不与同沿 bit_valid 混用
            active_q    <= 1'b1;
            tag_q       <= tag_i;
            qid_q       <= qid_i;
            upper_q     <= upper_i;
            err_q       <= 1'b0;
            done_q      <= 1'b0;
            final_applied_q <= 1'b0;
            exp_evt_q   <= 4'd1;
            guard_lvl_q <= 3'd0;
            // Flash 初始化：F<<6；F==7 时先给 448 并标记 guard 候选。
            // Flash 本身是第 1 次提交，所以 exp_evt_q 置 1，
            // 该事件由随后的 bit_valid（bit_index 任意，不参与比对）提交。
            if (flash_valid_i) begin
                prefix_q     <= {1'b0, flash_code_i, 6'b0};
                flash_done_q <= 1'b1;
                guard_q      <= (flash_code_i == 3'd7);
            end else begin
                prefix_q     <= 10'd0;
                flash_done_q <= 1'b0;
                guard_q      <= 1'b0;
            end
        end else if (abort_i) begin
            active_q <= 1'b0;
            done_q   <= 1'b1;
        end else if (bit_valid_i && !done_q) begin
            if (can_apply) begin
                prefix_q <= prefix_accept;
                exp_evt_q <= exp_evt_q + 4'd1;
                if (fresh_flash) begin
                    flash_done_q <= 1'b1;
                    guard_q      <= (flash_code_i == 3'd7);
                end
                // 注意顺序：下面这段对 exp_evt_q / prefix_q 的赋值在同一个
                // always_ff 里位于上方赋值之后，因此对同一位**后写生效**（覆盖）。
                if (guard_q && exp_evt_q == 4'd2 && !bit_i) begin
                    // 阈 512 被拒 -> 退出 guard，退回普通六位路径。
                    //
                    // 方案 §8.1："512 试探**不提交**；仍 7 次，实际多一次比较"。
                    // 关键：既然"不提交"，它就不占提交序号 ——
                    // 所以 exp_evt_q 要**回退到 2**，让 bit5 用序号 2 重新开始。
                    // 若只清 guard_q 而让序号继续走到 3，总提交会变成 8 次
                    // （Flash + 阈拒 + bit5..bit0），与"仍 7 次"矛盾。
                    //
                    // 同时 prefix 保持 Flash 的 448（阈被拒不改变已决前缀）。
                    guard_q   <= 1'b0;
                    prefix_q  <= 10'd448;
                    exp_evt_q <= 4'd2;
                end
                if (last_evt) begin
                    done_q   <= 1'b1;
                    final_applied_q <= 1'b1;
                    final_k_q <= (prefix_accept + {7'b0, upper_q} > 10'd520)
                                 ? 10'd520 : (prefix_accept + {7'b0, upper_q});
                end
            end else begin
                // 非法事件：置粘滞错误，不推进
                err_q <= 1'b1;
            end
        end else if (fresh_flash) begin
            // Flash 独自先到时锁存，等事件 1；此沿不能产生物理写门。
            prefix_q <= {1'b0, flash_code_i, 6'b0};
            flash_done_q <= 1'b1;
            guard_q <= (flash_code_i == 3'd7);
        end
    end

    // ---------------------------------------------------------------- 输出
    assign ev_valid_o       = apply_fire_o;
    assign ev_event_index_o = exp_evt_q;
    assign ev_bit_index_o   = bit_index_i;
    assign k_o              = k_wide[9:0];
    assign k_bad_o          = k_over;
    assign final_applied_o  = final_applied_q;
    assign final_k_o        = final_k_q;
    assign tag_o            = tag_q;
    assign qid_o            = qid_q;
    assign err_o            = err_q;
    assign done_o           = done_q;
    // last_evt 是组合的"本拍是否最后一个提交"：上层在 apply 那一拍
    // 用它锁存 final_post（若改用寄存器版 final_applied，会晚一拍、
    // 锁到下一拍的 mask —— 实测锁到全 0）。
    assign last_evt_o       = apply_fire_o & last_evt;

endmodule
