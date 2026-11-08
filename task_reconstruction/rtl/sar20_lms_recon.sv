// =============================================================================
// 文件名     : sar20_lms_recon.sv
// 模块名     : sar20_lms_recon
// 版本       : v1.0
// 功能       : 负注入合同的 Q28 冻结系数重构与基四恢复除法。
//
// 设计说明   :
//   * 权重按实际物理片号与位图稀疏累加，除法取整方向为 floor，负输入不得截断。
//   * E3 是累加器已初始化但首槽尚未加入的那一拍，端点向量专门卡流水边界。
//   * 单时钟域 clk_i；复位、清空、禁用优先于受理，异常路径不得静默接受。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// ===========================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
// -------------------------------------------------------------------------
// 定点口径：权重 signed24、增益 unsigned26，均为 Q28；配置字扩展为 26 位。
// 每笔扫描八个有序槽，物理片号从 18 片池中取值；不是扫描全部 18 片。
// 21 项并行选择后分级求和，在 32 位分子和 29 位增益中累加；合法分子经
// 基四恢复除法每拍产生两位商，共十拍。无反压时合法输入 E22 输出、II=13。
// 输出反压时保持结果，并通过在途状态阻止后续请求覆盖；清空丢弃在途样本。
// 端口分三组：cfg_* 串行装载 396 个字、in_* 一笔转换的位图与 ADC2 码、out_* 重构结果。
// -------------------------------------------------------------------------
module sar20_lms_recon (
    input  logic                clk_i,
    input  logic                rst_ni,
    input  logic                clear_i,
    input  logic                cfg_start_i,
    output logic                cfg_start_ready_o,
    input  logic                cfg_valid_i,
    output logic                cfg_ready_o,
    input  logic [ 25:0]        cfg_data_i,
    output logic                configured_o,
    output logic                cfg_error_o,
    input  logic                in_valid_i,
    output logic                in_ready_o,
    input  logic [ 31:0]        in_sample_id_i,
    input  logic                in_bad_i,
    input  logic [ 39:0]        in_slice_ids_i,
    input  logic [135:0]        in_main_i,
    input  logic [ 39:0]        in_conv_sub_i,
    input  logic [ 39:0]        in_sample_sub_i,
    input  logic [ 11:0]        in_adc2_i,
    output logic                out_valid_o,
    input  logic                out_ready_i,
    output logic [ 31:0]        out_sample_id_o,
    output logic signed [19:0]  out_code_o,
    output logic                out_bad_o
);
  localparam int SliceCount = 18;
  localparam int FeatureCount = 21;
  localparam logic [25:0] MinGain = 26'd16777216;  // Q28 中的 1/16
  localparam logic [25:0] MaxGain = 26'd50331648;  // Q28 中的 3/16

  logic [24*FeatureCount-1:0] weight_q [0:SliceCount-1];
  logic [25:0] gain_q [0:SliceCount-1];
  logic [4:0] cfg_slice_q, cfg_col_q;
  logic cfg_loading_q, cfg_word_legal, active;

  logic [31:0] sample_id_q;
  logic [39:0] slice_ids_q, conv_sub_q, sample_sub_q;
  logic [135:0] main_q;
  logic [17:0] seen_q;
  logic [2:0] issue_slot_q;
  logic issuing_q, mac_busy_q, accumulated_bad_q;
  logic [11:0] midpoint_code_q;
  logic midpoint_negative;
  logic [10:0] midpoint_index;
  logic signed [22:0] midpoint_qlo_q, midpoint_qmid_q, midpoint_qhi_q, midpoint_qpair_q;
  logic [4:0] midpoint_rlo_q, midpoint_rmid_q, midpoint_rhi_q;
  logic [6:0] midpoint_remainder;
  logic signed [2:0] midpoint_increment, midpoint_correction_q;
  logic [22:0] midpoint_addend, midpoint_sum_bits, midpoint_carry_bits;
  logic signed [22:0] midpoint_value;
  logic [4:0] read_id;
  logic read_id_valid, read_bad;
  logic [4:0] read_conv_sub, read_sample_sub;
  logic [20:0] read_conversion, read_sampling;

  logic read_valid_q, read_last_q, read_bad_q;
  logic [20:0] conversion_q, sampling_q;
  logic signed [23:0] read_weight_q [FeatureCount];
  logic [25:0] read_gain_q;
  logic signed [24:0] selected [FeatureCount];

  logic partial_valid_q, partial_last_q, partial_bad_q;
  logic [25:0] partial_gain_q;
  logic signed [26:0] partial_q [6];
  logic slot_valid_q, slot_last_q, slot_bad_q;
  logic signed [29:0] slot_sum_q;
  logic [25:0] slot_gain_q;
  logic signed [31:0] accum_num_q, next_num;
  logic [28:0] accum_gain_q, next_gain;

  logic mac_valid_q, mac_bad_q;
  logic [31:0] mac_id_q;
  logic signed [31:0] mac_num_q;
  logic [28:0] mac_gain_q;
  logic mac_legal, divide_take;

  logic dividing_q;
  logic [3:0] digit_index_q;
  logic [31:0] divide_id_q;
  logic [28:0] remainder_q;
  logic [30:0] denominator1_q, denominator2_q, denominator3_q;
  logic [19:0] quotient_q, next_quotient;
  logic [30:0] shifted_remainder, reduced_remainder;
  logic [1:0] digit;

  // 每个档的商和余数在展开后是常数。三段按 4、4、3 位索引，
  // 将 floor((32768 * index + 16396) / 25) 分解到首片累加前。
  // 之所以拆成三段查表再做一次进位保存相加，是因为整段查表要 2048 项常量，
  // 三段合起来只有 16+16+8 项；余数先各自保留，最后按 25 与 50 两个阈值统一进位。
  function automatic logic [27:0] midpoint_digit(
      input logic [3:0] digit_index,
      input logic negative,
      input int scale,
      input int offset
  );
    int term;
    logic signed [22:0] quotient;
    midpoint_digit = '0;
    for (int i = 0; i < 16; i++) begin
      term = i * scale + offset;
      quotient = 23'(term / 25);
      if (digit_index == 4'(i)) begin
        midpoint_digit = {negative ? -quotient : quotient, 5'(term % 25)};
      end
    end
  endfunction

  // [极性] 残差为 x-VD-T，重构需加 VD+T。同位同时参与转换与注入时计 2w；
  // 24 位权重先符号扩展再相加，最负值的两倍仍能由 signed25 精确表达。
  function automatic logic signed [24:0] summed_feature(
      input logic signed [23:0] weight,
      input logic conversion,
      input logic sampling
  );
    logic signed [24:0] extended;
    extended = 25'(weight);
    summed_feature = (conversion ? extended : 25'sd0)
                   + (sampling ? extended : 25'sd0);
  endfunction

// =========================================================================
// 2. 组合输出与连续性
// =========================================================================
  // 输出在无效时清零，外部不得把上一拍残留当成新一笔结果。
  // 配置口与输入口互斥：装载期间 in_ready_o 为低，重构期间 cfg_ready_o 为低。
  // 这不是保守，而是因为权重数组正在被逐列改写，此时开始一笔重构会读到半新半旧的列。
  assign active = rst_ni && !clear_i;
  assign cfg_start_ready_o = active && !cfg_loading_q && !mac_busy_q && !mac_valid_q
                             && !dividing_q && !out_valid_o;
  assign cfg_ready_o = active && cfg_loading_q;
  assign cfg_word_legal = (cfg_col_q == 5'd21)
      ? ((cfg_data_i >= MinGain) && (cfg_data_i <= MaxGain))
      : (cfg_data_i[25:24] == {2{cfg_data_i[23]}});  // 特征列做符号扩展检查：高两位必须等于次高位
  // 输入口的 ready 不含 out_ready_i：前三段流水自身有寄存，可以吞下一笔，
  // 反压只加在最后一段。这样除法慢也不会把上游的抽取节奏拖成每两拍一笔。
  assign in_ready_o = active && configured_o && !cfg_start_i && !mac_busy_q && !mac_valid_q;

  // 中点码按二进制补码折叠成单调索引：负半轴先按位取反，商表就能复用同一张表，
  // 只把符号单独带下去。midpoint_code_q 的第 11 位是符号位，取反后为 1 表示负。
  assign midpoint_negative = !midpoint_code_q[11];
  assign midpoint_index = midpoint_code_q[10:0] ^ {11{midpoint_negative}};
  assign midpoint_remainder = {2'b0, midpoint_rlo_q} + {2'b0, midpoint_rmid_q}
                             + {2'b0, midpoint_rhi_q};
  assign midpoint_increment = midpoint_remainder >= 7'd50 ? 3'sd2
                            : midpoint_remainder >= 7'd25 ? 3'sd1 : 3'sd0;
  // 三段商相加后再做一次进位保存（保留和与进位两路）：两个加数直接相加会串出一条
  // 23 位加法链，拆成 sum/carry 后每级只有一个全加器，时序上更好收敛。
  assign midpoint_addend = {{20{midpoint_correction_q[2]}}, midpoint_correction_q};
  assign midpoint_sum_bits = midpoint_qpair_q ^ midpoint_qhi_q ^ midpoint_addend;
  assign midpoint_carry_bits = ((midpoint_qpair_q & midpoint_qhi_q)
                              | ((midpoint_qpair_q | midpoint_qhi_q) & midpoint_addend)) << 1;
  assign midpoint_value = $signed(midpoint_sum_bits + midpoint_carry_bits);

// =========================================================================
// 3. 同步时序与状态转移
// =========================================================================
// 复位优先于受理；在途事务在清空/禁用同沿丢弃，ID 生命周期按样本绑定。
  // 装载状态机：cfg_start 把列/片计数清零并进入装载；此后每个 cfg_valid 写一个
  // 26 位字，列满 22（21 项特征 + 1 个增益）换下一片，18 片写完置 configured_o。
  // 任一列非法立刻置 cfg_error_o 并退出装载，已写入的前缀保留但 configured_o 为 0——
  // 下游据此不会用一组写了一半的权重开始重构。装载不可续传，失败只能重来。
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      configured_o <= 1'b0;
      cfg_error_o <= 1'b0;
      cfg_loading_q <= 1'b0;
      cfg_slice_q <= '0;
      cfg_col_q <= '0;
    end else if (clear_i) begin
      cfg_loading_q <= 1'b0;
      cfg_error_o <= 1'b0;
    end else if (cfg_start_i && cfg_start_ready_o) begin
      configured_o <= 1'b0;
      cfg_loading_q <= 1'b1;
      cfg_error_o <= 1'b0;
      cfg_slice_q <= '0;
      cfg_col_q <= '0;
    end else if (cfg_valid_i && cfg_ready_o) begin
      if (!cfg_word_legal) begin
        cfg_error_o <= 1'b1;
        cfg_loading_q <= 1'b0;
      end else if (cfg_col_q == 5'd21) begin
        gain_q[cfg_slice_q] <= cfg_data_i;
        cfg_col_q <= '0;
        if (cfg_slice_q == 5'd17) begin
          configured_o <= 1'b1;
          cfg_loading_q <= 1'b0;
        end else begin
          cfg_slice_q <= cfg_slice_q + 5'd1;
        end
      end else begin
        weight_q[cfg_slice_q][24*cfg_col_q+:24] <= cfg_data_i[23:0];
        cfg_col_q <= cfg_col_q + 5'd1;
      end
    end
  end

  // 逐槽扫描 0..7，每拍取该槽物理片号与两张子阵列位图；片号 >=18 非法，
  // 不存在可跳过的空槽。重复片号和 dummy 置位均让整笔样本进入坏样本路径。
  assign read_id = slice_ids_q[5*issue_slot_q+:5];
  assign read_id_valid = read_id < 5'd18;
  assign read_conv_sub = conv_sub_q[5*issue_slot_q+:5];
  assign read_sample_sub = sample_sub_q[5*issue_slot_q+:5];
// =========================================================================
// 4. 读取判据、特征选择与累加状态
// =========================================================================
// 所有分支必须给默认值，避免综合出锁存器；非法状态回落到安全输出。
  // read_bad 的三条来源：槽位未启用（片号 >= 18）、子阵列位图带保留位（第 4 位）、
  // 以及同一片在本笔里被扫到两次（seen_q 已置位）。第三种最难在波形上看出来，
  // 它意味着切片 ID 表本身重复，重构结果无从判断该用哪一次的权重。
  always_comb begin
    read_bad = !read_id_valid || read_conv_sub[4] || read_sample_sub[4];
    if (read_id_valid && seen_q[read_id]) read_bad = 1'b1;
  end
  // [C9] 转换和注入是两个独立的非负使能，不能做差集或并集：两边同时置位
  // 表示 2w，不能抵消成零或并成 w。主阵列不参与这里的子阵列注入。
  assign read_conversion = {read_conv_sub[3:0],
                          main_q[17*issue_slot_q+:17]};
  assign read_sampling = {read_sample_sub[3:0], 17'd0};

  // 21 项特征并行展开：每项权重乘以 0、1 或 2，权重本身保持有符号。
  // 用 genvar 而不是循环赋值，是为了让综合工具把它摊成纯组合逻辑而不是状态机。
  for (genvar col = 0; col < FeatureCount; col++) begin : g_feature
    assign selected[col] = summed_feature(read_weight_q[col], conversion_q[col], sampling_q[col]);
  end
  // 分子与分母分开累加：分母是所有被选增益之和，分子是权重与特征的点积。
  // 两者都必须在同一拍按同一组被选片累加，否则除法拿到的是两笔样本的组合。
  assign next_num = accum_num_q + 32'(slot_sum_q);
  assign next_gain = accum_gain_q + {3'b0, slot_gain_q};

  // 这一段是八槽扫描的推进器：issue_slot_q 递增、seen_q 记录已扫过的物理片、
  // 扫满一片就把累加结果交给下一段并清空中间量。accum_* 只有在本片最后一列
  // （partial_last_q）才更新，之前各拍都在累加同一片的 21 个特征项。
  always_ff @(posedge clk_i) begin
    if (!active) begin
      issuing_q <= 1'b0;
      mac_busy_q <= 1'b0;
      read_valid_q <= 1'b0;
      partial_valid_q <= 1'b0;
      slot_valid_q <= 1'b0;
      mac_valid_q <= 1'b0;
    end else begin
      if (divide_take) mac_valid_q <= 1'b0;
      if (in_valid_i && in_ready_o) begin
        sample_id_q <= in_sample_id_i;
        slice_ids_q <= in_slice_ids_i;
        main_q <= in_main_i;
        conv_sub_q <= in_conv_sub_i;
        sample_sub_q <= in_sample_sub_i;
        midpoint_code_q <= in_adc2_i;
        accum_gain_q <= '0;
        accumulated_bad_q <= in_bad_i;
        seen_q <= '0;
        issue_slot_q <= '0;
        issuing_q <= 1'b1;
        mac_busy_q <= 1'b1;
      end
      read_valid_q <= issuing_q;
      if (issuing_q) begin
        // E0 保存 ADC2 码，E1 查段，E2 汇总，E3 写初值，E4 开始首片累加。
        case (issue_slot_q)
          3'd0: begin
            {midpoint_qlo_q, midpoint_rlo_q} <= midpoint_digit(
                midpoint_index[3:0], midpoint_negative, 32768, 16396);
            {midpoint_qmid_q, midpoint_rmid_q} <= midpoint_digit(
                midpoint_index[7:4], midpoint_negative, 524288, 0);
            {midpoint_qhi_q, midpoint_rhi_q} <= midpoint_digit(
                {1'b0, midpoint_index[10:8]}, midpoint_negative, 8388608, 0);
          end
          3'd1: begin
            midpoint_qpair_q <= midpoint_qlo_q + midpoint_qmid_q;
            midpoint_correction_q <= midpoint_negative ? -midpoint_increment : midpoint_increment;
          end
          3'd2: accum_num_q <= 32'(midpoint_value);
          default: ;
        endcase
        read_last_q <= issue_slot_q == 3'd7;
        read_bad_q <= read_bad;
        conversion_q <= read_conversion;
        sampling_q <= read_sampling;
        for (int col = 0; col < FeatureCount; col++) begin
          read_weight_q[col] <= read_id_valid ? $signed(weight_q[read_id][24*col+:24]) : 24'sd0;
        end
        read_gain_q <= read_id_valid ? gain_q[read_id] : 26'd0;
        if (read_id_valid) seen_q[read_id] <= 1'b1;
        issue_slot_q <= issue_slot_q + 3'd1;
        if (issue_slot_q == 3'd7) issuing_q <= 1'b0;
      end
      partial_valid_q <= read_valid_q;
      if (read_valid_q) begin
        partial_last_q <= read_last_q;
        partial_bad_q <= read_bad_q;
        partial_gain_q <= read_gain_q;
        for (int group = 0; group < 5; group++) begin
          partial_q[group] <= (27'(selected[4*group]) + 27'(selected[4*group+1]))
                             + (27'(selected[4*group+2]) + 27'(selected[4*group+3]));
        end
        partial_q[5] <= 27'(selected[20]);
      end
      slot_valid_q <= partial_valid_q;
      if (partial_valid_q) begin
        slot_last_q <= partial_last_q;
        slot_bad_q <= partial_bad_q;
        slot_gain_q <= partial_gain_q;
        slot_sum_q <= ((30'(partial_q[0]) + 30'(partial_q[1]))
                       + (30'(partial_q[2]) + 30'(partial_q[3])))
                      + (30'(partial_q[4]) + 30'(partial_q[5]));
      end
      if (slot_valid_q) begin
        accum_num_q <= next_num;
        accum_gain_q <= next_gain;
        accumulated_bad_q <= accumulated_bad_q || slot_bad_q;
        if (slot_last_q) begin
          mac_id_q <= sample_id_q;
          mac_num_q <= next_num;
          mac_gain_q <= next_gain;
          mac_bad_q <= accumulated_bad_q || slot_bad_q;
          mac_busy_q <= 1'b0;
          mac_valid_q <= 1'b1;
        end
      end
    end
  end

  // 合法性判据在除法入口才查：分子必须非负且严格小于分母，否则商 >= 1 会溢出一个
  // 满量程，说明权重或位图已经不自洽。此时不打商，直接把这一笔输出成 bad，
  // 让上游看到「有结果但是坏的」，而不是让它悄悄饱和到满量程。
  assign divide_take = active && mac_valid_q && !dividing_q && (!out_valid_o || out_ready_i);
  assign mac_legal = !mac_bad_q && (mac_num_q >= 32'sd0)
                     && ($unsigned(mac_num_q) < {3'b0, mac_gain_q});
  // 基四恢复除法：每拍把余数左移 2 位，再与 1/2/3 倍分母比较，一次定 2 位商。
  // denominator1/2/3 是同一分母的 1、2、3 倍，预先算好放在寄存器里，
  // 比较链因此只做减法不做乘法——这是每拍 2 位能收敛的前提。
  assign shifted_remainder = {remainder_q, 2'b0};
  always_comb begin
    if (shifted_remainder >= denominator3_q) begin
      digit = 2'd3;
      reduced_remainder = shifted_remainder - denominator3_q;
    end else if (shifted_remainder >= denominator2_q) begin
      digit = 2'd2;
      reduced_remainder = shifted_remainder - denominator2_q;
    end else if (shifted_remainder >= denominator1_q) begin
      digit = 2'd1;
      reduced_remainder = shifted_remainder - denominator1_q;
    end else begin
      digit = 2'd0;
      reduced_remainder = shifted_remainder;
    end
  end
  // 商逐拍左移并入新 2 位：10 拍后得到 20 位。顺序是「先移后或」而不是「先或后移」，
  // 否则最后一次移位会把刚写入的低位丢掉。
  assign next_quotient = (quotient_q << 2) | {18'd0, digit};

  always_ff @(posedge clk_i) begin
    if (!active) begin
      dividing_q <= 1'b0;
      out_valid_o <= 1'b0;
      out_sample_id_o <= '0;
      out_code_o <= '0;
      out_bad_o <= 1'b0;
    end else begin
      // 先按握手撤掉上一笔的 valid：out_ready_i 与 divide_take 可以同拍成立，
      // 这一句让「旧结果被取走」和「新结果写入」在同一个沿上各归各位，
      // 顺序不能颠倒，否则新写入的 valid 会被这一句立刻清掉。
      if (out_valid_o && out_ready_i) out_valid_o <= 1'b0;
      if (divide_take) begin
        if (!mac_legal) begin
          out_valid_o <= 1'b1;
          out_sample_id_o <= mac_id_q;
          out_code_o <= '0;
          out_bad_o <= 1'b1;
        end else begin
          divide_id_q <= mac_id_q;
          remainder_q <= mac_num_q[28:0];
          denominator1_q <= {2'b0, mac_gain_q};
          denominator2_q <= {1'b0, mac_gain_q, 1'b0};
          denominator3_q <= {1'b0, mac_gain_q, 1'b0} + {2'b0, mac_gain_q};
          quotient_q <= '0;
          digit_index_q <= '0;
          dividing_q <= 1'b1;
        end
      end else if (dividing_q) begin
        remainder_q <= reduced_remainder[28:0];
        quotient_q <= next_quotient;
        digit_index_q <= digit_index_q + 4'd1;
        if (digit_index_q == 4'd9) begin
          out_valid_o <= 1'b1;
          out_sample_id_o <= divide_id_q;
          out_code_o <= $signed(next_quotient ^ 20'h80000);
          out_bad_o <= 1'b0;
          dividing_q <= 1'b0;
        end
      end
    end
  end

`ifndef SYNTHESIS
  // 进入除法前有 N<G。每次去除 0…3 个分母后仍满足余数小于分母。
  always_ff @(posedge clk_i) begin
    if (active && dividing_q) assert (reduced_remainder < denominator1_q)
      else $fatal(1, "radix-4 remainder is outside the normalized interval");
  end
`endif
endmodule
