//===========================================================================
// p2_tb.sv -- P2 算术核自检式 testbench
//===========================================================================
// 设计原则（沿用 P1 的教训，见 docs/rtl/P1_INTERFACE.md §8.3）
//   1. 每项测试有**独立错误账本**，避免打印限流把后面测试的失败信息吞掉。
//   2. 断言必须有"能变红"的对应变异实验（P2_INTERFACE.md §13 登记）。
//   3. 出错就计数/报错，**绝不静默继续**。
//   4. 向量文件读法：跳过 `//` 注释与 `#DATA` 标记，用 `$sscanf` 取 token 判定，
//      **不依赖字符位置**（`$fgets` 把首字符放在寄存器 MSB，P1 的 TB-2 踩过）。
//
// 依赖：只依赖**已存在**的模块（div_floor / adc2_dec / recon_core /
//       dem_state_gen / dem_addr_gen / swap_decode / unit_therm）。
//       slice_alloc / sadc_enc / 顶层的覆盖由 sim/tb/p2_smoke_tb.sv 与综合负责。
//
// 覆盖（对应 P2_INTERFACE.md §13）
//   T1  div_floor 定向（p2_div.hex）
//   T2  div_floor 定义式自检 q*d <= a < (q+1)*d —— **不依赖模型**的独立判据
//   T3  adc2_dec 定向（p2_scalar.hex），含 ties-to-even 8 样例
//   T4  recon_core 延迟实测 + cfg_ready 门控
//   T5  recon_core **2^20 全码 oracle**：dout == adc2_code，且无 clip/ovf
//   T6  recon_core 真实掩码用例（p2_masks.hex）
//   T7  recon_core 进位处密集斜坡（p2_ramp.hex）+ 单调性
//   T8  溢出（粘滞、不 wrap）与剪裁（与 analog_ovf 分开）
//   T9  L3 链路 M1->M2->M3->M5->recon_core，只给最外层激励
//===========================================================================
`timescale 1ns/1ps
`include "rtl_params.vh"

