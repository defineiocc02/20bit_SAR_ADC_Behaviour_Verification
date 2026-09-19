//===========================================================================
// p2_smoke_tb.sv -- P2/P3 最低频冒烟自检（**不是**完整验收）
//===========================================================================
// 职责
//   只做三件事，跑通即退出码 0：
//     T1  顶层配置通路：calib_regs 写入 + cfg_validate -> cfg_ready 拉高；
//         0x0000 权重写入与回读；cfg_ready=1 时的写拒绝（ERR_CFG_WRITE）。
//     T2  slice_alloc：跑 20 拍，断言 INV-4a（conv[n] == acq[n-1]）、
//         INV-4b（内部无重复）、INV-4c（acq/conv 不相交）、INV-4d（元素 < 16）。
//     T3  sadc_enc：几组温度计输入的 popcount。
//   **本 TB 不覆盖**：L2 全码 oracle、L3 链路 bit-exact、div_floor/adc2_dec 的
//   数值穷举、溢出粘滞与剪裁分离、综合时序。那些属后续阶段，不能由本文件冒充。
//
// 用法
//   python tools/run_rtl_sim.py --top p2_smoke_tb --tb sim/tb/p2_smoke_tb.sv \
//       --src rtl/core rtl/top rtl/params --incdir rtl/params --fresh
//   退出码：任何一处 MISMATCH -> $fatal(1)。"跑完但没比对"不算通过。
//===========================================================================
`timescale 1ns/1ps
`include "rtl_params.vh"

