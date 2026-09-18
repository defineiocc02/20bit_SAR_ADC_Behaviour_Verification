//===========================================================================
// synth/smoke/top_smoke.sv -- 综合通路的 smoke 顶层（仅用于 DC 面积/时序基线）
//===========================================================================
// 为什么需要它
//   rtl/core/ 下的 5 个模块（dem_state_gen / dem_addr_gen / swap_decode /
//   dither_gen / unit_therm）都是**并行子模块**，没有顶层。DC 必须有一个
//   顶层才能 elaborate + compile。
//
// 这个 wrapper 做什么
//   只做端口绑定和常量/计数器驱动，把 5 个模块全部例化起来，并把组合输出
//   打一拍寄存器，好让 DC 报出真实的 reg-to-reg 时序路径：
//
//     dither_gen(flop) --+--> swap_decode --+--> unit_therm --> main_on_q(flop)
//     dem_state_gen(flop)---> dem_addr_gen --+
//     coarse_in(port) -------------------+
//
//   数据通路本身是"故意跑起来"的：tick 计数器给 dem_state_gen 制造 en_a/en_b，
//   让两个 bank 独立推进，得到不断变化的 sid；sid 再经 dem_addr_gen 做地址
//   置换，喂给 unit_therm 当"逻辑位置"。
//
// 明确不做什么
//   * 不验证功能正确性（那是 sim/ 的 TB 的事，且需要 P1 向量）。
//   * 不改动 rtl/core/ 与 rtl/params/ 下任何文件 —— 全部只读引用。
//   * dither_gen 的 dither_code 是 8 位，swap_decode 收 16 位，这里做符号扩展。
//   * unit_therm 的 dither_rail 是 `[2*DITHER_UNITS_RANGE-1:0]` 的**输出**，
//     本 wrapper 不连它（悬空的子模块输出，合法）。本行刻意**不写参数的具体数值**
//     —— 见下面的更正记录。
//     ⚠️ 更正记录（2026-09-18）：本注释原先写"本配置 DITHER_UNITS_RANGE = 0、
//     宽度 [-1:0]、always_comb 循环体一次都不执行、综合时有 undriven 警告"。
//     逐条复核后**四句都是错的**：
//       - 头文件里是 `localparam [3:0] DITHER_UNITS_RANGE = 4'd2;`，宽度 [3:0]；
//       - 该端口是**输出**，悬空不产生 undriven 警告；
//         `synth/artifacts/smoke_10ns/elaborate.log` 里 grep
//         `undriven|not driven` **一条都没有**；
//       - 实测 `unit_therm` 的端口数 1062（`elaborate.log`），与"循环体不执行"
//         不相容。
//     为什么不可能发生"参数漂移导致旧数字失效"：核对时间线 ——
//     `rtl/params/rtl_params.vh` mtime = 10:36:57，本文件 = 11:05:43，
//     第一次 smoke 的 `status.txt` = 11:33:31。所有 smoke 数字都在
//     **现在这份头文件**下产出，"另一版参数"的说法不成立，已从 README 撤回。
//     教训：**注释不要复述参数值**（不会随头文件更新），要用就引用符号本身。
//===========================================================================
`include "rtl_params.vh"

module top_smoke (
    input  logic                                    clk,
    input  logic                                    rst_n,
    input  logic                                    dem_en,
    input  logic                                    bridge_en,
    input  logic                                    load,
    input  logic [8:0]                              coarse_in,
    output logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0]    main_on_q,
    output logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]     sub_on_q,
    output logic                                    rdac_over_q,
    output logic                                    valid_q
);

  // ---- 自由计数：产生 en / init，避免所有输入恒定 -------------------------
  logic [8:0] tick;
  always_ff @(posedge clk) begin
    if (!rst_n) tick <= 9'd0;
    else        tick <= tick + 9'd1;
  end

  // ---- dem_state_gen：两个 bank 独立推进 ---------------------------------
  logic        en_a, en_b;
  logic [8:0]  sid_a, sid_b;
  logic        ld;

  assign en_a = tick[0];
  assign en_b = tick[3];
  assign ld   = load;

  dem_state_gen u_dem_state (
      .clk    (clk),
      .rst_n  (rst_n),
      .dem_en (dem_en),
      .load   (ld),
      .init_a (tick),
      .init_b ({~tick[8], tick[7:0]}),
      .en_a   (en_a),
      .en_b   (en_b),
      .sid_a  (sid_a),
      .sid_b  (sid_b)
  );

  // ---- dem_addr_gen：sid -> 每物理地址的逻辑位置 -------------------------
  logic [N_UNIT_MAIN-1:0][5:0] main_logical;
  logic [N_UNIT_SUB-1:0][2:0]  sub_logical;

  dem_addr_gen u_dem_addr (
      .sid          (sid_a),
      .main_logical (main_logical),
      .sub_logical  (sub_logical)
  );

  // ---- dither_gen：片上随机 dither 占位 ---------------------------------
  logic signed [7:0] dither_code;
  logic              dither_valid;

  dither_gen u_dither (
      .clk         (clk),
      .rst_n       (rst_n),
      .en          (tick[1]),
      .dither_code (dither_code),
      .valid       (dither_valid)
  );

  // ---- swap_decode：coarse + dither -> 每 slice 的 (main,sub) 计数 -------
  logic signed [15:0]             dither_ext;
  logic [N_ACTIVE-1:0][6:0]       main_count;
  logic [N_ACTIVE-1:0][2:0]       sub_count;
  logic                           rdac_over;

  assign dither_ext = {{8{dither_code[7]}}, dither_code};
  assign valid_q    = dither_valid;

  swap_decode u_swap_decode (
      .coarse      (coarse_in),
      .dither_code (dither_ext),
      .dem_en      (dem_en),
      .bridge_en   (bridge_en),
      .sid_low     (sid_b[2:0]),
      .main_count  (main_count),
      .sub_count   (sub_count),
      .rdac_over   (rdac_over)
  );

  // ---- unit_therm：计数 + 逻辑位置 -> 实际开关掩码 ------------------------
  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] main_on;
  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  sub_on;

  unit_therm u_unit_therm (
      .main_logical (main_logical),
      .sub_logical  (sub_logical),
      .main_count   (main_count),
      .sub_count    (sub_count),
      .bank_dither  (dither_code),
      .main_on      (main_on),
      .sub_on       (sub_on),
      .dither_rail  ()                     // 见文件头说明：本配置下未驱动
  );

  // ---- 输出打拍：给出真实的 reg->reg 时序路径 ----------------------------
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      main_on_q   <= '0;
      sub_on_q    <= '0;
      rdac_over_q <= 1'b0;
    end else begin
      main_on_q   <= main_on;
      sub_on_q    <= sub_on;
      rdac_over_q <= rdac_over;
    end
  end

endmodule
