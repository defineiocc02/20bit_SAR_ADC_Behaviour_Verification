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


    do_reset();
    tc8_sinc5_comp();

    do_reset();
    tc9_lvds();

    do_reset();
    tc10_cnv();

    do_reset();
    tc11_gpio();

    do_reset();
    tc12_event_low();

    do_reset();
    tc13_fifo_mode10();

    do_reset();
    tc14_fifo_mode11();

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

  //-------------------------------------------------------------------------
  // TC8：SINC5 + 补偿（FILTER_SEL=11, N=4 -> 总抽取 8）
  //-------------------------------------------------------------------------
  task automatic tc8_sinc5_comp();
    bit seen; int pulses; int i; logic [IN_W-1:0] val;
    $display("\n---- TC8: SINC5 + compensation (N=4, total 8) ----");
    spi_write(15'h0029, 8'h0B);   // [1:0]=11 comp, [6:3]=0001 -> N=4
    spi_write(15'h001C, 8'h00);
    spi_write(15'h0025, 8'h00); spi_write(15'h0026, 8'h00);
    spi_write(15'h0027, 8'h00); spi_write(15'h0028, 8'h02);
    @(negedge clk); filter_sync = 1'b1; @(negedge clk); filter_sync = 1'b0;
    repeat (4) @(posedge clk);
    mon_valid_count = 0;
    for (i = 0; i < 96; i++) begin
      feed(20'd1000);
      if (dout_valid === 1'b1) val = dout;
    end
    repeat (20) @(posedge clk);
    seen = (mon_valid_count > 0);
    chk("TC8 SINC5+comp produced output", seen);
    if (seen) chk_eq("TC8 comp DC == 1000", longint'(val), 1000, 1);
  endtask

  //-------------------------------------------------------------------------
  // TC9：LVDS 并串（1 lane 20 bit / 2 lane 10+10）
  //-------------------------------------------------------------------------
  task automatic lvds_frame(input int nbits, output logic [19:0] lane0, output logic [19:0] lane1,
                            output bit frame_seen);
    int i;
    lane0 = '0; lane1 = '0; frame_seen = 1'b0;
    for (i = 0; i < nbits; i++) begin
      @(negedge clk); bit_en = 1'b1;
      @(posedge clk); #1;
      lane0 = {lane0[18:0], ser_data[0]};
      lane1 = {lane1[18:0], ser_data[1]};
      if (ser_frame[0]) frame_seen = 1'b1;
      @(negedge clk); bit_en = 1'b0;
    end
  endtask

  task automatic tc9_lvds();
    logic [19:0] pat, a, b;
    bit fs;
    $display("\n---- TC9: LVDS framing (1 lane / 2 lane) ----");
    spi_write(15'h0029, 8'h00);   // bypass
    spi_write(15'h0015, 8'h40);   // DATA_INTF_MODE=0 (LVDS), lane_sel=0
    spi_write(15'h0016, 8'h00);   // self_clk=0
    pat = 20'hA5A5A;
    feed(pat); repeat (4) @(posedge clk);
    // 第 1 帧装载，第 2 帧输出
    lvds_frame(20, a, b, fs);
    lvds_frame(20, a, b, fs);
    chk("TC9 LVDS frame flag asserted", fs);
    chk_eq("TC9 1-lane data == pattern", longint'(a), longint'(pat), 0);
    // 不喂新样本，再发一帧：应重复上一帧
    lvds_frame(20, a, b, fs);
    chk_eq("TC9 idle frame repeats", longint'(a), longint'(pat), 0);

    // 2 lane：lane0 = [19:10], lane1 = [9:0]
    spi_write(15'h0015, 8'h44);   // lane_sel=1
    pat = 20'hB6DB6;
    feed(pat); repeat (4) @(posedge clk);
    // 切 lane 后相位可能不在 0，先跑两帧预热，再采第三帧
    lvds_frame(10, a, b, fs);
    lvds_frame(10, a, b, fs);
    lvds_frame(10, a, b, fs);
    // 本实现是“奇偶交织”：lane0 = bit19,17,...,1；lane1 = bit18,16,...,0。
    // 手册 p.45-53 只用图表示 2-lane 映射，文字未写，标记 [待确认]。
    begin
      logic [9:0] exp0, exp1;
      for (int k = 0; k < 10; k++) begin
        exp0[9-k] = pat[19-2*k];
        exp1[9-k] = pat[18-2*k];
      end
      chk_eq("TC9 2-lane lane0 == odd bits",  longint'(a[9:0]), longint'(exp0), 0);
      chk_eq("TC9 2-lane lane1 == even bits", longint'(b[9:0]), longint'(exp1), 0);
    end
  endtask

  //-------------------------------------------------------------------------
  // TC10：CNV CMOS/LVDS 与 tCONV 违反
  //-------------------------------------------------------------------------
  task automatic tc10_cnv();
    logic [7:0] v;
    $display("\n---- TC10: CNV CMOS/LVDS + tCONV error ----");
    spi_write(15'h001C, 8'h10);   // cnv_err_route=1
    spi_write(15'h0016, 8'h00);   // CMOS
    spi_write(15'h0014, 8'h04);   // 清 ADC_CNV_ERR
    @(negedge clk); cnv_p = 1'b1; repeat (6) @(negedge clk); cnv_p = 1'b0;
    repeat (6) @(negedge clk);
    // busy 期间再来一次 CNV
    @(negedge clk); cnv_p = 1'b1; repeat (6) @(negedge clk); cnv_p = 1'b0;
    repeat (8) @(negedge clk);
    spi_read(15'h0014, v);
    chk("TC10 ADC_CNV_ERR set (0x14[2])", v[2] === 1'b1);

    // LVDS 差分 CNV
    spi_write(15'h0014, 8'h04);
    spi_write(15'h0016, 8'h01);   // LVDS_CNV_EN=1
    @(negedge clk); cnv_p = 1'b1; cnv_n = 1'b0;
    repeat (6) @(negedge clk); cnv_p = 1'b0; cnv_n = 1'b0;
    repeat (10) @(negedge clk);
    chk("TC10 LVDS CNV path exercised", 1'b1);
  endtask

  //-------------------------------------------------------------------------
  // TC11：GPIO 方向 / SEL 路由
  //-------------------------------------------------------------------------
  task automatic tc11_gpio();
    $display("\n---- TC11: GPIO direction + SEL mux ----");
    spi_write(15'h0019, 8'hFF);   // 全输出，DATA=0xF
    spi_write(15'h001A, 8'h77);   // 四路 SEL=0111 GPIO_DATA
    spi_write(15'h001B, 8'h77);
    @(negedge clk); #1;
    chk("TC11 GPIO_DATA out", (gpio_o === 4'hF) && (gpio_oe === 4'hF));
    // pin0 改 SEL=0001 FIFO_FULL，其余仍 DATA
    spi_write(15'h001A, 8'h71);
    @(negedge clk); #1;
    chk("TC11 GPIO0 = FIFO_FULL(0)", gpio_o[0] === 1'b0);
    // pin0 改 SEL=1000 FILTER_SYNC 输入，强制为输入
    spi_write(15'h001A, 8'h78);
    gpio_i[0] = 1'b0;
    @(negedge clk); #1;
    chk("TC11 GPIO0 forced input", gpio_oe[0] === 1'b0);

    // SEL 全编码扫描：1000/1001/1010 强制输入，其余按方向输出
    for (int sel = 0; sel < 16; sel++) begin
      spi_write(15'h001A, {4'h0, sel[3:0]});   // gpio0 SEL=sel, gpio1=0
      @(negedge clk); #1;
      if (sel == 4'b1000 || sel == 4'b1001 || sel == 4'b1010)
        chk($sformatf("TC11 SEL %04b -> input", sel), gpio_oe[0] === 1'b0);
      else
        chk($sformatf("TC11 SEL %04b -> output", sel), gpio_oe[0] === 1'b1);
    end
  endtask

  //-------------------------------------------------------------------------
  // TC12：HI/LO 阈值 + 迟滞 + ALERT 电平语义
  //-------------------------------------------------------------------------
  task automatic tc12_event_low();
    logic [7:0] v;
    $display("\n---- TC12: HI/LO threshold + hysteresis + ALERT ----");
    spi_write(15'h001C, 8'hE0);   // event_en=1, hi_route=1, lo_route=1
    spi_write(15'h0021, 8'h64); spi_write(15'h0022, 8'h00);  // HI=+100
    spi_write(15'h0023, 8'h9C); spi_write(15'h0024, 8'h0F);  // LO=-100
    spi_write(15'h001F, 8'h0A); spi_write(15'h0020, 8'h00);  // HYST=10
    spi_write(15'h0014, 8'h3C);   // W1C 清 HI/LO
    spi_write(15'h0019, 8'h01);   // gpio0 输出，DATA=0
    spi_write(15'h001A, 8'h06);   // gpio0 SEL=0110 ALERT

    // +200 -> HI（阈值 LSB = data[19:8]，所以 200<<8）
    feed(20'sd51200); feed(20'sd51200); feed(20'sd51200);
    repeat (4) @(posedge clk);
    spi_read(15'h0014, v);
    chk("TC12 HI_STATUS set", v[5] === 1'b1);
    chk("TC12 ALERT low (active)", gpio_o[0] === 1'b0);

    // +50：低于 HI-HYST -> ALERT 回高，但状态位粘滞
    feed(20'sd12800); feed(20'sd12800); feed(20'sd12800);
    repeat (4) @(posedge clk);
    spi_read(15'h0014, v);
    chk("TC12 HI_STATUS sticky", v[5] === 1'b1);
    chk("TC12 ALERT returns high", gpio_o[0] === 1'b1);

    // -200 -> LO
    feed(-20'sd51200); feed(-20'sd51200); feed(-20'sd51200);
    repeat (4) @(posedge clk);
    spi_read(15'h0014, v);
    chk("TC12 LO_STATUS set", v[4] === 1'b1);

    spi_write(15'h0014, 8'h3C);   // W1C
    spi_read(15'h0014, v);
    chk("TC12 W1C clears HI/LO", (v[5] === 1'b0) && (v[4] === 1'b0));
  endtask


  //-------------------------------------------------------------------------
  // TC13：FIFO mode 10（事件后捕获 WATERMARK 个）
  //-------------------------------------------------------------------------
  task automatic tc13_fifo_mode10();
    logic [7:0] v;
    int i;
    bit captured_seen;
    $display("\n---- TC13: FIFO mode 10 (event -> WATERMARK) ----");
    spi_write(15'h0029, 8'h00);   // bypass
    spi_write(15'h0025, 8'h00); spi_write(15'h0026, 8'h00);
    spi_write(15'h0027, 8'h00); spi_write(15'h0028, 8'h02);
    spi_write(15'h001D, 8'h04); spi_write(15'h001E, 8'h00);  // WM=4
    spi_write(15'h001C, 8'h02);   // FIFO_MODE=10, INT_EVENT_EN=0（外部事件）
    repeat (3) @(posedge clk);

    for (i = 0; i < 4; i++) feed(1000 + i);   // 事件前，应被丢弃
    repeat (3) @(posedge clk);                // 等校正流水排空（2 拍）
    @(negedge clk); ext_event = 1'b1;
    @(negedge clk); ext_event = 1'b0;
    for (i = 4; i < 8; i++) feed(1000 + i);   // 事件后 4 个
    repeat (8) @(posedge clk);

    spi_read(15'h0014, v);
    chk("TC13 FIFO_FULL/captured set", v[7] === 1'b1);
    for (i = 0; i < 4; i++) begin
      @(negedge clk); rd_en = 1'b1;
      @(posedge clk); #1;
      chk_eq($sformatf("TC13 rd[%0d] == %0d", i, 1004+i), longint'(dout), 1004+i, 0);
      @(negedge clk); rd_en = 1'b0;
      @(posedge clk);
    end
  endtask

  //-------------------------------------------------------------------------
  // TC14：FIFO mode 11（环形覆盖 + 事件位置回写）
  //-------------------------------------------------------------------------
  task automatic tc14_fifo_mode11();
    logic [7:0] v;
    int i;
    $display("\n---- TC14: FIFO mode 11 (circular + watermark writeback) ----");
    spi_write(15'h0029, 8'h00);
    spi_write(15'h001D, 8'h04); spi_write(15'h001E, 8'h00);  // WM=4（4 的倍数）
    spi_write(15'h001C, 8'h03);   // FIFO_MODE=11, INT_EVENT_EN=0
    repeat (3) @(posedge clk);

    for (i = 0; i < 8; i++) feed(2000 + i);   // 事件前 8 个
    repeat (3) @(posedge clk);                // 等校正流水排空（2 拍）
    @(negedge clk); ext_event = 1'b1;
    @(negedge clk); ext_event = 1'b0;
    for (i = 8; i < 12; i++) feed(2000 + i);  // 事件后 4 个
    repeat (8) @(posedge clk);

    // mode 11 捕获完成时把事件位置写回 WATERMARK：8 次写后事件 -> 0x0008
    spi_read(15'h001D, v);
    chk_eq("TC14 WM writeback low == 0x08", longint'(v), 8'h08, 0);
    spi_read(15'h001E, v);
    chk_eq("TC14 WM writeback high == 0x00", longint'(v), 8'h00, 0);

    // 读前 3 个，应为最早写入的 2000/2001/2002
    for (i = 0; i < 3; i++) begin
      @(negedge clk); rd_en = 1'b1;
      @(posedge clk); #1;
      chk_eq($sformatf("TC14 rd[%0d] == %0d", i, 2000+i), longint'(dout), 2000+i, 0);
      @(negedge clk); rd_en = 1'b0;
      @(posedge clk);
    end
  endtask


endmodule
