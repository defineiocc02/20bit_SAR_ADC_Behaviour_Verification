// 模块：caaz_pair_processor；CAAZ完整校准码成对后处理，输入斩波/CBIAS存储由模拟宏实现。
// 单位：code为20位offset-binary，pair_difference为21位signed半LSB差。
// CodeStream码中心合同：d=code_even-code_odd，Vpair=LSB*d/2；
// 最终20位使用现有uniform floor：floor((2^20+d)/2)，不宣称再量化无偏。
// clk上升沿、同步低有效rst_n；epoch_clear同沿优先并丢弃未完成pair。
// 开启时每个odd接受沿最多提交一个pair；raw flags/bad/无AZ状态或错配会置sticky fault，
// 直至clear/reset，不用后续valid记录自动修补缺样。
// 默认关闭为旧code/valid/ID/flags/analog_bad的零拍组合旁路。
// 旁路自身不门控reset/clear，须由上游提供有效资格；开启时reset/clear最高优先取消同沿odd。
// ID/chop/AZ代只验证数字配对；没有slice/权重或H_AZ输入，不能验证实际共享噪声与a=H_AZ/K相等。
// 时钟空洞可保持pending，不提供wallclock超时；缺样在下一笔ID检查时检出，fault后不自动重配。
// Source: PKU_VLSI2025_C32_1_PAPER PDF2/printed2 Fig5/6; PKU_VLSI2025_C32_1_SLIDES PDF28..35/footer
// Slide27..34.
module caaz_pair_processor #(
    parameter bit P_CAAZ_ENABLE = 1'b0
) (
    input wire clk,
    input wire rst_n,
    input wire epoch_clear,
    input wire in_valid,
    input wire [19:0] in_code,
    input wire [31:0] in_sample_id,
    input wire in_chop_neg,
    input wire [31:0] in_az_pair_id,
    input wire in_az_valid,
    input wire [4:0] in_flags,
    input wire in_analog_bad,
    output wire out_valid,
    output wire [19:0] out_code,
    output wire [31:0] out_sample_id,
    output wire [31:0] out_first_sample_id,
    output wire [31:0] out_az_pair_id,
    output wire [4:0] out_flags,
    output wire out_analog_bad,
    output wire signed [20:0] pair_difference,
    output wire pair_difference_valid,
    output wire fault,
    output wire fault_pulse,
    output wire pair_pending,
    output wire [3:0] fault_code
);
  if (!P_CAAZ_ENABLE) begin : g_bypass
    assign out_valid = in_valid;
    assign out_code = in_code;
    assign out_sample_id = in_sample_id;
    assign out_first_sample_id = in_sample_id;
    assign out_az_pair_id = in_az_pair_id;
    assign out_flags = in_flags;
    assign out_analog_bad = in_analog_bad;
    assign pair_difference = '0;
    assign pair_difference_valid = 1'b0;
    assign pair_pending = 1'b0;
    assign fault = 1'b0;
    assign fault_pulse = 1'b0;
    assign fault_code = '0;
  end else begin : g_pair
    localparam logic [3:0] ERR_INPUT_FLAGS = 4'd1;
    localparam logic [3:0] ERR_ANALOG_BAD = 4'd2;
    localparam logic [3:0] ERR_AZ_INVALID = 4'd3;
    localparam logic [3:0] ERR_SAMPLE_GAP = 4'd4;
    localparam logic [3:0] ERR_CHOP_PHASE = 4'd5;
    localparam logic [3:0] ERR_AZ_PAIR = 4'd6;

    logic waiting_for_odd;
    logic sample_history_valid;
    logic pair_history_valid;
    logic [19:0] even_sample_code;
    logic [31:0] even_sample_id;
    logic [31:0] even_az_pair_id;
    logic [31:0] last_accepted_sample_id;
    logic [31:0] last_completed_az_pair_id;
    logic pair_output_valid;
    logic pair_fault_sticky;
    logic pair_fault_event;
    logic [3:0] pair_fault_reason;
    logic [3:0] input_reject_reason;
    logic [19:0] pair_output_code;
    logic [31:0] pair_first_sample_id;
    logic [31:0] pair_last_sample_id;
    logic [31:0] pair_output_az_id;
    logic signed [20:0] pair_difference_reg;

    wire signed [20:0] exact_difference =
        $signed({1'b0, even_sample_code}) - $signed({1'b0, in_code});
    // offset-binary码中心为c+0.5-2^19；两码相减的0.5相消，所以21位d精确表示半LSB结果。
    // 加2^20后范围1..2^21-1，保留22位signed再右移，不能对20位码直接做二补数取负。
    wire signed [21:0] offset_sum = 22'sd1048576 +
        $signed({exact_difference[20], exact_difference});
    wire signed [21:0] floor_output_code = offset_sum >>> 1;

    // 拒收优先级：raw flags > analog_bad > AZ无效 > ID缺口 > 极性/存储代。
    // ID/代按模2^32递增；首记录必须正相，但本叶不硬编码它必须是顶层启动ID2。
    always_comb begin
      input_reject_reason = 4'd0;
      if (in_flags != 0)
        input_reject_reason = ERR_INPUT_FLAGS;
      else if (in_analog_bad)
        input_reject_reason = ERR_ANALOG_BAD;
      else if (!in_az_valid)
        input_reject_reason = ERR_AZ_INVALID;
      else if (sample_history_valid && in_sample_id != last_accepted_sample_id + 32'd1)
        input_reject_reason = ERR_SAMPLE_GAP;
      else if (waiting_for_odd == in_chop_neg) begin
        if (waiting_for_odd && in_az_pair_id != even_az_pair_id)
          input_reject_reason = ERR_AZ_PAIR;
        else if (!waiting_for_odd && pair_history_valid &&
                 in_az_pair_id != last_completed_az_pair_id + 32'd1)
          input_reject_reason = ERR_AZ_PAIR;
      end else
        input_reject_reason = ERR_CHOP_PHASE;
    end

    always_ff @(posedge clk) begin
      if (!rst_n || epoch_clear) begin
        waiting_for_odd <= 1'b0;
        sample_history_valid <= 1'b0;
        pair_history_valid <= 1'b0;
        even_sample_code <= '0;
        even_sample_id <= '0;
        even_az_pair_id <= '0;
        last_accepted_sample_id <= '0;
        last_completed_az_pair_id <= '0;
        pair_output_valid <= 1'b0;
        pair_fault_sticky <= 1'b0;
        pair_fault_event <= 1'b0;
        pair_fault_reason <= '0;
        pair_output_code <= '0;
        pair_first_sample_id <= '0;
        pair_last_sample_id <= '0;
        pair_output_az_id <= '0;
        pair_difference_reg <= '0;
      end else begin
        pair_output_valid <= 1'b0;
        pair_fault_event <= 1'b0;
        if (in_valid && !pair_fault_sticky) begin
          if (input_reject_reason != 0) begin
            waiting_for_odd <= 1'b0;
            pair_fault_sticky <= 1'b1;
            pair_fault_event <= 1'b1;
            pair_fault_reason <= input_reject_reason;
          end else begin
            last_accepted_sample_id <= in_sample_id;
            sample_history_valid <= 1'b1;
            if (!waiting_for_odd) begin
              even_sample_code <= in_code;
              even_sample_id <= in_sample_id;
              even_az_pair_id <= in_az_pair_id;
              waiting_for_odd <= 1'b1;
            end else begin
              pair_difference_reg <= exact_difference;
              pair_output_code <= floor_output_code[19:0];
              pair_first_sample_id <= even_sample_id;
              pair_last_sample_id <= in_sample_id;
              pair_output_az_id <= in_az_pair_id;
              pair_output_valid <= 1'b1;
              waiting_for_odd <= 1'b0;
              last_completed_az_pair_id <= in_az_pair_id;
              pair_history_valid <= 1'b1;
            end
          end
        end
      end
    end
    assign out_valid = pair_output_valid;
    assign out_code = pair_output_code;
    assign out_sample_id = pair_last_sample_id;
    assign out_first_sample_id = pair_first_sample_id;
    assign out_az_pair_id = pair_output_az_id;
    assign out_flags = 5'd0;
    assign out_analog_bad = 1'b0;
    assign pair_difference = pair_difference_reg;
    assign pair_difference_valid = pair_output_valid;
    assign pair_pending = waiting_for_odd;
    assign fault = pair_fault_sticky;
    assign fault_pulse = pair_fault_event;
    assign fault_code = pair_fault_reason;
  end
endmodule
