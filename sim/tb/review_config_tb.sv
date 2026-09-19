`timescale 1ns/1ps
module review_config_tb;
  logic clk=0, rst_n=0, cfg_wr=0, cfg_validate=0, cfg_clear_valid=0;
  logic [15:0] cfg_addr=0;
  logic [63:0] cfg_wdata=0, cfg_rdata;
  logic cfg_ready, dout_valid;
  int outputs=0, hist[-2:2];
  always #5 clk=~clk;
  sar20_digital_core dut(.clk(clk),.rst_n(rst_n),.cfg_wr(cfg_wr),
    .cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),.cfg_rdata(cfg_rdata),
    .cfg_validate(cfg_validate),.cfg_clear_valid(cfg_clear_valid),.cfg_ready(cfg_ready),
    .sadc_code(9'd256),.sadc_rdy(1'b1),.adc2_code(12'd2048),.adc2_rdy(1'b1),
    .ra_sat(1'b0),.rdac_ovf(1'b0),.adc2_over(1'b0),.inj_q(64'sd0),.dout_valid(dout_valid),
    .slice_sel(),.main_sw(),.sub_sw(),.dither_sw(),.sw_valid(),.dout(),
    .clip_low(),.clip_high(),.analog_ovf(),.acc_ovf(),.status_word());
  always @(negedge clk) if(dout_valid) outputs++;
  task automatic write_cfg(input logic [15:0] addr,input logic [63:0] data);
    @(negedge clk); cfg_addr=addr; cfg_wdata=data; cfg_wr=1;
    @(negedge clk); cfg_wr=0;
  endtask
  task automatic commit(input bit ready_expected);
    @(negedge clk); cfg_validate=1;
    @(negedge clk); cfg_validate=0;
    if(cfg_ready !== ready_expected) $fatal(1,"commit ready=%b expected=%b",cfg_ready,ready_expected);
  endtask
  task automatic write_weights(input bit omit_last);
    for(int s=0;s<18;s++) begin
      write_cfg(16'(16'h2000+s*256),0);
      for(int u=0;u<71;u++)
        if(!(omit_last && s==17 && u==70)) write_cfg(16'(u*8),64'd67108864);
    end
  endtask
  initial begin
    for(int k=-2;k<=2;k++) hist[k]=0;
    repeat(2) @(negedge clk); rst_n=1;
    write_cfg(16'h1008,-64'sd4294967296); write_cfg(16'h1010,64'd4294967296);
    commit(0); // no weights and offset never written
    write_weights(1);
    write_cfg(16'd0,64'd67108864); // duplicate is not the missing cell
    write_cfg(16'h1000,0);
    commit(0);
    if(dut.cal_err_code!=6) $fatal(1,"missing weight must report ERR_INCOMPLETE");
    write_cfg(16'd560,0); commit(0); // invalid zero must not mark written
    write_cfg(16'd560,64'h1000000000000001); commit(0); // high bits must not truncate
    write_cfg(16'd560,64'd67108864);
    // Write + validate is a rejected transaction; old min stays intact.
    @(negedge clk); cfg_wr=1; cfg_validate=1; cfg_addr=16'h1008; cfg_wdata=64'd8589934592;
    @(negedge clk); cfg_wr=0; cfg_validate=0;
    if(cfg_ready || dut.c_min!=-64'sd4294967296) $fatal(1,"write/commit collision accepted");
    write_cfg(16'h1018,7);
    cfg_validate=1;
    @(negedge clk); cfg_validate=0;
    if(cfg_ready) $fatal(1,"early control commit accepted");
    // Attempt an unrelated write while serialization is active: reject, keep control bits.
    cfg_wr=1; cfg_addr=16'h1000; cfg_wdata=123;
    @(negedge clk); cfg_wr=0;
    repeat(4) @(negedge clk);
    if({dut.c_smask_en,dut.c_bridge_en,dut.c_dem_en}!==3'b111 || dut.c_off!=0)
      $fatal(1,"control serialization was preempted");
    commit(1);
    write_cfg(16'h1000,456);
    if(dut.c_off!=0 || !cfg_ready) $fatal(1,"active config was writable");
    // Exercise the real internal dither path (no force/injected dither).
    repeat(10000) begin
      @(negedge clk);
      if(dut.rdac_load) hist[int'(dut.dith_q)]++;
    end
    for(int k=-2;k<=2;k++) if(hist[k]==0) $fatal(1,"internal dither missing bin %0d",k);
    if(outputs<500) $fatal(1,"normal configured core did not produce output");
    wait(dut.rc_busy); @(negedge clk); cfg_clear_valid=1;
    @(negedge clk); cfg_clear_valid=0;
    if(cfg_ready || dut.rc_busy || dout_valid) $fatal(1,"clear did not abort old epoch");
    commit(0); // retained values are not a newly loaded image
    repeat(100) begin @(negedge clk); if(dout_valid) $fatal(1,"stale epoch output"); end
    write_weights(0); commit(0); // scalar presence also must restart
    write_cfg(16'h1000,0); write_cfg(16'h1008,-64'sd4294967296); write_cfg(16'h1010,64'd4294967296);
    commit(1);
    $display("REVIEW_CONFIG_COMPLETE outputs=%0d",outputs); $finish;
  end
  initial begin #300000; $fatal(1,"config timeout"); end
endmodule
