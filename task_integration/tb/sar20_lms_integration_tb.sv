// =============================================================================
// 文件名     : sar20_lms_integration_tb.sv
// 模块名     : sar20_lms_integration_tb
// 版本       : v1.0
// 功能       : 片上 LMS 流水线的集成逐沿对拍测试台
//
// 设计说明   :
//   * two_inflight_tags 与 result_latency_23 共同约束重构流水，stall 不计入成功握手周期。
//   * 复杂常数（4294967296、544、1088）来自 Q32/Q28 缩放与桥补偿公式。
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
// 端口按功能分组，slot 0 在最低位；总线位宽与定点格式见同目录 SPEC。
// -------------------------------------------------------------------------
module sar20_lms_integration_tb;
  logic                clk_i;  // 单域测试时钟，所有受理与结果检查以其上升沿为界。
  logic                rst_ni;  // 同步低有效复位，必须取消在途样本；不能只清最终 valid。
  logic                clear_i;  // 清空在途事务的测试刺激，优先于同沿采样受理。
  logic                enable_i;  // 禁用与重新使能的生命周期刺激，禁止旧描述符复活。
  logic                advance_i;  // 接受一次才推进采集／转换角色，拒绝和反压不得产生新样本。
  logic                advance_ready_o;  // 接受一次才推进采集／转换角色，拒绝和反压不得产生新样本。
  logic         [17:0] eligible_i;  // 模拟释放资格，不等同于数字池空闲；不足八片必须 stall。
  logic                shuffle_i;
  logic                dem_enable_i;  // 该笔是否重分配单元的刺激，必须随采集描述符冻结。
  logic                bridge_enable_i;  // 选择本工程跨段候选映射，不能据此声称完整原芯片 DEM。
  logic                dither_enable_i;  // 该笔扰动开关，关闭应输出零字并保持扰动随机状态。
  logic                caaz_enable_i;  // 选择旁路或非重叠两笔配对，输出计数按模式分别检查。
  logic                seed_load_i;  // 非零随机状态的重载接口，冲突请求不应推进样本或随机流。
  logic         [31:0] slice_seed_i;  // 非零随机状态的重载接口，冲突请求不应推进样本或随机流。
  logic         [31:0] dem_seed_i;  // 非零随机状态的重载接口，冲突请求不应推进样本或随机流。
  logic         [30:0] dither_seed_i;  // 非零随机状态的重载接口，冲突请求不应推进样本或随机流。
  logic                seed_error_o;  // 非零随机状态的重载接口，冲突请求不应推进样本或随机流。
  logic                slot_accepted_o;  // 实际调度握手观测，期望账本只在受理后推进。
  logic                stall_o;  // 候选不足的拒绝观测，不是已接收样本的错误返回。
  logic                acq_valid_o;  // 事务有效位，须与同笔 ID、极性及 AZ 代一起核对。
  logic         [31:0] acq_sample_id_o;  // 逐笔归属标识，跨 ADC2 和重构延迟后必须原样保持。
  logic         [39:0] acq_slice_ids_o;  // 八个有序物理片号，slot 0 在最低位；无序集合不能替代。
  logic          [5:0] acq_word_o;  // 与样本绑定的六位字，不从最近时钟的随机状态重新推导。
  logic          [4:0] acq_lower_eighths_o;  // SADC 低路幅度分子，单位为名义注入档的八分之一。
  logic         [39:0] acq_sample_sub_high_o;  // 逻辑负注入掩码；不等同于已经验证的物理底板接轨。
  logic                acq_chop_neg_o;  // 实际返回的斩波极性，须与该样本冻结状态一致。
  logic                acq_az_capture_o;  // 该笔 RA 时窗的捕获意图，模拟成功确认另行检查。
  logic                acq_az_hold_o;  // 该笔 RA 时窗的保持意图，不以采集时钟位置猜测。
  logic         [30:0] acq_az_pair_id_o;  // 共享 AZ 的代号，必须与两笔真实返回的元数据一致。
  logic                conv_ready_o;  // 旧采集组晋升后的转换描述符有效，不是最终结果已退休。
  logic         [31:0] conv_sample_id_o;  // 逐笔归属标识，跨 ADC2 和重构延迟后必须原样保持。
  logic         [39:0] conv_slice_ids_o;  // 八个有序物理片号，slot 0 在最低位；无序集合不能替代。
  logic          [5:0] conv_word_o;  // 与样本绑定的六位字，不从最近时钟的随机状态重新推导。
  logic          [5:0] conv_total_eighths_o;  // RDAC 总幅度分子，单位为名义注入档的八分之一。
  logic         [39:0] conv_sample_sub_high_o;  // 逻辑负注入掩码；不等同于已经验证的物理底板接轨。
  logic                code_valid_i;  // 事务有效位，须与同笔 ID、极性及 AZ 代一起核对。
  logic          [8:0] code_i;  // 外部 SADC 最终九位粗码，不是已扣 upper 的命令。
  logic         [31:0] code_sample_id_i;  // 粗码所属样本，必须与当前转换组匹配。
  logic                mask_valid_o;  // 最终命令及其全部掩码同时有效，不能单独采信其中一条。
  logic                code_error_o;  // 粗码或归属被拒绝的观测，错误不能发布有效转换掩码。
  logic          [8:0] command_code_o;  // 最终 RDAC 命令，负注入合同按 c-U 检查，不允许回绕。
  logic        [135:0] main_mask_o;  // 同笔八槽主阵列转换位图，按实际物理片号解释。
  logic         [39:0] sub_mask_o;  // 同笔子阵列转换位图，dummy 非零应拒绝。
  logic         [31:0] bridge_correction_q32_o;  // 名义跨段差量，物理权重重构中不可重复叠加。
  logic                out_valid_o;  // 真实结果有效事件；反压时数据与归属应同时保持。
  logic signed  [19:0] out_code_o;  // 最终有符号码，只在对应有效事务中比较。
  logic         [31:0] out_sample_id_o;  // 逐笔归属标识，跨 ADC2 和重构延迟后必须原样保持。
  logic         [31:0] out_first_sample_id_o;  // 同步低有效复位，必须取消在途样本；不能只清最终 valid。
  logic         [30:0] out_az_pair_id_o;  // 共享 AZ 的代号，必须与两笔真实返回的元数据一致。
  logic                out_pair_o;  // 区分单笔旁路与两笔配对输出，计数不可混合。
  logic signed  [20:0] out_difference_o;  // 解调前的完整有符号差值，负奇数的除二方向须另验。
  logic                fault_o;  // 故障保持状态，与故障脉冲及正常输出分别核对。
  logic                fault_pulse_o;  // 新异常的一拍事件，用于与持续故障状态分开计数。
  logic          [2:0] caaz_fault_code_o;  // 异常分类观测，不能仅凭输出未出现推断错误原因。
  logic                alignment_fault_o;  // 故障保持状态，与故障脉冲及正常输出分别核对。
  logic                cfg_start_i;  // 新装载的握手边界，同沿输入的优先级要与合同一致。
  logic                cfg_start_ready_o;  // 新装载的握手边界，同沿输入的优先级要与合同一致。
  logic                cfg_valid_i;  // 配置字提交刺激，不能假定每沿都会被受理。
  logic                cfg_ready_o;  // 配置字真正受理的条件，未握手数据不得计入 396 字。
  logic         [25:0] cfg_data_i;  // 按物理片与列次序装载的 Q28 配置字，不能边算边换系数。
  logic                configured_o;  // 完整配置成功后才允许重构；中断装载不能沿用半套系数。
  logic                cfg_error_o;  // 坏配置字的拒绝状态，不应当作普通输入 bad。
  logic                adc2_valid_i;  // 事务有效位，须与同笔 ID、极性及 AZ 代一起核对。
  logic                adc2_ready_o;  // 同笔 ADC2 返回的受理条件，未握手不得释放转换上下文。
  logic         [31:0] adc2_sample_id_i;  // 逐笔归属标识，跨 ADC2 和重构延迟后必须原样保持。
  logic         [11:0] adc2_code_i;  // 外部 SADC 最终九位粗码，不是已扣 upper 的命令。
  logic                adc2_bad_i;  // 外部 ADC2 过载／无效指示，不能靠数字限幅隐藏。
  logic                adc2_chop_neg_i;  // 实际返回的斩波极性，须与该样本冻结状态一致。
  logic         [30:0] adc2_az_pair_id_i;  // 共享 AZ 的代号，必须与两笔真实返回的元数据一致。
  logic                adc2_az_ok_i;  // 模拟宏对捕获／保持有效性的确认，缺失不得当作成功。
  logic                adc2_error_o;  // ADC2 返回拒绝观测，正常与拒绝事务分别计数。
  logic          [1:0] recon_inflight_o;

  localparam int Samples = 4096;
  typedef struct packed {
    logic [5:0] word;  // 与样本绑定的六位字，不从最近时钟的随机状态重新推导。
    logic [39:0] ids;
    logic [8:0] coarse;
    logic [8:0] command_code;  // 最终 RDAC 命令，负注入合同按 c-U 检查，不允许回绕。
    logic [135:0] main;
    logic [39:0] sub;
    logic [39:0] sampled;
    logic [11:0] adc2;
    logic signed [19:0] golden_code;
  } sample_t;

  sample_t current, previous;
  int expected_code [Samples];
  logic [5:0] expected_word [Samples];
  int accept_cycle [Samples];
  int cycles, checks, adc2_captures, bypass, pairs, peak_inflight;
  int signed_negative_pairs, mixed_dither_pairs, rejects, macro_faults;
  int expected_output_id;
  bit scoreboarding, chopped;

  sar20_digital_lms_top dut (.*);

