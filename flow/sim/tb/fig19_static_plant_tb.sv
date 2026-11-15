// 职责：以实际sar20_digital_core和外部Fig19 binding验证连续合法源的有限静态双节点观测窗口。
// 接口：SADC/RDAC采集shift为signed Q32的V/Vfs；分别施加到真实acquiring组的已知输入。
// 采样：negedge phase0读取合格的两节点端口；negedge phase9仅用外部注册mask和独立plant权重算F。
// 观察：RDAC查询字段在posedge前缓存，注册pin在NBA后1ps读取；OBS/OUT记录真实launch/退休。
// 复位：DUT为同步低有效；先完整分期加载与回读再validate，不用cfgclear取消末笔或在途样本。
// 优先级：实际valid/owner决定宏采样；expected_reject仅用于断言，不驱动DUT或生成期望输出字。
// 真值：真实C/Cf和offset只属于私有plant；正常重构只见装载系数，数值退休由独立Python监督检查。
// 窗口：显式程序含N个目标和3个合法postamble；结束时3笔在途，不代表停机、暂停或静止。
// 边界：仅结束测试观测；无噪声、建立/保持、CBIAS、HK、器件非线性或STA证明。
`timescale 1ns/1fs
`include "rtl_params.vh"
module fig19_static_plant_tb #(
  parameter realtime P_TICK_NS  =  0.0  // tick单位ns；必须编译时显式提供正值。
);
  localparam int MAX_CAPTURE = 8192;  // 目标窗口1..8192笔；程序另含3笔显式合法postamble。
  localparam int MAX_PROGRAM = MAX_CAPTURE + 3;
  localparam int MAX_ID = MAX_PROGRAM + 1;
  localparam real Q_SCALE = 4294967296.0;
  logic clk = 0;

  initial begin
    if (P_TICK_NS > 0.0) begin
      forever #(P_TICK_NS/2) clk  =  ~clk;
    end
  end
  logic rst_n = 0, cfg_wr = 0, cfg_validate = 0, cfg_clear_valid = 0;
  logic [15:0] cfg_addr = 0;
  logic [63:0] cfg_wdata = 0;
  wire [63:0] cfg_rdata;
  wire cfg_ready;
  logic source_control_valid = 0;
  logic [CONTROL_WORD_BITS-1:0] source_control_word = 0;
  logic [STATE_BITS-1:0] source_control_state = 0;
  wire source_control_ready, acquisition_control_valid;
  wire [CONTROL_WORD_BITS-1:0] acquisition_control_word;
  wire [STATE_BITS-1:0] acquisition_control_state;
  wire signed [V_BITS-1:0] acquisition_sadc_shift_q, acquisition_rdac_shift_q;
  wire [3:0] phase;
  wire unused_quiet, unused_tp, unused_az, unused_amp, unused_precharge, unused_accurate;
  wire [17:0] acquiring, converting, selected, unused_aux, unused_hold_low;
  wire [1:0] unused_coarse_compare, unused_coarse_acquire;
  wire unused_flash_acquire, unused_fine_acquire, unused_fine_compare, unused_flash_sample;
  wire [1:0][8:0] coarse_trial;
  wire [11:0] fine_trial;
  logic [6:0] flash_therm;
  logic [1:0] coarse_ge;
  logic fine_ge;
  int coarse_target[2] = '{0, 0};
  int fine_target = 2048;
  logic ra_sat = 0, adc2_over = 0;
  wire [N_SLICES-1:0][N_UNIT_MAIN-1:0] main_sw;
  wire [N_SLICES-1:0][N_UNIT_SUB-1:0] sub_sw;
  wire sw_valid, dout_valid, analog_sticky, dout_analog_bad;
  wire [19:0] dout;
  wire [31:0] result_id, status_word;
  wire [4:0] result_flags;
  wire unused_clip_low, unused_clip_high, unused_acc_ovf, obs_valid;
  wire [CAL_OBS_BITS-1:0] obs_data;
  wire [33:0] obs_caaz;
  wire unused_chop, unused_az_capture, unused_az_hold, unused_raw_valid;
  wire conversion_fault;
  wire [91:0] unused_raw_data;
  wire signed [20:0] unused_difference;
  wire unused_difference_valid, unused_pair_fault;
  wire [31:0] unused_first_id, unused_pair_id;
  wire [3:0] unused_pair_fault_code;
  sar20_digital_core #(.P_CAL_OBSERVE(1'b1), .P_CAAZ_ENABLE(1'b0)) dut(
    .clk(clk), .rst_n(rst_n), .source_control_valid(source_control_valid),
    .source_control_word(source_control_word), .source_control_state(source_control_state),
    .source_control_ready(source_control_ready), .acquisition_control_valid(acquisition_control_valid),
    .acquisition_control_word(acquisition_control_word), .acquisition_control_state(acquisition_control_state),
    .acquisition_sadc_shift_q(acquisition_sadc_shift_q), .acquisition_rdac_shift_q(acquisition_rdac_shift_q),
    .flash_therm(flash_therm), .flash_valid(1'b1), .coarse_cmp_valid(2'b11), .coarse_cmp_ge(coarse_ge),
    .fine_cmp_valid(1'b1), .fine_cmp_ge(fine_ge), .analog_phase(phase), .quiet_sample(unused_quiet), .tp_clock(unused_tp),
    .ra_az(unused_az), .ra_amplify(unused_amp), .ref_precharge(unused_precharge), .ref_accurate(unused_accurate),
    .acquiring_mask(acquiring), .converting_mask(converting), .aux_charge_enable(unused_aux), .hold_low_enable(unused_hold_low),
    .coarse_compare_enable(unused_coarse_compare), .coarse_acquire_enable(unused_coarse_acquire),
    .flash_acquire_enable(unused_flash_acquire), .fine_acquire_enable(unused_fine_acquire),
    .coarse_trial(coarse_trial), .fine_trial(fine_trial), .fine_compare_enable(unused_fine_compare), .flash_sample(unused_flash_sample),
    .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata), .cfg_rdata(cfg_rdata),
    .cfg_validate(cfg_validate), .cfg_clear_valid(cfg_clear_valid), .cfg_ready(cfg_ready),
    .ra_sat(ra_sat), .rdac_ovf(1'b0), .adc2_over(adc2_over), .slice_sel(selected), .main_sw(main_sw), .sub_sw(sub_sw),
    .sw_valid(sw_valid), .dout(dout), .dout_valid(dout_valid), .clip_low(unused_clip_low), .clip_high(unused_clip_high),
    .analog_ovf(analog_sticky), .acc_ovf(unused_acc_ovf), .status_word(status_word), .conversion_fault(conversion_fault), .dout_sample_id(result_id),
    .dout_flags(result_flags), .cal_obs_valid(obs_valid), .cal_obs_data(obs_data), .dout_analog_bad(dout_analog_bad),
    .caaz_az_capture_ok(1'b0), .caaz_input_chop_neg(unused_chop), .caaz_az_capture_en(unused_az_capture),
    .caaz_az_hold(unused_az_hold), .cal_obs_caaz_data(obs_caaz), .caaz_raw_valid(unused_raw_valid),
    .caaz_raw_data(unused_raw_data), .caaz_pair_difference(unused_difference),
    .caaz_pair_difference_valid(unused_difference_valid), .caaz_pair_first_sample_id(unused_first_id),
    .caaz_pair_id(unused_pair_id), .caaz_pair_fault(unused_pair_fault), .caaz_pair_fault_code(unused_pair_fault_code));

  always_comb begin
    flash_therm = 7'((1<<(coarse_target[dut.u_structure.bank]>>6))-1);
    for (int b = 0;b<2;b++)coarse_ge[b] = coarse_target[b]>=int'(coarse_trial[b]);
    fine_ge = fine_target>=int'(fine_trial);
  end
  logic [47:0] loaded_weights[18][71];
  logic signed [63:0] loaded_scalars[3];
  real plant_weights[18][71];
  int stimulus_word[MAX_PROGRAM], stimulus_reject[MAX_PROGRAM];
  real stimulus_reference[MAX_PROGRAM];
  int record_by_id[MAX_ID+1], queries_by_id[MAX_ID+1], accepted_by_id[MAX_ID+1];
  bit sampled_by_id[MAX_ID+1], residue_by_id[MAX_ID+1], observed_by_id[MAX_ID+1], retired_by_id[MAX_ID+1];
  real held_rdac[18];
  int held_owner[18];
  bit held_valid[18];
  string weights_path, scalars_path, plant_path, stimulus_path, trace_dir;
  real plant_offset_v, vfs, adc2_min_v, adc2_max_v, ra_clip_v;
  int expected_samples = -1, program_samples = -1, expected_good = -1, adc2_max_closed = -1;
  int controls = 0, samples = 0, residues = 0, rdac_events = 0;
  int observations = 0, outputs = 0, checks = 0;
  bit healthy_capture = 0;
  int acquisition_owner = -1;
  bit pending_residue = 0, pending_ra_bad = 0, pending_adc_bad = 0;
  int pending_fine = 2048, unused_pending_owner = -1;
  int controls_fd = 0, samples_fd = 0, rdac_fd = 0, residues_fd = 0, events_fd = 0;

  task automatic require_ok(input bit condition, input string message);
    checks++;
    if (!condition)begin
      if (controls_fd != 0)$fflush(controls_fd);
      if (samples_fd != 0)$fflush(samples_fd);
      if (rdac_fd != 0)$fflush(rdac_fd);
      if (residues_fd != 0)$fflush(residues_fd);
      if (events_fd != 0)$fflush(events_fd);
      $fatal(1,"%s controls=%0d samples=%0d residues=%0d obs=%0d out=%0d", message, controls, samples, residues, observations, outputs);
    end
  endtask

  function automatic bit finite_real(input real value);
    logic [63:0] unused_representation;
    unused_representation = $realtobits(value);
    return unused_representation[62:52]!=11'h7ff;
  endfunction

  function automatic logic [63:0] parse_hex(input string token, input int max_digits);
    logic [63:0] value;
    int ch, nibble;
    if (!(token.len()>0 && token.len()<=max_digits))$fatal(1,"hex token exceeds declared width");
    value = 0;
    for (int j = 0;j<token.len();j++)begin
      ch = int'(token.getc(j));
      if (ch>=48 && ch<=57)nibble = ch-48;
      else if (ch>=65 && ch<=70)nibble = ch-65+10;
      else if (ch>=97 && ch<=102)nibble = ch-97+10;
      else begin
        $fatal(1,"invalid hex character");
        nibble = 0;
      end
      value = (value<<4)|64'(nibble);
    end
    return value;
  endfunction

  function automatic bit whitespace(input int ch);
    return ch==32 || ch==9 || ch==10 || ch==13 || ch==11 || ch==12;
  endfunction

  function automatic int decimal_word(input string token, input int maximum);
    int value, ch;
    value = 0;
    if (token.len()==0)$fatal(1,"empty decimal token");
    for (int i = 0;i<token.len();i++)begin
      ch = int'(token.getc(i));
      if (ch<48 || ch>57)$fatal(1,"decimal token must contain digits only");
      value = value*10+ch-48;
      if (value>maximum)$fatal(1,"decimal token exceeds explicit range");
    end
    return value;
  endfunction

  function automatic bit real_token_valid(input string token);
    int i, digits, ch;
    i = 0;
    digits = 0;
    if (token.len()==0)return 0;
    if (token.getc(i)==8'd43 || token.getc(i)==8'd45)i++;
    while (i<token.len())begin
      ch = int'(token.getc(i));
      if (ch<48||ch>57)break;
      digits++;
      i++;
    end
    if (i<token.len() && token.getc(i)==8'd46)begin
      i++;
      while (i<token.len())begin
        ch = int'(token.getc(i));
        if (ch<48||ch>57)break;
        digits++;
        i++;
      end
    end
    if (digits==0)return 0;
    if (i<token.len() && (token.getc(i)==8'd69 || token.getc(i)==8'd101))begin
      i++;
      digits = 0;
      if (i<token.len() && (token.getc(i)==8'd43 || token.getc(i)==8'd45))i++;
      while (i<token.len())begin
        ch = int'(token.getc(i));
        if (ch<48||ch>57)break;
        digits++;
        i++;
      end
      if (digits==0)return 0;
    end
    return i==token.len();
  endfunction

  task automatic parse_stimulus_row(input string line, output int word_value, output real reference_value, output int reject_value);
    string fields[3];
    int i, start_pos, count;
    i = 0;
    count = 0;
    while (i<line.len())begin
      while (i<line.len() && whitespace(int'(line.getc(i))))i++;
      if (i>=line.len())break;
      start_pos = i;
      while (i<line.len() && !whitespace(int'(line.getc(i))))i++;
      require_ok(count<3,"extra stimulus field");
      if (count<3)fields[count] = line.substr(start_pos, i-1);
      count++;
    end
    require_ok(count==3,"stimulus row must have exactly three fields");
    word_value = decimal_word(fields[0], 63);
    reject_value = decimal_word(fields[2], 1);
    require_ok(real_token_valid(fields[1]),"invalid decimal reference syntax");
    count = $sscanf(fields[1],"%f", reference_value);
    require_ok(count==1 && finite_real(reference_value),"invalid finite reference token");
  endtask

  function automatic logic signed [63:0] expected_lower_q(input int word_value);
    return ((word_value&16)!=0?64'sd33554432:64'sd0)+
    ((word_value&4)!=0?64'sd9586981:64'sd0)+
    ((word_value&2)!=0?64'sd4793490:64'sd0)+
    ((word_value&1)!=0?64'sd2396745:64'sd0);
  endfunction

  function automatic logic signed [63:0] expected_total_q(input int word_value);
    return expected_lower_q(word_value)+((word_value&32)!=0?64'sd67108864:64'sd0)+
    ((word_value&8)!=0?64'sd16777216:64'sd0);
  endfunction

  function automatic int uniform_coarse(input real value);
    if (value<=-vfs)return 0;
    if (value>=vfs)return 511;
    return int'($floor((value+vfs)*512.0/(2.0*vfs)));
  endfunction

  function automatic int uniform_fine(input real value);
    if (value<=adc2_min_v)return 0;
    if (value>=adc2_max_v)return 4095;
    return int'($floor((value-adc2_min_v)*4096.0/(adc2_max_v-adc2_min_v)));
  endfunction

  task automatic write_cfg(input logic [15:0] address, input logic [63:0] value);
    @(negedge clk);
    cfg_addr = address;
    cfg_wdata = value;
    cfg_wr = 1;
    @(negedge clk);
    cfg_wr = 0;
    #0.001;
    require_ok(cfg_rdata==value,"configuration readback mismatch");
  endtask

  task automatic configure;
    for (int s = 0;s<18;s++)begin
      // 窗口回读为实际行号，不是写入的占位数据。
      @(negedge clk);
      cfg_addr = 16'(32'h2000+s*256);
      cfg_wdata = 0;
      cfg_wr = 1;
      @(negedge clk);
      cfg_wr = 0;
      #0.001;
      require_ok(cfg_rdata==64'(s),"coefficient window readback mismatch");
      for (int u = 0;u<71;u++)write_cfg(16'(u*8), 64'(loaded_weights[s][u]));
    end
    for (int i = 0;i<3;i++)write_cfg(16'(32'h1000+i*8), 64'(loaded_scalars[i]));
    @(negedge clk);
    cfg_validate = 1;
    @(negedge clk);
    cfg_validate = 0;
    #0.001;
    require_ok(cfg_ready,"complete staged configuration rejected");
  endtask
  // 理想宏只在下降沿动作，避免与DUT上升沿捕获竞争。

  initial forever begin : macro_actions
    int owner, index, coarse_code, selected_count;
    logic bank_next;
    real sadc_sample, rdac_sample, f;
    bit on_bit, ra_bad, adc_bad;
    @(negedge clk);
    if (rst_n && cfg_ready)begin
      if (phase==1)begin
        require_ok(controls<program_samples,"finite window exceeded the explicit continuous-source program");
        source_control_valid = 1;
        source_control_state = '0;
        source_control_word = CONTROL_WORD_BITS'(stimulus_word[controls]);
      end
      if (phase==0)begin
        if (pending_residue)begin
          fine_target = pending_fine;
          ra_sat = pending_ra_bad;
          adc2_over = pending_adc_bad;
          pending_residue = 0;
        end
        else begin
          ra_sat = 0;
          adc2_over = 0;
        end
        if (acquisition_control_valid)begin
          owner = int'(dut.u_structure.current_id)+1;
          require_ok(owner>=2 && owner<=MAX_ID && owner==acquisition_owner,"quiet/source owner mismatch");
          index = record_by_id[owner];
          require_ok(index>=0 && index<program_samples,"quiet has no staged source record");
          require_ok(!sampled_by_id[owner],"source sample captured twice");
          require_ok(acquisition_control_word==CONTROL_WORD_BITS'(stimulus_word[index]) && acquisition_control_state==0,
          "actual acquisition metadata differs from accepted source");
          require_ok(acquisition_sadc_shift_q == -expected_lower_q(stimulus_word[index]) &&
          acquisition_rdac_shift_q == -expected_total_q(stimulus_word[index]),"Fig19 two-node output contract mismatch");
          sadc_sample = stimulus_reference[index]+real'($signed(acquisition_sadc_shift_q))/Q_SCALE*vfs;
          rdac_sample = stimulus_reference[index]+real'($signed(acquisition_rdac_shift_q))/Q_SCALE*vfs;
          require_ok(finite_real(sadc_sample)&&finite_real(rdac_sample),"nonfinite ideal sampled value");
          bank_next = ~dut.u_structure.bank;
          coarse_code = uniform_coarse(sadc_sample);
          coarse_target[bank_next] = coarse_code;
          require_ok($countones(acquiring)==8,"actual acquiring pool cardinality");
          for (int s = 0;s<18;s++)if (acquiring[s])begin
            held_rdac[s] = rdac_sample;
            held_owner[s] = owner;
            held_valid[s] = 1;
          end
          sampled_by_id[owner] = 1;
          samples++;
          $fdisplay(samples_fd,"%0d,%0d,%0.17g,%0d,%0d,%0.17g,%0.17g,%05h,%010h", owner, stimulus_word[index], stimulus_reference[index],
          $signed(acquisition_sadc_shift_q), $signed(acquisition_rdac_shift_q), sadc_sample, rdac_sample, acquiring, dut.u_structure.u_pool.acquiring_ids);
        end
      end
      if (phase==9 && dut.u_structure.current_word_valid && dut.u_structure.conv_valid)begin
        owner = int'(dut.u_structure.current_id);
        require_ok(owner>=2 && owner<=MAX_ID,"residue owner out of range");
        index = record_by_id[owner];
        require_ok(index>=0 && sampled_by_id[owner] && !residue_by_id[owner],"residue/source sample ownership mismatch");
        require_ok(selected===converting && $countones(selected)==8,"actual RDAC selection does not belong to conversion");
        require_ok(int'(dut.u_structure.resolved[dut.u_structure.bank])==coarse_target[dut.u_structure.bank],"coarse SAR final differs from ideal macro target");
        f = plant_offset_v;
        selected_count = 0;
        for (int s = 0;s<18;s++)if (selected[s])begin
          selected_count++;
          require_ok(held_valid[s] && held_owner[s]==owner,"physical held charge belongs to another sample");
          for (int u = 0;u<71;u++)begin
            on_bit = (u<63)?main_sw[s][u]:sub_sw[s][u-63];
            f+=plant_weights[s][u]*(held_rdac[s]+vfs*(on_bit?-1.0:1.0));
          end
        end
        require_ok(selected_count==8 && finite_real(f),"invalid ideal residue calculation");
        ra_bad = f < -ra_clip_v || f > ra_clip_v;
        adc_bad = f < adc2_min_v || (adc2_max_closed!=0 ? f > adc2_max_v : f >= adc2_max_v);
        require_ok(!pending_residue,"previous residue was not sampled by ADC2");
        pending_residue = 1;
        pending_fine = uniform_fine(f);
        pending_ra_bad = ra_bad;
        pending_adc_bad = adc_bad;
        unused_pending_owner = owner;
        residue_by_id[owner] = 1;
        residues++;
        $fdisplay(residues_fd,"%0d,%0d,%0d,%0d,%0.17g,%0.17g,%0d,%0d,%05h,%h,%h,%010h,%010h", owner, stimulus_word[index],
        dut.u_structure.resolved[dut.u_structure.bank], $signed(dut.u_structure.u_source_binding.rdac_code),
        stimulus_reference[index], f, pending_fine, ra_bad||adc_bad, selected, main_sw, sub_sw,
        dut.u_structure.u_pool.converting_ids, dut.u_structure.u_pool.acquiring_ids);
      end
    end
    else begin
      source_control_valid = 0;
      ra_sat = 0;
      adc2_over = 0;
    end
  end

  initial forever begin : observe_edge
    bit source_before, query_before, accepted_before, bound_valid_before;
    int source_owner_before, source_word_before;
    real source_reference_before;
    int query_owner, query_index, query_word, query_phase, query_prefix, query_code;
    logic [31:0] bound_error_before;
    logic signed [63:0] common_before;
    @(posedge clk);
    if (rst_n && cfg_ready)begin
      source_before = source_control_ready && source_control_valid;
      query_before = dut.u_structure.query_update;
      if (source_before)begin
        require_ok(controls<program_samples,"extra source handshake");
        acquisition_owner = int'(dut.u_structure.current_id)+2;
        require_ok(acquisition_owner>=2 && acquisition_owner<=MAX_ID && record_by_id[acquisition_owner]<0,"source ID reused");
        require_ok(source_control_word==CONTROL_WORD_BITS'(stimulus_word[controls]),"source bus differs from staged stimulus");
        source_owner_before = acquisition_owner;
        source_word_before = int'(source_control_word);
        source_reference_before = stimulus_reference[controls];
        record_by_id[acquisition_owner] = controls;
        controls++;
      end
      if (obs_valid)begin
        query_owner = int'(dut.u_structure.context_id);
        require_ok(query_owner>=2 && query_owner<=MAX_ID,"OBS owner out of range");
        query_index = record_by_id[query_owner];
        require_ok(query_index>=0 && !observed_by_id[query_owner] && residue_by_id[query_owner],"duplicate/orphan OBS");
        require_ok(query_index<expected_samples,"postamble unexpectedly entered the target OBS window");
        require_ok(stimulus_reject[query_index]==0,"negative control unexpectedly produced OBS");
        if (healthy_capture)
          require_ok(status_word==0 && !conversion_fault,"healthy target OBS has a controller/conversion fault");
        observed_by_id[query_owner] = 1;
        observations++;
        $fdisplay(events_fd,"0,OBS,%0d,,,,%h,%h,%h,%0d,%0d", query_owner, obs_data, obs_caaz, status_word, analog_sticky, conversion_fault);
      end
      if (dout_valid)begin
        query_owner = int'(result_id);
        require_ok(query_owner>=2 && query_owner<=MAX_ID,"OUT owner out of range");
        query_index = record_by_id[query_owner];
        require_ok(query_index>=0 && observed_by_id[query_owner] && !retired_by_id[query_owner],"duplicate/orphan OUT");
        require_ok(query_index<expected_samples,"postamble unexpectedly retired inside the target window");
        require_ok(stimulus_reject[query_index]==0,"negative control unexpectedly retired");
        if (healthy_capture)
          require_ok(status_word==0 && !conversion_fault,"healthy target OUT has a controller/conversion fault");
        retired_by_id[query_owner] = 1;
        outputs++;
        $fdisplay(events_fd,"0,OUT,%0d,%0d,%0d,%0d,,,%h,%0d,%0d", query_owner, dout, result_flags, dout_analog_bad, status_word, analog_sticky, conversion_fault);
      end
      if (query_before)begin
        query_owner = int'(dut.u_structure.current_id);
        require_ok(query_owner>=2 && query_owner<=MAX_ID,"RDAC query owner out of range");
        query_index = record_by_id[query_owner];
        require_ok(query_index>=0 && sampled_by_id[query_owner],"RDAC query has no actual sampled charge");
        query_word = int'(dut.u_structure.current_word);
        query_phase = int'(phase);
        query_prefix = int'(dut.u_structure.resolved[dut.u_structure.bank]);
        query_code = int'($signed(dut.u_structure.u_source_binding.rdac_code));
        accepted_before = dut.u_structure.accepted_update;
        bound_valid_before = dut.u_structure.bound_valid;
        bound_error_before = dut.u_structure.bound_error;
        common_before = dut.u_structure.bound_common_q;
        require_ok(query_word==stimulus_word[query_index],"RDAC query word changed ownership");
        require_ok(queries_by_id[query_owner]<7,"too many actual resolved-prefix loads");
        query_index = queries_by_id[query_owner];
        queries_by_id[query_owner]++;
        rdac_events++;
        if (accepted_before)accepted_by_id[query_owner]++;
      end
      // 所有owner/query输入已在沿前缓存；以下只观察NBA后真实注册端口。
      if (source_before || query_before) #0.001;
      if (source_before)begin
        require_ok(int'(dut.u_structure.current_id)+1==source_owner_before,
          "post-edge acquisition group lost its accepted future sample owner");
        require_ok(acquisition_control_valid && acquisition_control_state==0 &&
          acquisition_control_word==CONTROL_WORD_BITS'(source_word_before),
          "registered acquisition binding differs from accepted source");
        require_ok(acquisition_sadc_shift_q == -expected_lower_q(source_word_before) &&
          acquisition_rdac_shift_q == -expected_total_q(source_word_before),
          "accepted source did not bind the actual two-node shifts");
        require_ok($countones(acquiring)==8,"accepted source has no actual eight-slice acquisition group");
        $fdisplay(controls_fd,"%0d,%0d,%0.17g,%0d,%010h,%0d,%0d,%0d",
          source_owner_before, source_word_before, source_reference_before,
          acquisition_control_word, dut.u_structure.u_pool.acquiring_ids, acquisition_control_valid,
          $signed(acquisition_sadc_shift_q), $signed(acquisition_rdac_shift_q));
      end
      if (query_before)begin
        require_ok(sw_valid===accepted_before,"RDAC acceptance pulse differs from actual query guard");
        $fdisplay(rdac_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%05h,%h,%h", query_owner, query_index, query_phase,
        query_prefix, query_code, query_word, accepted_before, bound_valid_before, bound_error_before, $signed(common_before), selected, main_sw, sub_sw);
      end
    end
  end

  initial begin : staged_inputs_and_run
    int fd, count, reject_count, read_word, read_reject;
    string token, line, unused_tail;
    real read_real;
    logic [63:0] encoded;
    require_ok(P_TICK_NS>0,"P_TICK_NS must be explicitly set");
    require_ok(N_SLICES==18 && N_ACTIVE==8 && N_UNIT_MAIN==63 && N_UNIT_SUB==8 && N_UNIT_TOTAL==71 &&
    ADC2_BITS==12 && CONTROL_WORD_BITS==6 && STATE_BITS==1 && V_BITS==64 && V_FRAC==32 && W_BITS==48,
    "explicit Fig19 9bit/63+8/18/8/12bit/CW6/SW1 profile required");
    if (!$value$plusargs("weights=%s", weights_path)||!$value$plusargs("scalars=%s", scalars_path)||
    !$value$plusargs("plant_weights=%s", plant_path)||!$value$plusargs("plant_offset_v=%f", plant_offset_v)||
    !$value$plusargs("stimulus=%s", stimulus_path)||!$value$plusargs("expected_samples=%d", expected_samples)||
    !$value$plusargs("program_samples=%d", program_samples)||
    !$value$plusargs("expected_good=%d", expected_good)||!$value$plusargs("vfs=%f", vfs)||
    !$value$plusargs("adc2_min_v=%f", adc2_min_v)||!$value$plusargs("adc2_max_v=%f", adc2_max_v)||
    !$value$plusargs("adc2_max_closed=%d", adc2_max_closed)||!$value$plusargs("ra_clip_v=%f", ra_clip_v)||
    !$value$plusargs("trace_dir=%s", trace_dir))$fatal(1,"all Fig19 files/counts/window/endpoint plusargs are required");
    require_ok(expected_samples>0 && expected_samples<=MAX_CAPTURE &&
      program_samples==expected_samples+3 && program_samples<=MAX_PROGRAM &&
      expected_good>0 && expected_good<=expected_samples,"invalid finite capture/program/good counts");
    require_ok(finite_real(vfs)&&vfs==3.0 && finite_real(plant_offset_v) && finite_real(adc2_min_v)&&finite_real(adc2_max_v)&&
    adc2_min_v<adc2_max_v && finite_real(ra_clip_v)&&ra_clip_v>0 && (adc2_max_closed==0||adc2_max_closed==1),"invalid explicit plant/window/endpoint parameters");
    for (int id = 0;id<=MAX_ID;id++)begin
      record_by_id[id] = -1;
      queries_by_id[id] = 0;
      accepted_by_id[id] = 0;
      sampled_by_id[id] = 0;
      residue_by_id[id] = 0;
      observed_by_id[id] = 0;
      retired_by_id[id] = 0;
    end
    for (int s = 0;s<18;s++)begin
      held_rdac[s] = 0;
      held_owner[s] = -1;
      held_valid[s] = 0;
    end
    fd = $fopen(weights_path,"r");
    require_ok(fd!=0,"cannot open staged decoder weights");
    for (int s = 0;s<18;s++)for (int u = 0;u<71;u++)begin
      require_ok($fscanf(fd,"%s", token)==1,"missing decoder weight");
      encoded = parse_hex(token, 12);
      require_ok(encoded>0 && encoded<64'h0000800000000000,"decoder weight outside positive signed48 range");
      loaded_weights[s][u] = encoded[47:0];
    end
    require_ok($fscanf(fd,"%s", token)!=1,"extra decoder weight record");
    $fclose(fd);
    fd = $fopen(scalars_path,"r");
    require_ok(fd!=0,"cannot open staged scalars");
    for (int i = 0;i<3;i++)begin
      require_ok($fscanf(fd,"%s", token)==1,"missing scalar");
      loaded_scalars[i] = $signed(parse_hex(token, 16));
    end
    require_ok(loaded_scalars[1]<loaded_scalars[2],"decoder min/max order invalid");
    require_ok($fscanf(fd,"%s", token)!=1,"extra scalar record");
    $fclose(fd);
    fd = $fopen(plant_path,"r");
    require_ok(fd!=0,"cannot open independent plant weights");
    for (int s = 0;s<18;s++)for (int u = 0;u<71;u++)begin
      count = $fscanf(fd,"%f", read_real);
      require_ok(count==1 && finite_real(read_real)&&read_real>0,"missing/nonfinite/nonpositive plant weight");
      plant_weights[s][u] = read_real;
    end
    require_ok($fscanf(fd,"%s", token)!=1,"extra plant weight record");
    $fclose(fd);
    fd = $fopen(stimulus_path,"r");
    require_ok(fd!=0,"cannot open staged stimulus");
    reject_count = 0;
    for (int n = 0;n<program_samples;n++)begin
      require_ok($fgets(line, fd)>0,"missing stimulus row");
      parse_stimulus_row(line, read_word, read_real, read_reject);
      stimulus_word[n] = read_word;
      stimulus_reference[n] = read_real;
      stimulus_reject[n] = read_reject;
      if (n<expected_samples)reject_count+=read_reject;
      else require_ok(read_reject==0,"explicit postamble must contain three legal records");
    end
    require_ok($fscanf(fd,"%s", token)!=1,"extra stimulus row/field");
    $fclose(fd);
    require_ok(expected_samples-reject_count==expected_good,"staged reject count disagrees with expected_good");
    healthy_capture = reject_count==0;
    controls_fd = $fopen({trace_dir,"/controls.csv"},"w");
    samples_fd = $fopen({trace_dir,"/samples.csv"},"w");
    rdac_fd = $fopen({trace_dir,"/rdac_events.csv"},"w");
    residues_fd = $fopen({trace_dir,"/residues.csv"},"w");
    events_fd = $fopen({trace_dir,"/events.csv"},"w");
    require_ok(controls_fd!=0 && samples_fd!=0 && rdac_fd!=0 && residues_fd!=0 && events_fd!=0,"new existing trace_dir must be writable");
    $fdisplay(controls_fd,"sample_id,word,reference_v,actual_control_word,acquiring_ids,control_valid,sadc_shift_q,rdac_shift_q");
    $fdisplay(samples_fd,"sample_id,word,reference_v,sadc_shift_q,rdac_shift_q,sadc_sample_v,rdac_sample_v,acquiring_mask,acquiring_ids");
    $fdisplay(rdac_fd,"sample_id,event_index,phase,coarse_prefix,rdac_code,word,accepted,bound_valid,bound_error,common_q,selected_mask,main_physical,sub_physical");
    $fdisplay(residues_fd,"sample_id,word,coarse_final,rdac_final,reference_v,ra_v,adc2_code,analog_bad,selected_mask,main_physical,sub_physical,converting_ids,next_acquiring_ids");
    $fdisplay(events_fd,"epoch,kind,sample_id,code,flags,analog_bad,packet_hex,caaz_hex,status_word,analog_sticky,conversion_fault");
    repeat(3)@(negedge clk);
    rst_n = 1;
    configure();
    while (controls<program_samples || samples<expected_samples+2 || residues<expected_samples+2 ||
      rdac_events<7*(expected_samples+2) || observations<expected_good || outputs<expected_good)begin
      @(negedge clk);
      #0.002;
    end
    require_ok(controls==program_samples && samples==expected_samples+2 && residues==expected_samples+2 &&
      rdac_events==7*(expected_samples+2) && observations==expected_good && outputs==expected_good,
      "finite capture source/plant/event/OBS/OUT count mismatch");
    for (int id = 2;id<=MAX_ID;id++)if (record_by_id[id]>=0)begin
      int index;
      index = record_by_id[id];
      require_ok(index<program_samples,"accepted record outside the explicit program");
      if (index<expected_samples)begin
        require_ok(sampled_by_id[id]&&residue_by_id[id]&&queries_by_id[id]==7,"target missing actual sample/residue/seven prefix events");
        if (stimulus_reject[index]==0)require_ok(accepted_by_id[id]==7 && observed_by_id[id]&&retired_by_id[id],"good target missing accepted updates or output");
        else require_ok(accepted_by_id[id]<7 && !observed_by_id[id]&&!retired_by_id[id],"negative target lacked actual intermediate-prefix rejection");
      end
      else begin
        require_ok(!observed_by_id[id]&&!retired_by_id[id],"postamble must remain in flight at window end");
        if (index<expected_samples+2)
          require_ok(sampled_by_id[id]&&residue_by_id[id]&&queries_by_id[id]==7&&accepted_by_id[id]==7,
            "first two postamble records lack actual legal samples/residues/seven updates");
        else require_ok(!sampled_by_id[id]&&!residue_by_id[id]&&queries_by_id[id]==0&&accepted_by_id[id]==0,
          "third postamble must only be accepted, not sampled or converted");
      end
    end
    $fflush(controls_fd);
    $fflush(samples_fd);
    $fflush(rdac_fd);
    $fflush(residues_fd);
    $fflush(events_fd);
    $fclose(controls_fd);
    $fclose(samples_fd);
    $fclose(rdac_fd);
    $fclose(residues_fd);
    $fclose(events_fd);
    $display("FIG19_STATIC_PLANT_COMPLETE samples=%0d good=%0d rejects=%0d residues=%0d observations=%0d outputs=%0d checks=%0d capture_samples=%0d program_samples=%0d source_accepts=%0d postamble_sampled=2 unretired_postamble=3 finite_capture=1",
      samples, expected_good, reject_count, residues, observations, outputs, checks,
      expected_samples, program_samples, controls);
    $finish;
  end

  initial begin
    if (P_TICK_NS > 0.0) begin
      #(P_TICK_NS*((MAX_PROGRAM+16)*16+6000));
      $fatal(1,"Fig19 static plant watchdog");
    end
  end
endmodule
