// 模块：caaz_cycle_ctrl；CAAZ采集极性与CBIAS AZ存储代的数字控制，不实现模拟开关。
// input_chop_neg属于正在acquire的下一笔输入；current_*属于本周期RA。
// quiet沿取旧chop为current，再准备下一acquisition；实际pool primed 资格抑制启动伪样本，
// 首个有效转换ID2为正相，ID3为反相。
// legacy RA reset/储能电容准备仍每cycle执行，只有新az_capture_en为 even-only；
// odd必须独立保持CBIAS，不可把整个动态放大器reset停掉。
// 工程接口时序选当前phase1..8窗口、phase8沿接受az_capture_ok；原文AZ在采样期且无定量tick，
// 不能将本选点称为原文逐相位复刻。
// clk上升沿、同步低有效rst_n；disable清历史；本叶无独立epochclear，上层经rst_n/enable撤销。
// 新capture只更新CBIAS，旧ra_az若仍接到同一采集开关会在odd重抽噪声；标签/ack不能检测这种接错。
// input chop翻相早于pool下一组安装一tick，宏必须锁住前笔电荷，不能直接用TP AND组使能重开。
// Source: PKU_VLSI2025_C32_1_PAPER PDF2/printed2 Fig5/6; PKU_VLSI2025_C32_1_SLIDES PDF28..35/footer
// Slide27..34.
module caaz_cycle_ctrl #(
    parameter bit P_CAAZ_ENABLE = 1'b0
) (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire quiet_sample,
    input wire acquisition_primed,
    input wire [3:0] phase,
    input wire az_capture_ok,
    output wire input_chop_neg,
    output wire az_capture_en,
    output wire az_hold,
    output wire current_chop_neg,
    output wire [31:0] current_az_pair_id,
    output wire current_az_valid
);
  if (!P_CAAZ_ENABLE) begin : g_disabled
    assign input_chop_neg = 1'b0;
    assign az_capture_en = 1'b0;
    assign az_hold = 1'b0;
    assign current_chop_neg = 1'b0;
    assign current_az_pair_id = 32'd0;
    assign current_az_valid = 1'b0;
  end else begin : g_enabled
    logic acquiring_chop_neg;
    logic residue_chop_neg;
    logic residue_owner_valid;
    logic [31:0] residue_az_pair_id;
    logic residue_az_valid;
    logic [31:0] next_az_pair_id;
    logic [31:0] held_az_pair_id;
    logic held_az_valid;
    logic az_capture_reg;

    always_ff @(posedge clk) begin
      if (!rst_n || !enable) begin
        acquiring_chop_neg <= 1'b0;
        residue_chop_neg <= 1'b0;
        residue_owner_valid <= 1'b0;
        residue_az_pair_id <= '0;
        residue_az_valid <= 1'b0;
        next_az_pair_id <= '0;
        held_az_pair_id <= '0;
        held_az_valid <= 1'b0;
        az_capture_reg <= 1'b0;
      end else begin
        // phase0沿读旧acquiring polarity，沿后属于新current RA；同时把输入开关准备给下一笔采集。
        // 这里“current”与仍在fine转换的旧residue不是同一个sample ID。
        if (phase == 0)
          az_capture_reg <= acquisition_primed && !acquiring_chop_neg;
        else
          az_capture_reg <= phase < 8 && residue_owner_valid && !residue_chop_neg;

        if (quiet_sample) begin
          residue_chop_neg <= acquiring_chop_neg;
          residue_owner_valid <= acquisition_primed;
          if (acquisition_primed) begin
            acquiring_chop_neg <= !acquiring_chop_neg;
            if (!acquiring_chop_neg) begin
              residue_az_pair_id <= next_az_pair_id;
              residue_az_valid <= 1'b0;
              held_az_valid <= 1'b0;
            end else begin
              residue_az_pair_id <= held_az_pair_id;
              residue_az_valid <= held_az_valid;
            end
          end else begin
            residue_az_valid <= 1'b0;
          end
        end
        // even截止沿才取得模拟宏ack，失败仍推进AZ代并使even/odd都无效。
        // ack只确认该次采集资格，不测量odd保持泄漏或两笔AZ/信号传递系数是否相同。
        if (phase == 8 && residue_owner_valid && !residue_chop_neg) begin
          held_az_pair_id <= residue_az_pair_id;
          held_az_valid <= az_capture_ok;
          residue_az_valid <= az_capture_ok;
          next_az_pair_id <= residue_az_pair_id + 32'd1;
        end
      end
    end
    assign input_chop_neg = acquiring_chop_neg;
    assign az_capture_en = az_capture_reg;
    assign az_hold = residue_owner_valid && residue_chop_neg;
    assign current_chop_neg = residue_chop_neg;
    assign current_az_pair_id = residue_az_pair_id;
    assign current_az_valid = residue_az_valid;
  end
endmodule
