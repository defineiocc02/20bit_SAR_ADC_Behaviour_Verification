// 模块：slice_pool_ctrl；因果8-of-18采集/转换所有权调度。
// 职责：上一采集组晋升转换，从其10个ID补集中选新8个采集slice，保留2个空闲。
// 端口/单位：acquiring/converting为物理ID与18位mask；sample_id为32位采样编号。
// 时钟/复位：clk上升沿、同步低有效rst_n；disable同步清历史、恢复初始采集IDs0..7。
// 启动：首advance只priming，conversion_valid读取旧primed=0；后续advance才有效。
// 同沿规则：转换读取旧采集组，next_ids使用旧cursor；PRNG更新只影响下一次扫描起点。
// 调度：shuffle时8步LFSR更新游标，byte252..255保留旧游标；关闭shuffle后游标固定0。
// 边界：循环扫描保持8/8/2及组内唯一/组间互斥，不是所有8-of-18子集的均匀随机抽样。
// 这里的slice是采样/RDAC电容组，A/B bank是粗SAR逻辑轮换，不能理解成18颗完整ADC。
// shuffle=0仍执行补集采集，连续两笔转换组必不相交；关闭随机游标不等于锁定同一组。
// 生产结构顶层把shuffle接到同一个DEM配置位；本叶不提供独立的单元DEM配置。
// acquisition_primed暴露真实数字分配历史，不是模拟建立完成或采样精度证明。
// 来源：项目因果slice池合同；随机策略为工程选择，不声明复刻未披露硅实现。
module slice_pool_ctrl #(parameter logic [31:0] P_SEED = 32'h6d2b79f5) (
    input wire clk, rst_n, enable, advance, shuffle,
    output logic [7:0][4:0] acquiring_ids, converting_ids,
    output logic [17:0] acquiring_mask, converting_mask,
    output logic conversion_valid,
    output logic [31:0] sample_id,
    output wire acquisition_primed
);
  logic primed;
  assign acquisition_primed = primed;
  logic [31:0] random_state, random_next;
  initial if (P_SEED == 0) $fatal(1, "slice_pool_ctrl: zero PRNG seed");

  // Eight LFSR steps per allocation; zero is prohibited as an absorbing state.
  always_comb begin
    random_next = random_state;
    for (int k = 0; k < 8; k++)
      random_next = {1'b0, random_next[31:1]} ^ (random_next[0] ? 32'h80200003 : 32'd0);
  end

  logic [4:0] cursor;
  logic [7:0][4:0] next_ids;
  logic [17:0] next_mask;
  integer count, candidate;

  // 选组发生在转换前一周期的采集阶段。排除旧acquiring后尚余10组，
  // 按游标顺序取前8组；因此既有因果约束，也有扫描偏置，不是任取8组的均匀抽签。
  always_comb begin
    next_ids = '0;
    next_mask = '0;
    count = 0;
    candidate = 0;
    for (int k = 0; k < 18; k++) begin
      candidate = int'(cursor) + k;
      if (candidate >= 18) candidate -= 18;
      if (!acquiring_mask[candidate] && count < 8) begin
        next_ids[count] = 5'(candidate);
        next_mask[candidate] = 1;
        count++;
      end
    end
  end

  always_ff @(posedge clk) begin
    // reset/disable优先于advance。sample_id也计入首笔无效priming分配，
    // 所以上层首个有效ID为2；它不是“成功输出个数”，缺比较器valid也不回退ID。
    if (!rst_n || !enable) begin
      for (int a = 0; a < 8; a++) begin
        acquiring_ids[a] <= 5'(a);
        converting_ids[a] <= '0;
      end
      acquiring_mask <= 18'h000ff;
      converting_mask <= '0;
      conversion_valid <= 0;
      random_state <= P_SEED;
      cursor <= 5'd8;
      primed <= 0;
      sample_id <= 0;
    end else if (advance) begin
      converting_ids <= acquiring_ids;
      converting_mask <= acquiring_mask;
      conversion_valid <= primed;
      primed <= 1;
      sample_id <= sample_id + 1'b1;
      acquiring_ids <= next_ids;
      acquiring_mask <= next_mask;
      // 本沿先用旧cursor/旧acquiring完成选组，再更新下一次游标；首次关闭shuffle
      // 的这一沿仍用旧cursor，随后才固定0。252..255拒绝区保留游标，不丢分配事务。
      // quiet采样与本advance在上层相隔一tick；本模块不实现模拟采样关闭/保持。
      if (shuffle) begin
        random_state <= random_next;
        if (random_state[31:24] < 8'd252) cursor <= 5'(random_state[31:24] % 8'd18);
      end
      else cursor <= 0;
    end
  end
endmodule
