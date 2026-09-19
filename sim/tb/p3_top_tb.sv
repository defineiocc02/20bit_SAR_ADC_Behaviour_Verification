//===========================================================================
// p3_top_tb.sv -- 顶层 L3 数值测试（端到端 bit-exact）
//===========================================================================
// 职责
//   只驱动 `sar20_digital_core` 的**顶层端口**，做"寄存器载入 -> cfg_ready ->
//   逐样本驱动 -> 逐样本比对 dout/clip"的端到端验证。中间量（sid、掩码、计数、
//   slice_id、bank）一律不给，全部由 RTL 自己产生 —— 这正是 P2 §12 的 L3 口径。
//   本 TB **不做**任何参考计算：期望值全部来自 sim/vectors/p2_link_expected.hex。
//
// 向量（由 tools/export_rtl_vectors.py 生成）
//   p2_link_weights.hex   slice(2) unit(2) w_q(12)          1278 行
//   p2_recon_coef.hex     offset_q(16) adc2_min_q(16) adc2_max_q(16)   1 行
//   p2_link_stim.hex      bank(1) coarse(3) dither(2) inj_q(16) adc2_code(3)  N 行
//   p2_link_expected.hex  dout(5) clip_low(1) clip_high(1) analog_ovf(1)      N 行
//
// ---- 行对齐的推导（**必须复核**，这是本 TB 最容易被误配的地方）----------
//   ctrl_fsm 在 cfg_ready 拉高的那一拍恰好处于 phase 0（因为 !cfg_ready 时被强制
//   清 0），故设 c = 0 于"cfg_ready 首次读到 1 的那一拍"，则
//       phase(c) = c mod 16
//   （实测佐证：`$display` 打出的捕获节拍是 26, 42, 58, ... 步长 16）
//   * phase 0 在 c = 0 就发 sample_en -> slice_alloc 的 n 在 c=0 末沿由 0 变 1。
//     于是 phase 0..15 属于 **n = 1**，其 recon_start 落在 c = 15。
//   * slice_alloc 的 `conv_valid = (n != 0)`，即 **n = 0 不被转换**。
//     而模型的 sample 0 是有输出的（`p2_link_expected.hex` 第 0 行非空）。
//     所以 **RTL 转换 #k 对应文件第 (k + ROW_OFF) 行**，ROW_OFF = 1，
//     文件第 0 行在顶层没有对应物（登记为已知缺口，不是本 TB 可以修的事）。
//   * dout_valid 出现在 recon_start + NLAT 拍之后。文档与我在 RTL 侧的推导都写
//     NLAT = 12，**VCS 实测是 11**（首个 dout_valid 落在 c = 15 + 11 = 26）。
//     本 TB 以**实测**为准（localparam NLAT = 11），并把这个出入登记在这里。
//
// ---- 两个已知阻塞（详见交付汇报，**已报给 team-lead，未自行改 rtl/**）------
//   [A] **dither 无法注入**。`p2_link_stim.hex` 的 dither 列实测非零
//       （512 行中 384 行非零，取值 {-6,-3,0,3,6}），而 `sar20_digital_core`
//       的端口表里没有 dither 输入：顶层的 `swap_decode.dither_code` 接的是片内
//       `dither_gen`（LFSR）。P1 §0.1 与 P2 §12 都明令"要求 bit-exact 的链路级
//       测试必须把 dither 当激励注入"，P2 §12 的 stim 表也正是为这条设的列。
//       结论：**不加 dither 输入端口，本测试的 dout 逐样本比对必然不成立**。
//       本 TB 仍按契约如实读取 dither 列并**只把它用于分组统计**
//       （dither==0 / dither!=0 两本独立账本），以便定位。
//   [B] ~~`p2_recon_coef.hex` 当前**不由** build_p2_link 输出~~ **已解决**：team-lead
//       已补产该文件（1 行 3 列）。本 TB 现在直接读 sim/vectors/，并额外**回读**
//       0x1000/0x1008/0x1010 三个寄存器，把"文件里的值"与"RTL 里实际生效的值"
//       同时打出来 —— 这条是防止"载入通路错了但没人发现"的最低成本证据。
//
//   [A] 的处置（team-lead 裁决，2026-09-18）：**不在顶层加 dither 端口**（那会动摇
//       已冻结的接口）。改为测试台用**层次化 `force`** 把向量行的 dither 列注入：
//           force u_core.swap_dither_code  = 符号扩展到 16 位的 dither 列;
//           force u_core.sampling_dither_code = 8'sd0;
//       挂这两根线的理由：
//         * vector 用的是 dither_mode='quantizer'，dither 是**码偏移**（k = coarse +
//           dither），作用点正是 `swap_decode.dither_code`；
//         * `unit_therm` 的 dither_rail 实现的是 **sampling** 模式的轨
//           （rail[i] = (i-D) < bank_dither），与 quantizer 模式无关，
//           故把 bank_dither 钉成 0 让轨保持中性（d=0 时 2D 个轨恰好一半为 1）。
//       由 `+injdith` 打开；不加该 plusarg 就是"不注入"的对照跑法（A/B 可比）。
//
//       ⚠️ **必须如实登记的覆盖缺口**：`force` 把"dither 源"整条路径排除在本链路
//       覆盖之外 —— `dither_gen` 是 PCG64 的 RTL 替身，本来就不可能 bit-exact
//       （P1 §0.1），故本测试**不覆盖** dither_gen / unit_therm 的 dither_rail 分支。
//       这不是"全覆盖"。那些模块的验收仍归 P1 的统计判据。
//
// ---- 本 TB 的其它如实登记（不是"全覆盖"）---------------------------------
//   [C] 文件第 0 行在顶层无对应物（见上）。本 TB 只比对第 1..511 行。
//   [D] `p2_link_stim.hex` 的 coarse 只覆盖 **[50, 462]**（实测），coarse<50 或 >462
//       的点没有被向量覆盖。`+sweep` 在 force 状态下打到 coarse=0 时出现
//       `clip_high=1 / dout=1048575`（饱和），而 coarse=64 起曲线恢复成 eff≈coarse-1。
//       该点在向量里没有对照值、且 force 的 `bank_dither=0` 本身是**非物理状态**，
//       故**只登记、不下结论**（未改任何 rtl/ 文件）。
//   [E] `+dbgcfg` 里的 `offset_q` 回读：早期两次运行读到 1（同一 TB 修订下的
//       min/max 恒对），换成逐次回读 + 写监视的诊断版后，**连续 6 次运行全部读到 0**，
//       且写监视器从未捕获到对 `off_r` 的非 0 写入。此条按"未复现的低优先级疑点"
//       登记，**不作为 RTL bug 上报**。
//   [F] `analog_ovf` 的激励来源与**比对口径** —— 两件事都踩过，最终结论如下。
//       (F1) 激励：期望文件头是 `analog_ovf = rdac_over | adc2_over | ra_sat`，三条都是
//            **模拟域量**。其中 `adc2_over = (v < vmin) | (v > vmax)`（**严格外侧**，
//            `v == vmax` 不算，见 `adi_model/adc2.py:95`）**不可能从 `adc2_code` 反推**
//            （码到轨既可能"恰好在轨"也可能是"越轨被夹住"）。故顶层补了
//            `input logic adc2_over`，与 `ra_sat`/`rdac_ovf` 并列都是模拟域回读输入。
//            stim 现为 8 列，第 6/7/8 列分别驱动这三个端口。本 TB 保留**自适应列数**：
//            5 列老格式三端口恒 0，并在日志里明说"analog_ovf 的失配数不具判据意义"。
//       (F2) **口径**：顶层 `analog_ovf` 接的是 `status_regs.analog_ovf_sticky`，是**粘滞位**
//            （ADR 0014：一旦置位只由 clr/rst_n 解除）；而 `p2_link_expected.hex` 第 4 列是
//            **逐样本**量。两者定义不同，**不能逐样本直接比** —— 实测直接比会有 3356 行
//            "不同"，那是口径差、不是缺陷。本 TB 断言的是
//                `analog_ovf` == 期望 analog_ovf 的**累积 OR**（到本样本为止）
//            并把"逐样本口径下会差多少行"当**参考数**打出来（不做断言）。
//            ⚠️ **不能**用每样本发一拍 `cfg_clear_valid` 去清粘滞位：该端口同时接
//            `calib_regs.clear_valid`，会撤掉 `cfg_ready`、整条流水停摆（试过，不能用）。
//       (F3) ADR 0017：三个模拟标志与同一行的数据一起驱动；顶层在相位 14
//            捕获，随接受的转换进入粘滞状态。旧版“下一窗口相位 8 驱动上一行标志”
//            是绕过缺少输入锁存的补丁，现已删除。下面 [G] 保留历史工具问题记录。
//
//   [G] **覆盖率插桩没有改变功能行为（可引用的一手证据）**。带
//       `-cm line+cond+fsm+branch+tgl` 跑同一组激励时：
//         * `p2_tb`      ：checks=1057289 / errors=0，与不带覆盖率**完全一致**；
//         * `p3_top_tb`  ：checks=16386 / errors=202，与不带覆盖率那次**计数相同、
//                          失配集相同**（两次日志里打印的前 30 条失配行号完全相同：
//                          538, 540, 542, 555, 556, 562, 567, 571, 583, 587…，
//                          类型全是 `analog_ovf`）。
//       注意措辞边界：带覆盖率那两次**没有**取 `p3_caps.txt`，所以比对的是
//       "计数 + 日志里的失配行号集合"，不是逐样本 caps 比对 —— 别写成后者。
//       → 结论：`cov.vdb` 是可信的插桩产物；缺的只是"在一台 `urg` 能跑起来的机器上
//         出报告"（本机 `urg` 在 `covdb_get_license() -> vcs_checkout()` 崩溃，
//         `dve` 未安装；登记为工具链缺陷）。
//
//   [H] 上游加列 / 下游读取器没跟上的**静默错位**是多列定宽文本的典型失配源：
//       stim 从 5 列变 8 列时，只按 5 个字段 `$fscanf` 的读取器会把每行剩下的 3 列
//       当成**下一行的开头**，整段错位且**不报任何错**（team-lead 的 p2_tb T9 差点中招）。
//       本 TB 的对策是**先探列数再解析**（`$fgets`+`$sscanf` 数首行 token 数，
//       只认 5/8，其它值直接 `$fatal`），并对比 expected 的行数。改列数的人请同步这里。
//
//   [H2] 配套的可复现证据脚本（都在 sim/artifacts/ 下，只读、不碰 rtl/）：
//       * `run_3col_check.py`  —— 跑前先独立核对向量：① 三列 OR 是否逐行等于
//         expected 第 4 列；② 第 6 列是否逐行等于模型定义 `(k<0)|(k>511)`；
//         然后跑顶层并统计 dout/clip/analog_ovf 的逐样本失配数。
//       * `decide_divfloor.py` —— 决定性实验：复制 rtl 后把 `div_floor` 的商位排列
//         回退成 `qbits[s]`，比顶层 4095 个 dout 变了多少（用于回答"L3 判别力"）。
//       * `mutation_check.py`  —— 对 `p2_periph_tb` 的 5 处定向用例做 7 个变异验证，
//         每个变异都必须让对应断言变红（含 `ws_cap`/`ws_capoff` 对照对）。
//       三者都在 `sim/artifacts/mut/<name>/rtl/` 打**副本**做变异，原 `rtl/` 不动，
//       所以"还原"天然成立、不存在漏还原。
//
// 用法
//   python tools/run_rtl_sim.py --top p3_top_tb --tb sim/tb/p3_top_tb.sv \
//       --src rtl/core rtl/top rtl/params sim/vectors --incdir rtl/params --fresh \
//       --sim-arg "+vdir=sim/vectors" "+injdith"
//   plusargs：
//     +vdir=<dir>   向量目录（默认 sim/vectors）
//     +injdith      用 force 注入向量 dither 列（**bit-exact 判据必须开这个**）
//     +sweep        诊断：不用向量，扫 coarse 画 RTL 的 coarse->dout 传递曲线
//     +dbgcfg       诊断：逐次打印系数回读 / 监视 off_r 的每一次写入
//     +trace=<file> 观测 trace：逐样本落 `sample_idx dout clip analog_ovf`（见 [I]）
//   退出码：任何一处 MISMATCH -> 结束时报 FAIL 并 $fatal(1)。
//
//   [I] **观测 trace（`+trace=<file>`）—— 为论文判据（审计文档 §4）提供"字段级"观测**
//       列（空格分隔，每行一条，行序 = 捕获顺序）：
//         `sample_idx`  RTL 转换序号（十进制），充当论文里"记录的时间字段"
//         `dout`        20 位输出（十六进制）
//         `clip`        `{clip_high, clip_low}` 压成一个十六进制数字（0/1/2/3）
//         `analog_ovf` 粘滞位（0/1）
//       **X/Z 原样落盘**（`%h` 对未知位打印 `x`/`z`），供比较器按"X/Z 计为已观测值"处理。
//       每行都 `$fflush`：即使之后某处 `$fatal` 中止，已落盘的 trace 仍完整。
//       ⚠️ **只在显式给 `+trace` 时**才 fopen / 写文件 / 打印任何东西 —— 不给时该分支
//       完全不执行，故**输出与改动前逐字节一致**。这条"没给 plusarg 就不变"的证明方式
//       与"没给 `+injdith` 就是对照跑法"是同一套纪律。
//===========================================================================
`timescale 1ns/1ps
`include "rtl_params.vh"

