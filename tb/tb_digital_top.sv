`timescale 1ns/1ps
module tb_digital_top;
    logic [8:0] c; logic [11:0] a2; logic [5:0] w;
    logic [3:0] res,hp; logic [2:0] vp;
    logic [8:0] k; logic [16:0] main_m; logic [3:0] sub_m;
    logic [15:0] h_m; logic [7:0] y_m; logic [19:0] code; logic ovf;
    integer fd,r,n,err;
    integer cc,aa,ww,rr,hh,vv,ke,me,se,he,ye,ce;
    digital_top dut(.c_i(c),.adc2_i(a2),.word_i(w),.reserved_i(res),
                    .h_phase_i(hp),.v_phase_i(vp),.k_o(k),.main_o(main_m),
                    .sub_o(sub_m),.h_o(h_m),.y_o(y_m),
                    .code_o(code),.overflow_o(ovf));
    initial begin
        err=0;n=0;fd=$fopen("top_vec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d %d %d %d %d %d %d %d %d\n",
                      cc,aa,ww,rr,hh,vv,ke,me,se,he,ye,ce);
            if(r==12) begin
                c=cc[8:0]; a2=aa[11:0]; w=ww[5:0]; res=rr[3:0]; hp=hh[3:0]; vp=vv[2:0]; #1;
                n=n+1;
                if(k!==ke[8:0]||main_m!==me[16:0]||sub_m!==se[3:0]||
                   h_m!==he[15:0]||y_m!==ye[7:0]||code!==ce[19:0]) begin
                    err=err+1;
                    if(err<=4) $display("MISMATCH n=%0d c=%0d w=%0d k=%0d/%0d code=%0d/%0d h=%h/%h",
                                        n,cc,ww,k,ke,code,ce,h_m,he);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
