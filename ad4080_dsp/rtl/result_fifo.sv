//=============================================================================
// @file    result_fifo.sv
// @brief   带覆盖选项的同步读写 FIFO 存储体。
// @details 默认 16384×20，模指针差产生占用量；接受读请求的时钟沿寄存数据和 valid。
//          满时仅 overwrite_en 允许写入并丢弃最旧数据；否则保守拒写，不借用同沿读腾出的空间。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk 单域，不含 CDC。
// 复位: 指针和粘滞标志异步低有效复位；RAM、rd_data/rd_valid 不接异步复位。
// 延迟: 同步寄存读口，须由 rd_valid 限定；复位后首次合法读取前 rd_data 未定义。
// 吞吐: 每个 clk 最多一次写、一次读；满/空状态分别限制两端。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: DEPTH 须为不小于 2 的二次幂；0<=P_AE_LEVEL<DEPTH，1<=P_AF_MARGIN<DEPTH。
//     overflow/underflow 粘滞到复位；满时覆盖写不置 overflow。
//     RTL 同地址读写读出旧值；替换 SRAM 宏须确认冲突语义与端口时序。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module result_fifo #(
    parameter int DATA_W      = 20,     // DATA_W: 存储字位宽，默认 20 bit，仿真要求不小于 1。
    parameter int DEPTH       = 16384,  // DEPTH: 存储字数，须为不小于 2 的二次幂。
    parameter int P_AF_MARGIN = 16,     // P_AF_MARGIN: 近满余量，范围 1..DEPTH-1 个字。
    parameter int P_AE_LEVEL  = 16      // P_AE_LEVEL: 近空阈值，范围 0..DEPTH-1 个字。
) (
    input  logic              clk,
    input  logic              rst_n,      // 异步低有效；RAM读口未连接此复位。


    input  logic              wr_en,
    input  logic              overwrite_en, // 1 = full 时覆盖最旧（mode 11）
    input  logic [DATA_W-1:0] wr_data,


    input  logic              rd_en,
    output logic [DATA_W-1:0] rd_data,    // 寄存器输出，比 rd_en 晚 1 拍
    output logic              rd_valid,   // 与 rd_data 同拍的单拍有效


    output logic              full,
    output logic              empty,
    output logic              almost_full,
    output logic              almost_empty,
    output logic              overflow,    // 粘滞：写满时被拒
    output logic              underflow    // 粘滞：读空时被拒
);


  // 派生常量显式定宽，避免指针运算依赖隐式扩展。

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


  // RAM 采用同步写和同步读，读地址在 always_ff 中采样，以便推断 SRAM/BRAM。

  (* ram_style = "block" *) reg [DATA_W-1:0] mem [0:DEPTH-1];




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

  // 仅在 -DFORMAL 下检查占用量不超过深度、满时不作普通写、空时不读。
  // 形式环境的初始指针与复位值一致。

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


  // wr_fire 限定 RAM 写入；满时仅 overwrite_en 允许覆盖。

  always_ff @(posedge clk) begin
    if (wr_fire) begin
      mem[wr_ptr[AW_MEM-1:0]] <= wr_data;
    end
  end


  // 读口寄存输出，空读保持原值。

  // SYNTH: 读口不接异步复位，以保留 mem 推断为 RAM 的结构条件；否则可能
  //     展开为 327,680 个存储触发器。首次合法读取前 rd_data 为 X，须用 rd_valid 限定。
  always_ff @(posedge clk) begin
    rd_valid <= rd_fire;
    if (rd_fire) begin
      rd_data <= mem[rd_ptr[AW_MEM-1:0]];
    end
  end




  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr <= {AW{1'b0}};
      rd_ptr <= {AW{1'b0}};
    end else begin
      if (wr_fire) wr_ptr <= wr_ptr + ONE;
      if (rd_fire) rd_ptr <= rd_ptr + ONE;
      // 满时覆盖且没有同时读取，通过推进 rd_ptr 丢弃最旧数据。
      if (wr_fire && full && overwrite_en && !rd_fire) rd_ptr <= rd_ptr + ONE;
    end
  end


  // overflow/underflow 仅由复位清除，保持 INV-F4 的粘滞错误约定。

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