module p3_top_tb;

  // recon_start(ph15) -> dout_valid 的延迟。文档与我在 RTL 里的推导都写 12，
  // **实测是 11**（首个 dout_valid 落在 c = 15 + 11 = 26）。以实测为准，已报 team-lead。
  localparam int NLAT    = 11;
  localparam int MAXROW  = 4096;
  localparam int ROW_OFF = 1;      // RTL 转换 #k <-> 文件第 (k + ROW_OFF) 行

  string vdir = "sim/vectors";
  int    errors = 0;
  int    checks = 0;
  int    shown  = 0;

  // ---- 观测 trace（`+trace=<file>`）：论文判据需要的"逐样本可观测字段" -------------
  // 列：`sample_idx dout clip analog_ovf`，其中 `clip` = `{clip_high, clip_low}` 一个 hex 数字。
  // ⚠️ 只在给了 `+trace` 时才 fopen / 写文件 / 打印任何东西 —— 未给时**输出与改动前逐字节
  //    一致**。这是"只加一条 plusarg 分支、不动既有判据与既有输出"的**证明方式**（见模块头 [I]）。
  string trace_path = "";
  bit    trace_en   = 1'b0;
  int    fd_trace   = 0;

  logic clk = 1'b0;
  always #5 clk = ~clk;
  logic rst_n = 1'b0;

  //=========================================================================
  // DUT
  //=========================================================================
  logic        cfg_wr, cfg_validate, cfg_clear_valid, cfg_ready;
  logic [15:0] cfg_addr;
  logic [63:0] cfg_wdata, cfg_rdata;
  logic [B1-1:0]        sadc_code;
  logic                 sadc_rdy;
  logic [ADC2_BITS-1:0] adc2_code;
  logic                 adc2_rdy;
  logic                 ra_sat, rdac_ovf;
  // 契约修正（2026-09-18）：顶层新增 `adc2_over` —— 模型的 `adc2_over = (v<vmin)|(v>vmax)`
  // 是**严格外侧**判据，**不可能从 `adc2_code` 反推**（码到轨既可能"恰好在轨"也可能是
  // "越轨被夹住"），所以它和 `ra_sat`/`rdac_ovf` 一样属模拟域回读。见本文件 [F]。
  logic                 adc2_over;
  logic signed [V_BITS-1:0] inj_q;
  logic [N_SLICES-1:0]                          slice_sel;
  logic [N_SLICES-1:0][N_UNIT_MAIN-1:0]         main_sw;
  logic [N_SLICES-1:0][N_UNIT_SUB-1:0]          sub_sw;
  logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] dither_sw;
  logic        sw_valid;
  logic [OUT_BITS-1:0] dout;
  logic        dout_valid, clip_low, clip_high, analog_ovf, acc_ovf;
  logic [31:0] status_word;

  sar20_digital_core #(.P_STRUCTURAL(0)) u_core (
      .clk (clk), .rst_n (rst_n),
      .cfg_wr (cfg_wr), .cfg_addr (cfg_addr), .cfg_wdata (cfg_wdata),
      .cfg_rdata (cfg_rdata), .cfg_validate (cfg_validate),
      .cfg_clear_valid (cfg_clear_valid), .cfg_ready (cfg_ready),
      .sadc_code (sadc_code), .sadc_rdy (sadc_rdy),
      .adc2_code (adc2_code), .adc2_rdy (adc2_rdy),
      .ra_sat (ra_sat), .rdac_ovf (rdac_ovf), .adc2_over (adc2_over), .inj_q (inj_q),
      .slice_sel (slice_sel), .main_sw (main_sw), .sub_sw (sub_sw),
      .dither_sw (dither_sw), .sw_valid (sw_valid),
      .dout (dout), .dout_valid (dout_valid),
      .clip_low (clip_low), .clip_high (clip_high),
      .analog_ovf (analog_ovf), .acc_ovf (acc_ovf),
      .status_word (status_word)
  ,
      .dout_sample_id(), .dout_flags()
  ,
      .flash_therm('0), .flash_valid('0), .coarse_cmp_valid('0), .coarse_cmp_ge('0), .fine_cmp_valid('0), .fine_cmp_ge('0), .analog_phase(), .quiet_sample(), .tp_clock(), .ra_az(), .ra_amplify(), .ref_precharge(), .ref_accurate(), .acquiring_mask(), .converting_mask(), .aux_charge_enable(), .hold_low_enable(), .coarse_compare_enable(), .coarse_trial(), .quantizer_dither(), .acquisition_dither_rails(), .fine_trial(), .fine_compare_enable(), .coarse_acquire_enable(),.flash_acquire_enable(),.fine_acquire_enable(),.flash_sample()
  );

  //=========================================================================
  // 向量容器
  //=========================================================================
  int                 nrows;
  logic [8:0]         a_bank   [0:MAXROW-1];
  logic [8:0]         a_coarse [0:MAXROW-1];
  logic signed [15:0] a_dither [0:MAXROW-1];
  logic signed [63:0] a_inj    [0:MAXROW-1];
  logic [ADC2_BITS-1:0] a_adc2 [0:MAXROW-1];
  logic               a_rdac  [0:MAXROW-1];   // 新格式 col 6：模型 result.rdac_over
  logic               a_a2ov  [0:MAXROW-1];   // 新格式 col 7：模型 result.adc2_over
  logic               a_rasat [0:MAXROW-1];   // 新格式 col 8：模型 result.ra_sat
  int                 n_stim_col = 0;         // stim 实测列数：5=老格式 / 8=新格式
  logic [OUT_BITS-1:0]  a_edout[0:MAXROW-1];
  logic               a_ecl    [0:MAXROW-1];
  logic               a_ech    [0:MAXROW-1];
  logic               a_ean    [0:MAXROW-1];

  // 捕获缓冲
  logic [OUT_BITS-1:0] cap_dout [0:MAXROW-1];
  logic                cap_cl   [0:MAXROW-1];
  logic                cap_ch   [0:MAXROW-1];
  logic                cap_an   [0:MAXROW-1];
  int                  cap_cyc  [0:MAXROW-1];

  // 独立账本：dither==0 与 dither!=0 分开，便于把"缺 dither 注入"与其它缺陷分开
  int e_all = 0, e_dz = 0, e_dnz = 0;
  int n_dz = 0, n_dnz = 0;
  // analog_ovf 失配账本：按 coarse 分段（<50 / [50,462] / >462），回答"新覆盖的两端"
  int an_bad = 0, an_lo = 0, an_mid = 0, an_hi = 0;
  // 新覆盖两端（coarse<50 与 >462）的行数，用于把"两端是否 bit-exact"说清楚
  int n_lo = 0, n_hi = 0;
  // 模拟域回读三列的 1 计数：全体行 + "期望 analog_ovf=1"的那些行（team-lead 要的定位数）
  int n_rdac_all = 0, n_a2ov_all = 0, n_ra_all = 0;
  int n_rdac_an  = 0, n_a2ov_an  = 0, n_ra_an  = 0;
  int n_an_rows  = 0;                      // 期望 analog_ovf = 1 的行数
  int n_an_rdac  = 0;                      // 其中 rdac_over 独有
  int n_an_a2ov  = 0;                      // 其中 adc2_over 独有
  int n_an_both  = 0;                      // 其中两者同时为真
  int n_an_neither = 0;                    // 其中两者都不为真（只剩 ra_sat 或都不为真）
  logic exp_an_cum_q = 1'b0;               // "期望 analog_ovf"的累积 OR（粘滞口径）
  int   an_ps_diff   = 0;                  // 与"逐样本期望"相比不同的行数（只统计、不断言）

  // dither 注入（+injdith）：用层次化 force 把向量的 dither 列钉到 RTL 内部，
  // 不改 RTL、不改顶层端口表。见模块头的"覆盖缺口"登记。
  bit injdith = 1'b0;
  bit forced  = 1'b0;
  // ⚠️ VCS 不同意在 force 的右值里引用 automatic 变量（Error-[IRFPCA-AUTOVAR]），
  //    所以先拷进这个**模块级静态**变量再 force（踩过一次）。
  logic signed [15:0] dith_fv = 16'sd0;

  task automatic inj_dith(input logic signed [15:0] dv);
    begin
      dith_fv = dv;                                              // 静态副本
      force u_core.swap_dither_code  = {{8{dith_fv[7]}}, dith_fv[7:0]};
      force u_core.sampling_dither_code = 8'sd0;                  // 轨保持中性
      forced = 1'b1;
    end
  endtask

  task automatic inj_release();
    begin
      if (forced) begin
        release u_core.swap_dither_code;
        release u_core.sampling_dither_code;
        forced = 1'b0;
      end
    end
  endtask

  // ---- 临时诊断（+dbgcfg）：追 `off_r` 的每一次变化 --------------------------
  // 起因：系数回读在多次运行里 offset_q 时而是 0、时而是 1（min/max 恒对）。
  // 静态看 `off_r` 只可能被 0x1000 的一次写覆盖成 0，故必须实测到底是谁写了它，
  // 还是 `cfg_read` 的采样本身有问题。只在 `+dbgcfg` 下工作，默认零开销。
  bit dbgcfg = 1'b0;
  initial dbgcfg = $test$plusargs("dbgcfg");
  logic [V_BITS-1:0] off_watch = '0;

  always @(posedge clk) begin
    if (rst_n && dbgcfg && u_core.cal_wr_en && (u_core.cal_sel == 4'd0)) begin
      $display("[DBG-W]  t=%0t off_r <= 0x%016h (cfg_addr=0x%04h cfg_wr=%0b ctrl_seq=%0d)",
               $time, u_core.cfg_wdata[V_BITS-1:0], cfg_addr, cfg_wr, u_core.ctrl_seq);
    end
    if (rst_n && dbgcfg && (u_core.u_calib.off_r !== off_watch)) begin
      $display("[DBG-OFF] t=%0t off_r 0x%016h -> 0x%016h (cfg_addr=0x%04h cfg_wr=%0b cal_wr_en=%0b cal_sel=%0d ctrl_seq=%0d wdata=0x%016h)",
               $time, off_watch, u_core.u_calib.off_r, cfg_addr, cfg_wr,
               u_core.cal_wr_en, u_core.cal_sel, u_core.ctrl_seq, cfg_wdata);
      off_watch = u_core.u_calib.off_r;
    end
  end

  // ---- 运行期粘滞观测（用时钟域累加，不依赖 TB 主循环的采样时序）--------
  // 目的：判断"大面积饱和"是不是由 acc_ovf / gain_err 触发的 —— 这两个都不在
  // analog_ovf 里（契约 §4.6），所以只看 analog_ovf 会漏掉它们。
  int n_rdac_ovf_hi = 0, n_acc_ovf_hi = 0, n_sw_valid_hi = 0;
  always @(posedge clk) begin
    if (rst_n) begin
      if (rdac_ovf) n_rdac_ovf_hi <= n_rdac_ovf_hi + 1;
      if (acc_ovf)  n_acc_ovf_hi  <= n_acc_ovf_hi + 1;
      if (sw_valid) n_sw_valid_hi <= n_sw_valid_hi + 1;
    end
  end

  //=========================================================================
  // 通用工具（读法与 p2_tb.sv 一致：$sscanf 取 token 判 #DATA，不依赖字符位置）
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
    if ($fscanf(fd, "%h", v) != 1) $fatal(1, "p3_top_tb: short read");
  endfunction

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

  task automatic cfg_read(input logic [15:0] a, output logic [63:0] v);
    begin
      @(negedge clk);
      cfg_addr = a;
      cfg_wr   = 1'b0;
      #1;                    // cfg_rdata 是纯组合，等 1ps 让它稳定（TB 用，不进 RTL）
      v = cfg_rdata;
      if (dbgcfg) begin
        $display("[DBG-RD] t=%0t a=0x%04h rdata=0x%016h cfg_addr=0x%04h off_r=0x%016h min_r=0x%016h max_r=0x%016h",
                 $time, a, v, cfg_addr,
                 u_core.u_calib.off_r, u_core.u_calib.min_r, u_core.u_calib.max_r);
      end
    end
  endtask

  //=========================================================================
  // 载入：18 x 71 权重（窗口寄存器 + 数据地址）
  //=========================================================================
  task automatic load_weights(output int n_loaded);
    int          fd, s, u, cur_s;
    logic [63:0] vs, vu, vw;
    fd = open_data("p2_link_weights.hex");
    n_loaded = 0;
    cur_s    = -1;
    while ($fscanf(fd, "%h %h %h", vs, vu, vw) == 3) begin
      s = int'(vs);
      u = int'(vu);
      if (s >= N_SLICES || u >= N_UNIT_TOTAL) begin
        $display("FATAL: weight line out of range s=%0d u=%0d", s, u);
        $fatal(1);
      end
      if (s != cur_s) begin
        cfg_write(16'h2000 + 16'(s * 16'h100), {59'b0, 5'(s)});
        cur_s = s;
      end
      cfg_write(16'(u * 8), vw);
      n_loaded++;
    end
    $fclose(fd);
  endtask

  task automatic load_coef();
    int          fd;
    logic [63:0] vo, vm, vx;
    fd = open_data("p2_recon_coef.hex");
    rd(fd, vo); rd(fd, vm); rd(fd, vx);
    $fclose(fd);
    if (dbgcfg) begin
      $display("[DBG-F] t=%0t 文件读出 vo=0x%016h vm=0x%016h vx=0x%016h", $time, vo, vm, vx);
    end
    cfg_write(16'h1000, vo);          // offset_q
    cfg_write(16'h1008, vm);          // adc2_min_q
    cfg_write(16'h1010, vx);          // adc2_max_q
  endtask

  //=========================================================================
  // 载入：stim / expected
  //=========================================================================
  task automatic load_vectors();
    int          fd;
    string       ln;
    logic [63:0] vb, vc, vd, vi, va, vr, v2, vrs, vdo, vcl, vch, van;
    nrows = 0;
    fd = open_data("p2_link_stim.hex");
    // ---- 先探一行，数出列数：5 = 老格式（无模拟域回读列）/ 8 = 新格式（补了 3 列）----
    // 为什么要探：契约修正后 stim 会补 `rdac_over / adc2_over / ra_sat` 三列，
    // 而两种格式必须都能跑（探索期不能因为向量没更新就编译/加载失败）。
    if ($fgets(ln, fd) == 0) $fatal(1, "p3_top_tb: stim 没有数据行");
    n_stim_col = $sscanf(ln, "%h %h %h %h %h %h %h %h", vb, vc, vd, vi, va, vr, v2, vrs);
    if (n_stim_col != 5 && n_stim_col != 8) begin
      $display("FATAL: stim 列数 = %0d，只认 5 或 8", n_stim_col);
      $fatal(1);
    end
    // 第一行也要按同一口径收下（$fgets 已经把它消费掉了）
    a_bank[0]   = vb[8:0];
    a_coarse[0] = vc[8:0];
    a_dither[0] = $signed(vd[7:0]);
    a_inj[0]    = $signed(vi);
    a_adc2[0]   = va[ADC2_BITS-1:0];
    a_rdac[0]   = (n_stim_col == 8) ? vr[0]  : 1'b0;
    a_a2ov[0]   = (n_stim_col == 8) ? v2[0]  : 1'b0;
    a_rasat[0]  = (n_stim_col == 8) ? vrs[0] : 1'b0;
    nrows = 1;
    while (1'b1) begin
      int nm;
      if (n_stim_col == 8) nm = $fscanf(fd, "%h %h %h %h %h %h %h %h", vb, vc, vd, vi, va, vr, v2, vrs);
      else                 nm = $fscanf(fd, "%h %h %h %h %h", vb, vc, vd, vi, va);
      if (nm != n_stim_col) break;
      if (nrows >= MAXROW) $fatal(1, "p3_top_tb: too many stim rows");
      a_bank[nrows]   = vb[8:0];
      a_coarse[nrows] = vc[8:0];
      // 该列的文件宽度是 **2 个 hex 数字**，符号位在 bit 7。
      // 写成 $signed(vd[15:0]) 会得到 +0xfd = +253 而不是 -3（本 TB 曾犯，已修）。
      a_dither[nrows] = $signed(vd[7:0]);
      a_inj[nrows]    = $signed(vi);
      a_adc2[nrows]   = va[ADC2_BITS-1:0];
      a_rdac[nrows]   = (n_stim_col == 8) ? vr[0]  : 1'b0;
      a_a2ov[nrows]   = (n_stim_col == 8) ? v2[0]  : 1'b0;
      a_rasat[nrows]  = (n_stim_col == 8) ? vrs[0] : 1'b0;
      nrows++;
    end
    $fclose(fd);

    begin
      int ne;
      ne = 0;
      fd = open_data("p2_link_expected.hex");
      while ($fscanf(fd, "%h %h %h %h", vdo, vcl, vch, van) == 4) begin
        if (ne >= nrows) $fatal(1, "p3_top_tb: expected has more rows than stim");
        a_edout[ne] = vdo[OUT_BITS-1:0];
        a_ecl[ne]   = vcl[0];
        a_ech[ne]   = vch[0];
        a_ean[ne]   = van[0];
        ne++;
      end
      $fclose(fd);
      checks++;
      if (ne != nrows) begin
        errors++;
        $display("  MISMATCH row counts: stim=%0d expected=%0d", nrows, ne);
      end
    end
  endtask

  task automatic drive_row(input int r);
    begin
      sadc_code = a_coarse[r];
      adc2_code = a_adc2[r];
      inj_q     = a_inj[r];
      // All fields describe the same transaction. The core captures coarse
      // at phase 8 and fine/injection/analog flags together at phase 14.
      rdac_ovf = a_rdac[r]; adc2_over = a_a2ov[r]; ra_sat = a_rasat[r];
      if (injdith) inj_dith(a_dither[r]);
    end
  endtask

  //=========================================================================
  // 主流程
  //=========================================================================
  int i, k, c, ntest, ld;
  int last_cap_cyc;
  logic [63:0] sw_before;

  initial begin
    if (!$value$plusargs("vdir=%s", vdir)) vdir = "sim/vectors";
    injdith = $test$plusargs("injdith");

    // ---- 观测 trace（可选）：只在显式给 `+trace=<file>` 时才产生任何副作用 ----
    trace_en = $value$plusargs("trace=%s", trace_path);
    if (trace_en) begin
      fd_trace = $fopen(trace_path, "w");
      if (fd_trace == 0) $fatal(1, "p3_top_tb: cannot open trace file %s", trace_path);
      $display("[P3-TRACE] 观测 trace -> %s（列：sample_idx dout clip analog_ovf）", trace_path);
    end

    cfg_wr = 1'b0; cfg_addr = 16'd0; cfg_wdata = 64'd0;
    cfg_validate = 1'b0; cfg_clear_valid = 1'b0;
    sadc_code = 9'd0; sadc_rdy = 1'b1;
    adc2_code = 12'd0; adc2_rdy = 1'b1;
    ra_sat = 1'b0; rdac_ovf = 1'b0; adc2_over = 1'b0; inj_q = 64'sd0;

    $display("p3_top_tb: vdir=%s ROW_OFF=%0d NLAT=%0d injdith=%0b", vdir, ROW_OFF, NLAT, injdith);

    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    load_vectors();
    $display("[P3] 向量载入：stim/expected 各 %0d 行；stim 列数 = %0d（5=无模拟域回读列 / 8=含 rdac_over,adc2_over,ra_sat）",
             nrows, n_stim_col);
    if (n_stim_col == 5) begin
      $display("  ⚠️ 本轮 stim 是 5 列老格式：`rdac_ovf`/`adc2_over`/`ra_sat` **恒钉 0**，");
      $display("     `analog_ovf` 的失配数不具备判据意义（见模块头 [F]）。");
    end

    // ---- 权重与系数（cfg_ready 必须还是 0，否则写会被拒）----
    checks++;
    if (cfg_ready !== 1'b0) begin errors++; $display("  MISMATCH 载入前 cfg_ready 应为 0"); end

    load_weights(ld);
    checks++;
    if (ld != N_SLICES * N_UNIT_TOTAL) begin
      errors++;
      $display("  MISMATCH 权重行数 %0d，期望 %0d", ld, N_SLICES * N_UNIT_TOTAL);
    end
    $display("[P3] 权重载入 %0d 条", ld);

    load_coef();

    // ---- 系数回读：证明"文件里的值"确实进了 RTL（不是只写进去就假定生效）----
    begin
      logic [63:0] r_off, r_min, r_max;
      cfg_read(16'h1000, r_off);
      cfg_read(16'h1008, r_min);
      cfg_read(16'h1010, r_max);
      $display("[P3] 回读系数：offset_q=0x%016h(%0d) adc2_min_q=0x%016h(%0d) adc2_max_q=0x%016h(%0d)",
               r_off, $signed(r_off), r_min, $signed(r_min), r_max, $signed(r_max));
      if (dbgcfg) begin
        $display("[DBG-R] t=%0t cfg_addr=0x%04h cfg_wr=%0b is_calib=%0b is_off=%0b off_r=0x%016h c_off=0x%016h rst_n=%0b",
                 $time, cfg_addr, cfg_wr, u_core.is_calib, u_core.is_off,
                 u_core.u_calib.off_r, u_core.c_off, rst_n);
      end
      checks++;
      if (($signed(r_off) !== 64'sd0) ||
          ($signed(r_min) !== -64'sd53687091) ||
          ($signed(r_max) !== 64'sd590558003)) begin
        errors++;
        $display("  MISMATCH 系数回读与 p2_recon_coef.hex 不符（期望 0 / -53687091 / 590558003）");
      end
    end

    // ---- 控制位：quantizer_dither=1, sampling_mask=0, bridge=1, dem=1 ----
    // 与 tools/export_rtl_vectors.py 的 `_p2_run` 一致：
    //   replace(cfg, dem_enable=True, dither_mode="quantizer", dither_discrete=True)
    // dem_bridge_enable 未被覆盖 => 取 paper_literal 的 True。
    cfg_write(16'h1018, 64'hb);
    repeat (4) @(posedge clk);

    // ---- 载入期不得有写被拒 ----
    sw_before = {32'b0, status_word};
    checks++;
    if (status_word[31:6] !== 26'd0) begin
      errors++;
      $display("  MISMATCH 载入期出现错误码 err_code=%0d（写被拒）", status_word[31:6]);
    end

    // ---- 生效 ----
    @(negedge clk); cfg_validate = 1'b1;
    @(negedge clk); cfg_validate = 1'b0;
    repeat (2) @(posedge clk);
    checks++;
    if (cfg_ready !== 1'b1) begin errors++; $display("  MISMATCH validate 后 cfg_ready 未拉高"); end
    $display("[P3] cfg_ready=%0d", cfg_ready);

    //=========================================================================
    // 逐样本跑：c = 0 定义为 cfg_ready 首次读到 1 的那一拍
    //=========================================================================
    @(negedge clk);
    while (cfg_ready !== 1'b1) @(negedge clk);

    //=========================================================================
    // 诊断模式 `+sweep=1`：不用向量，直接扫 coarse，打印 RTL 的 coarse -> dout
    // 传递曲线。模型侧的铁律是 dout ~= coarse/512 * 2^20（见 analyze 的 eff_exp 列），
    // 所以这条曲线是否单调、斜率是否 ~1，能直接区分"对齐问题"与"映射本身错了"。
    // 只在确实要诊断时用；正常判据仍是下面的逐样本比对。
    //=========================================================================
    if ($test$plusargs("sweep")) begin
      int sweep[0:8];
      int si, rep;
      sweep[0] = 0;   sweep[1] = 64;  sweep[2] = 128; sweep[3] = 192;
      sweep[4] = 256; sweep[5] = 320; sweep[6] = 384; sweep[7] = 448; sweep[8] = 511;
      adc2_code = 12'd0;
      inj_q     = 64'sd0;
      // 注入模式下把 dither 钉成 0，让曲线是"coarse 的单值函数"；
      // 不注入时用的是片内 LFSR dither（曲线会带一点序列依赖）。
      if (injdith) inj_dith(16'sd0);
      $display("[P3-SWEEP] 恒定 adc2_code=0 inj_q=0 dither=%0s，每个 coarse 跑 3 次转换",
               injdith ? "注入0" : "片内LFSR");
      $display("  si coarse  i  c      dout    clip_low clip_high  eff=dout*512/2^20   <- coarse = 捕获时刻实际驱动的 sadc_code");
      k = 0;
      // 相位表（ctrl_fsm §7）：rdac_load = 相位 11，recon_start = 相位 15，
      // dout_valid 落在 recon_start + 11 = 相位 10。
      // 于是"capture@相位10 的重构用的是**上一个窗口**相位11 的 rdac_load"——
      // 换 coarse 只有在**捕获后立刻**做才赶得上本窗口的 rdac_load；
      // 放到相位 0 去换会整整慢一个窗口（该窗口 rdac_load 仍是旧值）。踩过两次。
      sadc_code = sweep[0][8:0];
      for (si = 0; si < 9; si = si + 1) begin
        for (rep = 0; rep < 3; rep = rep + 1) begin
          c = 0;
          while (1'b1) begin
            if (dout_valid) begin
              // ⚠️ 打的是**捕获时刻的 `sadc_code`**，不是循环变量 —— 循环变量会因为
              //    "换值/捕获"的先后关系错位一行，第一版就被这个坑误导过。
              $display("  %0d %0d      %0d  %0d  %0d   %0b        %0b        %0d",
                       si, sadc_code, k, c, dout, clip_low, clip_high,
                       (dout * 512) >> 20);
              k = k + 1;
              break;
            end
            @(posedge clk);
            c = c + 1;
            if (c > 64) $fatal(1, "p3_top_tb: sweep 模式等 dout_valid 超时");
          end
          // rep 0/1 保持同一 coarse；rep==2 时在**捕获的当拍**就换成下一组
          if (rep == 2) sadc_code = sweep[(si < 8) ? (si + 1) : 8][8:0];
          @(posedge clk);   // 离开本拍的 dout_valid 脉冲，否则下一轮会重复捕获同一拍
        end
      end
      $display("[P3-SWEEP] 共捕获 %0d 次；acc_ovf=%0b status_word=0x%08h", k, acc_ovf, status_word);
      inj_release();
      $finish;
    end

    // 此刻正处在 c = 0 这一拍
    ntest = nrows - ROW_OFF;
    if (ntest <= 0) $fatal(1, "p3_top_tb: 行数不足以覆盖 ROW_OFF");

    c = 0;
    k = 0;                       // 已捕获的转换数
    drive_row(ROW_OFF);          // 转换 #0 对应文件第 ROW_OFF 行
    last_cap_cyc = -1;

    while (1'b1) begin
      // ---- 采样本拍（c 拍）----
      if (dout_valid) begin
        cap_dout[k] = dout;
        cap_cl[k]   = clip_low;
        cap_ch[k]   = clip_high;
        cap_an[k]   = analog_ovf;
        cap_cyc[k]  = c;
        // ---- 观测 trace：逐样本一行；`sample_idx` 用本样本的 k（充当论文的"时间字段"）----
        // 每行都 $fflush：即使后面某处 $fatal 中止，已落盘的 trace 也完整（不自欺）。
        if (trace_en) begin
          $fwrite(fd_trace, "%0d %0h %0h %0h\n", k, dout, {clip_high, clip_low}, analog_ovf);
          $fflush(fd_trace);
        end
        k++;
      end
      @(posedge clk);
      c = c + 1;
      // ---- 每 16 拍是一个新样本的相位 0：换上下一行的**数据通路**激励 ----
      if ((c % 16) == 0) begin
        if ((ROW_OFF + (c / 16)) < nrows) drive_row(ROW_OFF + (c / 16));
      end
      if (k >= ntest) break;
      if (c > (16 * ntest + 200)) $fatal(1, "p3_top_tb: dout_valid 数量不足，超时");
    end

    $display("[P3] 捕获 %0d 次 dout_valid；首拍 c=%0d，末拍 c=%0d（应为 %0d, %0d；步长 16）",
             k, cap_cyc[0], cap_cyc[k-1], 15 + NLAT, 15 + NLAT + 16 * (ntest - 1));

    // 相位口径自检：偏移必须恒为 (15+NLAT) + 16*i
    checks++;
    if (cap_cyc[0] != (15 + NLAT)) begin
      errors++;
      $display("  MISMATCH 首个 dout_valid 落在 c=%0d，期望 %0d（相位口径不符）", cap_cyc[0], 15 + NLAT);
    end
    for (i = 1; i < k; i = i + 1) begin
      checks++;
      if (cap_cyc[i] != cap_cyc[i-1] + 16) begin
        errors++;
        if (shown < 30) begin
          shown++;
          $display("  MISMATCH dout_valid 节拍不齐：i=%0d c=%0d（上一拍 %0d）", i, cap_cyc[i], cap_cyc[i-1]);
        end
      end
    end

    //=========================================================================
    // 逐样本比对
    //=========================================================================
    // 明细 dump：即使比对不通过也要留下全量数据，供离线对齐分析（不要只留前 30 条）。
    begin
      int fd_dump;
      fd_dump = $fopen("p3_caps.txt", "w");
      if (fd_dump == 0) $fatal(1, "p3_top_tb: cannot open p3_caps.txt");
      $fwrite(
          fd_dump,
          "// i c dout clip_low clip_high analog_ovf | file_row coarse dither bank adc2 inj_q rdac_over adc2_over ra_sat exp_dout exp_cl exp_ch exp_an\n");
      $fclose(fd_dump);
    end

    for (i = 0; i < k; i = i + 1) begin
      int          r;
      logic        is_dz;
      int          fd_dump;
      logic        exp_an_cum;
      r = ROW_OFF + i;

      // ---- `analog_ovf` 的正确口径：**粘滞位**，可比的是"期望的累积 OR" ----
      // 顶层的 `analog_ovf` 接的是 status_regs 的 analog_ovf_sticky（ADR 0014：一旦置位
      // 只由 clr/rst_n 解除），而 expected 第 4 列是**逐样本**量。两者定义不同，
      // 所以逐样本断言只能拿"到本样本为止的期望累积 OR"来比。
      // （不能每样本发 cfg_clear_valid 去清：那个端口同时接 calib_regs.clear_valid，
      //   会把 cfg_ready 撤掉、整条流水停摆 —— 试过，不能这么用。）
      if (a_ean[r] === 1'b1) exp_an_cum_q = 1'b1;
      exp_an_cum = exp_an_cum_q;

      is_dz = (a_dither[r] == 16'sd0);
      if (is_dz) n_dz++; else n_dnz++;
      if (a_coarse[r] < 9'd50) n_lo++; else if (a_coarse[r] > 9'd462) n_hi++;

      // ---- 模拟域回读三列的分布（不猜，用向量里的真实值数）----
      if (a_rdac[r])  n_rdac_all++;
      if (a_a2ov[r])  n_a2ov_all++;
      if (a_rasat[r]) n_ra_all++;
      if (a_ean[r]) begin
        n_an_rows++;
        if (a_rdac[r])  n_rdac_an++;
        if (a_a2ov[r])  n_a2ov_an++;
        if (a_rasat[r]) n_ra_an++;
        if (a_rdac[r] && a_a2ov[r])            n_an_both++;
        else if (a_rdac[r] && !a_a2ov[r])      n_an_rdac++;
        else if (!a_rdac[r] && a_a2ov[r])      n_an_a2ov++;
        else                                   n_an_neither++;
      end

      fd_dump = $fopen("p3_caps.txt", "a");
      $fwrite(fd_dump, "%0d %0d %0d %0d %0d %0d | %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
              i, cap_cyc[i], cap_dout[i], cap_cl[i], cap_ch[i], cap_an[i],
              r, a_coarse[r], a_dither[r], a_bank[r], a_adc2[r], a_inj[r],
              a_rdac[r], a_a2ov[r], a_rasat[r],
              a_edout[r], a_ecl[r], a_ech[r], a_ean[r]);
      $fclose(fd_dump);

      checks++;
      if (cap_dout[i] !== a_edout[r]) begin
        errors++;
        if (is_dz) e_dz++; else e_dnz++;
        e_all++;
        if (shown < 30) begin
          shown++;
          $display("  MISMATCH 行 %0d （RTL 转换 #%0d, c=%0d）dout: got=%0d exp=%0d (coarse=%0d dither=%0d bank=%0d)",
                   r, i, cap_cyc[i], cap_dout[i], a_edout[r],
                   a_coarse[r], a_dither[r], a_bank[r]);
        end
      end

      checks++;
      if ((cap_cl[i] !== a_ecl[r]) || (cap_ch[i] !== a_ech[r])) begin
        errors++;
        if (shown < 30) begin
          shown++;
          $display("  MISMATCH 行 %0d clip: got(%0d,%0d) exp(%0d,%0d)",
                   r, cap_cl[i], cap_ch[i], a_ecl[r], a_ech[r]);
        end
      end

      // ---- analog_ovf：按**粘滞口径**断言（与期望的累积 OR 比，理由见循环上方注释）----
      checks++;
      if (cap_an[i] !== exp_an_cum) begin
        errors++;
        an_bad++;
        if (a_coarse[r] < 9'd50) an_lo++;
        else if (a_coarse[r] > 9'd462) an_hi++;
        else an_mid++;
        // 这两处：analog_ovf 的期望值来自模型的**模拟侧**（rdac_over | adc2_over | ra_sat），
        // 由 stim 第 6/7/8 列分别驱动三个顶层输入端口。见模块头 [F]。
        if (shown < 30) begin
          shown++;
          $display("  MISMATCH 行 %0d analog_ovf(粘滞口径): got=%0d exp_cum_or=%0d | 逐样本期望=%0d | coarse=%0d dither=%0d bank=%0d inj_q=%0d adc2_code=%0d dout=%0d exp_dout=%0d",
                   r, cap_an[i], exp_an_cum, a_ean[r], a_coarse[r], a_dither[r], a_bank[r],
                   a_inj[r], a_adc2[r], cap_dout[i], a_edout[r]);
        end
      end
      // 只为把"两种口径的差别"说清楚而统计、**不做断言**：与**逐样本**期望比有多少行不同
      if (cap_an[i] !== a_ean[r]) an_ps_diff++;
    end

    //=========================================================================
    // 汇总
    //=========================================================================
    $display("==================================================");
    $display("  p3_top  summary: checks=%0d errors=%0d", checks, errors);
    $display("  dout 比对：共 %0d 行（RTL 转换 #0..#%0d <=> 文件第 %0d..#%0d 行）",
             k, k - 1, ROW_OFF, ROW_OFF + k - 1);
    $display("    dither==0 行：%0d 行，其中 %0d 行 dout 不符", n_dz, e_dz);
    $display("    dither!=0 行：%0d 行，其中 %0d 行 dout 不符", n_dnz, e_dnz);
    // 新覆盖两端（这次向量的 coarse 覆盖 [0,511] 全域）：把"两端是否 bit-exact"说清楚
    $display("    新覆盖两端：coarse<50 共 %0d 行、coarse>462 共 %0d 行；这两段里 dout/clip 的失配数 = %0d",
             n_lo, n_hi, e_all);
    $display("    analog_ovf 失配（**粘滞口径**：与期望的累积 OR 比）：%0d 行（coarse<50 里 %0d 行 / [50,462] 里 %0d 行 / >462 里 %0d 行）",
             an_bad, an_lo, an_mid, an_hi);
    $display("    参考：若改拿**逐样本**期望去比，会有 %0d 行不同 —— 这不是失配，是口径不同（顶层 analog_ovf 是粘滞位）", an_ps_diff);
    // ---- 模拟域回读三列：全体分布 + 在"期望 analog_ovf=1"行里的归属（真实值数出来的）----
    $display("    模拟域回读列（本轮 stim %0d 列）：全体行里 rdac_over=%0d / adc2_over=%0d / ra_sat=%0d",
             n_stim_col, n_rdac_all, n_a2ov_all, n_ra_all);
    $display("    期望 analog_ovf=1 的行共 %0d 行，其归属：两者同时=%0d / 仅 rdac_over=%0d / 仅 adc2_over=%0d / 都不是=%0d（则应为 ra_sat）",
             n_an_rows, n_an_both, n_an_rdac, n_an_a2ov, n_an_neither);
    $display("    这 %0d 行里三列各自的 1 计数：rdac_over=%0d / adc2_over=%0d / ra_sat=%0d",
             n_an_rows, n_rdac_an, n_a2ov_an, n_ra_an);
    $display("    文件第 0 行在顶层无对应物（RTL 的 conv_valid = n>=1 跳过 n=0）");
    // ---- 溢出/粘滞诊断（决定"饱和"是不是 acc_ovf / gain_err 引起的）----
    $display("  rdac_ovf 高电平拍数=%0d  acc_ovf 高电平拍数=%0d  sw_valid 高电平拍数=%0d",
             n_rdac_ovf_hi, n_acc_ovf_hi, n_sw_valid_hi);
    // status_word 的位序（status_regs.sv）：{err_code[25:0], analog_ovf, clip_high,
    // clip_low, adc2_ovf, gain_err, acc_ovf} —— MSB 先拼接，所以 bit0 = acc_ovf。
    $display("  status_word=0x%08h -> err_code=%0d acc_ovf(b0)=%0b gain_err(b1)=%0b adc2_ovf(b2)=%0b clip_low(b3)=%0b clip_high(b4)=%0b analog_ovf(b5)=%0b",
             status_word, status_word[31:6],
             status_word[0], status_word[1], status_word[2],
             status_word[3], status_word[4], status_word[5]);
    $display("  acc_ovf 端口终值=%0b（粘滞，一旦置位不会自清）", acc_ovf);
    $display("  明细 dump -> p3_caps.txt");
    $display("  dither 注入：injdith=%0b。1=force swap_decode.dither_code 取向量列、unit_therm.bank_dither 钉 0；0=不注入的对照跑法。注意 dither_gen / dither_rail 不在本测试覆盖内。", injdith);
    $display("==================================================");
    inj_release();
    // ---- 观测 trace 收尾（仅在开了 `+trace` 时才有输出；未开时零副作用）----
    if (trace_en) begin
      $fflush(fd_trace);
      if (k == ntest) $fwrite(fd_trace, "# P3_TRACE_COMPLETE rows=%0d\n", k);
      $fclose(fd_trace);
      $display("[P3-TRACE] trace 关闭：共 %0d 行 -> %s", k, trace_path);
    end
    if (errors == 0) begin
      $display("P3 TOP RESULT: PASS");
    end else begin
      $display("P3 TOP RESULT: FAIL (%0d errors)", errors);
      $fatal(1, "p3_top_tb FAILED: %0d mismatch", errors);
    end
    $finish;
  end

  // 兜底超时（run_rtl_sim.py 的超时是第二道防线）
  initial begin
    #800000;
    $fatal(1, "p3_top_tb TIMEOUT");
  end

endmodule
