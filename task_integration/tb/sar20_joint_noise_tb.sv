// =============================================================================
// 文件名     : sar20_joint_noise_tb.sv
// 模块名     : sar20_joint_noise_tb
// 版本       : v1.0
// 功能       : 联合噪声回放的集成对拍测试台
//
// 设计说明   :
//   * 噪声归档、训练系数与每组向量由摘要哈希锁定，回放时不重新抽取噪声。
//   * RA 偏移、独立噪声与 AZ 噪声由行为模型在 ADC2 量化前注入。
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
module sar20_joint_noise_tb;
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
    logic signed [19:0] reference_raw;
  } sample_t;

  sample_t current, previous;
  logic previous_chop_neg;  // 实际返回的斩波极性，须与该样本冻结状态一致。
  logic [30:0] previous_az_pair;
  bit observe;
  int trace_file, cycles, checks, adc2_captures, reconstructed, outputs, peak_inflight;
  string coeff_path, vector_path, trace_path;

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
    #1;
    cycles++;
    // 记录沿上真正接收的信号。组合探针只读，不进入被测逻辑。
    if (observe && adc2_valid_i && adc2_ready_o) begin
      $fdisplay(trace_file, "A %0d %0d %0d", cycles, adc2_sample_id_i, adc2_code_i);
      adc2_captures++;
    end
    if (observe && dut.engine_out_valid && dut.engine_out_ready) begin
      $fdisplay(trace_file, "R %0d %0d %0d %0d", cycles, dut.engine_out_id,
                $signed(dut.engine_out_code), dut.engine_out_bad);
      reconstructed++;
    end
    clk_i = 1;
    #1;
    if (observe) begin
      check(!fault_o && !fault_pulse_o && !alignment_fault_o && caaz_fault_code_o == 0 &&
            !adc2_error_o && !cfg_error_o && !seed_error_o, "no unexpected faults during replay");
      if (int'(recon_inflight_o) > peak_inflight) peak_inflight = int'(recon_inflight_o);
      if (out_valid_o) begin
        $fdisplay(trace_file, "O %0d %0d %0d %0d %0d %0d %0d", cycles, out_sample_id_o,
                  out_first_sample_id_o, out_az_pair_id_o, out_pair_o, $signed(out_code_o),
                  $signed(out_difference_o));
        outputs++;
      end
    end
    clk_i = 0;
  endtask

  task automatic initialize();
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
    seed_load_i = 0;
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
    observe = 0;
    step_clock();
    rst_ni = 1;
    #1;
    check(!advance_ready_o && !configured_o, "configuration required before acquisition");
  endtask

  task automatic load_configuration();
    int coeff_file, rc;
    coeff_file = $fopen(coeff_path, "r");
    check(coeff_file != 0 && cfg_start_ready_o, "open frozen coefficient bank");
    cfg_start_i = 1;
    step_clock();
    cfg_start_i = 0;
    cfg_valid_i = 1;
    for (int idx = 0; idx < 396; idx++) begin
      rc = $fscanf(coeff_file, "%h", cfg_data_i);
      check(rc == 1 && cfg_ready_o, "coefficient ready");
      step_clock();
      check(!cfg_error_o, "coefficient accepted");
    end
    cfg_valid_i = 0;
    $fclose(coeff_file);
    check(configured_o && !cfg_ready_o, "complete coefficient bank");
    seed_load_i = 1;
    step_clock();
    seed_load_i = 0;
    repeat (3) step_clock();
    check(!seed_error_o && advance_ready_o, "frozen replay seeds loaded");
  endtask

  task automatic replay();
    int data_file, rc, start_cycle, sample_id;
    data_file = $fopen(vector_path, "r");
    trace_file = $fopen(trace_path, "w");
    check(data_file != 0 && trace_file != 0, "open replay and observation files");
    start_cycle = cycles;
    observe = 1;
    for (int s = 0; s <= Samples; s++) begin
      check(cycles == start_cycle + s * 16 && advance_ready_o, "16-tick dispatch cadence");
      if (s < Samples) begin
        rc = $fscanf(data_file, "%h", current);
        check(rc == 1, "read complete archived sample word");
      end
      advance_i = 1;
      step_clock();
      advance_i = 0;
      step_clock();
      check(slot_accepted_o && !stall_o, "eligible slices selected");
      step_clock();
      sample_id = s - 1;
      if (s > 0) begin
        check(conv_ready_o && conv_sample_id_o == 32'(sample_id) &&
              conv_slice_ids_o == previous.ids && conv_word_o == previous.word &&
              conv_total_eighths_o == previous.word && conv_sample_sub_high_o == previous.sampled,
              "same-ID archived conversion descriptor");
        code_valid_i = 1;
        code_sample_id_i = 32'(sample_id);
        code_i = previous.coarse;
      end else begin
        check(!conv_ready_o, "first acquisition only primes scheduler");
      end
      step_clock();
      code_valid_i = 0;
      check(acq_valid_o && acq_sample_id_o == 32'(s) &&
            acq_chop_neg_o == (caaz_enable_i && 1'(s)) &&
            acq_az_capture_o == (caaz_enable_i && !1'(s)) &&
            acq_az_hold_o == (caaz_enable_i && 1'(s)) &&
            acq_az_pair_id_o == (caaz_enable_i ? 31'(s / 2) : 31'd0), "CAAZ intent follows sample ID");
      if (s < Samples) begin
        check(acq_slice_ids_o == current.ids && acq_word_o == current.word &&
              acq_sample_sub_high_o == current.sampled &&
              acq_lower_eighths_o == {current.word[4], 1'b0, current.word[2:0]},
              "ordered slice IDs and native dither switch masks match archived sample");
      end
      if (s > 0) begin
        check(mask_valid_o && !code_error_o && command_code_o == previous.command_code &&
              main_mask_o == previous.main && sub_mask_o == previous.sub,
              "coarse result and conversion masks match archived sample");
        check(bridge_correction_q32_o ==
              32'((64'(previous.command_code[3:2]) * 64'd4294967296 + 64'd544) / 64'd1088),
              "bridge metadata follows the command");
      end
      repeat (11) step_clock();
      if (s > 0) begin
        adc2_valid_i = 1;
        adc2_sample_id_i = 32'(sample_id);
        adc2_code_i = previous.adc2;
        // 共享组由宏代理按上一笔实际发布的意图回送。独立 AZ 两组用旁路。
        adc2_chop_neg_i = previous_chop_neg;
        adc2_az_pair_id_i = previous_az_pair;
        adc2_az_ok_i = caaz_enable_i;
        #1;
        check(adc2_ready_o && !advance_ready_o, "archived ADC2 accepted on the fixed slot");
      end
      step_clock();
      adc2_valid_i = 0;
      check(advance_ready_o && !conv_ready_o, "ADC2 handoff releases only this conversion");
      previous = current;
      previous_chop_neg = acq_chop_neg_o;
      previous_az_pair = acq_az_pair_id_o;
    end
    repeat (25) step_clock();
    check(recon_inflight_o == 0, "all reconstruction tags retired");
    check(adc2_captures == Samples && reconstructed == Samples &&
          outputs == (caaz_enable_i ? Samples / 2 : Samples), "complete replay counts");
    $fclose(data_file);
    $fclose(trace_file);
  endtask

  initial begin
    if (!$value$plusargs("coeff=%s", coeff_path) || !$value$plusargs("vectors=%s", vector_path) ||
        !$value$plusargs("trace=%s", trace_path) || !$value$plusargs("caaz=%d", caaz_enable_i) ||
        !$value$plusargs("slice_seed=%h", slice_seed_i) || !$value$plusargs("dem_seed=%h", dem_seed_i) ||
        !$value$plusargs("dither_seed=%h", dither_seed_i))
      $fatal(1, "frozen coefficient, seed and replay paths are required");
    initialize();
    load_configuration();
    replay();
    $display("SAR20_JOINT_NOISE_COMPLETE cycles=%0d checks=%0d adc2_captures=%0d reconstructed=%0d outputs=%0d peak_inflight=%0d",
             cycles, checks, adc2_captures, reconstructed, outputs, peak_inflight);
    $finish;
  end
endmodule