// =========================================================================
// 2. 断言组与覆盖点
// =========================================================================
// 期望值来自独立参考；计数器只统计覆盖，不参与判错。
  task automatic check(input bit condition, input string message);
    checks++;
    if (!condition) $fatal(1, "cycle=%0d: %s", cycles, message);
  endtask

  task automatic step_clock();
    int id, diff_ref;
    #1;
    clk_i = 1;
    #1;
    clk_i = 0;
    cycles++;
    peak_inflight = (int'(recon_inflight_o) > peak_inflight) ? int'(recon_inflight_o) : peak_inflight;
    if (scoreboarding && out_valid_o) begin
      id = int'(out_sample_id_o);
      check(id == expected_output_id && id < Samples && !fault_o, "exact output order");
      check(cycles - accept_cycle[id] == 23, "ADC2 handoff to CAAZ publication is 23 cycles");
      if (chopped) begin
        diff_ref = expected_code[id - 1] - expected_code[id];
        check(out_pair_o && int'(out_code_o) == (diff_ref >>> 1) &&
              int'(out_difference_o) == diff_ref && out_first_sample_id_o == 32'(id - 1) &&
              out_az_pair_id_o == 31'(id / 2), "pair matches independent calibrated code reference");
        signed_negative_pairs += int'(diff_ref < 0);
        mixed_dither_pairs += int'(expected_word[id] != expected_word[id - 1]);
        pairs++;
        expected_output_id += 2;
      end else begin
        check(!out_pair_o && int'(out_code_o) == expected_code[id] &&
              int'(out_difference_o) == 2 * expected_code[id] && out_az_pair_id_o == 0 &&
              out_first_sample_id_o == 32'(id), "bypass matches independent calibrated code");
        bypass++;
        expected_output_id++;
      end
    end
  endtask

  task automatic reset();
    clk_i = 0;
    rst_ni = 0;
    clear_i = 0;
    enable_i = 1;
    advance_i = 0;
    eligible_i = 18'h3ffff;
    shuffle_i = 1;
    dem_enable_i = 1;
    bridge_enable_i = 1;
    dither_enable_i = 1;
    caaz_enable_i = chopped;
    seed_load_i = 0;
    slice_seed_i = 32'h243f_6a88;
    dem_seed_i = 32'h9e37_79b9;
    dither_seed_i = 31'h5a17_09c3;
    code_valid_i = 0;
    code_i = '0;
    code_sample_id_i = '0;
    cfg_start_i = 0;
    cfg_valid_i = 0;
    cfg_data_i = '0;
    adc2_valid_i = 0;
    adc2_sample_id_i = '0;
    adc2_code_i = '0;
    adc2_bad_i = 0;
    adc2_chop_neg_i = 0;
    adc2_az_pair_id_i = '0;
    adc2_az_ok_i = 0;
    step_clock();
    rst_ni = 1;
    #1;
    check(!configured_o && !advance_ready_o, "unconfigured core cannot acquire");
  endtask

  task automatic load_coeff(input string path);
    int file_handle, rc;
    file_handle = $fopen(path, "r");
    check(file_handle != 0 && cfg_start_ready_o, "read coefficient fixture");
    cfg_start_i = 1;
    step_clock();
    cfg_start_i = 0;
    cfg_valid_i = 1;
    for (int i = 0; i < 396; i++) begin
      rc = $fscanf(file_handle, "%h", cfg_data_i);
      check(rc == 1 && cfg_ready_o, "configuration word available");
      step_clock();
      check(!cfg_error_o, "configuration word accepted");
    end
    cfg_valid_i = 0;
    $fclose(file_handle);
    check(configured_o && !cfg_ready_o && advance_ready_o, "complete coefficient bank");
    seed_load_i = 1;
    step_clock();
    seed_load_i = 0;
    repeat (3) step_clock();
    check(!seed_error_o, "reloaded capture seed");
  endtask

  task automatic begin_slot();
    #1;
    check(advance_ready_o && !fault_o, "16-cycle dispatch admission");
    advance_i = 1;
    step_clock();
    advance_i = 0;
    step_clock();
    check(slot_accepted_o && !stall_o, "schedule response");
    step_clock();
  endtask

  task automatic replay(input string cfg_path, input string data_path, input bit pair_mode);
    int data_handle, rc, start_cycle, raw_id;
    scoreboarding = 0;
    chopped = pair_mode;
    reset();
    load_coeff(cfg_path);
    data_handle = $fopen(data_path, "r");
    check(data_handle != 0, "read sample fixture");
    scoreboarding = 1;
    expected_output_id = chopped ? 1 : 0;
    start_cycle = cycles;
    for (int s = 0; s <= Samples; s++) begin
      check(cycles == start_cycle + s * 16, "one request each 16 ticks");
      if (s < Samples) begin
        rc = $fscanf(data_handle, "%h", current);
        check(rc == 1, "sample fixture record");
        expected_code[s] = int'(current.golden_code);
        expected_word[s] = current.word;
      end
      raw_id = s - 1;
      begin_slot();
      if (s > 0) begin
        check(conv_ready_o && conv_sample_id_o == 32'(raw_id) && conv_slice_ids_o == previous.ids &&
              conv_word_o == previous.word && conv_total_eighths_o == previous.word &&
              conv_sample_sub_high_o == previous.sampled, "conversion descriptor matches fixture");
        code_valid_i = 1;
        code_sample_id_i = 32'(raw_id);
        code_i = previous.coarse;
      end
      step_clock();
      code_valid_i = 0;
      check(acq_valid_o && acq_sample_id_o == 32'(s) &&
            acq_chop_neg_o == (chopped && 1'(s)) && acq_az_hold_o == (chopped && 1'(s)) &&
            acq_az_capture_o == (chopped && !1'(s)) &&
            acq_az_pair_id_o == (chopped ? 31'(s / 2) : 31'd0), "aligned acquisition controls");
      if (s < Samples) begin
        check(acq_slice_ids_o == current.ids && acq_word_o == current.word &&
              acq_sample_sub_high_o == current.sampled &&
              acq_lower_eighths_o == {current.word[4], 1'b0, current.word[2:0]},
              "actual sampled bottom plates and SADC command match fixture");
      end
      if (s > 0) begin
        check(mask_valid_o && !code_error_o && command_code_o == previous.command_code &&
              main_mask_o == previous.main && sub_mask_o == previous.sub,
              "coarse and both conversion masks match actual fixture");
        check(bridge_correction_q32_o ==
              32'((64'(command_code_o[3:2]) * 64'd4294967296 + 64'd544) / 64'd1088),
              "bridge metadata provided while actual masks drive the weights");
      end
      if (s == 2) begin
        adc2_valid_i = 1;
        adc2_sample_id_i = 32'hdeadbeef;
        cfg_start_i = 1;
        #1;
        check(!adc2_ready_o && !cfg_start_ready_o, "reject wrong ID and runtime reconfiguration");
        step_clock();
        check(adc2_error_o && cfg_error_o && configured_o, "reported without disturbing bank");
        adc2_valid_i = 0;
        cfg_start_i = 0;
        rejects += 2;
        repeat (10) step_clock();
      end else begin
        repeat (11) step_clock();
      end
      if (s > 0) begin
        adc2_valid_i = 1;
        adc2_sample_id_i = 32'(raw_id);
        adc2_code_i = previous.adc2;
        adc2_chop_neg_i = chopped && 1'(raw_id);
        adc2_az_pair_id_i = 31'(raw_id / 2);
        adc2_az_ok_i = chopped;
        #1;
        check(adc2_ready_o && !advance_ready_o, "N+15 ADC2 handshake before next slot");
        accept_cycle[raw_id] = cycles + 1;
      end
      step_clock();
      adc2_valid_i = 0;
      if (s > 0) begin
        check(advance_ready_o && !conv_ready_o && !adc2_error_o && !alignment_fault_o,
              "conversion released independently of pending reconstruction");
        adc2_captures++;
      end
      previous = current;
    end
    repeat (25) step_clock();
    check(recon_inflight_o == 0 && !fault_o && !alignment_fault_o &&
          expected_output_id == (chopped ? Samples + 1 : Samples), "all results drained");
    scoreboarding = 0;
    $fclose(data_handle);
  endtask

  task automatic bad_macro_result(input bit late);
    clear_i = 1;
    step_clock();
    clear_i = 0;
    #1;
    check(configured_o && !fault_o && recon_inflight_o == 0, "clear preserves complete coefficients");
    begin_slot();
    step_clock();
    begin_slot();
    check(conv_ready_o && conv_sample_id_o == 0, "bad conversion fixture ID0");
    if (late) begin
      step_clock();
      check(acq_valid_o && acq_sample_id_o == 1, "acquisition published before late failure");
      repeat (4) step_clock();
    end
    adc2_valid_i = 1;
    adc2_bad_i = 1;
    adc2_sample_id_i = 0;
    advance_i = 1;
    #1;
    check(adc2_ready_o && !advance_ready_o, "same-ID macro failure can finish before coarse");
    step_clock();
    adc2_valid_i = 0;
    check(!acq_valid_o && !advance_ready_o && !slot_accepted_o && !fault_o,
          "bad ADC2 handoff suppresses new acquisition before the ordered fault returns");
    repeat (25) begin
      step_clock();
      check(!out_valid_o && !advance_ready_o && !slot_accepted_o && !acq_valid_o,
            "pending macro error freezes further admissions and never publishes a code");
    end
    check(fault_o && caaz_fault_code_o == 3'd4 && !alignment_fault_o &&
          !advance_ready_o && !fault_pulse_o && recon_inflight_o == 0, "ADC2 bad status propagated");
    macro_faults++;
    advance_i = 0;
  endtask

  string bypass_coeff, bypass_data, pair_coeff, pair_data;
  initial begin
    if (!$value$plusargs("bypass_coeff=%s", bypass_coeff) ||
        !$value$plusargs("bypass_data=%s", bypass_data) ||
        !$value$plusargs("pair_coeff=%s", pair_coeff) || !$value$plusargs("pair_data=%s", pair_data))
      $fatal(1, "capture and coefficient paths are required");
    replay(bypass_coeff, bypass_data, 0);
    replay(pair_coeff, pair_data, 1);
    bad_macro_result(0);
    bad_macro_result(1);
    $display("SAR20_LMS_INTEGRATION_COMPLETE cycles=%0d checks=%0d adc2_captures=%0d bypass=%0d pairs=%0d peak_inflight=%0d signed_negative_pairs=%0d mixed_dither_pairs=%0d rejects=%0d macro_faults=%0d",
             cycles, checks, adc2_captures, bypass, pairs, peak_inflight,
             signed_negative_pairs, mixed_dither_pairs, rejects, macro_faults);
    $finish;
  end
endmodule