module p2_tb;

  // recon_core 固定延迟。**口径必须写清**，否则 11/12 之争毫无意义：
  //   从 `start` 被撤销的那个 negedge 起算，到 `dout_valid` 为高的那个 negedge
  //   之间经过的 negedge 个数 = 11（P_STAGES=7，N_CYC=9）。
  // 文档里写的 "12" 用的是另一种口径（把 start 那一拍本身也计入）。
  // 本条由 T4 **实测**并把数字打印出来；若与常量不符则 T4 报错。
  localparam int NLAT = 11;

  logic clk = 1'b0;
  always #1 clk = ~clk;

  string vdir = "sim/vectors";
  // `+only=<name>`：只跑指定测试。T5 的 2^20 oracle 要几分钟，
  // 而在 T4/T6/T9 上定位问题时不必每次都付这个代价。空串 = 全跑。
  string only = "";
  int    errors = 0;
  int    checks = 0;
  int    shown  = 0;
  int    err_base = 0;
  int    e1, e2, e3, e4, e5, e6, e7, e8, e9;

  localparam int W_COUNT = int'(N_SLICES) * int'(N_UNIT_TOTAL);   // 1278

  logic  rst_n;

  // oracle 的常量（从 p2_oracle_spec.hex / p2_oracle_cfg.hex 读入；实测值，不手写）
  logic [63:0] o_w0, o_w1, o_w2;
  logic [63:0] or_min, or_max, or_off;

  logic                                 r_start, r_cfg_ready, r_clr, r_smask;
  logic [N_ACTIVE-1:0][4:0]             r_sid;
  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] r_mon;
  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  r_son;
  logic [2*DITHER_UNITS_RANGE-1:0]      r_rail;
  logic [ADC2_BITS-1:0]                 r_code;
  logic signed [V_BITS-1:0]             r_inj, r_off, r_min, r_max;
  logic [N_SLICES-1:0][N_UNIT_TOTAL-1:0][W_BITS-1:0] r_wrom;
  logic [OUT_BITS-1:0]                  r_dout;
  logic                                 r_dvalid, r_clipl, r_cliph, r_ovf, r_gerr, r_a2ovf;

  logic                            k_bank, k_dem_en, k_bridge;
  logic [8:0]                      k_coarse;
  logic signed [15:0]              k_dither;
  logic signed [7:0]               k_bank_dither;
  logic [8:0]                      k_sid_a, k_sid_b, k_sid;
  logic [N_UNIT_MAIN-1:0][5:0]     k_mlog;
  logic [N_UNIT_SUB-1:0][2:0]      k_slog;
  logic [N_ACTIVE-1:0][6:0]        k_mc;
  logic [N_ACTIVE-1:0][2:0]        k_sc;
  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] k_mon;
  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  k_son;
  logic [2*DITHER_UNITS_RANGE-1:0] k_rail;
  logic [N_ACTIVE-1:0][4:0]        k_sid_arr;
  logic [ADC2_BITS-1:0]            k_code;
  logic signed [V_BITS-1:0]        k_inj;
  logic                            k_start, k_dvalid;
  logic [OUT_BITS-1:0]             k_dout;
  logic                            k_clipl, k_cliph, k_ovf, k_gerr, k_a2ovf;

  int    link_analog_ovf_count = 0;

  //=========================================================================
  // 通用工具
  //=========================================================================
  function automatic int open_data(input string name);
    int    fd;
    string line, tok;
    fd = $fopen({vdir, "/", name}, "r");
    if (fd == 0) begin
      $display("FATAL: cannot open %s/%s", vdir, name);
      $fatal(1);
    end
    forever begin
      if ($fgets(line, fd) == 0) $fatal(1, "no #DATA marker in %s", name);
      if ($sscanf(line, "%s", tok) == 1 && tok == "#DATA") break;
    end
    return fd;
  endfunction

  function automatic void rd(input int fd, output logic [63:0] v);
    if ($fscanf(fd, "%h", v) != 1) $fatal(1, "p2_tb: short read");
  endfunction

  task automatic expect_eq(input string tag, input logic [63:0] got,
                           input logic [63:0] exp);
    checks++;
    if (got !== exp) begin
      errors++;
      if (shown < 30) begin
        shown++;
        $display("  MISMATCH %s: got=%0d exp=%0d", tag, $signed(got), $signed(exp));
      end
    end
  endtask

  //=========================================================================
  // T1/T2 除法器
  //=========================================================================
  logic                d_start;
  logic signed [62:0]  d_a;
  logic [63:0]         d_d;
  logic signed [62:0]  d_q;
  logic                d_err, d_done;

  div_floor #(.P_W_A(63), .P_W_D(64), .P_STAGES(7)) u_div (
      .clk(clk), .rst_n(rst_n), .start(d_start), .a(d_a), .d(d_d),
      .q(d_q), .err(d_err), .busy(), .done(d_done)
  );

  task automatic div_run(input logic signed [62:0] a, input logic [63:0] dd,
                         output logic signed [62:0] q, output logic e);
    int n;
    @(negedge clk);
    d_a = a;
    d_d = dd;
    d_start = 1'b1;
    @(negedge clk);
    d_start = 1'b0;
    n = 0;
    forever begin
      @(negedge clk);
      n++;
      if (d_done) break;
      if (n > 40) $fatal(1, "T1/T2 divider timeout");
    end
    q = d_q;
    e = d_err;
  endtask

  task automatic t1_div_directed();
    int          fd;
    logic [63:0] va, vd, vq;
    logic signed [62:0] q;
    logic        e;
    fd = open_data("p2_div.hex");
    err_base = errors;
    while ($fscanf(fd, "%h %h %h", va, vd, vq) == 3) begin
      div_run(va[62:0], vd, q, e);
      expect_eq("T1 div_floor", {1'b0, q}, {1'b0, vq[62:0]});
    end
    $fclose(fd);
    e1 = errors - err_base;
    $display("[T1] div_floor directed done: errors=%0d", e1);
  endtask

  task automatic t2_div_property();
    logic signed [62:0]  a, q;
    logic [63:0]         dd;
    logic                e;
    logic signed [255:0] lo, hi, av, dv;
    int                  i;
    err_base = errors;
    for (i = 0; i < 500; i++) begin
      a  = $signed($urandom());
      a  = (a <<< 31) ^ $signed($urandom());
      dd = {$urandom(), $urandom()} & 64'h07FF_FFFF_FFFF_FFFF;
      if (dd == 0) dd = 64'd1;
      div_run(a, dd, q, e);
      av = {{193{a[62]}}, a};
      dv = {{192{1'b0}}, dd};
      lo = q * dv;
      hi = (q + 63'sd1) * dv;
      checks = checks + 3;
      if (e !== 1'b0) begin
        errors++;
        $display("  MISMATCH T2 unexpected err, a=%0d d=%0d", a, dd);
      end
      if (!(lo <= av)) begin
        errors++;
        if (shown < 30) begin shown++; $display("  MISMATCH T2 lower a=%0d d=%0d q=%0d", a, dd, q); end
      end
      if (!(av < hi)) begin
        errors++;
        if (shown < 30) begin shown++; $display("  MISMATCH T2 upper a=%0d d=%0d q=%0d", a, dd, q); end
      end
    end
    div_run(63'sd5, 64'd0, q, e);
    checks = checks + 2;
    if (e !== 1'b1) begin
      errors++;
      $display("  MISMATCH T2 d=0 must raise err");
    end
    if (q !== 63'sd0) begin
      errors++;
      $display("  MISMATCH T2 d=0 must output q=0");
    end
    e2 = errors - err_base;
    $display("[T2] div_floor property (q*d<=a<(q+1)*d) done: errors=%0d", e2);
  endtask

  //=========================================================================
  // T3 adc2_dec（生产尺寸 12 bit）
  //=========================================================================
  // p2_scalar.hex **逐行**换 adc2_n_bits（实测用到 {1,2,3,4,8,12,20}）。而 P_ADC2_BITS 是
  // **elaboration 期的参数**、不是运行时端口 —— 所以只能按用到的取值各例化一份，
  // 再用一个多路选择器按行内的 n_bits 列分派。这是不得已，不是设计偏好。
  // 未覆盖的 n_bits 走 default 分支**强制置 ovf=1**，让断言必红而不是静默通过。
  logic [19:0]        c_code;
  logic [63:0]        c_nb;
  logic signed [63:0] c_min, c_max, c_fine;
  logic               c_ovf;
  logic signed [63:0] d_fine [0:6];
  logic               d_ovf  [0:6];

  adc2_dec #(.P_ADC2_BITS(1))  u_d1  (.adc2_code(c_code[0:0]),  .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[0]), .ovf(d_ovf[0]));
  adc2_dec #(.P_ADC2_BITS(2))  u_d2  (.adc2_code(c_code[1:0]),  .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[1]), .ovf(d_ovf[1]));
  adc2_dec #(.P_ADC2_BITS(3))  u_d3  (.adc2_code(c_code[2:0]),  .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[2]), .ovf(d_ovf[2]));
  adc2_dec #(.P_ADC2_BITS(4))  u_d4  (.adc2_code(c_code[3:0]),  .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[3]), .ovf(d_ovf[3]));
  adc2_dec #(.P_ADC2_BITS(8))  u_d8  (.adc2_code(c_code[7:0]),  .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[4]), .ovf(d_ovf[4]));
  adc2_dec #(.P_ADC2_BITS(12)) u_d12 (.adc2_code(c_code[11:0]), .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[5]), .ovf(d_ovf[5]));
  adc2_dec #(.P_ADC2_BITS(20)) u_d20 (.adc2_code(c_code),       .adc2_min_q(c_min), .adc2_max_q(c_max), .fine_q(d_fine[6]), .ovf(d_ovf[6]));

  always_comb begin
    case (c_nb)
      64'd1:   begin c_fine = d_fine[0]; c_ovf = d_ovf[0]; end
      64'd2:   begin c_fine = d_fine[1]; c_ovf = d_ovf[1]; end
      64'd3:   begin c_fine = d_fine[2]; c_ovf = d_ovf[2]; end
      64'd4:   begin c_fine = d_fine[3]; c_ovf = d_ovf[3]; end
      64'd8:   begin c_fine = d_fine[4]; c_ovf = d_ovf[4]; end
      64'd12:  begin c_fine = d_fine[5]; c_ovf = d_ovf[5]; end
      64'd20:  begin c_fine = d_fine[6]; c_ovf = d_ovf[6]; end
      default: begin c_fine = {V_BITS{1'b0}}; c_ovf = 1'b1; end
    endcase
  end

  task automatic t3_adc2_dec();
    int          fd;
    logic [63:0] vcode, vnb, vmin, vmax, voff, vfine, vovf;
    fd = open_data("p2_scalar.hex");
    err_base = errors;
    while ($fscanf(fd, "%h %h %h %h %h %h %h",
                   vcode, vnb, vmin, vmax, voff, vfine, vovf) == 7) begin
      c_code = vcode[19:0];
      c_nb   = vnb;
      c_min  = $signed(vmin);
      c_max  = $signed(vmax);
      #1;
      expect_eq("T3 adc2_dec", c_fine, vfine);
      expect_eq("T3 adc2_ovf", {63'b0, c_ovf}, {63'b0, vovf[0]});
    end
    $fclose(fd);
    e3 = errors - err_base;
    $display("[T3] adc2_dec directed (n_bits sweep + ties-to-even) done: errors=%0d", e3);
  endtask

  //=========================================================================
  // T4/T5 oracle：recon_core 以 straight_backend 尺寸重例化
  //=========================================================================
  // 几何取自模型 straight_backend：CalibrationSpec(1, 2, 1, ...) => n_active=1,
  // dac_n_main=2, dac_n_sub=1（shape = 1x3，与权重 [0.5,0.25,0.25] 一致）。
  // ⚠️ 初版写 1/2 是错的。T5 恰好不受影响（RDAC 码=0 时所有单位 plus=0、a=1，
  // 求和与主/子切分无关）—— 恰好不受影响 不等于 写对了。
  localparam int OR_NACT = 1, OR_NMAIN = 2, OR_NSUB = 1, OR_NSL = 1, OR_AB = 20;

  logic                              o_start, o_cfg_ready, o_clr, o_smask;
  logic [OR_NACT-1:0][4:0]           o_sid;
  logic [OR_NACT-1:0][OR_NMAIN-1:0]  o_mon;
  logic [OR_NACT-1:0][OR_NSUB-1:0]   o_son;
  logic [2*DITHER_UNITS_RANGE-1:0]   o_rail;
  logic [OR_AB-1:0]                  o_code;
  logic signed [63:0]                o_inj, o_off, o_min, o_max;
  logic [OR_NSL-1:0][OR_NMAIN+OR_NSUB-1:0][47:0] o_wrom;
  logic [19:0]                       o_dout;
  logic                              o_dvalid, o_clipl, o_cliph, o_ovf, o_gerr, o_a2ovf;

  recon_core #(
      .P_N_ACTIVE(OR_NACT), .P_N_MAIN(OR_NMAIN), .P_N_SUB(OR_NSUB),
      .P_N_SLICES(OR_NSL), .P_ADC2_BITS(OR_AB), .P_STAGES(7)
  ) u_oracle (
      .clk(clk), .rst_n(rst_n), .cfg_ready(o_cfg_ready), .start(o_start),
      .clr_ovf(o_clr), .sampling_mask_en(o_smask), .slice_id(o_sid),
      .main_on(o_mon), .sub_on(o_son), .dither_rail(o_rail), .adc2_code(o_code),
      .inj_q(o_inj), .w_rom(o_wrom), .offset_q(o_off), .adc2_min_q(o_min),
      .adc2_max_q(o_max), .dout(o_dout), .dout_valid(o_dvalid), .clip_low(o_clipl),
      .clip_high(o_cliph), .acc_ovf(o_ovf), .gain_err(o_gerr), .adc2_ovf(o_a2ovf),
      .busy()
  ,
      .sample_id(32'd0), .result_sample_id(), .result_flags()
  );

  task automatic run_start(input logic which);
    @(negedge clk);
    if (which) o_start = 1'b1;
    else       r_start = 1'b1;
    @(negedge clk);
    if (which) o_start = 1'b0;
    else       r_start = 1'b0;
  endtask

  task automatic t4_latency();
    int n;
    o_cfg_ready = 1'b0; o_clr = 1'b0; o_smask = 1'b0;
    o_sid = '0; o_mon = '0; o_son = '0; o_rail = '0; o_inj = 0; o_off = 0;
    o_wrom = '0;
    o_code = '0;      // 见复位段的缺陷记录：不设会让 a1/div_q/clip_* 全变 X
    o_wrom[0][0] = o_w0[47:0];
    o_wrom[0][1] = o_w1[47:0];
    o_wrom[0][2] = o_w2[47:0];
    err_base = errors;
    run_start(1'b1);
    repeat (30) begin
      @(negedge clk);
      expect_eq("T4 cfg_ready=0 gating", {63'b0, o_dvalid}, 64'd0);
    end
    o_cfg_ready = 1'b1;
    run_start(1'b1);
    n = 0;
    forever begin
      @(negedge clk);
      n++;
      if (o_dvalid) break;
      if (n > 40) $fatal(1, "T4 oracle timeout");
    end
    expect_eq("T4 recon latency vs NLAT", {32'b0, n}, {32'b0, NLAT});
    e4 = errors - err_base;
    $display("[T4] recon_core latency measured=%0d (NLAT=%0d), T4 errors=%0d", n, NLAT, e4);
  endtask

  task automatic t5_oracle();
    int          fd, n;
    logic [19:0] c;
    fd = open_data("p2_oracle_cfg.hex");
    $fclose(fd);
    err_base = errors;
    o_clr = 1'b0;
    c = 20'd0;
    while (1) begin
      o_code = c;
      run_start(1'b1);
      n = 0;
      forever begin
        @(negedge clk);
        n++;
        if (o_dvalid) break;
        if (n > 40) $fatal(1, "T5 oracle timeout");
      end
      checks++;
      if ({1'b0, o_dout} !== {44'b0, c}) begin
        errors++;
        if (shown < 30) begin shown++; $display("  MISMATCH T5 oracle c=%0d got=%0d", c, o_dout); end
      end
      if (o_clipl | o_cliph | o_ovf | o_gerr | o_a2ovf) begin
        errors++;
        if (shown < 30) begin
          shown++;
          $display("  MISMATCH T5 flags at c=%0d clipl=%0d cliph=%0d ovf=%0d gerr=%0d a2=%0d",
                   c, o_clipl, o_cliph, o_ovf, o_gerr, o_a2ovf);
        end
      end
      if (c == 20'hFFFFF) break;
      c = c + 20'd1;
    end
    e5 = errors - err_base;
    $display("[T5] 2^20 full-code oracle done: %0d codes checked, errors=%0d", 1 << 20, e5);
  endtask

  //=========================================================================
  // T6/T7/T8 生产尺寸 recon_core
  //=========================================================================
  recon_core u_recon (
      .clk(clk), .rst_n(rst_n), .cfg_ready(r_cfg_ready), .start(r_start),
      .clr_ovf(r_clr), .sampling_mask_en(r_smask), .slice_id(r_sid),
      .main_on(r_mon), .sub_on(r_son), .dither_rail(r_rail), .adc2_code(r_code),
      .inj_q(r_inj), .w_rom(r_wrom), .offset_q(r_off), .adc2_min_q(r_min),
      .adc2_max_q(r_max), .dout(r_dout), .dout_valid(r_dvalid), .clip_low(r_clipl),
      .clip_high(r_cliph), .acc_ovf(r_ovf), .gain_err(r_gerr), .adc2_ovf(r_a2ovf),
      .busy()
  ,
      .sample_id(32'd0), .result_sample_id(), .result_flags()
  );

  task automatic load_weights();
    int          fd, n;
    logic [63:0] vs, vu, vw;
    fd = open_data("p2_link_weights.hex");
    n = 0;
    while ($fscanf(fd, "%h %h %h", vs, vu, vw) == 3) begin
      if (vs < N_SLICES && vu < N_UNIT_TOTAL) r_wrom[vs[4:0]][vu[6:0]] = vw[47:0];
      n++;
    end
    $fclose(fd);
    // 真 bug 记录：N_SLICES 是 5 位、N_UNIT_TOTAL 是 7 位 localparam，
  // 直接相乘会按 max(5,7)=7 位算 -> 18*71=1278 截成 126。必须显式扩到 int 再乘。
  expect_eq("weights count", {32'b0, n}, {32'b0, W_COUNT});
  endtask

  task automatic rd_masks(input int fd);
    logic [63:0] v;
    int          i;
    for (i = 0; i < N_ACTIVE; i++) begin rd(fd, v); r_sid[i] = v[4:0]; end
    for (i = 0; i < N_ACTIVE; i++) begin rd(fd, v); r_mon[i] = v[N_UNIT_MAIN-1:0]; end
    for (i = 0; i < N_ACTIVE; i++) begin rd(fd, v); r_son[i] = v[N_UNIT_SUB-1:0]; end
  endtask

  // 真 bug 记录：`$feof` 只在**读过界之后**才为真，所以"while (!$feof) { 读一行 }"会在
  // 最后一行之后再读一次 -> short read -> $fatal。正确做法是**先读本行的第一个字段**，
  // 读不到就当作 EOF 退出；行内其余字段再用 rd() 读（它们一定存在）。
  task automatic rd_masks_row(input int fd, output logic ok);
    logic [63:0] v;
    int          i;
    ok = 1'b0;
    if ($fscanf(fd, "%h", v) != 1) return;
    r_sid[0] = v[4:0];
    for (i = 1; i < N_ACTIVE; i++) begin rd(fd, v); r_sid[i] = v[4:0]; end
    for (i = 0; i < N_ACTIVE; i++) begin rd(fd, v); r_mon[i] = v[N_UNIT_MAIN-1:0]; end
    for (i = 0; i < N_ACTIVE; i++) begin rd(fd, v); r_son[i] = v[N_UNIT_SUB-1:0]; end
    ok = 1'b1;
  endtask

  task automatic recon_run_r(output int n);
    n = 0;
    run_start(1'b0);
    forever begin
      @(negedge clk);
      n++;
      if (r_dvalid) break;
      if (n > 40) $fatal(1, "T6/T7/T8 recon timeout");
    end
  endtask

  task automatic t6_masks();
    int          fd, n, nrows;
    logic        rowok;
    logic [63:0] vcode, vinj, vexp, vcl, vch;
    fd = open_data("p2_masks.hex");
    err_base = errors;
    while (1) begin
      rd_masks_row(fd, rowok);
      if (!rowok) break;
      if ($fscanf(fd, "%h %h %h %h %h", vcode, vinj, vexp, vcl, vch) != 5) $fatal(1, "T6 short row");
      nrows = nrows + 1;
      r_code = vcode[ADC2_BITS-1:0];
      r_inj  = $signed(vinj);
      recon_run_r(n);
      expect_eq("T6 dout", {44'b0, r_dout}, vexp);
      expect_eq("T6 clip_low", {63'b0, r_clipl}, {63'b0, vcl[0]});
      expect_eq("T6 clip_high", {63'b0, r_cliph}, {63'b0, vch[0]});
    end
    $fclose(fd);
    $display("  T6: p2_masks.hex rows consumed = %0d", nrows);
    e6 = errors - err_base;
    $display("[T6] recon_core mask cases done: errors=%0d", e6);
  endtask

  task automatic t7_ramp();
    int          fd, n, inc_bad, nrows;
    logic        rowok;
    logic [63:0] vcode, vinj, vexp, vcl, vch, vrel;
    logic signed [7:0] vrel_s;      // rel_step 列只有 2 个 hex 字符，必须按 8 位符号扩展
    logic [19:0] prev;
    int          have_prev;
    fd = open_data("p2_ramp.hex");
    err_base  = errors;
    inc_bad   = 0;
    have_prev = 0;
    nrows     = 0;
    while (1) begin
      rd_masks_row(fd, rowok);
      if (!rowok) break;
      if ($fscanf(fd, "%h %h %h %h %h %h", vcode, vinj, vexp, vcl, vch, vrel) != 6) $fatal(1, "T7 short row");
      // 真 bug 记录：rel_step 列只有 2 个 hex 字符。用 64 位 %h 读进来后**必须取低 8 位再
      // 符号扩展**；直接 $signed(vrel) 会把 0xf0 当成 +240 而不是 −16，于是跨进位的跳变
      // 也被当成进位内检查 —— 实测正好误报 511−1 = 510 次。
      vrel_s = $signed(vrel[7:0]);
      nrows  = nrows + 1;
      r_code = vcode[ADC2_BITS-1:0];
      r_inj  = $signed(vinj);
      recon_run_r(n);
      expect_eq("T7 dout", {44'b0, r_dout}, vexp);
      // 同一进位内（rel >= 0）不允许回退或跳码
      if (have_prev && vrel_s > 0) begin
        checks++;
        if (!(r_dout >= prev && r_dout <= prev + 20'd1)) inc_bad++;
      end
      prev      = r_dout;
      have_prev = 1;
    end
    $fclose(fd);
    checks++;
    if (inc_bad != 0) begin
      errors++;
      $display("  MISMATCH T7 monotonicity violated %0d times", inc_bad);
    end
    e7 = errors - err_base;
    $display("[T7] recon_core carry ramp done: rows=%0d errors=%0d", nrows, e7);
  endtask

  // p2_sat.hex 复现的是 **4 bit oracle 后端**（straight_backend(4) 的尺寸 1/1/2），
  // 并用 w_scale 把权重整体左移 8 位。所以 T8 不能用生产尺寸的实例，
  // 必须再例化一份 4-bit 的 recon_core。掩码恒 0（fixture 里 RDAC 码 = 0）。
  logic                       sa_start, sa_clr;
  logic [0:0][4:0]            sa_sid;
  logic [0:0][1:0]            sa_mon;
  logic [0:0][0:0]            sa_son;
  logic [3:0]                 sa_code;
  logic signed [63:0]         sa_inj;
  logic [0:0][2:0][47:0]      sa_wrom;
  logic [19:0]                sa_dout;
  logic                       sa_dvalid, sa_clipl, sa_cliph, sa_ovf, sa_gerr, sa_a2ovf;

  recon_core #(
      .P_N_ACTIVE(1), .P_N_MAIN(2), .P_N_SUB(1), .P_N_SLICES(1),
      .P_ADC2_BITS(4), .P_STAGES(7)
  ) u_sat4 (
      .clk(clk), .rst_n(rst_n), .cfg_ready(r_cfg_ready), .start(sa_start),
      .clr_ovf(sa_clr), .sampling_mask_en(1'b0), .slice_id(sa_sid),
      .main_on(sa_mon), .sub_on(sa_son), .dither_rail(4'b0), .adc2_code(sa_code),
      .inj_q(sa_inj), .w_rom(sa_wrom), .offset_q(or_off),
      .adc2_min_q(or_min), .adc2_max_q(or_max), .dout(sa_dout),
      .dout_valid(sa_dvalid), .clip_low(sa_clipl), .clip_high(sa_cliph),
      .acc_ovf(sa_ovf), .gain_err(sa_gerr), .adc2_ovf(sa_a2ovf), .busy()
  ,
      .sample_id(32'd0), .result_sample_id(), .result_flags()
  );

  task automatic t8_sat();
    int          fd, n, nrows;
    logic [63:0] vinj, vcode, vw, vexp, vcl, vch;
    fd = open_data("p2_sat.hex");
    err_base = errors;
    nrows    = 0;
    sa_sid = '0; sa_mon = '0; sa_son = '0; sa_clr = 1'b0;
    while ($fscanf(fd, "%h %h %h %h %h %h", vinj, vcode, vw, vexp, vcl, vch) == 6) begin
      sa_inj  = $signed(vinj);
      sa_code = vcode[3:0];
      // w_scale = 0 -> 原样；= 1 -> 左移 8（导出器的 w_q_eff = w_q << 8）
      sa_wrom[0][0] = vw[0] ? (o_w0[47:0] << 8) : o_w0[47:0];
      sa_wrom[0][1] = vw[0] ? (o_w1[47:0] << 8) : o_w1[47:0];
      sa_wrom[0][2] = vw[0] ? (o_w2[47:0] << 8) : o_w2[47:0];
      @(negedge clk); sa_start = 1'b1;
      @(negedge clk); sa_start = 1'b0;
      n = 0;
      forever begin
        @(negedge clk); n++;
        if (sa_dvalid) break;
        if (n > 40) $fatal(1, "T8 sat timeout");
      end
      expect_eq("T8 dout", {44'b0, sa_dout}, vexp);
      expect_eq("T8 clip_low", {63'b0, sa_clipl}, {63'b0, vcl[0]});
      expect_eq("T8 clip_high", {63'b0, sa_cliph}, {63'b0, vch[0]});
      // 这些行都是算术合法的：acc_ovf 必须为 0（溢出路径见下，TB 自建）
      expect_eq("T8 acc_ovf must be 0 here", {63'b0, sa_ovf}, 64'd0);
      nrows = nrows + 1;
    end
    $fclose(fd);
    $display("  T8: p2_sat.hex rows consumed = %0d", nrows);
    e8 = errors - err_base;
    $display("[T8] saturation (4-bit oracle) done: errors=%0d", e8);
  endtask

  // ---- T8b：溢出粘滞与"输出保持"（TB 自建激励）----
  // 为什么必须自建：p2_sat.hex 的冻结列里**没有** acc_ovf，表达不了"粘滞 + 保持上一拍"；
  // 而在合法寄存器下 adc2_dec 的 ovf 结构上不可达（|delta| < 2^64 时商不会越界）。
  // 触发方式是让 op3 = total*inj_q 越界：total ≈ 2^35.15，取 inj_q = 2^62 -> ~2^97 >= 2^95。
  task automatic t8b_sticky_overflow();
    int         n;
    logic [19:0] held;
    err_base = errors;
    r_clr = 1'b0;
    // 先做一次合法转换，记下"上一拍合法值"
    r_sid = '0; r_mon = '0; r_son = '0; r_rail = '0; r_code = '0; r_inj = '0;
    recon_run_r(n);
    held = r_dout;
    // 越界注入
    r_inj = 64'sh4000_0000_0000_0000;      // 2^62
    recon_run_r(n);
    expect_eq("T8b acc_ovf set on out-of-range op3", {63'b0, r_ovf}, 64'd1);
    expect_eq("T8b dout holds previous legal value", {44'b0, r_dout}, {44'b0, held});
    // 粘滞：连续 8 拍保持，且再跑一次合法转换也不清
    repeat (8) begin
      @(negedge clk);
      expect_eq("T8b acc_ovf sticky", {63'b0, r_ovf}, 64'd1);
    end
    r_inj = '0;
    recon_run_r(n);
    expect_eq("T8b acc_ovf still sticky after a legal conversion", {63'b0, r_ovf}, 64'd1);
    // 只有显式清除能解除
    @(negedge clk); r_clr = 1'b1;
    @(negedge clk); r_clr = 1'b0;
    @(negedge clk);
    expect_eq("T8b acc_ovf cleared by clr_ovf", {63'b0, r_ovf}, 64'd0);
    e8 = errors - err_base + e8;
    $display("[T8b] sticky overflow / hold-previous done: cumulative T8 errors=%0d", e8);
  endtask

  //=========================================================================
  // T9 L3 链路：M1 -> M2 -> M3 -> M5 -> recon_core
  //=========================================================================
  dem_state_gen #(.A_RED(DEM_LCG_A_MOD), .W(9)) u_k_dem (
      .clk(clk), .rst_n(rst_n), .dem_en(k_dem_en), .load(1'b0),
      .init_a(9'd0), .init_b(9'd0),
      .en_a(~k_bank), .en_b(k_bank), .sid_a(k_sid_a), .sid_b(k_sid_b)
  );

  assign k_sid = k_bank ? k_sid_b : k_sid_a;

  dem_addr_gen u_k_addr (
      .sid(k_sid), .main_logical(k_mlog), .sub_logical(k_slog)
  );

  swap_decode u_k_sw (
      .coarse(k_coarse), .dither_code(k_dither), .dem_en(k_dem_en),
      .bridge_en(k_bridge), .sid_low(k_sid[2:0]),
      .main_count(k_mc), .sub_count(k_sc), .rdac_over()
  );

  unit_therm u_k_therm (
      .main_logical(k_mlog), .sub_logical(k_slog), .main_count(k_mc),
      .sub_count(k_sc), .bank_dither(k_bank_dither),
      .main_on(k_mon), .sub_on(k_son), .dither_rail(k_rail)
  );

  recon_core u_link (
      .clk(clk), .rst_n(rst_n), .cfg_ready(r_cfg_ready), .start(k_start),
      .clr_ovf(r_clr), .sampling_mask_en(r_smask), .slice_id(k_sid_arr),
      .main_on(k_mon), .sub_on(k_son), .dither_rail(k_rail), .adc2_code(k_code),
      .inj_q(k_inj), .w_rom(r_wrom), .offset_q(r_off), .adc2_min_q(r_min),
      .adc2_max_q(r_max), .dout(k_dout), .dout_valid(k_dvalid), .clip_low(k_clipl),
      .clip_high(k_cliph), .acc_ovf(k_ovf), .gain_err(k_gerr), .adc2_ovf(k_a2ovf),
      .busy()
  ,
      .sample_id(32'd0), .result_sample_id(), .result_flags()
  );

  task automatic t9_link();
    int          fd_s, fd_e, n, i;
    logic [63:0] vbank, vcoarse, vdither, vinj, vcode, vdout, vcl, vch, van;
    // `p2_link_stim.hex` 现在是 **8 列**（尾部追加 rdac_over(1) adc2_over(1) ra_sat(1)）。
    // 本 TB 用的是 `recon_core` 实例（没有这三个模拟域端口），所以只需**读进来跳过**——
    // 但**必须读完 8 个字段**，否则每行剩下的 3 列会被当成下一行的开头，整段错位。
    // 消费这三列的是顶层 L3（`sim/tb/p3_top_tb.sv`，它把三者接到 `rdac_ovf`/`adc2_over`/`ra_sat`）。
    logic [63:0] vrdacf, vadcf, vrasf;
    int          nflag_any, nflag_rdac, nflag_adc2, nflag_ra;
    logic        first;
    fd_s = open_data("p2_link_stim.hex");
    fd_e = open_data("p2_link_expected.hex");
    err_base = errors;
    // 复位并让 DEM 状态机从头开始
    k_dem_en = 1'b1; k_bridge = 1'b1; r_smask = 1'b0;
    nflag_any = 0; nflag_rdac = 0; nflag_adc2 = 0; nflag_ra = 0;
    @(negedge clk);
    first = 1'b1;
    while (1) begin
      if ($fscanf(fd_s, "%h %h %h %h %h %h %h %h",
                  vbank, vcoarse, vdither, vinj, vcode,
                  vrdacf, vadcf, vrasf) != 8) break;
      // 真缺陷记录（2026-09-18）：这里原先放了一个 `...(vrdacf|vadcf|vrasf) !== van[0]`
      // 的交叉核对，但 **`van` 是在下面读 `p2_link_expected.hex` 时才赋值的** ——
      // 于是它比的是**上一行**的 analog_ovf，凭空报出 261 个错（T1–T8 全 0，只有 T9 红）。
      // 该恒等式已由**两处更强的地方**保证，此处删去：
      //   ① 向量侧导出期断言 `rdac_over|adc2_over|ra_sat == stream.analog_overflow` 逐样本成立；
      //   ② 顶层 L3（`p3_top_tb.sv`）逐样本比对 `analog_ovf`（4095/4095 bit-exact）。
      // 保留计数（下三行）用于观测归属分布。
      nflag_any  = nflag_any  + int'(vrdacf[0] | vadcf[0] | vrasf[0]);
      nflag_rdac = nflag_rdac + int'(vrdacf[0]);
      nflag_adc2 = nflag_adc2 + int'(vadcf[0]);
      nflag_ra   = nflag_ra   + int'(vrasf[0]);
      if ($fscanf(fd_e, "%h %h %h %h", vdout, vcl, vch, van) != 4) $fatal(1, "T9 expected short");
      k_bank        = vbank[0];
      k_coarse      = vcoarse[8:0];
      // dither 列只有 2 个 hex 字符（取值 {-6,-3,0,3,6}），必须**按 8 位符号扩展**。
      // 用 $signed(vdither[15:0]) 会把 0xfa 当成 +250 而不是 −6 —— 与 T7 的 rel_step
      // 是同一类缺陷（子代理也独立踩过一次：dither 列没做符号扩展）。
      k_dither      = $signed({{8{vdither[7]}}, vdither[7:0]});
      k_bank_dither = 8'sd0;
      k_inj         = $signed(vinj);
      k_code        = vcode[ADC2_BITS-1:0];
      // pingpong 分配（与 slice_alloc 的确定性模式一致；权重与 slice 无关，取哪组不影响结果）
      for (i = 0; i < N_ACTIVE; i++) begin
        k_sid_arr[i] = k_bank ? 5'(8 + i) : 5'(i);
      end
      // 链路是纯组合的（M2/M3/M5），等它稳定后给 recon 一个 start
      #1;
      k_start = 1'b1;
      @(negedge clk);
      k_start = 1'b0;
      n = 0;
      forever begin
        @(negedge clk);
        n++;
        if (k_dvalid) break;
        if (n > 40) $fatal(1, "T9 link recon timeout");
      end
      expect_eq("T9 dout", {44'b0, k_dout}, vdout);
      expect_eq("T9 clip_low", {63'b0, k_clipl}, {63'b0, vcl[0]});
      expect_eq("T9 clip_high", {63'b0, k_cliph}, {63'b0, vch[0]});
      expect_eq("T9 no internal err", {61'b0, k_a2ovf, k_gerr, k_ovf}, 64'd0);
      if (van[0]) link_analog_ovf_count++;
      first = 1'b0;
      @(posedge clk);
    end
    $fclose(fd_s);
    $fclose(fd_e);
    e9 = errors - err_base;
    $display("[T9] L3 link (M1->M2->M3->M5->recon) done: errors=%0d, model analog_ovf rows=%0d",
             e9, link_analog_ovf_count);
    $display("  T9 stim 模拟域标志实测归属：any=%0d rdac_over=%0d adc2_over=%0d ra_sat=%0d",
             nflag_any, nflag_rdac, nflag_adc2, nflag_ra);
  endtask

  //=========================================================================
  // 主流程
  //=========================================================================
  initial begin
    if (!$value$plusargs("vdir=%s", vdir)) vdir = "sim/vectors";
    if (!$value$plusargs("only=%s", only)) only = "";
    $display("p2_tb: vdir=%s NLAT=%0d only=%0d", vdir, NLAT, only);

    rst_n       = 1'b0;
    d_start     = 1'b0;
    o_start     = 1'b0;
    r_start     = 1'b0;
    k_start     = 1'b0;
    o_cfg_ready = 1'b0;
    r_cfg_ready = 1'b1;
    o_clr       = 1'b0;
    r_clr       = 1'b0;
    o_smask     = 1'b0;
    r_smask     = 1'b0;
    sa_start    = 1'b0;
    sa_clr      = 1'b0;
    sa_code     = 4'd0;
    sa_inj      = '0;
    sa_wrom     = '0;
    sa_sid      = '0;
    sa_mon      = '0;
    sa_son      = '0;
    k_dem_en    = 1'b1;
    k_bridge    = 1'b1;
    k_bank      = 1'b0;
    k_coarse    = 9'd0;
    k_dither    = 16'sd0;
    k_bank_dither = 8'sd0;
    k_inj       = '0;
    k_code      = '0;
    k_sid_arr   = '0;
    // ⚠️ 真缺陷记录（2026-09-18，SVA 层抓出）：原先这里**漏了 `o_code`**，而 T4
    // 只检查 `dout_valid`、**从不检查 `dout` 的值**，所以 `o_code = X` 一路经
    // `adc2_dec` -> `op1` -> `num` -> `a1` -> `div_q` -> `clip_lo_c/clip_hi_c`
    // 传到 `clip_low`/`clip_high`（全部 X），**没有任何功能判据能发现**。
    // 断言 A8a（`!$isunknown({clip_low, clip_high})`）把它抓了出来 —— 实测探针：
    //   div_q=x x?=1 | clip_lo_c=x(1) clip_hi_c=x(1) | a1=x x?=1
    // 而 gain_s/total_s/rails_s 都是已知值。
    // 教训：把 DUT 的**每一个**输入端口在复位段就钉死，不要"用到哪设到哪"。
    o_sid = '0; o_mon = '0; o_son = '0; o_rail = '0; o_inj = '0; o_off = '0;
    o_code = '0; o_wrom = '0;
    r_sid = '0; r_mon = '0; r_son = '0; r_rail = '0; r_code = '0;
    r_inj = '0; r_off = '0; r_min = '0; r_max = '0; r_wrom = '0;

    repeat (3) @(posedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    load_weights();
    // ---- oracle 的权重与系数：从向量文件读，**不手写** ----
    begin
      int          fd;
      logic [63:0] vab, vna, vnm, vns, vmin, vmax, voff, vid;
      fd = open_data("p2_oracle_spec.hex");
      rd(fd, o_w0); rd(fd, o_w1); rd(fd, o_w2);
      $fclose(fd);
      fd = open_data("p2_oracle_cfg.hex");
      rd(fd, vmin); rd(fd, vmax); rd(fd, voff); rd(fd, vns); rd(fd, vab); rd(fd, vid);
      $fclose(fd);
      or_min = $signed(vmin);
      or_max = $signed(vmax);
      or_off = $signed(voff);
      o_min  = or_min; o_max = or_max; o_off = or_off;
      checks = checks + 4;
      // 列序（实测）：min_q max_q offset_q n_bits out_count identity
      if (vns[5:0] !== OR_AB)  begin errors++; $display("  MISMATCH oracle cfg n_bits=%0d TB=%0d", vns, OR_AB); end
      if (vab[31:0] !== (1 << OUT_BITS)) begin errors++; $display("  MISMATCH oracle cfg out_count=%0d", vab); end
      if (vid[0]  !== 1'b1)    begin errors++; $display("  MISMATCH oracle cfg identity flag not set"); end
    end
    // 生产配置的三系数：优先读专用文件；若尚未生成则退回 oracle cfg 的值
    // （本配置下二者恰好相同：min_q=0 / max_q=2^33 / offset=0 —— 但这不是巧合依赖，
    //   TB 会打印出来，且 p2_recon_coef.hex 到位后以它为准）
    begin
      int          fd;
      logic [63:0] vo, vm, vx;
      fd = $fopen({vdir, "/p2_recon_coef.hex"}, "r");
      if (fd == 0) begin
        $display("  NOTE T6/T7/T9: p2_recon_coef.hex 未找到，退回 oracle cfg 的系数（本配置下相同）");
        r_off = or_off; r_min = or_min; r_max = or_max;
      end else begin
        // 跳过注释与 #DATA 标记（与 open_data 同一套位置无关判定）
        begin
          string line, tok;
          forever begin
            if ($fgets(line, fd) == 0) $fatal(1, "no #DATA in p2_recon_coef.hex");
            if ($sscanf(line, "%s", tok) == 1 && tok == "#DATA") break;
          end
        end
        rd(fd, vo); rd(fd, vm); rd(fd, vx);
        $fclose(fd);
        r_off = $signed(vo); r_min = $signed(vm); r_max = $signed(vx);
      end
    end

    if (only == "" || only == "t1") t1_div_directed();
    if (only == "" || only == "t2") t2_div_property();
    if (only == "" || only == "t3") t3_adc2_dec();
    if (only == "" || only == "t4") begin
      t4_latency();
      // T4 专用内部探针：定位 A8a/A8b 报的 `cl=x ch=x` 来源。
      $display("  [T4 probe] cfg_ready=%0b dout_valid=%0b cl=%0b ch=%0b",
               o_cfg_ready, o_dvalid, o_clipl, o_cliph);
      $display("  [T4 probe] div_q=%0d x?=%0b | clip_lo_c=%0b(%0b) clip_hi_c=%0b(%0b)",
               u_oracle.div_q, $isunknown(u_oracle.div_q),
               u_oracle.clip_lo_c, $isunknown(u_oracle.clip_lo_c),
               u_oracle.clip_hi_c, $isunknown(u_oracle.clip_hi_c));
      $display("  [T4 probe] gain_s=%0d x?=%0b total_s=%0d x?=%0b rails_s=%0d x?=%0b",
               u_oracle.gain_s, $isunknown(u_oracle.gain_s),
               u_oracle.total_s, $isunknown(u_oracle.total_s),
               u_oracle.rails_s, $isunknown(u_oracle.rails_s));
      $display("  [T4 probe] a1=%0d x?=%0b shifted95=%0b ovf_pend=%0b gerr_pend=%0b",
               u_oracle.a1, $isunknown(u_oracle.a1),
               u_oracle.u_residue_mac.shifted[ACC_BITS-1], u_oracle.ovf_pend, u_oracle.gerr_pend);
    end
    // Portable CI runs the same full-code oracle in p2_oracle_tb, isolated
    // from the unrelated production-size reconstructions in this testbench.
    if (only == "" || only == "t5") begin
      if ($test$plusargs("skip_oracle")) $display("T5 delegated to p2_oracle_tb");
      else t5_oracle();
    end
    if (only == "" || only == "t6") t6_masks();
    if (only == "" || only == "t7") t7_ramp();
    if (only == "" || only == "t8") t8_sat();
    if (only == "" || only == "t8b") t8b_sticky_overflow();
    if (only == "" || only == "t9") t9_link();

    $display("p2_tb summary: checks=%0d errors=%0d", checks, errors);
    // 注意：Verilog 的字符串字面量**不能**像 C 那样跨行拼接（VCS 会报语法错）
    $display("  per-test errors: T1(div)=%0d T2(divprop)=%0d T3(adc2)=%0d T4(lat)=%0d",
             e1, e2, e3, e4);
    $display("                   T5(oracle)=%0d T6(masks)=%0d T7(ramp)=%0d T8(sat+sticky)=%0d T9(link)=%0d",
             e5, e6, e7, e8, e9);
    if (errors == 0) $display("P2 RESULT: PASS");
    else             $display("P2 RESULT: FAIL (%0d errors)", errors);
    $finish;
  end

endmodule
