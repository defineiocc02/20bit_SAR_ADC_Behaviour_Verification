//===========================================================================
// p2_periph_tb.sv -- 外设/控制类子模块的**定向**覆盖补齐（P2 缺口 C）
//===========================================================================
// 为什么单独一个文件
//   p2_smoke_tb.sv 只跑"顶层配置通路 + slice_alloc + sadc_enc"三件事，自报过 5 处
//   没有定向覆盖。本文件把 5 个模块**各自单独例化**，一处一处打定向激励并断言，
//   不去碰顶层（顶层通路与链路数值已由 p2_tb / p3_top_tb 覆盖）。
//
// 覆盖清单（每项都写清"断言什么"与"故意做错时哪里会红"）
//   T1 weight_store   写守卫三条 + 写入窗口 + 越界地址不落盘 + 灌满全库
//   T2 status_regs    粘滞只由 clr/rst_n 解除（逐拍核对）+ clip 非粘滞 + 位序
//   T3 rdac_drv       扇出逐位 + load 之外保持 + slice_id 重复 = 后者覆盖前者
//   T4 ctrl_fsm       相位图（0/8/10/11/14/15 六个脉冲）+ cfg_ready/run 不变量
//   T5 calib_regs     空量程被 validate 拒绝 + 写窗口 + 串行控制位 + clear 优先
//
// 用法
//   python tools/run_rtl_sim.py --top p2_periph_tb --tb sim/tb/p2_periph_tb.sv \
//       --src rtl/core rtl/top rtl/params --incdir rtl/params --fresh
//   plusargs：
//     +trace=<file> 观测 trace：逐观测节拍落一行**模块级观测面**（见 [I]）
//   退出码：任何一处 MISMATCH -> 结束时报 FAIL 并 $fatal(1)。
//
// ---------------------------------------------------------------------------
//   [I] **观测 trace（`+trace=<file>`）—— 模块级观测面，用于测量"我们的观测面有多大"**
//
//   动机：同一个变异体在**模块级 TB** 上可能被杀掉，而在**顶层 4 列 trace**
//   （`p3_top_tb` 的 `sample_idx/dout/clip/analog_ovf`）上完全看不见。若不把这两者
//   区分开，Phase 1b 会把"**观测面盲区**"统计成"**等价变异体**"，分数系统性偏低。
//   所以本 TB 也要有一份 trace，作为**更宽的观测面**。
//
//   列（空格分隔，每行一条；**逐 `posedge clk` 落一行**，共 27 列）：
//     `cyc`    观测节拍计数（10 进制），充当论文里"记录的时间字段"
//     `cb_*`   calib_regs：cfg_ready / err_code / offset_q / adc2_min_q / adc2_max_q
//              / dem_en / bridge_en / sampling_mask_en（8 列）
//     `ws_*`   weight_store：err_write / w_q[0][0]（2 列）
//     `st_*`   status_regs：4 个粘滞位 / 2 个 clip 位 / status_word / status_clr_value（8 列）
//     `rd_*`   rdac_drv：slice_sel / main_sw / sub_sw / dither_sw 的**全向量**（4 列）
//     `cf_*`   ctrl_fsm：phase / phase_onehot / 7 个脉冲压成一位向量 / sample_idx（4 列）
//   符号量（`cb_off/cb_min/cb_max`）以**补码十六进制**落盘 —— 本 trace 的用途是
//   **字符串比较**（论文判据），不做数值解析。X/Z 原样落 `x`/`z`。
//
//   ⚠️ **只在显式给 `+trace` 时**才 fopen / 写文件 / 打印任何东西。不给时采样块
//   每拍只做一次 `if`、不产生任何输出 ⇒ **既有输出逐字节不变**。采样刻意放在
//   **独立 `always` 块**里而不是插进既有 `initial`，就是为了让"没有动过任何既有行"
//   成为**结构事实**（`git diff` 应为 +N/−0），而不是一句承诺。
//
// ---------------------------------------------------------------------------
// 如实登记：**没有做成的**
//   [U1] weight_store 的"写入后 Sigma W < 2^60"容量拒绝分支，在冻结尺寸下
//        **不可达**，本 TB 无法用激励触发它 —— 见 T1 末尾的界证明（它是"结构性
//        死代码"而不是"漏测"）。因此 T1 里对它的断言形式是"灌满全库也不越界"，
//        真正的"触发后拒写"只能在把阈值降到可达域的 mutant 上验证（另附）。
//   [U2] 本 TB **不覆盖** rtl/top/sar20_digital_core.sv 里的 0x1018 三拍串行器
//        （它属顶层，且 p2_smoke_tb T1 已经走过一次）；calib_regs 的 sel 串行
//        语义在本文件里是用"直接驱动 sel"的方式覆盖的。
//   [U3] 本 TB **不覆盖** int/整数乘法以外的模拟量语义（rdac_drv 只是数字扇出，
//        真实底板驱动在模拟域）。
//===========================================================================
`timescale 1ns/1ps
`include "rtl_params.vh"

