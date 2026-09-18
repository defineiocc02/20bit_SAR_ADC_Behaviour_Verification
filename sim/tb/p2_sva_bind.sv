//===========================================================================
// p2_sva_bind.sv -- 把关键不变量做成**运行时断言**（用 bind，不改 RTL 源码）
//===========================================================================
// 为什么要这一层
//   testbench 只能验"我选的那些向量"；断言验的是"**每一次**时钟沿"。
//   计划 §6.4 要求 INV-1…INV-8 写成 SVA。这里落地其中**能在数字域表达**的几条。
//
// 与 TB 的分工（避免重复，也避免假装覆盖了没覆盖的）
//   * 本文件：**逐拍**成立的结构性不变量（排列是不是双射、掩码是不是温度计、
//     粘滞标志是不是真的粘滞、两个剪裁标志会不会同时置起）。
//   * TB（`p2_tb.sv` / `p1_tb.sv`）：**与模型逐位比对**的等价性，以及穷举覆盖。
//   两者缺一不可：断言不比对数值，TB 不覆盖"没选到的拍"。
//
// 覆盖映射
//   INV-4a/4b/4c  slice_alloc 的所有权与不重复        -> A1/A2/A3
//   INV-5         DEM 置换是双射                        -> A4
//   契约 §6 A1/A3 温度计掩码与计数一致                  -> A5
//   契约 §4.4     粘滞标志只由 clr/rst 解除             -> A6/A7
//   契约 §4.5     两个剪裁标志互斥                      -> A8
//   契约 §3.5     d==0 必须报 err（不是静默除）         -> A9
//
// 用法：把本文件当**源文件**交给 VCS 即可（VCS 支持 `bind`）：
//   tools/run_rtl_sim.py ... --tb sim/tb/p2_tb.sv sim/tb/p2_sva_bind.sv
// 断言失败会让仿真退出码非零（已加 `-exitstatus`）。
//===========================================================================
`include "rtl_params.vh"

//---------------------------------------------------------------------------
// A1/A2/A3: slice_alloc —— 所有权传递与不重复
//---------------------------------------------------------------------------
module slice_alloc_sva (
    input logic                     clk,
    input logic                     rst_n,
    input logic                     cfg_ready,
    input logic                     sample_en,
    input logic [N_ACTIVE-1:0][4:0] acq_slices,
    input logic [N_ACTIVE-1:0][4:0] conv_slices,
    input logic                     conv_valid
);
  logic dup_acq, dup_conv, overlap;
  integer i, j;

  always_comb begin
    dup_acq = 1'b0;
    dup_conv = 1'b0;
    overlap = 1'b0;
    for (i = 0; i < N_ACTIVE; i = i + 1) begin
      for (j = i + 1; j < N_ACTIVE; j = j + 1) begin
        if (acq_slices[i] == acq_slices[j])  dup_acq  = 1'b1;
        if (conv_slices[i] == conv_slices[j]) dup_conv = 1'b1;
        if (acq_slices[i] == conv_slices[j]) overlap  = 1'b1;
      end
    end
  end

  // INV-4a：所有权传递。**只在 n 真的推进的那一拍之后**断言 ——
  // n 稳定时 acq 与 conv 是**不同组**，逐拍比较会误报（这是本断言唯一的坑）。
  A1_ownership: assert property (@(posedge clk) disable iff (!rst_n)
      (cfg_ready && sample_en) |=> (conv_slices == $past(acq_slices)));

  // INV-4b/4c：同一样本内不重复、且采集组与转换组不相交
  A2_no_dup: assert property (@(posedge clk) disable iff (!rst_n)
      cfg_ready |-> (!dup_acq && !dup_conv));
  A3_disjoint: assert property (@(posedge clk) disable iff (!rst_n)
      cfg_ready |-> !overlap);
endmodule

bind slice_alloc slice_alloc_sva u_sva (
    .clk(clk), .rst_n(rst_n), .cfg_ready(cfg_ready), .sample_en(sample_en),
    .acq_slices(acq_slices), .conv_slices(conv_slices), .conv_valid(conv_valid));

//---------------------------------------------------------------------------
// A4: dem_addr_gen —— 主阵列置换必须是双射（INV-5）
//---------------------------------------------------------------------------
// ⚠️ `dem_addr_gen` 是**纯组合**模块，**没有 clk / rst_n 端口**。
// 所以这里不能用并发属性（`assert property (@(posedge clk) ...)`），
// 只能用 `always_comb` 里的**即时断言** —— 它在组合块重新求值时检查。
module dem_addr_gen_sva (
    input logic [N_UNIT_MAIN-1:0][5:0]     main_logical,
    input logic [N_UNIT_SUB-1:0][2:0]      sub_logical
);
  logic perm_bad;
  integer i, j;

  always_comb begin
    perm_bad = 1'b0;
    for (i = 0; i < N_UNIT_MAIN; i = i + 1) begin
      if (main_logical[i] >= N_UNIT_MAIN) perm_bad = 1'b1;   // 越界 => 一定不是置换
      for (j = i + 1; j < N_UNIT_MAIN; j = j + 1) begin
        if (main_logical[i] == main_logical[j]) perm_bad = 1'b1;
      end
    end
    for (i = 0; i < N_UNIT_SUB; i = i + 1) begin
      if (sub_logical[i] >= N_UNIT_SUB) perm_bad = 1'b1;
      for (j = i + 1; j < N_UNIT_SUB; j = j + 1) begin
        if (sub_logical[i] == sub_logical[j]) perm_bad = 1'b1;
      end
    end
  end

  always_comb begin
    // ⚠️ 必须用**延迟**即时断言 `assert #0`：它在当前时间步的 Observed 区求值，
    // 即**所有 delta 周期都稳定之后**。写成普通 `assert(!perm_bad)` 会在中间态上求值 ——
    // 当目标模块的多个输入在同一时刻被改、而它们经过的**组合链深度不同**时，
    // 会看到"新 main_logical + 旧 sub_logical"这类混合态，产生**假阳性**。
    // 这是本项目实测到的坑：第一次跑 p2_tb 时这条断言命中 7166 行，而 TB 的数值比对全绿。
    assert #0 (!perm_bad)
      else $error("INV-5 violated: main/sub logical map is not a bijection");
  end
