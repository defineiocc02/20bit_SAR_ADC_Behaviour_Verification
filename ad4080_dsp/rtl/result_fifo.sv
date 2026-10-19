//=============================================================================
// @file    result_fifo.sv
// @brief   同步 FIFO 存储体（默认 16K x 20）
// @details 同步写、同步读、寄存器化读口；full/empty 由指针模减得到；
//          overflow/underflow 粘滞；full 时可选覆盖最旧（mode 11）。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 2.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.57（16K sample depth）
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效；存储器内容不复位
// 延迟     : 读 1 clk
// 吞吐     : 1 写 + 1 读/clk
// 可综合性 : 是；ASIC 需替换为 SRAM 宏（当前为 reg 数组 + ram_style）
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版
//   2.0.0 2026-10-19 增加 overwrite_en；复位统一为异步
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   职责一句话
//     把 ADC 每次转换的 20-bit 结果按产生顺序缓冲起来，先写先出；对外给出
//     full/empty/almost_full/almost_empty 与粘滞的 overflow/underflow 标志。
//     本模块**不做**数据打包、不做跨时钟域、不做触发/抽点，也不碰 SAR 核。
//
//   来源
//     AD4080 数据手册公开特性："Conversion result FIFO, 16K sample depth"。
//     本仓库原 RTL 没有该 FIFO（grep 无结果），本文件是**新补的独立模块**，
//     不修改 rtl/core 下任何文件。
//     20-bit 取 rtl_params.vh 的 OUT_BITS = 20（转换结果宽度）。
//
//   单位契约
//     无量纲。wr_data/rd_data 是 OUT_BITS 位无符号原始码，本模块不解释其数值。
//
//   参数来源分级
//     DATA_W  = 20    [推导] OUT_BITS（rtl_params.vh），构造端口传入，默认值仅作参考。
//     DEPTH   = 16384 [公开数据手册] "16K sample depth"。
//     ASSUMPTION: P_AF_MARGIN / P_AE_LEVEL = 16 是水线，纯设计选择，手册未规定。
//     综合前若 OUT_BITS 变动，请由上层显式传入 DATA_W，不要在模块内另立参数表。
//
//   接口与延迟（**这就是契约**，仿真/TB/上层必须按此使用）
//     * 单时钟、同步复位（rst_n 低有效），复位清零指针、标志与 rd_data。
//     * 写：wr_en && !full 的那一拍被接收（wr_fire）。
//           wr_en && full  -> 该拍**不写**（不覆盖旧数据），并把 overflow 置 1。
//     * 读：rd_en && !empty 的那一拍被接收（rd_fire）。
//           rd_en && empty -> 该拍**不读**，rd_data 保持不变，并把 underflow 置 1。
//     * 读延迟固定 1 拍：rd_fire 在第 t 拍 -> rd_data 在第 t+1 拍有效，
//       同时 rd_valid 在第 t+1 拍为 1（单拍脉冲）。rd_valid 是为"寄存器化读口"
//       配套加的，数据手册没有对应引脚概念。
//     * 写与读可同拍发生（各自独立判 full/empty）。
//     * **保守语义（有意为之，不是遗漏）**：full 且同拍有 rd_en 时，写仍被拒绝并置
//       overflow —— 即"满"必须靠先排空解除，不做同拍借位。上层若需要该优化，
//       必须改这里并重新跑 TB，不得在 TB 里绕。
//     * overflow / underflow 是**粘滞**标志：一经置位保持到下一次复位，唯一清除
//       手段是 rst_n（本模块没有 err_clear 引脚）。它们只表示"发生过"，不表示"当前"。
//
//   深度与指针
//     AW     = $clog2(DEPTH) + 1        // 16384 -> 15 bit（14 位地址 + 1 位回绕位）
//     count  = wr_ptr - rd_ptr          // AW 位无符号模减；真实差值 <= DEPTH < 2^AW，
//                                       // 故回绕不会混叠，count 始终落在 [0, DEPTH]
//     empty       = (count == 0)
//     full        = (count == DEPTH)
//     almost_full = (count >= DEPTH - P_AF_MARGIN)   // 剩余空位 <= 16
//     almost_empty= (count <= P_AE_LEVEL)            // 存量 <= 16
//     注意：full 时 almost_full 必为 1，empty 时 almost_empty 必为 1（包含关系，不是四选一）。
//     count 用组合减法得到，面积约一个 15-bit 减法器 + 两个比较器；若时序吃紧，
//     可改成"count 寄存器 + 加减 1"，但对外的 full/empty 定义必须保持等价。
//
//   存储与综合
//     mem 声明为 reg [DATA_W-1:0] mem [0:DEPTH-1] + 同步写/同步读（读地址也打拍），
//     目的是让综合器把它推成单口/简单双口 SRAM 或 FPGA BRAM；已加
//     (* ram_style = "block" *) 提示。**如实登记**：16384x20 的阵列如果综合器
//     判定为"小阵列/异步读"，仍可能退化成分布式 RAM（LUTRAM），那不是流片形态。
//     实际流片必须换成工艺厂 SRAM 宏（或双口 SRAM），届时 mem 的读写端口直接接到
//     宏的 A/B 口，外层的 full/empty/指针逻辑不变。
//     资源量级：16384 x 20 = 327,680 bit = 40 KiB（不含校验位与冗余列）。
//     按 512x20 的 BRAM 粗算需要 32 块；按 1024x18 的 BRAM 则需要 2 列深
//     (16384/1024=16) x 2 片宽 (20/18=2) = 32 块，数量级一致。实际块数取决于工艺库。
//
//   契约与不变量（复核时逐条可查）
//     INV-F1: count ∈ [0, DEPTH]，恒等于"已写未读"的样本数。
//     INV-F2: full 与 empty 不可能同拍为 1（DEPTH >= 2）。
//     INV-F3: wr_fire <=> (wr_en && !full)；rd_fire <=> (rd_en && !empty)。
//     INV-F4: overflow 一旦为 1，在复位前不再回 0；underflow 同理。
//     INV-F5: 读出的数据序列 == 写入的数据序列（FIFO 序），由 TB 的 scoreboard 检查。
//     INV-F6: full 时写入不改变任何 mem 单元（"不覆盖"）。
//
//   未验证项（诚实登记，见 /tmp/fifo_report.md）
//     * 本机没有 Verilator/VCS，**本模块没有跑过任何仿真**，也没有跑过综合。
//     * TB 已写好（sim/tb/result_fifo_tb.sv），但**未执行**。
//     * 时序/面积/功耗未评估；BRAM 推断结果未在综合器上确认。
//     * 跨时钟域、异步复位、ECC、字节使能均不支持。
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
    input  logic              rst_n,      // 同步复位，低有效

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

  // --------------------------------------------------------------------------
  // 写侧：只有 wr_fire 才写；full 时 wr_en 不碰 mem（INV-F6）
  // --------------------------------------------------------------------------
  always_ff @(posedge clk) begin
    if (wr_fire) begin
      mem[wr_ptr[AW_MEM-1:0]] <= wr_data;
    end
  end

  // --------------------------------------------------------------------------
  // 读侧：寄存器输出，延迟 1 拍；空读时保持原值
  // --------------------------------------------------------------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_data  <= {DATA_W{1'b0}};
      rd_valid <= 1'b0;
    end else begin
      rd_valid <= rd_fire;
      if (rd_fire) begin
        rd_data <= mem[rd_ptr[AW_MEM-1:0]];
      end
    end
  end

  // --------------------------------------------------------------------------
  // 指针推进（同步复位）
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