module p2_periph_tb;

  localparam int N_SLOT = N_SLICES * N_UNIT_TOTAL;      // 1278
  localparam logic [W_BITS-1:0] WMAX  = {{(W_BITS-47){1'b0}}, 1'b1} << 47;  // 2^47
  localparam logic [W_BITS-1:0] WMAX1 = WMAX - {{(W_BITS-1){1'b0}}, 1'b1};  // 2^47-1
  // calib_regs 的错误码常量（与 calib_regs.sv 同值；那里是 localparam，无法 import）
  localparam logic [31:0] E_NONE  = 32'd0;
  localparam logic [31:0] E_EMPTY = 32'd3;
  localparam logic [31:0] E_WRITE = 32'd5;

  int errors = 0;
  int checks = 0;
  int shown  = 0;
  int err_base = 0;

  logic clk = 1'b0;
  always #5 clk = ~clk;
  logic rst_n = 1'b0;

  // ---- 观测 trace（`+trace=<file>`）：**模块级观测面**，逐观测节拍落一行 -----------
  // 列见模块头 [I]。只在显式给 `+trace` 时才有任何副作用（fopen / 写文件 / 打印）；
  // 不给时采样块每拍只做一次 `if`，**不产生任何输出**。
  // ⚠️ 采样刻意放在**独立的 always 块**里，而不是插进既有 initial ——
  //    这样"没有动过任何既有行"就是结构事实（git diff 应为 +N/−0），而不是承诺。
  // ⚠️ 采样块本体必须放在**所有 DUT 信号声明之后**（VCS 报 `Error-[IND] Identifier
  //    not declared`，踩过一次）：这里只放变量，块本体在 initial 之前。
  string trace_path = "";
  bit    trace_en   = 1'b0;
  int    fd_trace   = 0;
  int    trace_cyc  = 0;

  task automatic chk(input string name, input logic cond);
    begin
      checks = checks + 1;
      if (cond !== 1'b1) begin
        errors = errors + 1;
        if (shown < 60) begin
          $display("[%0t] MISMATCH: %s", $time, name);
          shown = shown + 1;
        end
      end
    end
  endtask

  // 每块跑完打小计：限流打印不会吞掉"后面块全错"这件事
  task automatic sub(input string tag, input int base);
    begin
      $display("  %s 小计：checks=%0d errors=%0d", tag, checks, errors - base);
    end
  endtask

  //=========================================================================
  // T1 DUT: weight_store（全尺寸）
  //=========================================================================
  logic        ws_cfg_ready, ws_wr_en, ws_err;
  logic [4:0]  ws_slice;
  logic [6:0]  ws_unit;
  logic [W_BITS-1:0] ws_data;
  logic [N_SLICES-1:0][N_UNIT_TOTAL-1:0][W_BITS-1:0] ws_wq;

  weight_store u_ws (
      .clear_load(1'b0), .load_complete(),
      .clk (clk), .rst_n (rst_n), .cfg_ready (ws_cfg_ready),
      .wr_en (ws_wr_en), .wr_slice (ws_slice), .wr_unit (ws_unit),
      .wr_data (ws_data), .err_write (ws_err), .w_q (ws_wq)
  );

  task automatic ws_write(input logic wr, input int s, input int u,
                          input logic [W_BITS-1:0] d, input logic exp_err);
    begin
      @(negedge clk);
      ws_wr_en = wr; ws_slice = 5'(s); ws_unit = 7'(u); ws_data = d;
      @(negedge clk);                 // 跨过一个 posedge
      chk($sformatf("T1 err_write=%0b (s=%0d u=%0d d=%0h)", exp_err, s, u, d),
          ws_err === exp_err);
      ws_wr_en = 1'b0;
    end
  endtask

  //=========================================================================
  // T2 DUT: status_regs
  //=========================================================================
  logic        sr_clr, ev_acc, ev_gain, ev_adc2, ev_cl, ev_ch, ev_an;
  logic [31:0] sr_err, sr_word, sr_clrval;
  logic        st_acc, st_gain, st_adc2, st_an, st_cl, st_ch;

  status_regs u_sr (
      .clk (clk), .rst_n (rst_n), .clr (sr_clr),
      .ev_acc_ovf (ev_acc), .ev_gain_err (ev_gain), .ev_adc2_ovf (ev_adc2),
      .ev_clip_low (ev_cl), .ev_clip_high (ev_ch), .ev_analog_ovf (ev_an),
      .err_code (sr_err), .acc_ovf_sticky (st_acc), .gain_err_sticky (st_gain),
      .adc2_ovf_sticky (st_adc2), .clip_low_last (st_cl), .clip_high_last (st_ch),
      .analog_ovf_sticky (st_an), .status_word (sr_word),
      .status_clr_value (sr_clrval)
  );

  //=========================================================================
  // T3 DUT: rdac_drv
  //=========================================================================
  logic          rd_load;
  logic [N_ACTIVE-1:0][4:0]        rd_slice_id;
  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] rd_main_on;
  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  rd_sub_on;
  logic [2*DITHER_UNITS_RANGE-1:0] rd_dither_rail;
  logic [N_SLICES-1:0]             rd_sel;
  logic [N_SLICES-1:0][N_UNIT_MAIN-1:0] rd_main;
  logic [N_SLICES-1:0][N_UNIT_SUB-1:0]  rd_sub;
  logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] rd_dsw;

  rdac_drv u_rd (
      .clk (clk), .rst_n (rst_n), .load (rd_load),
      .slice_id (rd_slice_id), .main_on (rd_main_on), .sub_on (rd_sub_on),
      .dither_rail (rd_dither_rail),
      .slice_sel (rd_sel), .main_sw (rd_main), .sub_sw (rd_sub), .dither_sw (rd_dsw)
  );

  //=========================================================================
  // T4 DUT: ctrl_fsm
  //=========================================================================
  logic        cf_cfg_ready, cf_run;
  logic [PHASES-1:0] cf_onehot;
  logic [$clog2(PHASES)-1:0] cf_phase;
  logic        cf_sample_en, cf_acq, cf_sadc, cf_dem, cf_rdac, cf_adc2, cf_recon;
  logic [31:0] cf_idx;

  ctrl_fsm #(.P_PHASES (PHASES)) u_cf (
      .clk (clk), .rst_n (rst_n), .cfg_ready (cf_cfg_ready), .run (cf_run),
      .phase_onehot (cf_onehot), .phase (cf_phase),
      .sample_en (cf_sample_en), .acq_phase (cf_acq), .sadc_latch (cf_sadc),
      .dem_advance (cf_dem), .rdac_load (cf_rdac), .adc2_latch (cf_adc2),
      .recon_start (cf_recon), .sample_idx (cf_idx)
  );

  //=========================================================================
  // T5 DUT: calib_regs
  //=========================================================================
  logic        cb_wr, cb_val, cb_clr, cb_ready;
  logic [3:0]  cb_sel;
  logic signed [V_BITS-1:0] cb_dv;
  logic        cb_db;
  logic [31:0] cb_err;
  logic signed [V_BITS-1:0] cb_off, cb_min, cb_max;
  logic        cb_dem, cb_brg, cb_smk;

  calib_regs #(.P_ADC2_BITS (ADC2_BITS)) u_cb (
      .weights_ready(1'b1), .config_busy(1'b0),
      .clk (clk), .rst_n (rst_n), .wr_en (cb_wr), .sel (cb_sel),
      .data_v (cb_dv), .data_b (cb_db), .validate (cb_val), .clear_valid (cb_clr),
      .cfg_ready (cb_ready), .err_code (cb_err),
      .offset_q (cb_off), .adc2_min_q (cb_min), .adc2_max_q (cb_max),
      .dem_en (cb_dem), .bridge_en (cb_brg), .sampling_mask_en (cb_smk)
  );

  task automatic cb_reg(input logic [3:0] sel, input logic signed [V_BITS-1:0] dv,
                        input logic db);
    begin
      @(negedge clk);
      cb_sel = sel; cb_dv = dv; cb_db = db; cb_wr = 1'b1;
      @(negedge clk);
      cb_wr = 1'b0;
    end
  endtask

  task automatic cb_pulse_validate();
    begin
      @(negedge clk); cb_val = 1'b1;
      @(negedge clk); cb_val = 1'b0;
      @(posedge clk);                 // 等逻辑落定
    end
  endtask

  //=========================================================================
  // 主流程
  //=========================================================================
  int  i, s, u, n, n_sen;
  logic [31:0] idx0;
  logic [W_BITS-1:0] snap00;
  int  nz;

  // ---- 观测 trace 采样块：**必须放在所有 DUT 信号声明之后**（VCS 要求先声明后使用）----
  always @(posedge clk) begin
    if (trace_en) begin
      $fwrite(fd_trace,
              "%0d %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h %0h\n",
              trace_cyc,
              cb_ready, cb_err, cb_off, cb_min, cb_max, cb_dem, cb_brg, cb_smk,
              ws_err, ws_wq[0][0],
              st_acc, st_gain, st_adc2, st_an, st_cl, st_ch, sr_word, sr_clrval,
              rd_sel, rd_main, rd_sub, rd_dsw,
              cf_phase, cf_onehot,
              {cf_sample_en, cf_acq, cf_sadc, cf_dem, cf_rdac, cf_adc2, cf_recon}, cf_idx);
      $fflush(fd_trace);   // 每拍落盘：之后某处 $fatal 中止也不丢已观测的部分
      trace_cyc = trace_cyc + 1;
    end
  end

  initial begin
    // 观测 trace（可选）：只在显式给 `+trace=<file>` 时才产生任何副作用
    trace_en = $value$plusargs("trace=%s", trace_path);
    if (trace_en) begin
      fd_trace = $fopen(trace_path, "w");
      if (fd_trace == 0) $fatal(1, "p2_periph_tb: cannot open trace file %s", trace_path);
      $display("[P2P-TRACE] 观测 trace -> %s（列见模块头 [I]）", trace_path);
    end

    // 默认输入
    ws_cfg_ready = 1'b0; ws_wr_en = 1'b0; ws_slice = 5'd0; ws_unit = 7'd0; ws_data = '0;
    sr_clr = 1'b0; ev_acc = 0; ev_gain = 0; ev_adc2 = 0; ev_cl = 0; ev_ch = 0; ev_an = 0;
    sr_err = 32'd0;
    rd_load = 1'b0; rd_dither_rail = '0;
    for (i = 0; i < N_ACTIVE; i = i + 1) begin
      rd_slice_id[i] = 5'd0; rd_main_on[i] = '0; rd_sub_on[i] = '0;
    end
    cf_cfg_ready = 1'b0; cf_run = 1'b0;
    cb_wr = 1'b0; cb_sel = 4'd0; cb_dv = '0; cb_db = 1'b0; cb_val = 1'b0; cb_clr = 1'b0;

    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    //=====================================================================
    $display("=== T1 weight_store：写守卫 / 写窗口 / 越界不落盘 / 灌满全库 ===");
    //=====================================================================
    err_base = errors;

    ws_write(1'b1, 0, 0, {{(W_BITS-27){1'b0}}, 27'd67108864}, 1'b0);   // 2^26 正常写
    chk("T1 正常写后 w[0][0]=2^26", ws_wq[0][0] == {{(W_BITS-27){1'b0}}, 27'd67108864});

    snap00 = ws_wq[0][0];
    ws_write(1'b1, 0, 0, '0, 1'b1);                 // W = 0 -> 拒
    chk("T1 拒写后 w[0][0] 不变", ws_wq[0][0] == snap00);

    ws_write(1'b1, 0, 0, WMAX, 1'b1);               // W = 2^47 -> 拒（严格 <）
    chk("T1 拒写后 w[0][0] 仍不变", ws_wq[0][0] == snap00);

    ws_write(1'b1, 0, 0, WMAX1, 1'b0);              // W = 2^47-1 -> 接受（边界）
    chk("T1 边界值 2^47-1 被接受", ws_wq[0][0] == WMAX1);

    // 写入窗口：cfg_ready = 1 时拒写
    ws_cfg_ready = 1'b1;
    ws_write(1'b1, 3, 5, {{(W_BITS-27){1'b0}}, 27'd67108864}, 1'b1);
    chk("T1 cfg_ready=1 时 w[3][5] 仍为 0", ws_wq[3][5] == '0);
    ws_cfg_ready = 1'b0;

    // 越界地址：拒写，且**不能**因为 s_idx/u_idx 掩蔽到 0 而污染 w[0][0]
    snap00 = ws_wq[0][0];
    ws_write(1'b1, N_SLICES, 0, {{(W_BITS-27){1'b0}}, 27'd67108864}, 1'b1);
    chk("T1 越界 slice 拒写且 w[0][0] 未被污染", ws_wq[0][0] == snap00);
    ws_write(1'b1, 0, N_UNIT_TOTAL, {{(W_BITS-27){1'b0}}, 27'd67108864}, 1'b1);
    chk("T1 越界 unit 拒写且 w[0][0] 未被污染", ws_wq[0][0] == snap00);
    ws_write(1'b1, N_SLICES, N_UNIT_TOTAL, {{(W_BITS-27){1'b0}}, 27'd67108864}, 1'b1);
    chk("T1 双越界拒写且 w[0][0] 未被污染", ws_wq[0][0] == snap00);

    // 灌满全库（每格都写合法上界 2^47-1）：容量守卫**不许**拒任何一次写
    n = 0;
    for (s = 0; s < N_SLICES; s = s + 1) begin
      for (u = 0; u < N_UNIT_TOTAL; u = u + 1) begin
        @(negedge clk);
        ws_wr_en = 1'b1; ws_slice = 5'(s); ws_unit = 7'(u); ws_data = WMAX1;
        @(negedge clk);
        if (ws_err !== 1'b0) n = n + 1;
        ws_wr_en = 1'b0;
      end
    end
    chk("T1 灌满全库 1278 格无一次被容量守卫拒", n == 0);

    @(negedge clk); #1;                    // 让 sum_all 稳定在最新值
    nz = 0;
    for (s = 0; s < N_SLICES; s = s + 1)
      for (u = 0; u < N_UNIT_TOTAL; u = u + 1)
        if (ws_wq[s][u] !== WMAX1) nz = nz + 1;
    chk("T1 1278 格全部落盘为 2^47-1", nz == 0);

    // 容量守卫在冻结尺寸下不可达的**界证明**（写成断言，不是注释）
    //   可达的最大 Sigma W = P_N_SLICES*P_N_UNITS*(2^47-1)
    //   端口可寻址的上限      = 32 * 128 = 4096 格（wr_slice 5 位 / wr_unit 7 位）
    //   4096*(2^47-1) < 2^59 < 2^60 = SUM_MAX  -> sum_new < SUM_MAX 恒真
    chk("T1 界证明：1278*(2^47-1) < 2^60",
        (64'd1278 * {{(64-W_BITS){1'b0}}, WMAX1}) < (64'd1 << 60));
    chk("T1 界证明：端口可寻址上限 4096*(2^47-1) < 2^60",
        (64'd4096 * {{(64-W_BITS){1'b0}}, WMAX1}) < (64'd1 << 60));
    chk("T1 全库和确实 < 2^60（SUM_MAX）", u_ws.sum_all < (64'd1 << 60));

    $display("  [U1] 容量分支 sum_new < SUM_MAX 在冻结尺寸下不可达（界证明见上两条）；");
    $display("       本 TB 用「灌满全库也不越界」代替，真正的触发只在 mutant 上做。");

    sub("T1", err_base);

    //=====================================================================
    $display("=== T2 status_regs：粘滞只由 clr/rst_n 解除 / clip 非粘滞 / 位序 ===");
    //=====================================================================
    rst_n = 1'b0; repeat (3) @(posedge clk); rst_n = 1'b1; repeat (2) @(posedge clk);
    err_base = errors;

    chk("T2 复位后 4 个粘滞位全 0",
        (st_acc === 1'b0) && (st_gain === 1'b0) && (st_adc2 === 1'b0) && (st_an === 1'b0));
    chk("T2 复位后 clip 两位全 0", (st_cl === 1'b0) && (st_ch === 1'b0));
    chk("T2 status_clr_value = 32'h3F", sr_clrval == 32'h0000_003F);

    // 一次性事件 -> 粘滞
    @(negedge clk); ev_acc = 1'b1; ev_gain = 1'b1; ev_adc2 = 1'b1; ev_an = 1'b1;
    @(negedge clk); ev_acc = 1'b0; ev_gain = 1'b0; ev_adc2 = 1'b0; ev_an = 1'b0;
    @(posedge clk); #1;
    chk("T2 一次性事件后 4 个粘滞位全 1",
        (st_acc === 1'b1) && (st_gain === 1'b1) && (st_adc2 === 1'b1) && (st_an === 1'b1));

    // 逐拍核对：事件撤销后 64 拍内粘滞**绝不允许**自清
    for (i = 0; i < 64; i = i + 1) begin
      @(posedge clk); #1;
      chk($sformatf("T2 粘滞自保持 i=%0d", i),
          (st_acc === 1'b1) && (st_gain === 1'b1) && (st_adc2 === 1'b1) && (st_an === 1'b1));
    end

    // clip 非粘滞：跟随 ev，撤销即落
    @(negedge clk); ev_cl = 1'b1; ev_ch = 1'b0;
    @(posedge clk); #1;
    chk("T2 clip_low 跟随 ev_cl=1", st_cl === 1'b1);
    chk("T2 clip_high 保持 0", st_ch === 1'b0);
    @(negedge clk); ev_cl = 1'b0; ev_ch = 1'b1;
    @(posedge clk); #1;
    chk("T2 clip_low 撤销即落（非粘滞）", st_cl === 1'b0);
    chk("T2 clip_high 跟随 ev_ch=1", st_ch === 1'b1);

    // clr：只清 4 个粘滞位，**不**碰 clip，也**不**改 err_code
    // ⚠️ clip 是非粘滞（逐拍跟随 ev_clip_*），所以"clr 生效那一拍"必须让 ev_clip_*
    //    保持为 1，否则看到的是"ev 撤销导致 clip 落"，不是"clr 影响 clip"。踩过一次。
    sr_err = 32'h0000_002A;
    @(negedge clk); ev_cl = 1'b1; ev_ch = 1'b1;
    @(negedge clk); sr_clr = 1'b1;                 // 这一拍 ev_cl/ev_ch 仍为 1
    @(posedge clk); #1;                            // clr 生效拍
    chk("T2 clr 后 4 个粘滞位归零",
        (st_acc === 1'b0) && (st_gain === 1'b0) && (st_adc2 === 1'b0) && (st_an === 1'b0));
    chk("T2 clr 不影响 clip_low（同拍 ev_cl=1）", st_cl === 1'b1);
    chk("T2 clr 不影响 clip_high（同拍 ev_ch=1）", st_ch === 1'b1);
    chk("T2 clr 不改 err_code", sr_err == 32'h0000_002A);
    @(negedge clk); sr_clr = 1'b0; ev_cl = 1'b0; ev_ch = 1'b0;
    @(posedge clk); #1;
    chk("T2 撤销 ev 后 clip 归 0（印证非粘滞）", (st_cl === 1'b0) && (st_ch === 1'b0));

    // clr 之后事件撤销：粘滞保持在 0（没有幽灵置位）
    for (i = 0; i < 8; i = i + 1) begin
      @(posedge clk); #1;
      chk($sformatf("T2 clr 后保持 0 i=%0d", i),
          (st_acc === 1'b0) && (st_gain === 1'b0) && (st_adc2 === 1'b0) && (st_an === 1'b0));
    end

    // 位序：status_word = {err_code[25:0], an, ch, cl, adc2, gain, acc}
    // 分两拍核对：粘滞位靠 ev_*，clip 位靠 ev_clip_*，两者不能在同一拍都拿满。
    @(negedge clk);
    ev_acc = 1'b1; ev_gain = 1'b1; ev_adc2 = 1'b1; ev_an = 1'b1;
    ev_cl = 1'b1; ev_ch = 1'b0; sr_err = 32'h0000_0035;
    @(posedge clk); #1;
    chk("T2 status_word 位序（an=1,ch=0,cl=1,adc2=gain=acc=1）",
        sr_word == {6'd0, 26'h35, 1'b1, 1'b0, 1'b1, 1'b1, 1'b1, 1'b1});
    @(negedge clk);
    ev_acc = 1'b0; ev_gain = 1'b0; ev_adc2 = 1'b0; ev_an = 1'b0; ev_ch = 1'b1;
    @(posedge clk); #1;                            // 粘滞保持、clip 两位都在
    chk("T2 status_word 低 6 位全 1 = 6'b111111",
        sr_word == {6'd0, 26'h35, 6'b111111});
    // 全清后 word 必须为 0（err_code 也清）
    sr_err = 32'd0;
    @(negedge clk); sr_clr = 1'b1; ev_cl = 1'b0; ev_ch = 1'b0;
    @(posedge clk); #1;
    chk("T2 clr 后 status_word = 0", sr_word == 32'd0);
    @(negedge clk); sr_clr = 1'b0;              // ⚠️ 必须撤销，否则下一段一直被清（踩过）

    // rst_n 也必须能解除粘滞
    @(negedge clk); ev_acc = 1'b1;
    @(posedge clk); #1;
    chk("T2 再置 acc_ovf=1", st_acc === 1'b1);
    @(negedge clk); ev_acc = 1'b0;
    rst_n = 1'b0; repeat (2) @(posedge clk); #1;
    chk("T2 rst_n 能解除 acc_ovf", st_acc === 1'b0);
    rst_n = 1'b1; repeat (2) @(posedge clk);

    sub("T2", err_base);

    //=====================================================================
    $display("=== T3 rdac_drv：逐位扇出 / load 外保持 / slice_id 重复 ===");
    //=====================================================================
    rst_n = 1'b0; repeat (3) @(posedge clk); rst_n = 1'b1; repeat (2) @(posedge clk);
    err_base = errors;

    chk("T3 复位后 slice_sel 全 0", rd_sel == {N_SLICES{1'b0}});

    // 8 个互不相同的 slice_id = 0..7
    for (i = 0; i < N_ACTIVE; i = i + 1) begin
      rd_slice_id[i] = 5'(i);
      rd_main_on[i]  = {{(N_UNIT_MAIN-1){1'b0}}, 1'b1} << i[N_UNIT_MAIN-1:0];
      rd_sub_on[i]   = {{(N_UNIT_SUB-1){1'b0}}, 1'b1} << i[2:0];
    end
    rd_dither_rail = 4'b1010;
    @(negedge clk); rd_load = 1'b1;
    @(negedge clk); rd_load = 1'b0;
    @(posedge clk); #1;

    for (i = 0; i < N_SLICES; i = i + 1) begin
      if (i < N_ACTIVE) begin
        chk($sformatf("T3 slice_sel[%0d]=1", i), rd_sel[i] === 1'b1);
        chk($sformatf("T3 main_sw[%0d] 逐位", i), rd_main[i] == rd_main_on[i]);
        chk($sformatf("T3 sub_sw[%0d] 逐位", i), rd_sub[i] == rd_sub_on[i]);
        chk($sformatf("T3 dither_sw[%0d] 逐位", i), rd_dsw[i] == rd_dither_rail);
      end else begin
        chk($sformatf("T3 未选中 slice_sel[%0d]=0", i), rd_sel[i] === 1'b0);
        chk($sformatf("T3 未选中 main_sw[%0d]=0", i), rd_main[i] == '0);
        chk($sformatf("T3 未选中 sub_sw[%0d]=0", i), rd_sub[i] == '0);
        chk($sformatf("T3 未选中 dither_sw[%0d]=0", i), rd_dsw[i] == '0);
      end
    end

    // load 之外保持：改输入、不发 load，输出必须纹丝不动
    for (i = 0; i < N_ACTIVE; i = i + 1) begin
      rd_slice_id[i] = 5'(i + 8);
      rd_main_on[i]  = {N_UNIT_MAIN{1'b1}};
    end
    rd_dither_rail = 4'b0101;
    repeat (4) @(posedge clk); #1;
    for (i = 0; i < N_ACTIVE; i = i + 1) begin
      chk($sformatf("T3 load=0 时 slice_sel[%0d] 保持", i), rd_sel[i] === 1'b1);
      chk($sformatf("T3 load=0 时 main_sw[%0d] 保持", i), rd_main[i] == ({{(N_UNIT_MAIN-1){1'b0}}, 1'b1} << i[N_UNIT_MAIN-1:0]));
    end

    // slice_id 重复：后者覆盖前者，且**不**报错（只是可观测的"最后写胜出"）
    rd_slice_id[0] = 5'd0;
    rd_slice_id[1] = 5'd0;                       // 与 a=0 重复
    for (i = 2; i < N_ACTIVE; i = i + 1) rd_slice_id[i] = 5'(i);
    for (i = 0; i < N_ACTIVE; i = i + 1) begin
      rd_main_on[i] = '0; rd_sub_on[i] = '0;
    end
    rd_main_on[1] = {N_UNIT_MAIN{1'b1}};         // a=1 是写到 index 0 的最后一笔
    rd_main_on[7] = {{(N_UNIT_MAIN-1){1'b0}}, 1'b1};
    @(negedge clk); rd_load = 1'b1;
    @(negedge clk); rd_load = 1'b0;
    @(posedge clk); #1;

    chk("T3 重复 slice_id 后 slice_sel[0] 仍为 1", rd_sel[0] === 1'b1);
    chk("T3 重复 slice_id 后 slice_sel[1] 为 0（没有槽写它）", rd_sel[1] === 1'b0);
    chk("T3 重复 slice_id = 后者覆盖前者（main_sw[0] 取 a=1 的值）",
        rd_main[0] == rd_main_on[1]);
    chk("T3 重复 slice_id 不影响其它槽（main_sw[7]）", rd_main[7] == rd_main_on[7]);
    nz = 0;
    for (i = 0; i < N_SLICES; i = i + 1) if (rd_sel[i] === 1'b1) nz = nz + 1;
    chk("T3 重复后选中槽数 = 7（8 个活动 slice 里有 1 对重合）", nz == 7);

    sub("T3", err_base);

    //=====================================================================
    $display("=== T4 ctrl_fsm：相位图 / cfg_ready 与 run 的不变量 ===");
    //=====================================================================
    rst_n = 1'b0; repeat (3) @(posedge clk); rst_n = 1'b1; repeat (2) @(posedge clk);
    err_base = errors;

    // 不变量 1：cfg_ready=0 -> phase 恒 0，所有脉冲恒 0
    cf_cfg_ready = 1'b0; cf_run = 1'b1;
    for (i = 0; i < 24; i = i + 1) begin
      @(negedge clk); #1;
      chk($sformatf("T4 cfg_ready=0 时 phase=0 i=%0d", i), cf_phase === 4'd0);
      chk($sformatf("T4 cfg_ready=0 时无脉冲 i=%0d", i),
          (cf_sample_en === 1'b0) && (cf_acq === 1'b0) && (cf_sadc === 1'b0) &&
          (cf_dem === 1'b0) && (cf_rdac === 1'b0) && (cf_adc2 === 1'b0) && (cf_recon === 1'b0));
      chk($sformatf("T4 cfg_ready=0 时 onehot=0 i=%0d", i), cf_onehot == {PHASES{1'b0}});
    end
    chk("T4 cfg_ready=0 时 sample_idx 不涨", cf_idx == 32'd0);

    // 不变量 2：已配置但 run=0 -> phase 归 0，无脉冲
    cf_cfg_ready = 1'b1; cf_run = 1'b0;
    repeat (5) @(posedge clk);
    @(negedge clk); #1;
    chk("T4 run=0 时 phase 归 0", cf_phase === 4'd0);
    chk("T4 run=0 时无脉冲",
        (cf_sample_en === 1'b0) && (cf_rdac === 1'b0) && (cf_recon === 1'b0));

    // 相位图：连跑 3 个完整 16 拍窗口，逐拍核对相位与六个脉冲。
    // ⚠️ 不要试图"先对齐到相位 0 再数"——`cf_run=1` 到循环第一个 `@(posedge)` 之间
    //    是否夹着 posedge 取决于进入时刻，对齐本身就可能不成立（踩过两次）。
    //    改成**相位无关**的口径：在任意 48 个连续拍里，sample_en 必须恰好出现 3 次，
    //    sample_idx 必须恰好 +3（两者同一条件，互为交叉印证）。
    cf_run = 1'b1;
    @(negedge clk);                          // 停在某一拍的**中点**
    idx0  = cf_idx;
    n_sen = 0;
    for (n = 0; n < 3 * PHASES; n = n + 1) begin
      if (cf_sample_en === 1'b1) n_sen = n_sen + 1;
      chk($sformatf("T4 onehot 与 phase 一致 n=%0d", n),
          cf_onehot == ({{(PHASES-1){1'b0}}, 1'b1} << cf_phase));
      chk($sformatf("T4 sample_en 只在相位 0 n=%0d", n),
          cf_sample_en === (cf_phase == 4'd0));
      chk($sformatf("T4 sadc_latch 在相位 8 n=%0d", n),
          cf_sadc === (cf_phase == 4'd8));
      chk($sformatf("T4 dem_advance 在相位 10 n=%0d", n),
          cf_dem === (cf_phase == 4'd10));
      chk($sformatf("T4 rdac_load 在相位 11 n=%0d", n),
          cf_rdac === (cf_phase == 4'd11));
      chk($sformatf("T4 adc2_latch 在相位 14 n=%0d", n),
          cf_adc2 === (cf_phase == 4'd14));
      chk($sformatf("T4 recon_start 在相位 15 n=%0d", n),
          cf_recon === (cf_phase == 4'd15));
      chk($sformatf("T4 acq_phase 覆盖相位 0..6 n=%0d", n),
          cf_acq === (cf_phase <= 4'd6));
      @(posedge clk);
      #1;                                    // 推进到下一拍的中点，保持采样口径一致
    end
    $display("  [T4] 48 拍内 sample_en=%0d 次；sample_idx 由 %0d 变到 %0d",
             n_sen, idx0, cf_idx);
    chk("T4 48 拍内 sample_en 恰好 3 次", n_sen == 3);
    chk("T4 48 拍内 sample_idx 恰好 +3", cf_idx == (idx0 + 32'd3));

    // run 拉低 -> 回到相位 0 并停住
    @(negedge clk); cf_run = 1'b0;
    repeat (4) @(posedge clk);
    @(negedge clk); #1;
    chk("T4 run 拉低后 phase=0", cf_phase === 4'd0);
    chk("T4 run 拉低后 sample_idx 冻结", cf_idx == idx0 + 32'd3);
    $display("  [T4] 拉低 run 后：phase=%0d sample_idx=%0d（冻结值 %0d）",
             cf_phase, cf_idx, idx0 + 32'd3);

    sub("T4", err_base);

    //=====================================================================
    $display("=== T5 calib_regs：空量程拒绝 / 写窗口 / 控制位 / clear 优先 ===");
    //=====================================================================
    rst_n = 1'b0; repeat (3) @(posedge clk); rst_n = 1'b1; repeat (2) @(posedge clk);
    err_base = errors;

    chk("T5 复位后 cfg_ready=0", cb_ready === 1'b0);
    chk("T5 复位后 err_code=0", cb_err == E_NONE);
    chk("T5 复位后三系数为 0", (cb_off == 0) && (cb_min == 0) && (cb_max == 0));
    chk("T5 复位后 dem=0 / bridge=1 / smask=0（DEM 陷阱）",
        (cb_dem === 1'b0) && (cb_brg === 1'b1) && (cb_smk === 1'b0));

    // 空量程：min = max = 0（复位默认）-> validate 必须拒绝
    cb_pulse_validate();
    chk("T5 [空] min=max=0 时 validate 被拒（cfg_ready 保持 0）", cb_ready === 1'b0);
    chk("T5 [空] err_code = ERR_RANGE_EMPTY(3)", cb_err == E_EMPTY);

    // min == max != 0
    cb_reg(4'd1, 64'sd1000, 1'b0);
    cb_reg(4'd2, 64'sd1000, 1'b0);
    cb_pulse_validate();
    chk("T5 [空] min==max=1000 仍被拒", cb_ready === 1'b0);
    chk("T5 [空] err_code 仍为 3", cb_err == E_EMPTY);

    // min > max
    cb_reg(4'd1, 64'sd2000, 1'b0);
    cb_reg(4'd2, 64'sd1000, 1'b0);
    cb_pulse_validate();
    chk("T5 [空] min>max 仍被拒", cb_ready === 1'b0);
    chk("T5 [空] err_code 仍为 3", cb_err == E_EMPTY);

    // min < max -> 通过；顺带用真实口径的三系数
    cb_reg(4'd1, -64'sd53687091, 1'b0);
    cb_reg(4'd2,  64'sd590558003, 1'b0);
    cb_reg(4'd0,  64'sd0, 1'b0);
    cb_pulse_validate();
    chk("T5 [非空] min<max 时 validate 通过", cb_ready === 1'b1);
    chk("T5 [非空] err_code 清 0", cb_err == E_NONE);
    chk("T5 [非空] 三系数落盘", (cb_off == 0) && (cb_min == -64'sd53687091) &&
                                (cb_max == 64'sd590558003));

    // 写窗口：cfg_ready=1 时写被拒
    cb_reg(4'd0, 64'sd777, 1'b0);
    chk("T5 生效期写被拒 -> ERR_CFG_WRITE(5)", cb_err == E_WRITE);
    chk("T5 生效期写被拒后 offset_q 不变", cb_off == 64'sd0);

    // 控制位 sel=3/4/5
    @(negedge clk); cb_val = 1'b0; cb_clr = 1'b1;   // 先撤销生效才能写
    @(negedge clk); cb_clr = 1'b0;
    repeat (2) @(posedge clk);
    chk("T5 clear_valid 撤销 cfg_ready", cb_ready === 1'b0);

    cb_reg(4'd3, '0, 1'b1);   // dem_en
    cb_reg(4'd4, '0, 1'b0);   // bridge_en
    cb_reg(4'd5, '0, 1'b1);   // sampling_mask_en
    chk("T5 sel=3/4/5 分别写 dem/bridge/smask", (cb_dem === 1'b1) && (cb_brg === 1'b0) && (cb_smk === 1'b1));

    // 未定义 sel（6..15）：报 ERR_CFG_WRITE，不改任何寄存器
    cb_reg(4'd6,  64'sd12345, 1'b1);
    cb_reg(4'd9,  64'sd54321, 1'b1);
    cb_reg(4'd15, 64'sd99999, 1'b1);
    chk("T5 未定义 sel 不改三系数", (cb_off == 64'sd0) && (cb_min == -64'sd53687091) &&
                                    (cb_max == 64'sd590558003));
    chk("T5 未定义 sel 不改控制位", (cb_dem === 1'b1) && (cb_brg === 1'b0) && (cb_smk === 1'b1));

    // New load epoch requires all three scalar fields again.
    cb_reg(4'd0, 64'sd0, 1'b0);
    // clear 优先于 validate（同拍）
    cb_reg(4'd1, -64'sd53687091, 1'b0);
    cb_reg(4'd2,  64'sd590558003, 1'b0);
    cb_pulse_validate();
    chk("T5 重新 validate 通过", cb_ready === 1'b1);
    @(negedge clk); cb_val = 1'b1; cb_clr = 1'b1;    // 同拍
    @(negedge clk); cb_val = 1'b0; cb_clr = 1'b0;
    @(posedge clk); #1;
    chk("T5 validate 与 clear 同拍时 clear 胜（cfg_ready=0）", cb_ready === 1'b0);

    sub("T5", err_base);

    // 观测 trace 收尾（只在开了 `+trace` 时才有输出；未开时零副作用）
    if (trace_en) begin
      $fflush(fd_trace);
      $fclose(fd_trace);
      $display("[P2P-TRACE] trace 关闭：共 %0d 行 -> %s", trace_cyc, trace_path);
    end

    //=====================================================================
    $display("================================================");
    $display("  p2_periph: checks=%0d errors=%0d", checks, errors);
    $display("================================================");
    if (errors != 0) $fatal(1, "p2_periph FAILED: %0d mismatch", errors);
    $display("p2_periph PASS");
    $finish;
  end

  // 兜底超时
  initial begin
    #400000;
    $fatal(1, "p2_periph TIMEOUT");
  end

endmodule
