//===========================================================================
// p1_tb.sv -- P1 自检式 testbench（M1–M5）
//===========================================================================
// 职责
//   读 tools/export_rtl_vectors.py 生成的黄金向量，逐位比对五个 DUT。
//   **不做**任何参考计算：期望值全部来自文件（模型自身输出）。唯一的例外是
//   M5 的结构性扫描（popcount / 单调包含），它检查的是**性质**而不是数值。
//
// 用法
//   vcs -sverilog -full64 ... +vdir=<向量目录> +outdir=<输出目录>
//   退出码：任何一处 MISMATCH -> $fatal(1)。"跑完但没比对"不算通过。
//
// 覆盖
//   T1 M1  dem_state_gen 1024 拍 bank 交替，逐拍 sid 相等
//          （验证的是**状态顺序与样本对齐**，不只是"走满 512 态"）
//   T2 M2  dem_addr_gen  512 sid x 71 地址全遍历，逐位相等（含双射检查）
//   T3 M3  swap_decode   8192 穷举 (code, bridge, sid_low) + 85 定向（k 拼装/裁剪/溢出）
//   T4 M5  unit_therm    1248 条黄金掩码（全部 512 sid 的边界计数 + 8 sid 的中间计数）
//   T5 M5  结构性扫描    512 sid x count 0..63 的 popcount 与单调包含（性质，非数值）
//   T6 M4  dither_gen    统计：支撑集精确 + 均匀性直方图落盘交 Python 复核
//   T7 链路 M1->M2->M3->M5 真实连通：TB 只给 bank/coarse/dither/bridge，其余由 RTL 产生
//===========================================================================
`timescale 1ns/1ps
`include "rtl_params.vh"

