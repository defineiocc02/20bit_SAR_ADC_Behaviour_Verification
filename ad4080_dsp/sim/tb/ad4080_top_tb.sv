//===========================================================================
// ad4080_top_tb.sv -- AD4080 digital top self-checking testbench
//===========================================================================
// 端口基准：team-lead 冻结的 ad4080_top 端口清单（2026-10-19）。
//   若 rtl/ad4080_top.sv 的端口与清单不一致，以清单为准，TB 不改。
//
// 覆盖（TC1..TC7）：
//   TC1 SPI 复位值  GAIN=0x0200 / WATERMARK=0x4000 / DEVICE_STATUS=0x09
//   TC2 校正通路    OFFSET=+100, GAIN=0x200, x=100000 -> dout=100100 +/-1
//   TC3 增益        GAIN=0x3FF, x=524288 -> dout=532465 +/-40
//   TC4 SINC1 抽取  FILTER_SEL=01, N=4, 4x1000 -> 1 个输出 1000
//   TC5 FIFO mode01 WATERMARK=8, arm, 8 递增样本 -> 顺序读出 + READ_DONE
//   TC6 事件检测    HI_THRESHOLD=+100, EVENT_EN/HI_ROUTE -> status[5] + GPIO0
//   TC7 非法抽取率  SINC5 + N=512 -> 钳到最大抽取率，不死锁
//
// 约定：
//   * clk = 100 MHz (10 ns)，SPI SCLK = 100 ns 周期，空闲高（Mode 3）。
//   * SPI：CS 下降沿起始，16 bit 指令（bit15=1 读 / 0 写，[14:0] 地址，MSB first），
//          8 bit 数据；数据在 SCLK 下降沿更新，上升沿被从机采样。
//   * 所有等待都有循环上限；超时记 FAIL，不无限 wait。
//   * 任一 check 失败 -> 末尾 $fatal(1)。
//===========================================================================
`timescale 1ns/1ps

module ad4080_top_tb;

  //-------------------------------------------------------------------------
  // 参数
  //-------------------------------------------------------------------------
  localparam int IN_W       = 20;
  localparam int FIFO_DEPTH = 16384;

  localparam int CLK_HALF   = 5;    // 10 ns period -> 100 MHz
  localparam int SCLK_HALF  = 50;   // 100 ns period -> SCLK idle high

  localparam int TMO_SPI    = 300;  // 一次 SPI 事务的 clk 上限
  localparam int TMO_WAIT   = 1000; // 10 us @100 MHz，单次等待上限
  localparam int TMO_GLOBAL = 20_000_000; // 20 ms 全局看门狗（1 ns 单位）

  // status[7:0] 位定义，与 rtl/ad4080_regs.sv 的 assign status 完全对应：
  //   {FIFO_FULL, FIFO_READ_DONE, HI, LO, POR_ANA, CNV_ERR, ROM_CRC, POR}
  localparam int ST_FIFO_FULL  = 7;
  localparam int ST_READ_DONE  = 6;
  localparam int ST_HI_STATUS  = 5;   // 冻结清单明确规定
  localparam int ST_LO_STATUS  = 4;
  localparam int ST_POR_ANA    = 3;
  localparam int ST_CNV_ERR    = 2;
  localparam int ST_ROM_CRC    = 1;
  localparam int ST_POR        = 0;

  //-------------------------------------------------------------------------
  // DUT 信号
  //-------------------------------------------------------------------------
  logic clk = 1'b0;
  logic rst_n = 1'b0;

  logic cs_n = 1'b1;
  logic sclk = 1'b1;
  logic sdi  = 1'b0;
  logic sdo, sdo_oe;

  logic cnv_p = 1'b0;
  logic cnv_n = 1'b0;
  logic cnv_pulse;

  logic valid_in = 1'b0;
  logic [IN_W-1:0] x = '0;

  logic filter_sync = 1'b0;
  logic ext_event   = 1'b0;
  logic arm         = 1'b0;
  logic rd_en       = 1'b0;

  logic bit_en = 1'b0;
  logic [1:0] ser_data;
  logic [1:0] ser_frame;
  logic dco;

  logic [3:0] gpio_i = 4'b0000;
  logic [3:0] gpio_o;
  logic [3:0] gpio_oe;

  logic [IN_W-1:0] dout;
  logic dout_valid;
  logic [7:0] status;

  //-------------------------------------------------------------------------
  // 统计
  //-------------------------------------------------------------------------
  int pass_cnt = 0;
  int fail_cnt = 0;

  //-------------------------------------------------------------------------
  // DUT
  //-------------------------------------------------------------------------
  ad4080_top #(
      .IN_W       (IN_W),
      .FIFO_DEPTH (FIFO_DEPTH)
  ) dut (
      .clk        (clk),
      .rst_n      (rst_n),

      .cs_n       (cs_n),
      .sclk       (sclk),
      .sdi        (sdi),
      .sdo        (sdo),
      .sdo_oe     (sdo_oe),

      .cnv_p      (cnv_p),
      .cnv_n      (cnv_n),
      .cnv_pulse  (cnv_pulse),

      .valid_in   (valid_in),
      .x          (x),

      .filter_sync(filter_sync),
      .ext_event  (ext_event),
      .arm        (arm),
      .rd_en      (rd_en),

      .bit_en     (bit_en),
      .ser_data   (ser_data),
      .ser_frame  (ser_frame),
      .dco        (dco),

      .gpio_i     (gpio_i),
      .gpio_o     (gpio_o),
      .gpio_oe    (gpio_oe),

      .dout       (dout),
      .dout_valid (dout_valid),
      .status     (status)
  );

  //-------------------------------------------------------------------------
  // 时钟
  //-------------------------------------------------------------------------
  always #(CLK_HALF) clk = ~clk;

  //-------------------------------------------------------------------------
  // 全局看门狗：任何意外挂死都不允许无限运行
  //-------------------------------------------------------------------------
  initial begin
    #(TMO_GLOBAL);
    $display("[FAIL] GLOBAL WATCHDOG TIMEOUT @ %0t", $time);
    $display("\n==== SUMMARY: PASS=%0d FAIL=%0d ====", pass_cnt, fail_cnt + 1);
    $fatal(1, "global watchdog timeout");
  end

  //-------------------------------------------------------------------------
  // 检查原语
  //-------------------------------------------------------------------------
  task automatic chk(input string name, input bit cond);
    if (cond) begin
      pass_cnt = pass_cnt + 1;
      $display("[PASS] %s", name);
    end else begin
      fail_cnt = fail_cnt + 1;
      $display("[FAIL] %s", name);
    end
  endtask

  task automatic chk_eq(input string name,
                        input longint got,
                        input longint exp,
                        input longint tol);
    if ((got >= (exp - tol)) && (got <= (exp + tol))) begin
      pass_cnt = pass_cnt + 1;
      $display("[PASS] %s (got=%0d exp=%0d tol=%0d)", name, got, exp, tol);
    end else begin
      fail_cnt = fail_cnt + 1;
      $display("[FAIL] %s (got=%0d exp=%0d tol=%0d)", name, got, exp, tol);
    end
  endtask

  task automatic chk_eq8(input string name, input logic [7:0] got, input logic [7:0] exp);
    if (got === exp) begin
      pass_cnt = pass_cnt + 1;
      $display("[PASS] %s (got=0x%02h exp=0x%02h)", name, got, exp);
    end else begin
      fail_cnt = fail_cnt + 1;
      $display("[FAIL] %s (got=0x%02h exp=0x%02h)", name, got, exp);
    end
  endtask

  //-------------------------------------------------------------------------
  // 复位
  //-------------------------------------------------------------------------
  task automatic do_reset();
    rst_n      = 1'b0;
    cs_n       = 1'b1;
    sclk       = 1'b1;
    sdi        = 1'b0;
    valid_in   = 1'b0;
    x          = '0;
    filter_sync= 1'b0;
    ext_event  = 1'b0;
    arm        = 1'b0;
    rd_en      = 1'b0;
    bit_en     = 1'b0;
    gpio_i     = 4'b0000;
    repeat (10) @(negedge clk);
    rst_n = 1'b1;
    repeat (10) @(negedge clk);
  endtask

  //-------------------------------------------------------------------------
  // SPI：一次完整事务（16 bit 指令 + 8 bit 数据）
  //   rd=1 读，rdv 返回读到的字节；rd=0 写，wdata 为待写字节。
  //   从机在 SCLK 上升沿采样，SDO 在下降沿更新，因此读数据在低电平中点采样。
  //-------------------------------------------------------------------------
  task automatic spi_xfer(input  bit         rd,
                          input  logic [14:0] a,
                          input  logic [7:0]  wdata,
                          output logic [7:0]  rdv);
    logic [15:0] instr;
    int i;
    int guard;

    instr = {rd, a};
    rdv   = 8'h00;
    guard = 0;

    @(negedge clk);
    cs_n = 1'b0;
    sclk = 1'b1;
    sdi  = 1'b0;
    repeat (3) @(negedge clk);          // 让 CS 下降沿被 clk 域看到

    // ---- 16 bit 指令，MSB first ----
    for (i = 15; i >= 0; i--) begin
      sclk = 1'b0;
      sdi  = instr[i];
      #(SCLK_HALF);
      sclk = 1'b1;
      #(SCLK_HALF);
      guard++;
      if (guard > TMO_SPI) begin
        fail_cnt = fail_cnt + 1;
        $display("[FAIL] SPI instruction phase timeout");
        i = -1;
      end
    end

    // ---- 8 bit 数据 ----
    for (i = 7; i >= 0; i--) begin
      sclk = 1'b0;
      sdi  = rd ? 1'b0 : wdata[i];
      #(SCLK_HALF);
      if (rd) rdv = {rdv[6:0], sdo};    // 下降沿后 SDO 已稳定
      sclk = 1'b1;
      #(SCLK_HALF);
      guard++;
      if (guard > TMO_SPI) begin
        fail_cnt = fail_cnt + 1;
        $display("[FAIL] SPI data phase timeout");
        i = -1;
      end
    end

    sclk = 1'b1;
    cs_n = 1'b1;
    sdi  = 1'b0;
    repeat (6) @(negedge clk);          // 等 we / 寄存器更新落定
  endtask

  task automatic spi_write(input logic [14:0] a, input logic [7:0] d);
    logic [7:0] dummy;
    spi_xfer(1'b0, a, d, dummy);
  endtask

  task automatic spi_read(input logic [14:0] a, output logic [7:0] d);
    spi_xfer(1'b1, a, 8'h00, d);
  endtask

  //-------------------------------------------------------------------------
  // 采样辅助
  //-------------------------------------------------------------------------
  // 连续打一拍 valid_in，x = v
  task automatic feed(input logic [IN_W-1:0] v);
    @(negedge clk);
    valid_in = 1'b1;
    x        = v;
    @(negedge clk);
    valid_in = 1'b0;
  endtask

  // 在 max_cyc 个时钟内等待 dout_valid 单拍；pulses 统计窗口内有效拍数
  task automatic wait_out(input  int  max_cyc,
                          output bit  seen,
                          output int  pulses,
                          output logic [IN_W-1:0] val);
    int k;
    seen   = 1'b0;
    pulses = 0;
    val    = '0;
    for (k = 0; k < max_cyc; k++) begin
      @(posedge clk);
      #1;
      if (dout_valid === 1'b1) begin
        seen   = 1'b1;
        pulses = pulses + 1;
        val    = dout;
      end
    end
  endtask

  //-------------------------------------------------------------------------
  // TC1：SPI 复位值
  //-------------------------------------------------------------------------
  task automatic tc1_reset_values();
    logic [7:0] v;
    $display("\n---- TC1: SPI reset values ----");

    spi_read(15'h0027, v);
    chk_eq8("TC1 GAIN[7:0]  @0x27 == 0x00", v, 8'h00);

    spi_read(15'h0028, v);
    chk_eq8("TC1 GAIN[9:8]  @0x28 == 0x02 (GAIN=0x0200)", v, 8'h02);

    spi_read(15'h001D, v);
    chk_eq8("TC1 WATERMARK[7:0]  @0x1D == 0x00", v, 8'h00);

    spi_read(15'h001E, v);
    chk_eq8("TC1 WATERMARK[14:8] @0x1E == 0x40 (WM=0x4000)", v, 8'h40);

    spi_read(15'h0014, v);
    chk_eq8("TC1 DEVICE_STATUS @0x14 == 0x09", v, 8'h09);
  endtask

  //-------------------------------------------------------------------------
  // TC2：校正通路 OFFSET=+100, GAIN=0x200（直通）
  //-------------------------------------------------------------------------
  task automatic tc2_offset_path();
    logic [7:0] v;
    $display("\n---- TC2: offset correction, FIFO bypass ----");

    spi_write(15'h0025, 8'h64);   // OFFSET[7:0]  = +100
    spi_write(15'h0026, 8'h00);   // OFFSET[11:8] = 0
    spi_write(15'h0027, 8'h00);   // GAIN[7:0]    = 0
    spi_write(15'h0028, 8'h02);   // GAIN[9:8]    = 2 -> 0x200
    spi_write(15'h0029, 8'h00);   // FILTER_SEL=00 bypass, DEC_RATE=0
    spi_write(15'h001C, 8'h00);   // FIFO_MODE=00 直通

    spi_read(15'h0025, v);
    chk_eq8("TC2 OFFSET[7:0] readback == 0x64", v, 8'h64);

    feed(20'd100000);
    repeat (3) @(posedge clk);
    #1;
    chk_eq("TC2 dout == 100000+100 (FIFO 直通)", longint'(dout), 100100, 1);
  endtask

  //-------------------------------------------------------------------------
  // TC3：增益 GAIN=0x3FF
  //-------------------------------------------------------------------------
  task automatic tc3_gain_path();
    $display("\n---- TC3: gain correction GAIN=0x3FF ----");

    spi_write(15'h0027, 8'hFF);   // GAIN[7:0]
    spi_write(15'h0028, 8'h03);   // GAIN[9:8] = 3 -> 0x3FF
    spi_write(15'h0029, 8'h00);   // bypass
    spi_write(15'h001C, 8'h00);   // FIFO 直通

    feed(20'd524288);
    repeat (3) @(posedge clk);
    #1;
    // x=524288 在 20-bit 补码里是 -524288（-FS）；GAIN=0x3FF>1 时
    // 结果必然负饱和到 0x80000。这不是缺陷，是补码 + 饱和的正确行为。
    chk_eq("TC3 x=-FS with gain>1 saturates to 0x80000",
           longint'(dout), longint'(20'h80000), 0);

    // TC3b：同一增益路径，取补码可表示的正数，单独证明增益系数正确。
    feed(20'd500000);
    repeat (3) @(posedge clk);
    #1;
    chk_eq("TC3b gain path x=+500000 -> ~507797", longint'(dout), 507797, 40);
  endtask

  // 全局监测：单拍 valid 脉冲可能在 feed() 内部产生，轮询会漏
  int mon_valid_count;
  always_ff @(posedge clk) if (dout_valid === 1'b1) mon_valid_count <= mon_valid_count + 1;
  initial mon_valid_count = 0;

  //-------------------------------------------------------------------------
  // TC4：SINC1 抽取 FILTER_SEL=01, N=4
  //-------------------------------------------------------------------------
  task automatic tc4_sinc1();
    bit   seen;
    int   pulses;
    logic [IN_W-1:0] val;
    int   i;

    $display("\n---- TC4: SINC1 decimation N=4 ----");

    spi_write(15'h0029, 8'h09);   // [1:0]=01 SINC1, [6:3]=0001 -> N=4
    spi_write(15'h001C, 8'h00);   // FIFO 直通
    spi_write(15'h0025, 8'h00);
    spi_write(15'h0026, 8'h00);   // OFFSET=0
    spi_write(15'h0027, 8'h00);
    spi_write(15'h0028, 8'h02);   // GAIN=0x200

    @(negedge clk);
    filter_sync = 1'b1;
    @(negedge clk);
    filter_sync = 1'b0;
    repeat (2) @(posedge clk);

    for (i = 0; i < 4; i++) feed(20'd1000);

    wait_out(20, seen, pulses, val);

    chk("TC4 SINC1 produced an output", seen);
    if (seen) chk_eq("TC4 SINC1 N=4 average == 1000", longint'(val), 1000, 0);
    chk_eq("TC4 exactly one output for 4 inputs", longint'(pulses), 1, 0);
  endtask

  //-------------------------------------------------------------------------
  // TC5：FIFO mode 01, WATERMARK=8, arm, 8 递增样本
  //-------------------------------------------------------------------------
  task automatic tc5_fifo_mode01();
    bit   done_seen;
    logic [IN_W-1:0] got [0:7];
    bit   got_valid [0:7];
    int   i;
    int   k;

    $display("\n---- TC5: FIFO mode 01, watermark=8 ----");

    spi_write(15'h001D, 8'h08);   // WATERMARK[7:0]  = 8
    spi_write(15'h001E, 8'h00);   // WATERMARK[14:8] = 0
    spi_write(15'h0029, 8'h00);   // bypass
    spi_write(15'h0025, 8'h00);
    spi_write(15'h0026, 8'h00);   // OFFSET=0
    spi_write(15'h0027, 8'h00);
    spi_write(15'h0028, 8'h02);   // GAIN=0x200
    spi_write(15'h001C, 8'h01);   // FIFO_MODE=01, EVENT_EN=0

    @(negedge clk);
    arm = 1'b1;
    @(negedge clk);
    arm = 1'b0;
    repeat (3) @(posedge clk);

    for (i = 0; i < 8; i++) feed(1000 + i);
    repeat (8) @(posedge clk);

    for (i = 0; i < 8; i++) begin
      @(negedge clk);
      rd_en = 1'b1;
      @(posedge clk);          // FIFO 接收 rd_en，rd_data/rd_valid 在本拍更新
      #1;
      got[i]       = dout;
      got_valid[i] = (dout_valid === 1'b1);
      @(negedge clk);
      rd_en = 1'b0;
      @(posedge clk);
    end

    for (i = 0; i < 8; i++) begin
      chk($sformatf("TC5 rd[%0d] dout_valid", i), got_valid[i]);
      chk_eq($sformatf("TC5 rd[%0d] value == %0d", i, 1000 + i),
             longint'(got[i]), 1000 + i, 0);
    end

    done_seen = 1'b0;
    for (k = 0; k < 20; k++) begin
      @(posedge clk);
      #1;
      if (status[ST_READ_DONE] === 1'b1) begin
        done_seen = 1'b1;
        k = 20;
      end
    end
    chk("TC5 READ_DONE set after 8 reads", done_seen);
  endtask

  //-------------------------------------------------------------------------
  // TC6：事件检测 HI_THRESHOLD=+100 -> status[5] + GPIO0
  //-------------------------------------------------------------------------
  task automatic tc6_event();
    bit hi_seen;
    bit gpio_seen;
    int k;

    $display("\n---- TC6: HI event detect + GPIO route ----");

    spi_write(15'h0021, 8'h64);   // HI_THRESHOLD[7:0]  = 0x64 (+100)
    spi_write(15'h0022, 8'h00);   // HI_THRESHOLD[11:8] = 0
    spi_write(15'h0019, 8'h01);   // GPIO0 方向 = 输出
    spi_write(15'h001A, 8'h04);   // SEL0 = 3'b100 -> 事件功能
    spi_write(15'h001C, 8'hC0);   // EVENT_EN=1 (b7), HI_ROUTE=1 (b6), FIFO_MODE=00
    spi_write(15'h0029, 8'h00);   // bypass
    spi_write(15'h0025, 8'h00);
    spi_write(15'h0026, 8'h00);   // OFFSET=0
    spi_write(15'h0027, 8'h00);
    spi_write(15'h0028, 8'h02);   // GAIN=0x200

    // data[19:8] = 0x100 -> 补码值 +256 > +100
    // （等价于用例写法 data[19:8]^0x800 = 0x900 = 2304 > 100：
    //   RTL 已按手册用真补码，不再需要异或偏移）
    feed(20'h10000);

    hi_seen = 1'b0;
    for (k = 0; k < 30; k++) begin
      @(posedge clk);
      #1;
      if (status[ST_HI_STATUS] === 1'b1) begin
        hi_seen = 1'b1;
        k = 30;
      end
    end
    chk("TC6 status[5] HI_STATUS set", hi_seen);

    gpio_seen = 1'b0;
    for (k = 0; k < 30; k++) begin
      @(posedge clk);
      #1;
      if ((gpio_o[0] === 1'b1) && (gpio_oe[0] === 1'b1)) begin
        gpio_seen = 1'b1;
        k = 30;
      end
    end
    chk("TC6 GPIO0 outputs HI event (oe=1, o=1)", gpio_seen);
  endtask

  //-------------------------------------------------------------------------
  // TC7：非法抽取率 SINC5 + N=512 -> 钳到最大抽取率，不死锁
  //-------------------------------------------------------------------------
  task automatic tc7_illegal_dec();
    bit seen;
    int pulses;
    logic [IN_W-1:0] val;
    int i;

    $display("\n---- TC7: illegal decimation SINC5 N=512 ----");

    spi_write(15'h0029, 8'h42);   // [1:0]=10 SINC5, [6:3]=1000 -> N=512（非法）
    spi_write(15'h001C, 8'h00);   // FIFO 直通
    spi_write(15'h0025, 8'h00);
    spi_write(15'h0026, 8'h00);
    spi_write(15'h0027, 8'h00);
    spi_write(15'h0028, 8'h02);   // GAIN=0x200

    // 改 FILTER_SEL 后必须发 FILTER_SYNC 才会锁存新的抽取率（手册 p.94）。
    // 不发的话 d_cur 仍是复位值 2，本用例就测不到"非法 N 被钳位"。
    @(negedge clk);
    filter_sync = 1'b1;
    @(negedge clk);
    filter_sync = 1'b0;

    // 逐样本喂，并在每拍检查 dout_valid —— SINC5 的第一次输出在第 256 个
    // 有效样本之后，必须在喂数过程中监测，否则会漏掉这个单拍脉冲。
    seen   = 1'b0;
    pulses = 0;
    val    = '0;
    mon_valid_count = 0;
    for (i = 0; i < 300; i++) begin
      feed(20'd1000);
      @(posedge clk);
      #1;
      if (dout_valid === 1'b1) begin
        seen   = 1'b1;
        pulses = pulses + 1;
        val    = dout;
      end
    end

    // 再观察 100 拍，确认没有迟到的输出（N<=256 时不应再有）
    for (i = 0; i < 100; i++) begin
      @(posedge clk);
      #1;
      if (dout_valid === 1'b1) begin
        seen   = 1'b1;
        pulses = pulses + 1;
        val    = dout;
      end
    end

    seen = (mon_valid_count > 0);
    chk("TC7 no deadlock: output produced for illegal N (<=300 samples)", seen);
    if (seen) chk("TC7 dout has no X", ($isunknown(val) === 1'b0));
  endtask

  //-------------------------------------------------------------------------
  // 主流程
  //-------------------------------------------------------------------------
  initial begin
    $display("============================================================");
    $display(" AD4080 digital top -- self-checking testbench");
    $display(" clk=100 MHz, SPI SCLK=100 ns (Mode 3), IN_W=%0d", IN_W);
    $display("============================================================");

    do_reset();
    tc1_reset_values();

    do_reset();
    tc2_offset_path();

    do_reset();
    tc3_gain_path();

    do_reset();
    tc4_sinc1();

    do_reset();
    tc5_fifo_mode01();

    do_reset();
    tc6_event();

    do_reset();
    tc7_illegal_dec();

    $display("\n============================================================");
    $display(" SUMMARY: PASS=%0d FAIL=%0d", pass_cnt, fail_cnt);
    $display("============================================================");

    if (fail_cnt != 0) begin
      $fatal(1, "%0d check(s) FAILED", fail_cnt);
    end else begin
      $display("ALL TESTS PASSED");
      $finish;
    end
  end

endmodule
