// Independent audit vectors are generated with Python integer arithmetic.
// Run: python tools/audit_rtl_arithmetic.py
`timescale 1ns/1ps
module arithmetic_comb_audit(output logic complete = 0);
 logic signed[63:0] f,o,i,mi,ma,af,ef;
 logic [63:0] g,t;
 logic signed[65:0] ra;
 wire [62:0] a;
 logic [62:0] ea;
 wire ov,ao;
 logic eo;
 logic [11:0] c;
 cal_residue_mac mac(.fine_r(f),.off_r(o),.inj_r(i),.gain_s(g),.total_s(t),.rails_s(ra),.a1(a),.any_ovf(ov));
 adc2_dec adc(.adc2_code(c),.adc2_min_q(mi),.adc2_max_q(ma),.fine_q(af),.ovf(ao));
 initial begin
 string vdir;
 int fd,n,line;
 if (!$value$plusargs("VDIR=%s",vdir)) vdir=".";
 fd=$fopen({vdir,"/mac.hex"},"r"); line=0; if(fd==0)$fatal(1,"vector file missing");
 while(!$feof(fd)) begin
  n=$fscanf(fd,"%h %h %h %h %h %h %h %h\n",f,o,i,g,t,ra,eo,ea);
  if(n!=8) $fatal(1,"parse %d",n);
  #1; line++;
  if(ov!==eo || (!ov && a!==ea)) $fatal(1,"MAC mismatch line=%0d ov=%b expected=%b a=%h expected=%h",line,ov,eo,a,ea);
 end
 $display("MAC_INDEPENDENT_PASS cases=%0d",line); if(line!=40010)$fatal(1,"MAC count mismatch");$fclose(fd);
 fd=$fopen({vdir,"/adc.hex"},"r");line=0; if(fd==0)$fatal(1,"vector file missing");
 while(!$feof(fd)) begin
  n=$fscanf(fd,"%h %h %h %h\n",c,mi,ma,ef);
  if(n!=4) $fatal(1,"parse %d",n);
  #1;line++;
  if(ao || af!==ef) $fatal(1,"ADC mismatch line=%0d ov=%b fine=%h expected=%h",line,ao,af,ef);
 end
 $display("ADC_INDEPENDENT_PASS cases=%0d",line); if(line!=40000)$fatal(1,"ADC count mismatch"); $fclose(fd); complete=1;
 end
endmodule
module arithmetic_seq_audit(output logic finished = 0);
 logic clk=0,rst_n=0,start=0;
 always #1 clk=~clk;
 logic signed[62:0] a,q,eq;
 logic [63:0] d;
 logic de,done,busy,ee;
 div_floor #(.P_W_A(63),.P_W_D(64),.P_STAGES(7)) dv(.clk(clk),.rst_n(rst_n),.start(start),.a(a),.d(d),.q(q),.err(de),.done(done),.busy(busy));
 logic ready=1,clear=0,complete=0;
 logic [19:0] candidate=0,dout;
 logic [31:0] id=0,rid;
 logic lo=0,hi=0,ov=0,ge=0,ao=0;
 logic valid,olo,ohi,oov,oge,oao;
 logic [4:0] flags;
 cal_output_stage out(.clk(clk),.rst_n(rst_n),.cfg_ready(ready),.clr_ovf(clear),.complete(complete),.candidate(candidate),.candidate_sample_id(id),.candidate_clip_low(lo),.candidate_clip_high(hi),.candidate_acc_ovf(ov),.candidate_gain_err(ge),.candidate_adc2_ovf(ao),.dout(dout),.dout_valid(valid),.clip_low(olo),.clip_high(ohi),.acc_ovf(oov),.gain_err(oge),.adc2_ovf(oao),.result_sample_id(rid),.result_flags(flags));
 task automatic edge_check(input logic[19:0] ed,input logic[4:0] ef,input logic[2:0] es,input logic[1:0] ec,input logic ev,input logic[31:0] ei);
  @(posedge clk);#0.1;
  if({dout,flags,oao,oge,oov,ohi,olo,valid,rid} !== {ed,ef,es,ec,ev,ei})
   $fatal(1,"output stage mismatch id=%0d dout=%0d flags=%b sticky=%b clip=%b valid=%b",id,dout,flags,{oao,oge,oov},{ohi,olo},valid);
  @(negedge clk);
 endtask
 initial begin
 string vdir;
 int fd,n,line,lat;
 if (!$value$plusargs("VDIR=%s",vdir)) vdir=".";
 a=0;d=1;
 repeat(2)@(negedge clk);rst_n=1;
 // A low-clipped valid code; error result keeps old word/clip but reports own event.
 complete=1;candidate=0;lo=1;id=11;
 edge_check(0,5'b01000,3'b000,2'b01,1,11);
 candidate=20'hfffff;lo=0;hi=1;ov=1;id=12;
 edge_check(0,5'b00001,3'b001,2'b01,1,12);
 // Good data resumes immediately; sticky history must not be used as current validity.
 candidate=203;hi=0;ov=0;id=13;
 edge_check(203,5'b00000,3'b001,2'b00,1,13);
 // Same-edge clear and new error: new event wins.
 candidate=404;clear=1;ge=1;ao=1;id=14;
 edge_check(203,5'b00110,3'b110,2'b00,1,14);
 // Configuration cancellation discards pending output, but simple ready gating preserves history.
 clear=0;ready=0;ov=1;id=15;
 edge_check(0,5'b00000,3'b110,2'b00,0,0);
 ready=1;complete=0;ov=0;ge=0;ao=0;clear=1;
 edge_check(0,5'b00000,3'b000,2'b00,0,0);
 clear=0;complete=1;candidate=505;id=16;
 edge_check(505,5'b00000,3'b000,2'b00,1,16);
 complete=0;
 edge_check(505,5'b00000,3'b000,2'b00,0,16);
 $display("OUTPUT_FLAG_SEQUENCE_PASS scenarios=8");
 fd=$fopen({vdir,"/div.hex"},"r");line=0; if(fd==0)$fatal(1,"vector file missing");
 while(!$feof(fd)) begin
  n=$fscanf(fd,"%h %h %h %h\n",a,d,eq,ee);if(n!=4)$fatal(1,"parse");
  start=1;@(negedge clk);start=0;lat=0;
  while(!done) begin
   @(negedge clk);lat++;
   if(lat==3)begin start=1;a=63'sd17;d=64'd3;end
   if(lat==4)start=0;
   if(lat>10)$fatal(1,"divide timeout");
  end
  line++;
  if(q!==eq || de!==ee || lat!=9)$fatal(1,"DIV mismatch line=%0d got=%h expected=%h err=%b expected_err=%b latency=%0d",line,q,eq,de,ee,lat);
  @(negedge clk);
 end
 $display("DIV_INDEPENDENT_PASS cases=%0d latency=9 busy_requests_ignored",line); if(line!=10048)$fatal(1,"DIV count mismatch");$fclose(fd);finished=1;
 end
endmodule

module arithmetic_audit_tb;
 wire comb_complete, seq_complete;
 arithmetic_comb_audit comb(.complete(comb_complete));
 arithmetic_seq_audit seq(.finished(seq_complete));
 initial begin
  wait(comb_complete && seq_complete);
  $display("ARITHMETIC_AUDIT_COMPLETE mac=40010 adc=40000 div=10048 flag_sequences=8");
  $finish;
 end
 initial begin #1000000; $fatal(1,"arithmetic audit watchdog"); end
endmodule
