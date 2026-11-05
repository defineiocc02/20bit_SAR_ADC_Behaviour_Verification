`timescale 1ns/1ps
// 以 16 tick 输入间隔驱动单 lane 的 20 bit 帧，记录样本遗漏与 bit_en 恒高的 DCO 电平。
// 这是会随行为变化而失败的已知限制诊断，成功结束不等于无损串行传输通过。
module lvds_cadence_tb;
  logic clk=0,rst_n=0,valid_in=0,bit_en=1;
  logic [19:0] data_in=0;
  wire [1:0] ser_data,ser_frame;
  wire dco;
  always #5 clk=~clk;
  lvds_framer dut(.clk(clk),.rst_n(rst_n),.valid_in(valid_in),.data_in(data_in),
                  .lane_sel(1'b0),.bit_en(bit_en),.ser_data(ser_data),.ser_frame(ser_frame),.dco(dco));
  int bits=0,frame_count=0,input_count=0,high_cycles=0,low_cycles=0;
  int missing=0;
  bit seen[0:11];
  logic [19:0] assembled=0;
  bit recording=0;
  always @(negedge clk) begin
    if(rst_n) begin
      if(dco) high_cycles++; else low_cycles++;
      if(ser_frame[0]) begin
        if(recording && bits!=20) $fatal(1,"bad frame length %0d",bits);
        bits=0;assembled=0;recording=1;
      end
      if(recording) begin
        assembled={assembled[18:0],ser_data[0]};bits++;
        if(bits==20) begin
          frame_count++;
          if(assembled>=20'h10100 && assembled<20'h1010c) seen[int'(assembled-20'h10100)]=1;
          $display("SERIAL_FRAME n=%0d code=%05h",frame_count,assembled);recording=0;
        end
      end
      if(valid_in) begin input_count++;$display("INPUT n=%0d code=%05h",input_count,data_in);end
    end
  end
  initial begin
    for(int k=0;k<12;k++) seen[k]=0;
    repeat(3) @(negedge clk);#1;rst_n=1;
    for(int t=0;t<192;t++) begin
      @(negedge clk);#1;
      valid_in=(t%16==0);
      if(valid_in) data_in=20'h10100+20'(t/16);
    end
    @(negedge clk);#1;valid_in=0;
    repeat(60) @(negedge clk);
    for(int k=0;k<12;k++) if(!seen[k]) missing++;
    // 样本遗漏或 DCO 电平行为变化时使诊断失败，强制重新评估已知限制；
    // 成功结束不表示串行通路功能验收通过。
    if(input_count!=12 || low_cycles>2 || missing==0)
      $fatal(1,"known cadence limitation changed; review the diagnostic");
    $display("LVDS_CADENCE_PROBE_COMPLETE inputs=%0d frames=%0d missing=%0d dco_high=%0d dco_low=%0d",input_count,frame_count,missing,high_cycles,low_cycles);
    $finish;
  end
  initial begin #10000; $fatal(1,"watchdog"); end
endmodule
