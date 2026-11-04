`timescale 1ns/1ps
module weight_msb_case #(parameter int NS=18,NU=71)(output logic complete=0);
 logic clk=0,rst_n=0,clear_load=0,cfg_ready=0,wr_en=0;
 logic [4:0] wr_slice=0;
 logic [6:0] wr_unit=0;
 logic [47:0] wr_data=0;
 wire old_err,new_err,old_complete,new_complete;
 wire [NS-1:0][NU-1:0][47:0] old_w,new_w;
 logic [47:0] expected[NS][NU];
 bit written[NS][NU];
 int steps=0,accepted=0,rejected=0;
 int unsigned rng=32'h97ba1284+NS+NU;
 always #5 clk=~clk;
 weight_store #(.P_N_SLICES(NS),.P_N_UNITS(NU)) now_dut(.*,.err_write(new_err),.load_complete(new_complete),.w_q(new_w));
 weight_store_before #(.P_N_SLICES(NS),.P_N_UNITS(NU)) old_dut(.*,.err_write(old_err),.load_complete(old_complete),.w_q(old_w));
 function automatic int unsigned random_word();
   rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;
 endfunction
 task automatic step(input bit reset_value,clear_value,ready_value,write_value,
                     input int s,u,input logic[47:0] data);
   bit accept_model,err_model,all_written;
   @(negedge clk);
   rst_n=reset_value;clear_load=clear_value;cfg_ready=ready_value;
   wr_en=write_value;wr_slice=5'(s);wr_unit=7'(u);wr_data=data;
   accept_model=write_value&&!clear_value&&!ready_value&&s<NS&&u<NU&&data>0&&data<48'h800000000000;
   err_model=write_value&&!accept_model;
   #1;
   if(old_err!==err_model||new_err!==err_model)$fatal(1,"ERROR_FLAG NS=%0d NU=%0d step=%0d",NS,NU,steps);
   @(posedge clk);#1;steps++;
   if(!reset_value)begin
     for(int i=0;i<NS;i++)for(int j=0;j<NU;j++)begin expected[i][j]=0;written[i][j]=0;end
   end else if(clear_value)begin
     for(int i=0;i<NS;i++)for(int j=0;j<NU;j++)written[i][j]=0;
   end else if(accept_model)begin expected[s][u]=data;written[s][u]=1;accepted++;end
   if(err_model)rejected++;
   all_written=1;
   for(int i=0;i<NS;i++)for(int j=0;j<NU;j++)begin
     all_written&=written[i][j];
     if(old_w[i][j]!==expected[i][j]||new_w[i][j]!==expected[i][j])
       $fatal(1,"VALUE NS=%0d NU=%0d step=%0d at=%0d,%0d old=%h new=%h oracle=%h",NS,NU,steps,i,j,old_w[i][j],new_w[i][j],expected[i][j]);
     if(old_w[i][j][47]!==0||new_w[i][j][47]!==0)$fatal(1,"MSB_INVARIANT");
   end
   if(old_complete!==all_written||new_complete!==all_written)$fatal(1,"LOAD_COMPLETE");
 endtask
 initial begin
   int s,u;logic[47:0] data;
   step(0,0,0,0,0,0,0);
   step(1,0,0,1,0,0,0); // illegal zero
   step(1,0,0,1,0,0,48'h800000000000); // illegal MSB, no lower bits
   step(1,0,0,1,0,0,48'hffffffffffff); // illegal MSB with all lower bits
   step(1,0,0,1,0,0,1);
   step(1,0,0,1,0,0,48'h7fffffffffff); // exact legal maximum and replacement
   step(1,0,1,1,0,0,2); // locked; prior max must remain
   step(1,1,0,1,0,0,3); // clear/write collision rejected, data retained
   step(1,0,0,0,0,0,0);
   if(NS<32)step(1,0,0,1,NS,0,4);
   if(NU<128)step(1,0,0,1,0,NU,5);
   for(int i=0;i<NS;i++)for(int j=0;j<NU;j++)step(1,0,0,1,i,j,48'(1+i*NU+j));
   if(!new_complete)$fatal(1,"FULL_IMAGE_NOT_COMPLETE");
   step(1,0,0,1,NS-1,NU-1,48'h7fffffffffff);
   step(1,0,0,1,NS-1,NU-1,7); // replace after complete
   step(1,1,0,0,0,0,0); // preserve values, invalidate all written bits
   step(1,0,0,1,0,0,9);
   for(int k=0;k<512;k++)begin
     s=int'(random_word()%32);u=int'(random_word()%128);
     data={16'(random_word()),random_word()};
     step(k!=257,k%43==0,k%7==0,k%5!=0,s,u,data);
   end
   step(0,0,0,1,0,0,48'h7fffffffffff); // reset wins over otherwise accepted write
   step(1,0,0,1,NS-1,NU-1,48'h7fffffffffff);
   $display("WEIGHT_MSB_CASE_PASS NS=%0d NU=%0d steps=%0d accepted=%0d rejected=%0d",NS,NU,steps,accepted,rejected);
   complete=1;
 end
endmodule
module weight_msb_equiv_tb;
 wire [2:0] complete;
 weight_msb_case a(complete[0]);
 weight_msb_case #(.NS(32),.NU(1)) b(complete[1]);
 weight_msb_case #(.NS(1),.NU(128)) c(complete[2]);
 initial begin wait(&complete);$display("WEIGHT_MSB_EQUIV_COMPLETE geometries=3");$finish;end
 initial begin #30000;$fatal(1,"TIMEOUT");end
endmodule
