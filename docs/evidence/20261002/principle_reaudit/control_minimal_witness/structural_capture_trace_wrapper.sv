`timescale 1ns/1ps
module structural_capture_trace_wrapper;
  structural_protocol_tb tb();
  logic [31:0] shadow_accepted_id=0;
  logic shadow_accept=0;
  always_ff @(posedge tb.clk) begin
    if(!tb.rst_n || !tb.enable) begin
      shadow_accepted_id<=0; shadow_accept<=0;
    end else begin
      shadow_accept<=tb.launch;
      if(tb.launch) shadow_accepted_id<=tb.id;
    end
  end
  integer fd,edge_count=0,context_update_edge=0,fine_update_edge=0,accepts=0;
  initial begin
    fd=$fopen("structural_capture_trace.csv","w");
    if(fd==0) $fatal(1,"cannot open structural trace");
    $fdisplay(fd,"edge,pre_phase,rst_n,enable,quiet,capture,context_valid,pre_context_id,pre_fine_code,launch,coarse_start,coarse_done,coarse_update,load_rdac,post_phase,post_context_id,post_fine_code,post_accepted_id,post_accept,context_age,fine_code_age");
    forever begin
      bit q,c,l,cv,crst,en,coarse_start,coarse_done,coarse_update,load_rdac;
      int phase0,phase1,cage,fage;
      logic[31:0] id0; logic[11:0] fine0;
      @(posedge tb.clk);
      edge_count++;
      phase0=int'(tb.phase);q=tb.d.quiet_sample;c=tb.d.capture;l=tb.launch;cv=tb.d.context_valid;
      id0=tb.id;fine0=tb.fc;crst=tb.rst_n;en=tb.enable;
      coarse_start=tb.d.advance && tb.flash_valid;
      coarse_done=tb.d.coarse_done[tb.d.bank];
      coarse_update=tb.d.coarse_update[tb.d.bank];load_rdac=tb.d.load_rdac;
      cage=edge_count-context_update_edge;fage=edge_count-fine_update_edge;
      #0.001;
      phase1=int'(tb.phase);
      if(!crst || !en) begin
        context_update_edge=0;fine_update_edge=0;
        if(shadow_accept || shadow_accepted_id!=0) $fatal(1,"shadow reset/disable failed");
      end else begin
        if(l) begin
          accepts++;
          if(phase0!=15 || !cv || cage!=15 || fage!=1 || !shadow_accept || shadow_accepted_id!==id0)
            $fatal(1,"capture alignment mismatch edge=%0d phase=%0d context_age=%0d fine_age=%0d",edge_count,phase0,cage,fage);
        end
        if(q) context_update_edge=edge_count;
        if(phase0==14) fine_update_edge=edge_count;
      end
      $fdisplay(fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",edge_count,phase0,crst,en,q,c,cv,id0,fine0,l,coarse_start,coarse_done,coarse_update,load_rdac,phase1,tb.id,tb.fc,shadow_accepted_id,shadow_accept,cage,fage);
    end
  end
  final begin
    $display("STRUCTURAL_CAPTURE_ALIGNMENT_PASS edges=%0d accepts=%0d context_age=15 fine_code_age=1",edge_count,accepts);
    $fclose(fd);
  end
endmodule
