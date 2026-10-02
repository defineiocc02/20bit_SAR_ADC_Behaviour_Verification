`timescale 1ns/1ps
// Independent holdout from a noisy external fit, quantized to actual RTL
// registers.  The fixture generator sees digital observations and a separate
// training reference; this bench only sees the resulting registers and codes.
module calibration_fit_tb;
  logic clk=0,rst_n=0,start=0;
  always #1 clk=~clk;
  wire [17:0][70:0][47:0] weights;
  wire [17:0][63:0] row_total;
  logic [47:0] weight_mem [0:1277];
  logic [7:0][4:0] ids;
  logic [7:0][62:0] main_on;
  logic [7:0][7:0] sub_on;
  logic [3:0] rails;
  logic [11:0] fine;
  logic signed [63:0] inj_q,source_off,source_min,source_max;
  wire signed [63:0] offset_q,adc2_min_q,adc2_max_q;
  logic [31:0] result_id;
  logic [19:0] dout;
  logic [4:0] flags;
  logic valid,busy;
  string vdir,path;
  integer fd,parsed,latency,samples;
  logic [7:0][4:0] next_id;
  logic [7:0][62:0] next_main;
  logic [7:0][7:0] next_sub;
  logic [3:0] next_rails;
  logic [11:0] next_fine;
  logic [63:0] next_inj;
  logic [19:0] expected;
  logic [4:0] expected_flags;
  logic ws_wr_en=0,cal_wr_en=0,controls_write=0,validate=0;
  logic [4:0] wr_slice=0;
  logic [6:0] wr_unit=0;
  logic [47:0] wr_data=0;
  logic [3:0] cal_sel=0;
  logic signed [63:0] cal_data=0;
  wire weights_ready,ws_err,cfg_ready,sampling_en;
  wire [31:0] cfg_error;

  weight_store #(.P_N_SLICES(18),.P_N_UNITS(71)) u_store(
    .clk(clk),.rst_n(rst_n),.cfg_ready(cfg_ready),.clear_load(1'b0),
    .load_complete(weights_ready),.wr_en(ws_wr_en),.wr_slice(wr_slice),
    .wr_unit(wr_unit),.wr_data(wr_data),.err_write(ws_err),.w_q(weights), .row_total(row_total), .selected_weight());
  calib_regs u_registers(
    .clk(clk),.rst_n(rst_n),.wr_en(cal_wr_en),.sel(cal_sel),.data_v(cal_data),
    .data_b(1'b0),.controls_write(controls_write),.controls_data(4'b0111),
    .quantizer_dither_en(),.weights_ready(weights_ready),
    .config_busy(ws_wr_en || cal_wr_en || controls_write),.validate(validate),
    .clear_valid(1'b0),.cfg_ready(cfg_ready),.err_code(cfg_error),
    .offset_q(offset_q),.adc2_min_q(adc2_min_q),.adc2_max_q(adc2_max_q),
    .dem_en(),.bridge_en(),.sampling_mask_en(sampling_en));

  recon_core #(.P_N_ACTIVE(8),.P_N_MAIN(63),.P_N_SUB(8),.P_N_SLICES(18),
               .P_DIT_N(4),.P_DIT_END(71),.P_USE_ROW_TOTALS(1)) dut(
    .clk(clk),.rst_n(rst_n),.cfg_ready(cfg_ready),.start(start),.sample_id(32'(samples)),
    .clr_ovf(1'b0),.sampling_mask_en(sampling_en),.slice_id(ids),
    .main_on(main_on),.sub_on(sub_on),.dither_rail(rails),.adc2_code(fine),
    .inj_q(inj_q),.w_rom(weights),.offset_q(offset_q),
    .adc2_min_q(adc2_min_q),.adc2_max_q(adc2_max_q),
    .dout(dout),.dout_valid(valid),.clip_low(),.clip_high(),.acc_ovf(),
    .gain_err(),.adc2_ovf(),.busy(busy),.result_sample_id(result_id),
    .result_flags(flags), .row_total(row_total));

  initial begin
    if(!$value$plusargs("vdir=%s",vdir)) vdir="sim/vectors";
    path={vdir,"/fitted18_weights.hex"};
    $readmemh(path,weight_mem);
    path={vdir,"/fitted18_scalars.hex"};
    fd=$fopen(path,"r");
    if(fd==0) $fatal(1,"cannot open fitted scalar registers");
    parsed=$fscanf(fd,"%h %h %h",source_off,source_min,source_max);
    if(parsed!=3) $fatal(1,"malformed fitted scalar registers");
    $fclose(fd);
    path={vdir,"/fitted18_holdout.hex"};
    fd=$fopen(path,"r");
    if(fd==0) $fatal(1,"cannot open fitted holdout");
    ids='0;main_on='0;sub_on='0;rails='0;fine='0;inj_q='0;
    samples=0;
    repeat(2) @(negedge clk);
    rst_n=1;
    for(int s=0;s<18;s++) for(int u=0;u<71;u++) begin
      @(negedge clk);
      ws_wr_en=1;wr_slice=5'(s);wr_unit=7'(u);wr_data=weight_mem[s*71+u];
      if(ws_err) $fatal(1,"fitted physical weight rejected s=%0d u=%0d",s,u);
    end
    @(negedge clk);ws_wr_en=0;
    if(!weights_ready) $fatal(1,"fitted physical table incomplete");
    for(int s=0;s<18;s++) for(int u=0;u<71;u++)
      if(weights[s][u]!==weight_mem[s*71+u])
        $fatal(1,"fitted coefficient did not load s=%0d u=%0d",s,u);
    for(int k=0;k<3;k++) begin
      @(negedge clk);
      cal_wr_en=1;cal_sel=4'(k);
      case(k)
        0: cal_data=source_off;
        1: cal_data=source_min;
        default: cal_data=source_max;
      endcase
    end
    @(negedge clk);cal_wr_en=0;controls_write=1;
    @(negedge clk);controls_write=0;validate=1;
    @(negedge clk);validate=0;
    if(!cfg_ready || cfg_error!=0 || !sampling_en ||
       offset_q!==source_off || adc2_min_q!==source_min || adc2_max_q!==source_max)
      $fatal(1,"fitted configuration failed atomic validation");
    while(!$feof(fd)) begin
      parsed=$fscanf(fd,"%h %h %h %h %h %h %h %h\n",
        next_id,next_main,next_sub,next_rails,next_fine,next_inj,
        expected,expected_flags);
      if(parsed==-1) break;
      if(parsed!=8) $fatal(1,"malformed fitted holdout record %0d",samples+1);
      @(negedge clk);
      if(busy) $fatal(1,"calibrator did not become idle");
      samples++;
      ids=next_id;main_on=next_main;sub_on=next_sub;rails=next_rails;
      fine=next_fine;inj_q=next_inj;start=1;
      @(negedge clk);start=0;latency=0;
      ids='1;main_on='0;sub_on='0;rails='0;fine='0;inj_q='0;
      while(!valid) begin
        @(negedge clk);latency++;
        if(latency>12) $fatal(1,"fitted holdout timeout %0d",samples);
      end
      if(result_id!=32'(samples) || latency!=11 ||
         dout!==expected || flags!==expected_flags)
        $fatal(1,"fitted holdout mismatch n=%0d got=%h expected=%h id=%0d latency=%0d flags=%b expected_flags=%b",
          samples,dout,expected,result_id,latency,flags,expected_flags);
      // Preserve the observed result, not only a final PASS marker, so a
      // reviewer can independently plot every held-out output and its timing.
      $display("FIT_ROW n=%0d actual=%05h expected=%05h flags=%02h expected_flags=%02h latency=%0d id=%0d",
               samples,dout,expected,flags,expected_flags,latency,result_id);
    end
    $fclose(fd);
    if(samples!=128) $fatal(1,"fitted holdout was incomplete: %0d",samples);
    $display("CALIBRATION_FIT_COMPLETE samples=%0d fitted_external_weights=1278 cfg_commit=PASS",samples);
    $finish;
  end
  initial begin #100000; $fatal(1,"fitted holdout watchdog"); end
endmodule