endmodule

bind dem_addr_gen dem_addr_gen_sva u_sva (
    .main_logical(main_logical), .sub_logical(sub_logical));

//---------------------------------------------------------------------------
// A5: unit_therm —— 掩码必须是**纯温度计**（popcount == 饱和后的计数）
//---------------------------------------------------------------------------
// 同上：`unit_therm` 也是纯组合模块，用即时断言。
module unit_therm_sva (
    input logic [N_ACTIVE-1:0][6:0]         main_count,
    input logic [N_ACTIVE-1:0][2:0]         sub_count,
    input logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] main_on,
    input logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  sub_on
);
  logic bad;
  int   pc_main, pc_sub;
  integer a, p;

  always_comb begin
    bad = 1'b0;
    for (a = 0; a < N_ACTIVE; a = a + 1) begin
      pc_main = 0;
      pc_sub  = 0;
      for (p = 0; p < N_UNIT_MAIN; p = p + 1) pc_main = pc_main + int'(main_on[a][p]);
      for (p = 0; p < N_UNIT_SUB; p = p + 1)  pc_sub  = pc_sub  + int'(sub_on[a][p]);
      // 计数在 [0, N] 内时必须恰好相等；超出量程时掩码饱和到全 1
      if (main_count[a] <= N_UNIT_MAIN) begin
        if (pc_main != int'(main_count[a])) bad = 1'b1;
      end else if (pc_main != N_UNIT_MAIN) begin
        bad = 1'b1;
      end
      if (sub_count[a] <= N_UNIT_SUB) begin
        if (pc_sub != int'(sub_count[a])) bad = 1'b1;
      end else if (pc_sub != N_UNIT_SUB) begin
        bad = 1'b1;
      end
    end
  end

  always_comb begin
    // 同上：必须用 `assert #0`（Observed 区求值）。普通即时断言会在
    // "计数已更新、掩码还没跟上的那个 delta"上求值 -> 假阳性（实测 7166 行）。
    assert #0 (!bad)
      else $error("A1/A3 violated: mask is not a pure thermometer of the count");
  end
endmodule

bind unit_therm unit_therm_sva u_sva (
    .main_count(main_count), .sub_count(sub_count),
    .main_on(main_on), .sub_on(sub_on));

//---------------------------------------------------------------------------
// A6/A7/A8: recon_core —— 粘滞标志与剪裁互斥
//---------------------------------------------------------------------------
module recon_core_sva (
    input logic clk,
    input logic rst_n,
    input logic clr_ovf,
    input logic cfg_ready,
    input logic acc_ovf,
    input logic gain_err,
    input logic adc2_ovf,
    input logic clip_low,
    input logic clip_high,
    input logic dout_valid
);
  // 契约 §4.4：粘滞标志**只**由 clr_ovf / rst_n 解除。
  // 逐拍更新、新的合法转换、或输出变化都**不得**把它清掉。
  A6_acc_ovf_sticky: assert property (@(posedge clk) disable iff (!rst_n)
      ($past(acc_ovf) && !$past(clr_ovf)) |-> acc_ovf);
  A7_gain_err_sticky: assert property (@(posedge clk) disable iff (!rst_n)
      ($past(gain_err) && !$past(clr_ovf)) |-> gain_err);

  // 契约 §4.5：clip_low 与 clip_high 互斥（一个 word 不可能既 <0 又 >=2^20）。
  // ⚠️ 必须**分开**两条，且都带 `$error` 打出真实值 —— 否则 VCS 的默认文案
  // （`started at X failed at X`）**不含任何关键词**，用 grep 找 "violated"/"Error"
  // 会漏掉它（本项目实测：13 次失败被漏过去，只看退出码才发现不对劲）。
  // 分成两条是为了区分两种成因：X 传播 vs 真的同时为 1。
  A8a_clip_known: assert property (@(posedge clk) disable iff (!rst_n || !cfg_ready)
      !$isunknown({clip_low, clip_high}))
    else $error("A8a: clip flags are X (cfg_ready=%0b cl=%0b ch=%0b dout_valid=%0b)",
                cfg_ready, clip_low, clip_high, dout_valid);
  A8b_clip_exclusive: assert property (@(posedge clk) disable iff (!rst_n || !cfg_ready)
      !(clip_low && clip_high))
    else $error("A8b: clip_low AND clip_high both set (cl=%0b ch=%0b dout_valid=%0b)",
                clip_low, clip_high, dout_valid);
endmodule

bind recon_core recon_core_sva u_sva (
    .clk(clk), .rst_n(rst_n), .clr_ovf(clr_ovf), .cfg_ready(cfg_ready),
    .acc_ovf(acc_ovf), .gain_err(gain_err), .adc2_ovf(adc2_ovf),
    .clip_low(clip_low), .clip_high(clip_high), .dout_valid(dout_valid));

//---------------------------------------------------------------------------
// A9: div_floor —— d == 0 必须报 err，而不是静默给一个商
//---------------------------------------------------------------------------
// ⚠️ 端口宽度必须与被 bind 的实例一致。本仓库里 `div_floor` 只有一种参数化
// （`P_W_D = SUM_BITS = 64`，见 `recon_core` 与 `p2_tb`）；若将来出现别的宽度，
// 这里要改成 `bind ... #(.W_D(<该宽度>))`，否则端口宽度不匹配会报错。
module div_floor_sva #(parameter int W_D = 64) (
    input logic         clk,
    input logic         rst_n,
    input logic         start,
    input logic [W_D-1:0] d,
    input logic         err
);
  // ⚠️ 用**有界**窗口：无界的 `##[1:$]` 是活性属性，仿真里永远不会"完成"，
  // 既不会被判通过也不会被判失败（这是个安静的陷阱）。上界取 200 >
  // N_CYC+2（P_STAGES=7 时约 11 拍），足够覆盖 err 的置起时刻。
  A9_zero_divisor: assert property (@(posedge clk) disable iff (!rst_n)
      (start && (d == '0)) |-> ##[1:200] err);
endmodule

bind div_floor div_floor_sva u_sva (
    .clk(clk), .rst_n(rst_n), .start(start), .d(d), .err(err));
