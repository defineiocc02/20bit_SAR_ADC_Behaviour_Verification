// =============================================================================
// 文件名     : caaz20_properties.sv
// 模块名     : caaz20_properties
// 版本       : v1.0
// 功能       : CAAZ 的有界形式安全属性
//
// 设计说明   :
//   * 含跨周期 $past 断言，只能证明给定深度内的性质，不是完备形式验证。
//   * 复位后若干拍内属性才成立，故断言必须带同步复位释放条件。
//   * 单时钟域 clk_i；复位、清空、禁用优先于受理，异常路径不得静默接受。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
// -------------------------------------------------------------------------
// 端口按功能分组，slot 0 在最低位；总线位宽与定点格式见同目录 SPEC。
// -------------------------------------------------------------------------
module caaz20_properties (
    input logic clk_i, rst_ni, clear_i, enable_i, caaz_enable_i,
    input logic acq_valid_i,
    input logic [31:0] acq_sample_id_i,
    input logic raw_valid_i,
    input logic [31:0] raw_sample_id_i,
    input logic signed [19:0] raw_code_i,
    input logic raw_bad_i, raw_chop_neg_i,
    input logic [30:0] raw_az_pair_id_i,
    input logic raw_az_ok_i
);
  logic acq_valid, chop_neg, az_capture, az_hold, out_valid, out_pair;
  logic fault, fault_pulse;
  logic [31:0] acq_id, out_id, out_first_id;
  logic [30:0] acq_az_id, out_az_id;
  logic [2:0] fault_code;
  logic signed [19:0] out_code, last_even_code;
  logic signed [20:0] out_difference;
  logic seen_past, seen_even;
  logic [31:0] last_even_id;
  logic signed [21:0] twice_code;

  caaz20_pair dut (
      .clk_i(clk_i), .rst_ni(rst_ni), .clear_i(clear_i), .enable_i(enable_i),
      .caaz_enable_i(caaz_enable_i), .acq_valid_i(acq_valid_i), .acq_sample_id_i(acq_sample_id_i),
      .raw_valid_i(raw_valid_i), .raw_sample_id_i(raw_sample_id_i), .raw_code_i(raw_code_i),
      .raw_bad_i(raw_bad_i), .raw_chop_neg_i(raw_chop_neg_i), .raw_az_pair_id_i(raw_az_pair_id_i),
      .raw_az_ok_i(raw_az_ok_i), .acq_ctrl_valid_o(acq_valid), .acq_ctrl_sample_id_o(acq_id),
      .acq_chop_neg_o(chop_neg), .acq_az_capture_o(az_capture), .acq_az_hold_o(az_hold),
      .acq_az_pair_id_o(acq_az_id), .out_valid_o(out_valid), .out_code_o(out_code),
      .out_sample_id_o(out_id), .out_first_sample_id_o(out_first_id), .out_az_pair_id_o(out_az_id),
      .out_pair_o(out_pair), .out_difference_o(out_difference), .fault_o(fault),
      .fault_pulse_o(fault_pulse), .fault_code_o(fault_code)
  );

  initial seen_past = 1'b0;
// =========================================================================
// 2. 组合输出与连续性
// =========================================================================
// 输出在无效时清零，外部不得把上一拍残留当成新一笔结果。
  assign twice_code = $signed({out_code[19], out_code, 1'b0});

// =========================================================================
// 3. 同步时序与状态转移
// =========================================================================
// 复位优先于受理；在途事务在清空/禁用同沿丢弃，ID 生命周期按样本绑定。
  always_ff @(posedge clk_i) begin
    seen_past <= 1'b1;
    if (!rst_ni || clear_i || !enable_i) begin
      seen_even <= 1'b0;
      last_even_id <= '0;
      last_even_code <= '0;
    end else if (raw_valid_i && !raw_sample_id_i[0]) begin
      seen_even <= 1'b1;
      last_even_id <= raw_sample_id_i;
      last_even_code <= raw_code_i;
    end

    if (seen_past) begin
      assert (!(az_capture && az_hold));
      if ($past(!rst_ni || clear_i || !enable_i)) begin
        assert (!fault && !out_valid && !acq_valid && !fault_pulse);
      end else if ($past(fault)) begin
        assert (fault && !fault_pulse && fault_code == $past(fault_code));
      end
      if (fault) assert (!out_valid && !acq_valid && fault_code >= 1 && fault_code <= 6);
      if (acq_valid) begin
        assert ($past(acq_valid_i) && acq_id == $past(acq_sample_id_i));
        assert (chop_neg == ($past(caaz_enable_i) && acq_id[0]));
        assert (az_hold == chop_neg);
        assert (az_capture == ($past(caaz_enable_i) && !acq_id[0]));
        assert (acq_az_id == ($past(caaz_enable_i) ? acq_id[31:1] : 31'd0));
      end
      if (out_valid) begin
        assert ($past(raw_valid_i) && !$past(raw_bad_i));
        assert (out_id == $past(raw_sample_id_i));
        assert (out_pair == $past(caaz_enable_i));
        if (out_pair) begin
          assert (out_id[0] && out_first_id + 32'd1 == out_id && out_az_id == out_id[31:1]);
          assert ($past(raw_chop_neg_i && raw_az_ok_i) && out_az_id == $past(raw_az_pair_id_i));
          assert (seen_even && out_first_id == last_even_id);
          assert (22'(out_difference) == 22'(last_even_code) - 22'($past(raw_code_i)));
          assert (twice_code <= 22'(out_difference));
          assert (22'(out_difference) < twice_code + 22'sd2);
        end else begin
          assert (out_first_id == out_id && out_az_id == 0 && out_code == $past(raw_code_i));
        end
      end
    end
  end
endmodule