module p2_smoke_tb;

  localparam int N_ACT = N_ACTIVE;
  localparam int N_CMP = (1 << B1) - 1;

  int errors = 0;
  int checks = 0;
  int shown  = 0;
  int e1 = 0, e2 = 0, e3 = 0;      // 每项独立账本：限流打印不会吞掉后面的失败
  int err_base = 0;

  logic clk = 1'b0;
  always #5 clk = ~clk;
  logic rst_n = 1'b0;

  task automatic chk(input string name, input logic cond);
    begin
      checks = checks + 1;
      if (!cond) begin
        errors = errors + 1;
        if (shown < 40) begin
          $display("[%0t] MISMATCH: %s", $time, name);
          shown = shown + 1;
        end
      end
    end
  endtask

  task automatic bump(input int base, input string tag);
    begin
      if ((errors - base) != 0) $display("  %s: %0d 处不匹配", tag, errors - base);
    end
  endtask

  //=========================================================================
  // 顶层 DUT
  //=========================================================================
  logic        cfg_wr;
  logic [15:0] cfg_addr;
  logic [63:0] cfg_wdata;
  logic [63:0] cfg_rdata;
  logic        cfg_validate, cfg_clear_valid, cfg_ready;
  logic [B1-1:0]        sadc_code;
  logic                 sadc_rdy;
  logic [ADC2_BITS-1:0] adc2_code;
  logic                 adc2_rdy;
  logic                 ra_sat, rdac_ovf;
  logic signed [V_BITS-1:0] inj_q;
  logic [N_SLICES-1:0]                        slice_sel;
  logic [N_SLICES-1:0][N_UNIT_MAIN-1:0]       main_sw;
  logic [N_SLICES-1:0][N_UNIT_SUB-1:0]        sub_sw;
  logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] dither_sw;
  logic        sw_valid;
  logic [OUT_BITS-1:0] dout;
  logic        dout_valid, clip_low, clip_high, analog_ovf, acc_ovf;
  logic [31:0] status_word;

  sar20_digital_core u_core (
      .clk (clk), .rst_n (rst_n),
      .cfg_wr (cfg_wr), .cfg_addr (cfg_addr), .cfg_wdata (cfg_wdata),
      .cfg_rdata (cfg_rdata), .cfg_validate (cfg_validate),
      .cfg_clear_valid (cfg_clear_valid), .cfg_ready (cfg_ready),
      .sadc_code (sadc_code), .sadc_rdy (sadc_rdy),
      .adc2_code (adc2_code), .adc2_rdy (adc2_rdy),
      .ra_sat (ra_sat), .rdac_ovf (rdac_ovf), .adc2_over(1'b0), .inj_q (inj_q),
      .slice_sel (slice_sel), .main_sw (main_sw), .sub_sw (sub_sw),
      .dither_sw (dither_sw), .sw_valid (sw_valid),
      .dout (dout), .dout_valid (dout_valid),
      .clip_low (clip_low), .clip_high (clip_high),
      .analog_ovf (analog_ovf), .acc_ovf (acc_ovf),
      .status_word (status_word)
  );

  //=========================================================================
  // 独立实例：slice_alloc（T2）
  //=========================================================================
  logic              sa_cfg_ready, sa_sample_en;
  logic [N_ACT-1:0][4:0] sa_acq, sa_conv, sa_grp;
  logic              sa_conv_valid, sa_bank;
  logic [31:0]       sa_idx;

  slice_alloc u_alloc (
      .clk (clk), .rst_n (rst_n), .cfg_ready (sa_cfg_ready),
      .sample_en (sa_sample_en),
      .acq_slices (sa_acq), .conv_slices (sa_conv),
      .conv_valid (sa_conv_valid), .bank (sa_bank),
      .sample_idx (sa_idx), .group (sa_grp)
  );

  //=========================================================================
  // 独立实例：sadc_enc（T3）
  //=========================================================================
  logic [N_CMP-1:0] se_cmp;
  logic [B1-1:0]    se_code;

  sadc_enc #(.P_B1 (B1), .P_N_CMP (N_CMP)) u_senc (
      .cmp_raw (se_cmp), .sadc_code (se_code)
  );

  //=========================================================================
  // 配置写任务：拉一拍 cfg_wr
  //=========================================================================
  task automatic cfg_write(input logic [15:0] a, input logic [63:0] d);
    begin
      @(negedge clk);
      cfg_addr  = a;
      cfg_wdata = d;
      cfg_wr    = 1'b1;
      @(negedge clk);
      cfg_wr    = 1'b0;
    end
  endtask

  //=========================================================================
  // 主流程
  //=========================================================================
  logic [4:0] prev_acq [0:N_ACT-1];
  logic [4:0] cur_acq  [0:N_ACT-1];
  logic [4:0] cur_conv [0:N_ACT-1];
  int         i, j, k;
  int         base;
  logic       dup;

  // dout_valid 是**单拍脉冲**，直接在某拍采样会漏；用粘滞观察位把它钉住。
  logic seen_dv = 1'b0;
  always @(posedge clk) if (dout_valid) seen_dv <= 1'b1;

  initial begin
    cfg_wr         = 1'b0;
    cfg_addr       = 16'd0;
    cfg_wdata      = 64'd0;
    cfg_validate   = 1'b0;
    cfg_clear_valid= 1'b0;
    sadc_code      = 9'd0;
    sadc_rdy       = 1'b0;
    adc2_code      = 12'd0;
    adc2_rdy       = 1'b0;
    ra_sat         = 1'b0;
    rdac_ovf       = 1'b0;
    inj_q          = 64'sd0;
    sa_cfg_ready   = 1'b0;
    sa_sample_en   = 1'b0;
    se_cmp         = {N_CMP{1'b0}};

    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    //----------------------------------------------------------------------
    $display("=== T1 顶层配置通路（calib_regs / weight_store / cfg_rdata）===");
    //----------------------------------------------------------------------
    chk("T1 复位后 cfg_ready=0",   cfg_ready   == 1'b0);
    chk("T1 复位后 dout_valid=0",  dout_valid  == 1'b0);
    chk("T1 复位后 slice_sel=0",   slice_sel   == {N_SLICES{1'b0}});

    // 与 sim/vectors/registers_paper_literal.json 同值
    cfg_write(16'h1008, 64'hFFFF_FFFF_FCCB_CCCD);  // adc2_min_q = -53687091
    cfg_write(16'h1010, 64'h0000_0000_2333_3333);  // adc2_max_q = 590558003
    cfg_write(16'h1000, 64'd0);                    // offset_q   = 0
    cfg_write(16'h1018, 64'h3);                    // dem_en=1, bridge_en=1, smask=0
    repeat (4) @(posedge clk);                     // 等 3 拍控制位串行器走完

    // Complete configuration image: one legal word per physical address.
    for (int s = 0; s < N_SLICES; s++) begin
      cfg_write(16'(16'h2000 + s*256), 64'd0);
      for (int u = 0; u < N_UNIT_TOTAL; u++)
        cfg_write(16'(u*8), 64'd67108864);
    end
    cfg_write(16'h2000, 64'd0);
    repeat (2) @(posedge clk);

    chk("T1 未 validate 时 cfg_ready=0", cfg_ready == 1'b0);

    @(negedge clk); cfg_validate = 1'b1;
    @(negedge clk); cfg_validate = 1'b0;
    repeat (2) @(posedge clk);
    chk("T1 validate 后 cfg_ready=1", cfg_ready == 1'b1);

    // ---- 回读 ----
    cfg_addr = 16'h1008; #1;
    chk("T1 回读 adc2_min_q", cfg_rdata == 64'hFFFF_FFFF_FCCB_CCCD);
    cfg_addr = 16'h1010; #1;
    chk("T1 回读 adc2_max_q", cfg_rdata == 64'h0000_0000_2333_3333);
    cfg_addr = 16'h1018; #1;
    chk("T1 回读控制位 = 3'b011", cfg_rdata[2:0] == 3'b011);
    cfg_addr = 16'h0000; #1;
    chk("T1 回读权重 w[0][0] = 2^26", cfg_rdata == 64'd67108864);
    cfg_addr = 16'h2000; #1;
    chk("T1 回读权重窗口 slice = 0", cfg_rdata[4:0] == 5'd0);

    // ---- cfg_ready=1 时的写拒绝 ----
    cfg_write(16'h0008, 64'd67108864);             // u = 1，应被拒
    chk("T1 生效期写权重被拒 -> ERR_CFG_WRITE(5)", status_word[31:6] == 26'd5);
    repeat (2) @(posedge clk);

    sadc_rdy = 1'b1; adc2_rdy = 1'b1; // fixed-phase capture requires valid input
    // ---- 生效后开始转换：dout_valid 必须在固定延迟内出现 ----
    inj_q     = 64'sd0;
    adc2_code = 12'd0;
    repeat (64) @(posedge clk);
    chk("T1 配置生效后出现过 dout_valid", seen_dv == 1'b1);

    e1 = errors;
    bump(0, "T1");
    $display("T1 小计：不匹配 %0d", e1);

    //----------------------------------------------------------------------
    $display("=== T2 slice_alloc（INV-4a..4d）===");
    //----------------------------------------------------------------------
    // 重新复位独立实例，用独立端口驱动
    rst_n = 1'b0;
    sa_cfg_ready = 1'b0; sa_sample_en = 1'b0;
    repeat (3) @(posedge clk);
    rst_n = 1'b1;
    sa_cfg_ready = 1'b1;
    @(negedge clk);

    for (i = 0; i < N_ACT; i = i + 1) prev_acq[i] = sa_acq[i];
    chk("T2 n=0 时 conv_valid=0", sa_conv_valid == 1'b0);

    err_base = errors;
    for (k = 1; k <= 20; k = k + 1) begin      @(negedge clk);
      sa_sample_en = 1'b1;
      @(negedge clk);
      sa_sample_en = 1'b0;

      for (i = 0; i < N_ACT; i = i + 1) begin
        cur_acq[i]  = sa_acq[i];
        cur_conv[i] = sa_conv[i];
      end

      // INV-4a：conv[n] 逐元素等于 acq[n-1]
      for (i = 0; i < N_ACT; i = i + 1) begin
        chk($sformatf("T2 INV-4a n=%0d a=%0d", k, i), cur_conv[i] == prev_acq[i]);
      end

      // INV-4b：各自内部无重复
      dup = 1'b0;
      for (i = 0; i < N_ACT; i = i + 1)
        for (j = i + 1; j < N_ACT; j = j + 1)
          if (cur_acq[i] == cur_acq[j]) dup = 1'b1;
      chk($sformatf("T2 INV-4b acq 无重复 n=%0d", k), !dup);
      dup = 1'b0;
      for (i = 0; i < N_ACT; i = i + 1)
        for (j = i + 1; j < N_ACT; j = j + 1)
          if (cur_conv[i] == cur_conv[j]) dup = 1'b1;
      chk($sformatf("T2 INV-4b conv 无重复 n=%0d", k), !dup);

      // INV-4c：同一 n 上 acq / conv 不相交
      dup = 1'b0;
      for (i = 0; i < N_ACT; i = i + 1)
        for (j = 0; j < N_ACT; j = j + 1)
          if (cur_acq[i] == cur_conv[j]) dup = 1'b1;
      chk($sformatf("T2 INV-4c acq/conv 不相交 n=%0d", k), !dup);

      // INV-4d：元素 < 16
      for (i = 0; i < N_ACT; i = i + 1) begin
        chk($sformatf("T2 INV-4d acq<%0d n=%0d a=%0d", 16, k, i), cur_acq[i]  < 5'd16);
        chk($sformatf("T2 INV-4d conv<%0d n=%0d a=%0d", 16, k, i), cur_conv[i] < 5'd16);
      end

      // 其它结构性判据
      chk($sformatf("T2 bank 等于 n mod 2 n=%0d", k), sa_bank == (k[0] ? 1'b1 : 1'b0));
      chk($sformatf("T2 conv_valid=1 n=%0d", k), sa_conv_valid == 1'b1);
      chk($sformatf("T2 sample_idx=n n=%0d", k), sa_idx == k);
      chk($sformatf("T2 group=conv n=%0d", k), sa_grp == sa_conv);

      for (i = 0; i < N_ACT; i = i + 1) prev_acq[i] = cur_acq[i];
    end
    e2 = errors - err_base;
    bump(err_base, "T2");
    $display("T2 小计：不匹配 %0d", e2);

    //----------------------------------------------------------------------
    $display("=== T3 sadc_enc popcount ===");
    //----------------------------------------------------------------------
    err_base = errors;
    // 3.1 低 v 位全 1 的温度计码 -> popcount = v
    for (k = 0; k <= N_CMP; k = k + 1) begin
      se_cmp = {N_CMP{1'b0}};
      for (i = 0; i < N_CMP; i = i + 1) if (i < k) se_cmp[i] = 1'b1;
      #1;
      chk($sformatf("T3 低 %0d 位温度计", k), se_code == k[B1-1:0]);
    end
    // 3.2 高 v 位全 1 的温度计码 -> popcount = v（校验位序无关性）
    for (k = 0; k <= 8; k = k + 1) begin
      se_cmp = {N_CMP{1'b0}};
      for (i = 0; i < N_CMP; i = i + 1) if (i >= N_CMP - k) se_cmp[i] = 1'b1;
      #1;
      chk($sformatf("T3 高 %0d 位温度计", k), se_code == k[B1-1:0]);
    end
    e3 = errors - err_base;
    bump(err_base, "T3");
    $display("T3 小计：不匹配 %0d", e3);

    //----------------------------------------------------------------------
    $display("================================================");
    $display("  p2_smoke: checks=%0d errors=%0d  (T1=%0d T2=%0d T3=%0d)",
             checks, errors, e1, e2, e3);
    $display("================================================");
    if (errors != 0) $fatal(1, "p2_smoke FAILED: %0d mismatch", errors);
    $display("p2_smoke PASS");
    $finish;
  end

  // 兜底超时：TB 卡死时不至于把仿真挂成超时（run_rtl_sim 的超时是第二道防线）
  initial begin
    #200000;
    $fatal(1, "p2_smoke TIMEOUT: 仿真未在预算内结束");
  end
endmodule
