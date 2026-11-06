// 根据每笔实际底板位图和片外训练系数重构 20 位转换结果。
// 所有端口共用一个时钟。输入 ID 的归属由 ADC2 接口适配器保证。
`timescale 1ns/1ps
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
  logic [4:0] read_id;
  logic read_id_valid, read_bad;
  logic [4:0] read_conv_sub, read_sample_sub;
  logic [20:0] read_positive, read_negative;

  logic read_valid_q, read_last_q, read_bad_q;
  logic [20:0] positive_q, negative_q;
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

  function automatic logic signed [31:0] adc2_midpoint(input logic [11:0] code);
    logic signed [13:0] numerator;
    logic [12:0] magnitude;
    logic [28:0] scaled, quantized;
    numerator = $signed({1'b0, code, 1'b1}) - 14'sd4096;
    magnitude = 13'(numerator[13] ? -numerator : numerator);
    scaled = {2'b0, magnitude, 14'b0} + 29'd12;
    quantized = scaled / 29'd25;
    return numerator[13] ? -$signed({3'b0, quantized}) : $signed({3'b0, quantized});
  endfunction

  function automatic logic signed [24:0] signed_feature(
      input logic signed [23:0] weight,
      input logic positive,
      input logic negative
  );
    logic signed [24:0] extended;
    extended = 25'(weight);
    if (positive) return extended;
    if (negative) return -extended;
    return 25'sd0;
  endfunction

  assign active = rst_ni && !clear_i;
  assign cfg_start_ready_o = active && !cfg_loading_q && !mac_busy_q && !mac_valid_q
                             && !dividing_q && !out_valid_o;
  assign cfg_ready_o = active && cfg_loading_q;
  assign cfg_word_legal = (cfg_col_q == 5'd21)
      ? ((cfg_data_i >= MinGain) && (cfg_data_i <= MaxGain))
      : (cfg_data_i[25:24] == {2{cfg_data_i[23]}});
  assign in_ready_o = active && configured_o && !cfg_start_i && !mac_busy_q && !mac_valid_q;

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

  assign read_id = slice_ids_q[5*issue_slot_q+:5];
  assign read_id_valid = read_id < 5'd18;
  assign read_conv_sub = conv_sub_q[5*issue_slot_q+:5];
  assign read_sample_sub = sample_sub_q[5*issue_slot_q+:5];
  always_comb begin
    read_bad = !read_id_valid || read_conv_sub[4] || read_sample_sub[4];
    if (read_id_valid && seen_q[read_id]) read_bad = 1'b1;
  end
  assign read_positive = {read_conv_sub[3:0] & ~read_sample_sub[3:0],
                          main_q[17*issue_slot_q+:17]};
  assign read_negative = {read_sample_sub[3:0] & ~read_conv_sub[3:0], 17'd0};

  for (genvar col = 0; col < FeatureCount; col++) begin : g_feature
    assign selected[col] = signed_feature(read_weight_q[col], positive_q[col], negative_q[col]);
  end
  assign next_num = accum_num_q + 32'(slot_sum_q);
  assign next_gain = accum_gain_q + {3'b0, slot_gain_q};

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
        accum_num_q <= adc2_midpoint(in_adc2_i);
        accum_gain_q <= '0;
        accumulated_bad_q <= in_bad_i;
        seen_q <= '0;
        issue_slot_q <= '0;
        issuing_q <= 1'b1;
        mac_busy_q <= 1'b1;
      end
      read_valid_q <= issuing_q;
      if (issuing_q) begin
        read_last_q <= issue_slot_q == 3'd7;
        read_bad_q <= read_bad;
        positive_q <= read_positive;
        negative_q <= read_negative;
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

  assign divide_take = active && mac_valid_q && !dividing_q && (!out_valid_o || out_ready_i);
  assign mac_legal = !mac_bad_q && (mac_num_q >= 32'sd0)
                     && ($unsigned(mac_num_q) < {3'b0, mac_gain_q});
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
  assign next_quotient = (quotient_q << 2) | {18'd0, digit};

  always_ff @(posedge clk_i) begin
    if (!active) begin
      dividing_q <= 1'b0;
      out_valid_o <= 1'b0;
      out_sample_id_o <= '0;
      out_code_o <= '0;
      out_bad_o <= 1'b0;
    end else begin
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
