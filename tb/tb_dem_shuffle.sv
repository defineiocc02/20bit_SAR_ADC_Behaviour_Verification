`timescale 1ns/1ps
module tb_dem_shuffle;
    logic [8:0] k; logic [3:0] r,hp; logic [2:0] vp;
    logic [15:0] h; logic [7:0] y;
    integer fd,rr,n,err,kk,rri,hpi,vpi,he,ye;
    dem_shuffle dut(.k_i(k),.reserved_i(r),.h_phase_i(hp),.v_phase_i(vp),.h_o(h),.y_o(y));
    initial begin
        err=0;n=0;fd=$fopen("dem_vec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            rr=$fscanf(fd,"%d %d %d %d %d %d\n",kk,rri,hpi,vpi,he,ye);
            if(rr==6) begin
                k=kk[8:0]; r=rri[3:0]; hp=hpi[3:0]; vp=vpi[2:0]; #1; n=n+1;
                if(h!==he[15:0]||y!==ye[7:0]) begin
                    err=err+1;
                    if(err<=3) $display("MISMATCH k=%0d r=%0d hp=%0d vp=%0d h=%h/%h y=%h/%h",kk,rri,hpi,vpi,h,he,y,ye);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
