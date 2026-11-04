//=============================================================================
// @file    result_fifo.sv
// @brief   带覆盖选项的同步读写FIFO存储体
// @details 默认16384x20；模指针差产生占用量；读数据/rd_valid在接受读请求的时钟沿寄存。
//          满时overwrite_en允许写入并丢最旧数据；否则保守拒写，即使同沿有读也不借位。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : clk单域，无CDC
// 复位     : 指针和sticky标志异步低有效复位；RAM、rd_data/rd_valid不接异步复位
// 延迟     : 同步寄存读口；用rd_valid限定读数据，复位后首次合法读前rd_data未定义
// 吞吐     : 最多1写+1读/clk，满/空分别限制各端
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : 无
// 边界     : DEPTH为2幂且>=2；0<=P_AE_LEVEL<DEPTH，1<=P_AF_MARGIN<DEPTH。
//          overflow/underflow粘滞到复位；overwrite满写不置overflow。
//          同地址读写的RTL仿真为读旧值；替换SRAM宏需确认该冲突语义和端口时序。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module result_fifo #(
    parameter int DATA_W      = 20,     // 转换结果位宽；默认 OUT_BITS
    parameter int DEPTH       = 16384,  // 深度，必须是 2 的幂
    parameter int P_AF_MARGIN = 16,     // almost_full：剩余空位 <= 该值
    parameter int P_AE_LEVEL  = 16      // almost_empty：存量 <= 该值
) (
    input  logic              clk,
    input  logic              rst_n,      // 异步低有效；RAM读口未连接此复位。

    // ---- 写端口 ----
    input  logic              wr_en,
    input  logic              overwrite_en, // 1 = full 时覆盖最旧（mode 11）
    input  logic [DATA_W-1:0] wr_data,

    // ---- 读端口 ----
    input  logic              rd_en,
    output logic [DATA_W-1:0] rd_data,    // 寄存器输出，比 rd_en 晚 1 拍
    output logic              rd_valid,   // 与 rd_data 同拍的单拍有效

    // ---- 状态 ----
    output logic              full,
    output logic              empty,
    output logic              almost_full,
    output logic              almost_empty,
    output logic              overflow,    // 粘滞：写满时被拒
    output logic              underflow    // 粘滞：读空时被拒
);

  // --------------------------------------------------------------------------
  // 派生常量（全部显式位宽，禁止隐式扩展）
  // --------------------------------------------------------------------------
  localparam int AW     = $clog2(DEPTH) + 1;  // 含回绕位
  localparam int AW_MEM = $clog2(DEPTH);      // 纯地址位

  localparam logic [AW-1:0] ONE       = {{(AW-1){1'b0}}, 1'b1};
  localparam logic [AW-1:0] V_DEPTH   = AW'(DEPTH);
  localparam logic [AW-1:0] V_AF_TH   = AW'(DEPTH - P_AF_MARGIN);
  localparam logic [AW-1:0] V_AE_TH   = AW'(P_AE_LEVEL);

`ifndef FORMAL
  // 参数自检只在仿真时执行；形式构建跳过（Yosys 不支持 $fatal）
  initial begin
    if (DATA_W < 1)
      $fatal(1, "result_fifo: DATA_W must be >= 1");
    if (DEPTH < 2 || (DEPTH & (DEPTH - 1)) != 0)
      $fatal(1, "result_fifo: DEPTH must be a power of two and >= 2");
    if (P_AF_MARGIN < 1 || P_AF_MARGIN >= DEPTH)
      $fatal(1, "result_fifo: P_AF_MARGIN must be in [1, DEPTH-1]");
    if (P_AE_LEVEL < 0 || P_AE_LEVEL >= DEPTH)
      $fatal(1, "result_fifo: P_AE_LEVEL must be in [0, DEPTH-1]");
  end
`endif

  // --------------------------------------------------------------------------
  // 存储体：同步写、同步读（读出地址在 always_ff 里取，便于推成 SRAM/BRAM）
  // --------------------------------------------------------------------------
  (* ram_style = "block" *) reg [DATA_W-1:0] mem [0:DEPTH-1];

  // --------------------------------------------------------------------------
  // 指针与计数
  // --------------------------------------------------------------------------
  logic [AW-1:0] wr_ptr;
  logic [AW-1:0] rd_ptr;
  logic [AW-1:0] count;

  logic          wr_fire;
  logic          rd_fire;

  // count 由两个指针的模减得到：真实占用量 <= DEPTH，不会与回绕混叠（INV-F1）。
  assign count = wr_ptr - rd_ptr;

  assign empty        = (count == {AW{1'b0}});
  assign full         = (count == V_DEPTH);
  assign almost_full  = (count >= V_AF_TH);
  assign almost_empty = (count <= V_AE_TH);

  assign wr_fire = wr_en && (!full || overwrite_en);
  assign rd_fire = rd_en && !empty;

`ifdef FORMAL
  // --------------------------------------------------------------------
  // 形式不变量（仅 -DFORMAL）：占用量不超过深度；满不写、空不读。
  // 形式环境的初始指针与复位值一致。
  // --------------------------------------------------------------------
  initial begin
    wr_ptr = {AW{1'b0}};
    rd_ptr = {AW{1'b0}};
  end

  always_comb begin
    assert (count <= V_DEPTH);
    assert (!(wr_fire && full && !overwrite_en));
    assert (!(rd_fire && empty));
  end
`endif

  // --------------------------------------------------------------------------
  // 写侧：wr_fire限定写入；full时仅overwrite_en允许覆盖。
  // --------------------------------------------------------------------------
  always_ff @(posedge clk) begin
    if (wr_fire) begin
      mem[wr_ptr[AW_MEM-1:0]] <= wr_data;
    end
  end

  // --------------------------------------------------------------------------
  // 读侧：寄存器输出，延迟 1 拍；空读时保持原值
  // --------------------------------------------------------------------------
  // SYNTH: 读口**不带异步复位**，否则综合器无法把 mem 推断成 RAM
  //        （会退化成 327,680 个触发器）。rd_data 在第一次读之前为 X，
  //        由 rd_valid 限定有效窗口；这是 SRAM 输出的标准行为。
  always_ff @(posedge clk) begin
    rd_valid <= rd_fire;
    if (rd_fire) begin
      rd_data <= mem[rd_ptr[AW_MEM-1:0]];
    end
  end

  // --------------------------------------------------------------------------
  // 指针推进；异步低有效复位。
  // --------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr <= {AW{1'b0}};
      rd_ptr <= {AW{1'b0}};
    end else begin
      if (wr_fire) wr_ptr <= wr_ptr + ONE;
      if (rd_fire) rd_ptr <= rd_ptr + ONE;
      // full + overwrite + no read: drop the oldest by advancing rd_ptr
      if (wr_fire && full && overwrite_en && !rd_fire) rd_ptr <= rd_ptr + ONE;
    end
  end

  // --------------------------------------------------------------------------
  // 粘滞错误标志：只由复位清除（INV-F4）
  // --------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      overflow  <= 1'b0;
      underflow <= 1'b0;
    end else begin
      if (wr_en && full && !overwrite_en) overflow <= 1'b1;
      if (rd_en && empty) underflow <= 1'b1;
    end
  end

endmodule

`default_nettype wire
