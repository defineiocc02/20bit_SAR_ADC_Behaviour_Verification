`timescale 1ns/1ps
module tb_cdac_decode;
    logic [8:0] k;
    logic [16:0] main_m; logic [3:0] sub_m;
    integer fd,r,n,err,kk,me,se;
    cdac_decode dut(.k_i(k), .main_o(main_m), .sub_o(sub_m));
    initial begin
        err=0; n=0; fd=$fopen("cdac_vec.txt","r");
        if (fd==0) begin $display("FATAL no vec"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d\n",kk,me,se);
            if (r==3) begin
                k=kk[8:0]; #1; n=n+1;
                if (main_m!==me[16:0] || sub_m!==se[3:0]) begin
                    err=err+1;
                    if (err<=5) $display("MISMATCH k=%0d main=%h exp=%h sub=%h exp=%h",kk,main_m,me,sub_m,se);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if (err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
