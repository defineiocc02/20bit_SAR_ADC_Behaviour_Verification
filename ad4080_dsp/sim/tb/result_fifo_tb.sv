//===========================================================================
// result_fifo_tb.sv -- result_fifo 自检 TB
//===========================================================================
// 覆盖内容（与 /tmp/fifo_report.md 的"已写但未跑"清单一一对应）
//   P0 复位值：empty/almost_empty=1，full/almost_full=0，overflow/underflow=0
//   P1 写满 DEPTH 个：每拍核对 count/full/empty/almost_*，并用 scoreboard 记序
//   P2 满时继续写：overflow 置位（粘滞）、count 不变、旧数据不被覆盖
//      满时同拍写+读：写被拒（overflow 保持）、读正常，count-1
//   P3 排空：逐个比对数据顺序；最后 empty=1；空读使 underflow 置位且 rd_data 不跳变
//   P4 随机 10000 拍：每拍比对 count/full/empty/almost_*/rd_valid，读数据对 scoreboard
//   P5 指针回绕：再走一整轮 DEPTH 写 + DEPTH 读（回绕位第二次翻转）
//   P6 复位中途清空：部分占用的 FIFO 复位后指针/标志全清，随后功能正常
//
// 用法（本机没有 Verilator/VCS，**本 TB 尚未执行过**）：
//   sim/run_vcs.sh -top result_fifo_tb -incdir rtl/params \
//       rtl/dsp/result_fifo.sv sim/tb/result_fifo_tb.sv
//   可选 +seed=<n> 改变随机相位种子。
//
// 说明：TB 直接层次化访问 dut.count / dut.overflow 是**故意**的 —— count 是
// 内部不变量的观测点，从端口看不到；这属于白盒检查，不替代黑盒端口检查。
//===========================================================================
`timescale 1ns/1ps

module result_fifo_tb;

  localparam int DATA_W = 20;
  localparam int DEPTH  = 16384;
  localparam int AF_M   = 16;
  localparam int AE_L   = 16;
  localparam int AW     = $clog2(DEPTH) + 1;

  logic               clk = 1'b0;
  logic               rst_n = 1'b0;
  logic               wr_en = 1'b0;
  logic [DATA_W-1:0]  wr_data = '0;
  logic               rd_en = 1'b0;
  logic [DATA_W-1:0]  rd_data;
  logic               rd_valid;
  logic               full, empty, almost_full, almost_empty, overflow, underflow;

  always #1 clk = ~clk;

  result_fifo #(
      .DATA_W      (DATA_W),
      .DEPTH       (DEPTH),
      .P_AF_MARGIN (AF_M),
      .P_AE_LEVEL  (AE_L)
  ) dut (
      .clk(clk), .rst_n(rst_n),
      .wr_en(wr_en), .wr_data(wr_data),
      .rd_en(rd_en), .rd_data(rd_data), .rd_valid(rd_valid),
      .full(full), .empty(empty),
      .almost_full(almost_full), .almost_empty(almost_empty),
      .overflow(overflow), .underflow(underflow)
  );

  // --------------------------------------------------------------------------
  // 检查/统计
  // --------------------------------------------------------------------------
  int errors = 0;
  int checks = 0;

  `define CHK(cond, msg) \
    begin \
      checks++; \
      if (!(cond)) begin \
        errors++; \
        if (errors <= 25) $display("[%0t] FAIL: %s", $time, msg); \
      end \
    end

  // scoreboard：按写入顺序排队，读出必须逐项相等
  logic [DATA_W-1:0] sb[$];

  // 参考模型（只模拟"计数 + 标志"，数据顺序交给 scoreboard）
  int   m_count = 0;
  logic m_ovf   = 1'b0;
  logic m_unf   = 1'b0;

  // --------------------------------------------------------------------------
  // 单拍驱动：negedge 驱动 -> posedge DUT 采样 -> #0.5 结算后再检查
  // --------------------------------------------------------------------------
  task automatic tick(input logic do_wr, input logic [DATA_W-1:0] wd, input logic do_rd);
    logic was_full, was_empty;
    logic [DATA_W-1:0] exp;

    @(negedge clk);
    was_full  = full;      // 采样沿之前的状态
    was_empty = empty;
    wr_en     = do_wr;
    wr_data   = wd;
    rd_en     = do_rd;

    @(posedge clk);
    #0.5;                  // 停在两个时钟沿之间，跨过 NBA 区取本拍结果
                           // （不能用 #1：会正好落在下一个 negedge 上产生竞争）

    // ---- 参考模型推进 ----
    if (do_wr && !was_full) begin
      sb.push_back(wd);
      m_count++;
    end else if (do_wr && was_full) begin
      m_ovf = 1'b1;
    end

    if (do_rd && !was_empty) begin
      if (sb.size() == 0) begin
        `CHK(1'b0, "scoreboard underflow (read fired with empty queue)")
      end else begin
        exp = sb.pop_front();
        m_count--;
        if (rd_data !== exp)
          `CHK(1'b0, $sformatf("read data mismatch: got %05h exp %05h", rd_data, exp))
      end
    end else if (do_rd && was_empty) begin
      m_unf = 1'b1;
    end

    // ---- 端口与模型比对 ----
    `CHK(dut.count == m_count,      "internal count != model count")
    `CHK(empty        === (m_count == 0),      "empty mismatch")
    `CHK(full         === (m_count == DEPTH),  "full mismatch")
    `CHK(almost_full  === (m_count >= DEPTH - AF_M), "almost_full mismatch")
    `CHK(almost_empty === (m_count <= AE_L),   "almost_empty mismatch")
    `CHK(rd_valid     === (do_rd && !was_empty), "rd_valid mismatch")
    `CHK(overflow     === m_ovf, "overflow mismatch (sticky)")
    `CHK(underflow    === m_unf, "underflow mismatch (sticky)")
    `CHK(sb.size() == m_count, "scoreboard size != model count")
  endtask

  task automatic do_reset();
    @(negedge clk);
    rst_n   = 1'b0;
    wr_en   = 1'b0;
    rd_en   = 1'b0;
    wr_data = '0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);
    m_count = 0;
    m_ovf   = 1'b0;
    m_unf   = 1'b0;
    sb.delete();
  endtask

  // --------------------------------------------------------------------------
  // 主流程
  // --------------------------------------------------------------------------
  initial begin : main
    int            i;
    int            seed;
    int unsigned   rnd;
    int            exp_idx;
    logic [DATA_W-1:0] d;

    if (DATA_W != 20)
      $fatal(1, "TB DATA_W=%0d; standalone AD4080 DSP uses 20-bit results", DATA_W);

    seed = 32'h1234_5678;
    void'($value$plusargs("seed=%d", seed));
    rnd = $urandom(seed);

    // ---------------- P0 复位 ----------------
    rst_n = 1'b0;
    repeat (4) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);
    `CHK(empty === 1'b1,          "P0 empty != 1 after reset")
    `CHK(almost_empty === 1'b1,   "P0 almost_empty != 1 after reset")
    `CHK(full === 1'b0,           "P0 full != 0 after reset")
    `CHK(almost_full === 1'b0,    "P0 almost_full != 0 after reset")
    `CHK(overflow === 1'b0,       "P0 overflow != 0 after reset")
    `CHK(underflow === 1'b0,      "P0 underflow != 0 after reset")
    `CHK(rd_valid === 1'b0,       "P0 rd_valid != 0 after reset")
    `CHK(dut.count == 0,          "P0 count != 0 after reset")
    $display("[%0t] P0 reset OK", $time);

    // ---------------- P1 写满 ----------------
    for (i = 0; i < DEPTH; i++) begin
      tick(1'b1, i[DATA_W-1:0], 1'b0);
      if (i == DEPTH - AF_M - 1)
        `CHK(almost_full === 1'b1, "P1 almost_full 未在 count=DEPTH-AF_M 时置位")
      if (i == DEPTH - AF_M - 2)
        `CHK(almost_full === 1'b0, "P1 almost_full 提前置位")
    end
    `CHK(full === 1'b1,        "P1 full != 1 after DEPTH writes")
    `CHK(empty === 1'b0,       "P1 empty != 0 when full")
    `CHK(almost_full === 1'b1, "P1 almost_full != 1 when full")
    `CHK(dut.count == DEPTH,   "P1 count != DEPTH")
    $display("[%0t] P1 fill %0d OK (errors=%0d)", $time, DEPTH, errors);

    // ---------------- P2 满时写：overflow、不覆盖 ----------------
    for (i = 0; i < 4; i++) tick(1'b1, 20'hBAD00 + i[DATA_W-1:0], 1'b0);
    `CHK(overflow === 1'b1,    "P2 overflow 未在满写时置位")
    `CHK(dut.count == DEPTH,   "P2 满写改变了 count")
    `CHK(full === 1'b1,        "P2 满写后 full 掉了")
    // 粘滞性：再空转 3 拍仍为 1
    repeat (3) tick(1'b0, '0, 1'b0);
    `CHK(overflow === 1'b1,    "P2 overflow 不是粘滞的")

    // 满时同拍写+读：写被拒、读正常（保守语义）
    d = 20'h5A5A5;
    tick(1'b1, d, 1'b1);
    `CHK(dut.count == DEPTH - 1, "P2 满时同拍写+读后 count != DEPTH-1")
    `CHK(overflow === 1'b1,      "P2 满时同拍写+读后 overflow 掉了")
    $display("[%0t] P2 full/overflow OK", $time);

    // ---------------- P3 排空 + underflow ----------------
    // P2 的同拍读已经取走 head（data=0），故从 1 开始比对。
    exp_idx = 1;
    for (i = 0; i < DEPTH - 1; i++) begin
      tick(1'b0, '0, 1'b1);
      if (rd_data !== exp_idx[DATA_W-1:0])
        `CHK(1'b0, $sformatf("P3 顺序错: got %05h exp %05h @%0d", rd_data, exp_idx, i))
      exp_idx++;
    end
    `CHK(empty === 1'b1,        "P3 读完后 empty != 1")
    `CHK(almost_empty === 1'b1, "P3 读完后 almost_empty != 1")
    `CHK(full === 1'b0,         "P3 读完后 full != 0")
    `CHK(dut.count == 0,        "P3 读完后 count != 0")

    d = rd_data;                 // 空读前后 rd_data 必须保持
    for (i = 0; i < 4; i++) tick(1'b0, '0, 1'b1);
    `CHK(underflow === 1'b1,     "P3 underflow 未在空读时置位")
    `CHK(rd_data === d,          "P3 空读改变了 rd_data")
    `CHK(rd_valid === 1'b0,      "P3 空读时 rd_valid 为 1")
    `CHK(dut.count == 0,         "P3 空读改变了 count")
    $display("[%0t] P3 drain/underflow OK (errors=%0d)", $time, errors);

    // ---------------- P4 随机 10000 拍 ----------------
    // 先复位清掉粘滞标志，再预填 2000 个，使随机游走有足够余量。
    do_reset();
    for (i = 0; i < 2000; i++) tick(1'b1, 20'h10000 + i[DATA_W-1:0], 1'b0);
    `CHK(underflow === 1'b0 && overflow === 1'b0, "P4 预填阶段误置粘滞标志")

    for (i = 0; i < 10000; i++) begin
      rnd = $urandom(seed);
      // 读只在非空时发起（空读已由 P3 单独覆盖），写按随机位
      tick(rnd[0], rnd[19:0], (m_count > 0) ? rnd[1] : 1'b0);
    end
    $display("[%0t] P4 random 10000 OK (count=%0d, errors=%0d)", $time, dut.count, errors);

    // ---------------- P5 指针回绕：再走一整轮 ----------------
    while (!empty) tick(1'b0, '0, 1'b1);
    for (i = 0; i < DEPTH; i++) tick(1'b1, 20'h20000 + i[DATA_W-1:0], 1'b0);
    `CHK(full === 1'b1, "P5 第二轮未写满")
    for (i = 0; i < DEPTH; i++) tick(1'b0, '0, 1'b1);
    `CHK(empty === 1'b1, "P5 第二轮未读空")
    `CHK(dut.count == 0, "P5 第二轮 count != 0")
    $display("[%0t] P5 wrap OK", $time);

    // ---------------- P6 中途复位 ----------------
    for (i = 0; i < 100; i++) tick(1'b1, i[DATA_W-1:0], 1'b0);
    do_reset();
    `CHK(empty === 1'b1 && full === 1'b0, "P6 复位后指针未清")
    `CHK(overflow === 1'b0 && underflow === 1'b0, "P6 复位未清粘滞标志")
    `CHK(dut.wr_ptr == 0 && dut.rd_ptr == 0, "P6 复位后指针非 0")
    for (i = 0; i < 10; i++) tick(1'b1, 20'h30000 + i[DATA_W-1:0], 1'b0);
    for (i = 0; i < 10; i++) tick(1'b0, '0, 1'b1);
    `CHK(empty === 1'b1, "P6 复位后功能异常")
    $display("[%0t] P6 mid-reset OK", $time);

    // ---------------- 总结 ----------------
    if (errors == 0) begin
      $display("=================================================");
      $display(" RESULT_FIFO_TB PASS: %0d checks, 0 errors", checks);
      $display("=================================================");
      $finish;
    end else begin
      $display("=================================================");
      $display(" RESULT_FIFO_TB FAIL: %0d checks, %0d errors", checks, errors);
      $display("=================================================");
      $fatal(1, "result_fifo_tb failed");
    end
  end

  // 看门狗：正常约 2e5 拍 = 4e5 ns；给足余量
  initial begin
    #10_000_000;
    $fatal(1, "result_fifo_tb timeout");
  end

endmodule