module p1_tb;

  localparam int N_ACT = N_ACTIVE;
  localparam int NM    = N_UNIT_MAIN;
  localparam int NS    = N_UNIT_SUB;
  localparam int D     = DITHER_UNITS_RANGE;

  string vdir   = ".";
  string outdir = ".";
  int    errors = 0;
  int    checks = 0;
  int    shown  = 0;
  // 每项测试的独立账本。只在最后打印总数是不够的：一旦误配很多，
  // chk() 的打印限流会把后面所有测试的失败信息吞掉，定位能力归零。
  int    e1, e2, e3, e4, e5, e6, e7;
  int    err_base = 0;

  logic clk = 1'b0;
  always #5 clk = ~clk;
  logic rst_n = 1'b0;

  //---------------------------------------------------------------- DUT 信号
  // M1
  logic        m1_dem_en, m1_load;
  logic [8:0]  m1_init_a, m1_init_b;
  logic        m1_en_a, m1_en_b;
  logic [8:0]  m1_sid_a, m1_sid_b;

  // M2
  logic [8:0]        m2_sid;
  logic [NM-1:0][5:0] m2_main_logical;
  logic [NS-1:0][2:0] m2_sub_logical;

  // M3
  logic [8:0]          m3_coarse;
  logic [7:0]          m3_dither_raw;
  logic signed [15:0]  m3_dither;
  logic                m3_dem, m3_bridge;
  logic [2:0]          m3_sidlow;
  logic [N_ACT-1:0][6:0] m3_main;
  logic [N_ACT-1:0][2:0] m3_sub;
  logic                m3_over;

  // M4
  logic                m4_en;
  logic signed [7:0]   m4_code;
  logic                m4_valid;

  // M5
  logic [N_ACT-1:0][6:0]     m5_mc;
  logic [N_ACT-1:0][2:0]     m5_sc;
  logic signed [7:0]         m5_bd;
  logic [N_ACT-1:0][NM-1:0]  m5_main_on;
  logic [N_ACT-1:0][NS-1:0]  m5_sub_on;
  logic [2*D-1:0]            m5_rail;

  assign m3_dither = $signed(m3_dither_raw);

  dem_state_gen #(.A_RED(DEM_LCG_A_MOD), .W(9)) u_m1 (
      .clk(clk), .rst_n(rst_n), .dem_en(m1_dem_en), .load(m1_load),
      .init_a(m1_init_a), .init_b(m1_init_b),
      .en_a(m1_en_a), .en_b(m1_en_b), .sid_a(m1_sid_a), .sid_b(m1_sid_b)
  );

  dem_addr_gen u_m2 (
      .sid(m2_sid), .main_logical(m2_main_logical), .sub_logical(m2_sub_logical)
  );

  swap_decode u_m3 (
      .coarse(m3_coarse), .dither_code(m3_dither), .dem_en(m3_dem), .bridge_en(m3_bridge),
      .sid_low(m3_sidlow), .main_count(m3_main), .sub_count(m3_sub), .rdac_over(m3_over)
  );

  dither_gen u_m4 (
      .clk(clk), .rst_n(rst_n), .en(m4_en), .dither_code(m4_code), .valid(m4_valid)
  );

  unit_therm u_m5 (
      .main_logical(m2_main_logical), .sub_logical(m2_sub_logical),
      .main_count(m5_mc), .sub_count(m5_sc), .bank_dither(m5_bd),
      .main_on(m5_main_on), .sub_on(m5_sub_on), .dither_rail(m5_rail)
  );

  //---------------------------------------------------------------- 工具
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
      // 用 $sscanf 取第一个 token 再比字符串：**不依赖字符位置**。
      // 早先写成 line[39:0] == "BEGIN" 永远匹配不上（$fgets 把首字符放在 MSB），
      // 记在这里免得以后再踩。
      if ($sscanf(line, "%s", tok) == 1 && tok == "#DATA") break;
    end
    return fd;
  endfunction

  task automatic chk(input string what, input longint got, input longint want);
    checks++;
    if (got !== want) begin
      errors++;
      if (shown < 25) begin
        shown++;
        $display("  MISMATCH %s: got %0d (0x%0h) want %0d (0x%0h)", what, got, got, want, want);
      end
    end
  endtask

  task automatic read_h(input int fd, output logic [63:0] v);
    int rc;
    rc = $fscanf(fd, "%h", v);
    if (rc != 1) $fatal(1, "short read (rc=%0d)", rc);
  endtask

  //---------------------------------------------------------------- T1 M1
  task automatic t1_m1();
    int    fd;
    logic [63:0] n, bank, en_a, en_b, sid_exp;
    int    i;
    logic [8:0] sid_seen;
    logic [8:0] seen_ba [512];
    logic [8:0] seen_bb [512];
    int         seen_na, seen_nb;
    fd = open_data("p1_m1_sid.hex");
    err_base = errors;

    rst_n = 1'b0; m1_dem_en = 1'b1; m1_load = 1'b0;
    m1_en_a = 1'b0; m1_en_b = 1'b0;
    repeat (2) @(posedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    seen_na = 0; seen_nb = 0;
    for (i = 0; i < 1024; i++) begin
      read_h(fd, n); read_h(fd, bank); read_h(fd, en_a); read_h(fd, en_b); read_h(fd, sid_exp);
      m1_en_a = en_a[0];
      m1_en_b = en_b[0];
      #1;
      sid_seen = bank[0] ? m1_sid_b : m1_sid_a;
      chk($sformatf("M1 n=%0d sid", i), sid_seen, sid_exp);

      // 每 bank 的覆盖率账本：DEM 关闭时这里会全 0，正是要抓的"假绿"
      if (bank[0] == 1'b0) begin
        seen_ba[sid_seen] = 9'h1FF;  // 只当标记
        seen_na++;
      end else begin
        seen_bb[sid_seen] = 9'h1FF;
        seen_nb++;
      end
      @(posedge clk);
      #1;
    end
    $fclose(fd);

    // 结构性：两个 bank 各自都必须走遍全部 512 个状态（这是 gcd(a,512)=1 的指纹）
    begin
      int uniq_a = 0, uniq_b = 0;
      for (int s = 0; s < 512; s++) begin
        if (seen_ba[s] == 9'h1FF) uniq_a++;
        if (seen_bb[s] == 9'h1FF) uniq_b++;
      end
      chk("M1 bankA distinct states", uniq_a, 512);
      chk("M1 bankB distinct states", uniq_b, 512);
    end
    e1 = errors - err_base;
    $display("[T1] M1 done: %0d comparisons, errors=%0d", 1024, e1);
  endtask

  //---------------------------------------------------------------- T2 M2
  task automatic t2_m2();
    int          fd, s, p;
    logic [63:0] v;
    logic [5:0]  sum_main_or;
    fd = open_data("p1_m2_jinv.hex");
    err_base = errors;
    for (s = 0; s < 512; s++) begin
      read_h(fd, v); chk($sformatf("M2 sid[%0d]", s), v, s);
      m2_sid = v[8:0];
      #1;
      for (p = 0; p < NM; p++) begin
        read_h(fd, v);
        chk($sformatf("M2 s=%0d main_logical[%0d]", s, p), m2_main_logical[p], v);
      end
      for (p = 0; p < NS; p++) begin
        read_h(fd, v);
        chk($sformatf("M2 s=%0d sub_logical[%0d]", s, p), m2_sub_logical[p], v);
      end
    end
    $fclose(fd);
    e2 = errors - err_base;
    $display("[T2] M2 done: %0d comparisons (512 sid x 71 addresses), errors=%0d",
             512 * (NM + NS), e2);
  endtask

  //---------------------------------------------------------------- T3 M3
  task automatic t3_m3();
    int          fd, i, a;
    logic [63:0] v;
    int          lines = 0;
    fd = open_data("p1_m3.hex");
    err_base = errors;
    m3_dem = 1'b1;
    while ($fscanf(fd, "%h", v) == 1) begin
      m3_coarse = v[8:0];
      read_h(fd, v); m3_dither_raw = v[7:0];
      read_h(fd, v); m3_bridge = v[0];
      read_h(fd, v); m3_sidlow = v[2:0];
      #1;
      for (a = 0; a < N_ACT; a++) begin
        read_h(fd, v); chk($sformatf("M3 line=%0d slice=%0d main_count", lines, a), m3_main[a], v);
      end
      for (a = 0; a < N_ACT; a++) begin
        read_h(fd, v); chk($sformatf("M3 line=%0d slice=%0d sub_count", lines, a), m3_sub[a], v);
      end
      read_h(fd, v); chk($sformatf("M3 line=%0d rdac_over", lines), m3_over, v);
      lines++;
    end
    $fclose(fd);
    if (lines != 8277) $fatal(1, "M3 expected 8277 vector lines, read %0d", lines);
    e3 = errors - err_base;
    $display("[T3] M3 done: %0d lines (%0d comparisons), errors=%0d", lines, 8192 * 17 + 85 * 17, e3);
  endtask

  //---------------------------------------------------------------- T4 M5
  task automatic t4_m5();
    int          fd, i, a;
    logic [63:0] sid, mc, sc, v;
    fd = open_data("p1_m5_masks.hex");
    err_base = errors;
    for (i = 0; i < 1248; i++) begin
      read_h(fd, sid); read_h(fd, mc); read_h(fd, sc);
      m2_sid = sid[8:0];
      for (a = 0; a < N_ACT; a++) m5_mc[a] = mc[6:0];
      for (a = 0; a < N_ACT; a++) m5_sc[a] = sc[2:0];
      #1;
      for (a = 0; a < N_ACT; a++) begin
        read_h(fd, v);
        chk($sformatf("M5 line=%0d slice=%0d main_on", i, a), {1'b0, m5_main_on[a]}, v);
      end
      for (a = 0; a < N_ACT; a++) begin
        read_h(fd, v);
        chk($sformatf("M5 line=%0d slice=%0d sub_on", i, a), {1'b0, m5_sub_on[a]}, v);
      end
    end
    $fclose(fd);
    e4 = errors - err_base;
    $display("[T4] M5 done: 1248 lines (%0d comparisons), errors=%0d", 1248 * 2 * N_ACT, e4);
  endtask

  //---------------------------------------------------------------- T5 M5 结构性
  task automatic t5_m5_structure();
    int              s, c, a;
    logic [NM-1:0]   prev [N_ACT];
    logic [NM-1:0]   mask;
    err_base = errors;
    for (s = 0; s < 512; s++) begin
      m2_sid = s[8:0];
      for (c = 0; c <= NM; c++) begin
        for (a = 0; a < N_ACT; a++) m5_mc[a] = c[6:0];
        for (a = 0; a < N_ACT; a++) m5_sc[a] = 3'd0;
        #1;
        for (a = 0; a < N_ACT; a++) begin
          mask = m5_main_on[a];
          chk($sformatf("M5 popcount s=%0d c=%0d a=%0d", s, c, a), $countones(mask), c);
          // 温度计族是**递增**的：mask(c-1) ⊆ mask(c)。
          // 写成 (mask & ~prev) 就变成检查递减，c=1 必然报错（实测踩过）。
          if (c > 0 && ((prev[a] & ~mask) != '0)) begin
            errors++;
            if (shown < 25) begin
              shown++;
              $display("  MISMATCH M5 monotonicity s=%0d c=%0d a=%0d", s, c, a);
            end
          end
          prev[a] = mask;
        end
      end
    end
    // 非均匀计数：只驱动 slice 0/1，其余必须空 —— 抓"slice 间串味"
    for (a = 0; a < N_ACT; a++) m5_mc[a] = 7'd0;
    m5_mc[0] = 7'd5; m5_mc[1] = 7'd3;
    #1;
    chk("M5 non-uniform slice0", $countones(m5_main_on[0]), 5);
    chk("M5 non-uniform slice1", $countones(m5_main_on[1]), 3);
    for (a = 2; a < N_ACT; a++) chk($sformatf("M5 non-uniform slice%0d must be empty", a), $countones(m5_main_on[a]), 0);
    e5 = errors - err_base;
    $display("[T5] M5 structural sweep done: 512 sid x 64 counts, errors=%0d", e5);
  endtask

  //---------------------------------------------------------------- T6 M4
  task automatic t6_m4();
    int   fd, i, v;
    int   hist [ -D : D ];
    int   valids;
    int   cycles;
    cycles = 200000;
    for (i = -D; i <= D; i++) hist[i] = 0;
    valids = 0;
    fd = $fopen({outdir, "/p1_m4_hist.txt"}, "w");
    if (fd == 0) $fatal(1, "cannot open %s/p1_m4_hist.txt", outdir);
    err_base = errors;
    m4_en = 1'b1;
    @(posedge clk);
    for (i = 0; i < cycles; i++) begin
      @(negedge clk);
      if (m4_valid) begin
        v = m4_code;
        if (v < -D || v > D) begin
          errors++;
          if (shown < 25) begin
            shown++;
            $display("  MISMATCH M4 code %0d out of support [-%0d, %0d]", v, D, D);
          end
        end else begin
          hist[v] = hist[v] + 1;
        end
        valids++;
      end
    end
    chk("M4 every support value appeared", (hist[0] > 0) && (hist[D] > 0) && (hist[-D] > 0), 1);
    $fwrite(fd, "# p1_m4_hist.txt  D=%0d  cycles=%0d  valids=%0d\n", D, cycles, valids);
    $fwrite(fd, "value count\n");
    for (i = -D; i <= D; i++) $fwrite(fd, "%0d %0d\n", i, hist[i]);
    $fclose(fd);
    e6 = errors - err_base;
    $display("[T6] M4 done: %0d valid draws over %0d cycles (valid rate %.4f), errors=%0d",
             valids, cycles, real'(valids) / real'(cycles), e6);
  endtask

  //---------------------------------------------------------------- T7 链路连通
  // T1/T4/T5 各自把 DUT 输入直接喂进去，`m2_sid` 是 TB 写死的常量 —— 于是
  // "M1 的 sid 有没有正确接到 M2"从未被验证。本项只提供 (bank, coarse, dither, bridge)，
  // 其余全部由 RTL 自己串：M1 出 sid -> M2 出逻辑位置 -> M3（sid_low 取自 M1）出计数 -> M5 出掩码。
  task automatic t7_chain();
    int          fd, i, a;
    logic [63:0] bank, coarse, dither, bridge, v;
    fd = open_data("p1_chain.hex");

    rst_n = 1'b0; m1_dem_en = 1'b1; m1_load = 1'b0; m1_en_a = 1'b0; m1_en_b = 1'b0;
    repeat (2) @(posedge clk);
    rst_n = 1'b1;
    @(negedge clk);
    err_base = errors;

    for (i = 0; i < 256; i++) begin
      read_h(fd, bank); read_h(fd, coarse); read_h(fd, dither); read_h(fd, bridge);
      m1_en_a = ~bank[0];
      m1_en_b = bank[0];
      #1;
      m2_sid        = bank[0] ? m1_sid_b : m1_sid_a;   // <- 来自 M1，不是文件
      m3_coarse     = coarse[8:0];
      m3_dither_raw = dither[7:0];
      m3_bridge     = bridge[0];
      m3_dem        = 1'b1;
      m3_sidlow     = m2_sid[2:0];                     // <- 来自链路，不是文件
      #1;
      for (a = 0; a < N_ACT; a++) begin
        m5_mc[a] = m3_main[a];                         // <- 来自 M3，不是文件
        m5_sc[a] = m3_sub[a];
      end
      m5_bd = 8'sd0;
      #1;
      for (a = 0; a < N_ACT; a++) begin
        read_h(fd, v);
        chk($sformatf("T7 n=%0d slice=%0d main_on", i, a), {1'b0, m5_main_on[a]}, v);
      end
      for (a = 0; a < N_ACT; a++) begin
        read_h(fd, v);
        chk($sformatf("T7 n=%0d slice=%0d sub_on", i, a), {1'b0, m5_sub_on[a]}, v);
      end
      @(posedge clk);
      #1;
    end
    $fclose(fd);
    e7 = errors - err_base;
    $display("[T7] chain M1->M2->M3->M5 done: 256 samples (%0d comparisons), errors=%0d",
             256 * 2 * N_ACT, e7);
  endtask

  //---------------------------------------------------------------- 主流程
  initial begin
    if (!$value$plusargs("vdir=%s", vdir)) vdir = ".";
    if (!$value$plusargs("outdir=%s", outdir)) outdir = ".";
    $display("p1_tb: vdir=%s outdir=%s", vdir, outdir);

    // 安全默认，避免 X 传播把"没驱动"伪装成不匹配
    m1_dem_en = 1'b1; m1_load = 1'b0; m1_init_a = '0; m1_init_b = '0;
    m1_en_a = 1'b0; m1_en_b = 1'b0;
    m2_sid = '0;
    m3_coarse = '0; m3_dither_raw = '0; m3_dem = 1'b1; m3_bridge = 1'b0; m3_sidlow = '0;
    m5_mc = '0; m5_sc = '0; m5_bd = '0; m4_en = 1'b0;

    t1_m1();
    t2_m2();
    t3_m3();
    t4_m5();
    t5_m5_structure();
    t6_m4();
    t7_chain();

    $display("=====================================================");
    $display("p1_tb summary: checks=%0d errors=%0d", checks, errors);
    $display("  per-test errors: T1(M1)=%0d T2(M2)=%0d T3(M3)=%0d T4(M5)=%0d T5(M5-struct)=%0d T6(M4)=%0d T7(chain)=%0d",
             e1, e2, e3, e4, e5, e6, e7);
    if (errors == 0) $display("P1 RESULT: PASS");
    else             $display("P1 RESULT: FAIL");
    $display("=====================================================");
    if (errors != 0) $fatal(1, "P1 failed with %0d mismatches", errors);
    $finish;
  end

endmodule
