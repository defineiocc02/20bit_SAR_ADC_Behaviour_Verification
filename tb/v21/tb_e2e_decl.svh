// =============================================================================
// 文件名     : tb_e2e_decl.svh
// 模块名     : （由 tb_i4b_e2e 引入）
// 版本       : v1.0
// 功能       : 端到端测试的时钟复位、DUT 接口信号、
//              节点模型（adc2_node_macro）例化、轨迹记录、计分板与驱动任务。
// 设计说明   :
//   * tb_i4b_e2e 将这些信号接到 DUT 端口，tb_e2e_body 负责交易顺序和断言。
//   * 节点模型根据快照计算 ADC2 原始码，辅助任务负责将原始码按拍送回 DUT。
//   * 功能测试用 10 ns 时钟周期。事件驱动和轨迹读取在不同仿真时刻进行，
//     避免驱动和采样在同一个下降沿竞争。
//
// 变更记录   :
//   2026-11-16  v1.0 从 tb_i4b_e2e.sv 抽出声明与辅助任务。
// =============================================================================

    // ---------------------------------------------------------------- 时钟与复位
    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    // ---------------------------------------------------------------- 轨迹与计数
    // 轨迹按固定字段顺序记录，以便对照不同交易的同一拍。
    int  tr_fd;
    bit  tr_en  = 1'b0;   // 轨迹写文件使能（+TRACE 才置 1）
    bit  cnt_en = 1'b0;   // 交易计数使能（复位稳定后置 1，与轨迹无关）
    // 三项计数都须至少为 2，确保激励走完整条采样、捕获和输出通路。
    int  n_prepare_acc = 0;   // prepare 实际受理笔数（valid & ready）
    int  n_capture     = 0;   // adc2 返回捕获笔数（rsp_valid）
    int  n_out         = 0;   // 20 位输出笔数（out_valid）

    // ---------------------------------------------------------------- DUT 输入
    logic        prepare_valid = 1'b0;
    logic [31:0] prepare_tag = 32'd0;
    logic [5:0]  prepare_word = 6'd0;
    logic [2:0]  prepare_h = 3'd0, prepare_v = 3'd0;
    logic        prepare_half = 1'b0, prepare_coin = 1'b0;
    logic [2:0]  prepare_U = 3'd0, prepare_flash = 3'd0;
    logic        prepare_qid = 1'b0, stop_i = 1'b0;

    logic sample_event = 1'b0, input_captured = 1'b0;
    logic residue_captured = 1'b0, hold_ack = 1'b0;

    logic       ev_bit_valid_i = 1'b0, ev_bit_i = 1'b0;
    logic [3:0] ev_bit_index_i = 4'd0, ev_event_index_i = 4'd0;
    logic       ev_qid_i = 1'b0;

    // 真实 sched18 生产路径（sched_ext_sel = 0）：不用外部掩码播种
    logic        sched_ext_sel = 1'b0;
    logic [17:0] sched_acq = 18'd0, sched_conv = 18'd0, sched_idle = 18'd0;
    logic [39:0] sched_ids = 40'd0;
    logic        sched_seed_we = 1'b1;
    logic [31:0] sched_seed = 32'h1234_5678;

    // ADC2 握手（节点模型通过它回送 raw）
    logic        adc2_valid_i = 1'b0;
    logic [12:0] adc2_raw_i = 13'd0;
    logic        adc2_range_err_i = 1'b0;

    // ---------------- 权重表配置口 ----------------
    // 用片号和列索引写入一个 40 位权重。整表提交后，DUT 才能受理采样请求。
    logic        w_cfg_we   = 1'b0;
    logic        w_cfg_sel  = 1'b0;
    logic [4:0]  w_cfg_sid  = 5'd0;
    logic [4:0]  w_cfg_idx  = 5'd0;
    logic [39:0] w_cfg_data = 40'd0;
    logic        w_cfg_commit = 1'b0;
    logic        w_cfg_reload = 1'b0;
    wire         w_cfg_valid;
    wire         w_cfg_busy;
    wire         w_cfg_range_err;

    // ---------------- 权重配置写入任务 ----------------
    //
    // 一个 40 位字占一拍：置 cfg_* 后在 negedge 稳定，posedge 写入。
    // 整表重装：先 reload 清 written，再写 360 字，最后 commit。
    // 中途复位（rst_n=0）会把 written_q 清零，之后必须调本任务恢复配置，
    // 否则 cfg_valid=0、内部受理被门禁挡住（实测：越窗用例发不出去）。
    task automatic wt_install_nominal();
        begin
            @(negedge clk); w_cfg_reload = 1'b1;
            @(posedge clk); @(negedge clk); w_cfg_reload = 1'b0;
            for (int s = 0; s < 18; s++) begin
                for (int j = 0; j < 16; j++)
                    wt_write(1'b0, 5'(s), 5'(j), 40'd268435456);
                wt_write(1'b1, 5'(s), 5'd0, 40'd33554432);
                wt_write(1'b1, 5'(s), 5'd1, 40'd16777216);
                wt_write(1'b1, 5'(s), 5'd2, 40'd8388608);
                wt_write(1'b1, 5'(s), 5'd3, 40'd8388608);
            end
            wt_commit();
        end
    endtask

    // 原子生效：只在 written 已全 1 时生效（CONFIG_CONTRACT §3）
    task automatic wt_commit();
        begin
            @(negedge clk);
            w_cfg_commit = 1'b1;
            @(posedge clk);
            @(negedge clk);
            w_cfg_commit = 1'b0;
            // 等一个沿让 cfg_valid 稳定
            @(posedge clk);
            @(negedge clk);
        end
    endtask

    task automatic wt_write(input logic sel, input logic [4:0] sid,
                            input logic [4:0] idx, input logic [39:0] data);
        begin
            @(negedge clk);
            w_cfg_we   = 1'b1;
            w_cfg_sel  = sel;
            w_cfg_sid  = sid;
            w_cfg_idx  = idx;
            w_cfg_data = data;
            @(posedge clk);
            @(negedge clk);
            w_cfg_we   = 1'b0;
        end
    endtask

    logic [7:0]  cfg_epoch = 8'd1;
    logic signed [26:0] b_q24_cfg = 27'sd0;

    // ---------------------------------------------------------------- DUT 输出
    // 输出由 DUT 驱动，测试任务读取这些信号生成断言和轨迹。
    wire         prepare_ready;
    wire [17:0]  acq_sel_o, conv_sel_o, idle_sel_o;
    wire [71:0]  rdac_pre_state_o;
    wire [17:0]  pre_wr_o;
    wire [359:0] rdac_post_state_o;
    wire [17:0]  post_wr_o;
    wire         adc2_ready_o, adc2_capture_arm_o;
    wire [159:0] node_post_o;
    wire [31:0]  node_pre_o;
    wire [39:0]  node_ids_o;
    wire         node_qid_o;
    wire [31:0]  node_tag_o;
    wire         node_valid_o;
    wire [2:0]   node_h_o, node_v_o;
    wire         adc2_rsp_valid_o, adc2_rsp_qid_o, adc2_rsp_range_o;
    wire [31:0]  adc2_rsp_tag_o;
    wire [12:0]  adc2_rsp_raw_o;
    wire         adc2_late_err_o, adc2_release_ok_o;
    wire         out_valid, out_sat, out_err;
    wire [19:0]  out_code;
    wire [31:0]  out_tag;
    wire         apply_fire_o, k_bad_o, final_applied_o, err_o, done_o, qid_o;
    wire [9:0]   k_o, final_k_o;
    wire [31:0]  tag_o;
    wire [159:0] post_slot_o;

    // ---------------------------------------------------------------- 节点模型
    // 模拟宏 + Vin 真值都在仿真边界：DUT 只送不可变快照，本模型算 y 与 13 位 raw。
    // 节点模型使用 8 片名义权重，DUT 通过上面的配置口独立装表。
    logic signed [26:0] vin_q24 = 27'sd0;    // Vin，Q24/27 位
    logic signed [26:0] b_q24 = 27'sd0;
    logic [39:0]        G_q30 = 40'd34359738368;   // 名义 G=32（Q30）

    logic [39:0] w_main_tab [0:7][0:15];
    logic [39:0] w_sub_tab  [0:7][0:3];

    wire node_calc = node_valid_o;
    wire        nm_y_valid;
    wire signed [39:0] nm_y_q24;
    wire [12:0] nm_raw;
    wire        nm_range;

    // 捕获时刻锁存模型输出：回送的就是模型算出的值，不是常量。
    logic [12:0] cap_raw_a, cap_raw_b;
    logic        cap_rng_a, cap_rng_b;

    adc2_node_macro u_node (
        .clk(clk), .rst_n(rst_n),
        .calc_valid(node_calc),
        .x_q24(vin_q24),
        .b_q24(b_q24),
        .G_q30(G_q30),
        .post_mask(node_post_o),
        .pre_mask(node_pre_o),
        .w_main(w_main_tab), .w_sub(w_sub_tab),
        .y_valid(nm_y_valid), .y_q24(nm_y_q24),
        .raw_code(nm_raw), .range_err(nm_range));

    // ---------------------------------------------------------------- 计分板
    int errors = 0, checks = 0;

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 30) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    task automatic okc(input string msg);
        checks = checks + 1;
        $display("[%0t] OK: %s", $time, msg);
    endtask

    function automatic int pc18(input logic [17:0] v);
        int c; c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    function automatic logic [39:0] pack_ids(input int a[0:7]);
        logic [39:0] v; v = 40'd0;
        for (int i = 0; i < 8; i++) v[i*5 +: 5] = 5'(a[i]);
        return v;
    endfunction

    int A_IDS[0:7] = '{0,1,2,3,4,5,6,7};
    int B_IDS[0:7] = '{9,10,11,12,13,14,15,17};

    // ---------------------------------------------------------------- 驱动任务
    task automatic do_sample(input logic ic, input logic rc, input logic hk);
        @(negedge clk);
        sample_event = 1'b1; input_captured = ic;
        residue_captured = rc; hold_ack = hk;
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0;
        residue_captured = 1'b0; hold_ack = 1'b0;
    endtask

    task automatic do_prepare(input logic [31:0] tag, input logic [5:0] w,
                              input logic [2:0] h, input logic [2:0] v,
                              input logic half, input logic coin,
                              input logic q, input logic [2:0] F);
        @(negedge clk);
        prepare_valid = 1'b1; prepare_tag = tag; prepare_word = w;
        prepare_h = h; prepare_v = v; prepare_half = half; prepare_coin = coin;
        // U 由 word 派生：U = (w[5]?4:0) + (w[3]?1:0)。
        // DUT 只做一致性检查（U_conflict 会让 err_o 变 1），必须与 RTL 同算法。
        prepare_U = {1'b0, w[5]} * 3'd4 + {2'b0, w[3]};
        prepare_flash = F; prepare_qid = q;
        @(posedge clk); #1;
        @(negedge clk);
        prepare_valid = 1'b0;
    endtask

    logic rsp_seen;
    logic        d5_valid_seen;
    logic [19:0] d5_code_seen;
    logic [31:0] d5_tag_seen;
    logic        d5_err_seen;
    logic ev_applied;
    logic [9:0] ev_k;

    task automatic do_event(input logic [3:0] bi, input logic [3:0] ei,
                            input logic bv, input logic q);
        @(negedge clk);
        ev_bit_valid_i = 1'b1; ev_bit_i = bv; ev_bit_index_i = bi;
        ev_event_index_i = ei; ev_qid_i = q;
        #1;
        ev_applied = apply_fire_o; ev_k = k_o;
        @(posedge clk); #1;
        @(negedge clk);
        ev_bit_valid_i = 1'b0;
    endtask

    // 模拟宏：把节点模型算出的 raw 通过握手送回（7 级 recon 流水 → 等 7 拍）
    task automatic deliver_raw(input logic [12:0] raw, input logic rng,
                               output logic [31:0] got_tag);
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = raw; adc2_range_err_i = rng;
        #1;
        got_tag = adc2_rsp_tag_o;
        rsp_seen = adc2_rsp_valid_o;
        // recon_v21 是七级流水：
        //   S1a Y-b / S1b alpha 除法 / S2a-S2c N 与 3 拍倒数 I 汇合 /
        //   S3 X / S4 Q->code
        // rsp_valid 在 posedge1 采入，out_valid 要到 posedge7 才为 1。
        @(posedge clk); #1;
        @(posedge clk); #1;
        @(posedge clk); #1;
        @(posedge clk); #1;
        @(posedge clk); #1;
        @(posedge clk); #1;
        @(posedge clk); #1;
        d5_valid_seen = out_valid;
        d5_code_seen  = out_code;
        d5_tag_seen   = out_tag;
        d5_err_seen   = out_err;
        @(posedge clk);
        @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0; adc2_range_err_i = 1'b0;
    endtask

    // ---------------------------------------------------------------- sticky 脉冲
    // posedge 置位、negedge 记录后清零；同时锁存该拍随脉冲出现的值。
    bit          st_apply = 1'b0;
    bit          st_rsp   = 1'b0;
    bit          st_outv  = 1'b0;
    logic [31:0] rsp_tag_lat;
    logic [12:0] rsp_raw_lat;
    logic [19:0] out_code_lat;
    logic [31:0] out_tag_lat;
    logic        out_err_lat;

    always @(posedge clk) if (rst_n) begin
        if (apply_fire_o) st_apply <= 1'b1;
        if (adc2_rsp_valid_o) begin
            st_rsp      <= 1'b1;
            rsp_tag_lat <= adc2_rsp_tag_o;
            rsp_raw_lat <= adc2_rsp_raw_o;
        end
        if (out_valid) begin
            st_outv      <= 1'b1;
            out_code_lat <= out_code;
            out_tag_lat  <= out_tag;
            out_err_lat  <= out_err;
        end
    end

    // ---------------------------------------------------------------- 轨迹
    // negedge + 4 ns：距上一 posedge 9 ns（全部稳定），距下一 posedge 1 ns（不与驱动竞争）。
    always @(negedge clk) begin
        #4;
        if (tr_en) begin
            $fdisplay(tr_fd, "T %0d", $time);
            $fdisplay(tr_fd, "prepare_ready %0b", prepare_ready);
            $fdisplay(tr_fd, "acq_sel %0h", acq_sel_o);
            $fdisplay(tr_fd, "conv_sel %0h", conv_sel_o);
            $fdisplay(tr_fd, "pre_wr %0h", pre_wr_o);
            $fdisplay(tr_fd, "post_wr %0h", post_wr_o);
            $fdisplay(tr_fd, "arm %0b", adc2_capture_arm_o);
            $fdisplay(tr_fd, "rsp_valid %0b", st_rsp);
            $fdisplay(tr_fd, "rsp_tag %0h", st_rsp ? rsp_tag_lat : adc2_rsp_tag_o);
            $fdisplay(tr_fd, "rsp_raw %0h", st_rsp ? rsp_raw_lat : adc2_rsp_raw_o);
            $fdisplay(tr_fd, "apply %0b", st_apply);
            $fdisplay(tr_fd, "k %0h", k_o);
            $fdisplay(tr_fd, "final %0b", final_applied_o);
            $fdisplay(tr_fd, "final_k %0h", final_k_o);
            $fdisplay(tr_fd, "err %0b", err_o);
            $fdisplay(tr_fd, "done %0b", done_o);
            $fdisplay(tr_fd, "out_valid %0b", st_outv);
            $fdisplay(tr_fd, "out_code %0h", st_outv ? out_code_lat : out_code);
            $fdisplay(tr_fd, "out_err %0b", st_outv ? out_err_lat : out_err);
            $fdisplay(tr_fd, "out_tag %0h", st_outv ? out_tag_lat : out_tag);
            $fdisplay(tr_fd, "---");
            // 记录后清零：下一个采样窗口重新累积
            st_apply = 1'b0;
            st_rsp   = 1'b0;
            st_outv  = 1'b0;
        end
    end

    // ---------------------------------------------------------------- 交易计数
    // 用阻塞赋值（不是 <=）：body 在最后一拍关掉 cnt_en 后立刻打印计数，
    // 若用非阻塞赋值，最后一批 posedge 的增量还停在 NBA 队列里没落到变量，
    // 打印出来会**少 1**（实测：真实 2 笔、打印 1 笔）。
    always @(posedge clk) if (cnt_en && rst_n) begin
        if (prepare_valid && prepare_ready) n_prepare_acc = n_prepare_acc + 1;
        if (adc2_rsp_valid_o)               n_capture     = n_capture + 1;
        if (out_valid)                      n_out         = n_out + 1;
    end
