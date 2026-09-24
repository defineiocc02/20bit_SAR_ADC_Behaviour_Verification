`timescale 1ns/1ps
module review_top_protocol_tb;
  logic clk=0,rst_n=0,wr=0,validate=0,clear=0,ready;
  always #5 clk=~clk;
  logic [15:0] addr=0;
  logic [63:0] wdata=0,rdata;
  logic [8:0] coarse=0;
  logic [11:0] fine=0;
  logic crdy=1,frdy=1,rov=0,aov=0,rsat=0;
  logic signed [63:0] inj=0;
  wire [19:0] dout;
  wire valid,analog_ovf,sw_valid;
  wire [31:0] status_word;
  wire [17:0] slice_sel;
  wire [17:0][62:0] main_sw;
  wire [17:0][7:0] sub_sw;
  wire [17:0][3:0] dither_sw;
  int checks=0;
  sar20_digital_core #(.P_STRUCTURAL(0)) dut (.clk(clk),.rst_n(rst_n),.cfg_wr(wr),.cfg_addr(addr),.cfg_wdata(wdata),
    .cfg_rdata(rdata),.cfg_validate(validate),.cfg_clear_valid(clear),.cfg_ready(ready),
    .sadc_code(coarse),.sadc_rdy(crdy),.adc2_code(fine),.adc2_rdy(frdy),
    .rdac_ovf(rov),.adc2_over(aov),.ra_sat(rsat),.inj_q(inj),.dout(dout),.dout_valid(valid),
    .clip_low(),.clip_high(),.analog_ovf(analog_ovf),.acc_ovf(),.status_word(status_word),
    .slice_sel(slice_sel),.main_sw(main_sw),.sub_sw(sub_sw),.dither_sw(dither_sw),.sw_valid(sw_valid),
      .dout_sample_id(), .dout_flags()
  ,
      .flash_therm('0), .flash_valid('0), .coarse_cmp_valid('0), .coarse_cmp_ge('0), .fine_cmp_valid('0), .fine_cmp_ge('0), .analog_phase(), .quiet_sample(), .tp_clock(), .ra_az(), .ra_amplify(), .ref_precharge(), .ref_accurate(), .acquiring_mask(), .converting_mask(), .aux_charge_enable(), .hold_low_enable(), .coarse_compare_enable(), .coarse_trial(), .quantizer_dither(), .acquisition_dither_rails(), .fine_trial(), .fine_compare_enable(), .coarse_acquire_enable(),.flash_acquire_enable(),.fine_acquire_enable(),.flash_sample()
  );
  task automatic check(input bit ok,input string msg);
    checks++; if(!ok) $fatal(1,"%s",msg);
  endtask
  task automatic write_cfg(input logic [15:0] a,input logic [63:0] v);
    @(negedge clk); addr=a; wdata=v; wr=1;
    @(negedge clk); wr=0;
  endtask
  task automatic read_check(input logic [15:0] a,input logic [63:0] v);
    addr=a; #1; check(rdata===v,$sformatf("read %h got=%h exp=%h",a,rdata,v));
  endtask
  task automatic commit(input bit expected);
    @(negedge clk); validate=1;
    @(negedge clk); validate=0; check(ready===expected,"configuration commit");
  endtask
  task automatic weights_load();
    for(int s=0;s<18;s++) begin
      write_cfg(16'(16'h2000+256*s),0);
      for(int u=0;u<71;u++) write_cfg(16'(8*u),64'd67108864);
    end
    write_cfg(16'h1000,0); write_cfg(16'h1008,0); write_cfg(16'h1010,64'd8589934592);
  endtask
  // Independent exact integer form of the published reconstruction equation;
  // no DUT internal sums or divider outputs are used as expected values.
  function automatic logic [19:0] expected_code(input int c,input int a,input longint iq,input bit sampling,input int dither);
    logic signed [255:0] g,total,rails,f,numerator,denominator,q;
    total=256'sd568*256'sd67108864;
    g=(sampling?256'sd536:256'sd568)*256'sd67108864;
    rails=total-2*8*(c/8+c%8)*256'sd67108864;
    if(sampling) rails+=16*dither*256'sd67108864;
    f=(2*a+1)*256'sd1048576;
    numerator=((f<<<30)-(rails<<<32)-total*iq+(g<<<32))<<<20;
    denominator=g<<<33;
    q=numerator/denominator;
    if(numerator<0 && numerator%denominator!=0) q--;
    if(q<0) return 20'd0;
    if(q>=1048576) return 20'hfffff;
    return q[19:0];
  endfunction
  initial begin
    int n,head,tail,planned_c,planned_a;
    longint planned_i;
    logic [19:0] expected[0:63];
    int expected_id[0:63];
    bit expected_analog[0:63], sticky;
    repeat(2) @(negedge clk); rst_n=1;
    // Control writes must be atomic even if the three-cycle sequencer is aborted.
    read_check(16'h1018,2);
    write_cfg(16'h1018,7);
    read_check(16'h1018,2);
    @(negedge clk); clear=1;
    @(negedge clk); clear=0;
    read_check(16'h1018,2);
    write_cfg(16'h1018,0); repeat(4) @(negedge clk);
    read_check(16'h1018,0);
    // Bad values and reserved fields remain visible after the write strobe falls.
    write_cfg(16'h0000,0); repeat(3) @(negedge clk);
    check(status_word[31:6]==1,"weight range error must be sticky");
    write_cfg(16'h1018,64'h100); repeat(3) @(negedge clk);
    check(status_word[31:6]==5,"reserved control bits must be rejected");
    weights_load();
    read_check(16'h0001,0); read_check(16'h0800,0); read_check(16'h03f8,0);
    read_check(16'h2101,0); read_check(16'h3200,0);
    write_cfg(16'h1018,12); repeat(4) @(negedge clk); commit(0);
    check(dut.cal_err_code==10,"sampling and quantizer modes are exclusive");
    write_cfg(16'h1018,0); repeat(4) @(negedge clk); commit(1);
    head=0;tail=0;n=0;sticky=0;planned_c=57;planned_a=2011;planned_i=0;
    // Check forty transactions, with two missing-ready deadlines and hostile
    // data/flag changes outside the defined capture edges.
    repeat(16*42) begin
      if(valid) begin
        check(head<tail,"unexpected output or stale transaction");
        check(dut.dout_sample_id==32'(expected_id[head]),"top calibration sample ID");
        check(dut.dout_flags[2:0]==0,"unexpected calibration error");
        check(dout===expected[head],$sformatf("sample %0d got=%0d exp=%0d",head,dout,expected[head]));
        check(analog_ovf===expected_analog[head],"analog flags from wrong sample");
        head++;
      end
      case(dut.phase)
        0: begin
          n++; planned_c=57+n; planned_a=2011+n; planned_i=longint'(n)*1234567;
          coarse=9'(planned_c); fine=12'(planned_a); inj=planned_i;
          crdy=(n!=3 && n<=40); frdy=(n!=6 && n<=40);
          rov=0;aov=0;rsat=0;
        end
        1: begin rov=1;aov=1;rsat=1; end // must not enter the captured sample flags
        9: begin
          check(dut.sadc_ok==(n!=3 && n<=40),"SADC deadline qualification");
          coarse=9'd511; // deliberately invalidate the live bus after capture
        end
        14: begin rov=0;aov=(n==10);rsat=0; end
        15: begin
          fine=0;inj=-64'sd9223372036854775807;rov=1;aov=1;rsat=1;
          if(n<=40 && n!=3 && n!=6) begin
            if(n==10) sticky=1;
            expected_id[tail]=n;
            expected[tail]=expected_code(planned_c,planned_a,planned_i,0,0);
            expected_analog[tail]=sticky;tail++;
          end
        end
      endcase
      check(dut.swap_dither_code==0 && dut.sampling_dither_code==0,"dither-off mode must be silent");
      @(negedge clk);
    end
    check(head==38 && tail==38,"missing-ready samples must be dropped exactly once");
    check(status_word[31:6]==8,"missing ADC2 ready must remain observable");
    clear=1; @(negedge clk); clear=0;
    check(!ready&&!valid&&!sw_valid&&slice_sel==0,"epoch clear outputs");
    check(main_sw=='0&&sub_sw=='0&&dither_sw=='0,"epoch clear switching state");
    check(dut.phase==0&&dut.alloc_sample_idx==0&&dut.sid_a==0&&dut.sid_b==0,"epoch clear scheduler/DEM");
    check(dut.input_err==0&&!analog_ovf,"epoch clear errors");
    repeat(20) begin @(negedge clk); check(!valid&&!sw_valid,"aborted epoch must stay silent"); end
    // Reconfigure two complete epochs and exercise both physical dither routes.
    for(int mode=0;mode<2;mode++) begin
      bit observed_nonzero;
      weights_load();
      write_cfg(16'h1018,mode==0?8:4); repeat(4) @(negedge clk); commit(1);
      crdy=1;frdy=1;coarse=250;fine=2048;inj=0;rov=0;aov=0;rsat=0;
      observed_nonzero=0;head=0;tail=0;
      repeat(16*18) begin
        @(negedge clk);
        if(valid) begin
          check(head<tail,"mode output without accepted transaction");
          check(dout===expected[head],"unforced mode reconstruction must match integer equation");
          head++;
        end
        if(dut.phase==15) begin
          expected[tail]=expected_code(250+(mode==0?int'($signed(dut.dith_q)):0),
            2048,0,mode==1,int'($signed(dut.dith_q)));
          tail++;
        end
        if(dut.phase==12) begin
          if(dut.dith_q!=0) observed_nonzero=1;
          if(mode==0) begin
            check(dut.swap_dither_code==16'($signed(dut.dith_q)),"quantizer route sign extension");
            check(dut.sampling_dither_code==0,"quantizer mode must not inject sampling rails");
          end else begin
            check(dut.swap_dither_code==0,"sampling mode must not change coarse quantizer code");
            check(dut.sampling_dither_code==dut.dith_q,"sampling route code");
            check(int'($countones(dut.dither_rail))==2+int'($signed(dut.dith_q)),"sampling rail count");
          end
        end
      end
      check(head>=16 && tail-head<=1,"mode conversion throughput");
      check(observed_nonzero,"mode test must exercise nonzero dither");
      clear=1; @(negedge clk); clear=0;
    end
    $display("REVIEW_TOP_PROTOCOL_COMPLETE checks=%0d outputs=%0d",checks,38);$finish;
  end
  initial begin #300000; $fatal(1,"top protocol timeout"); end
endmodule
