`timescale 1ns/1ps
// 相位发生器对拍：17280 拍，逐拍打印三轴供 Python 比对
module tb_dem_phase_gen;
    logic clk=0, rstn=0, adv=0;
    logic [3:0] res, hp; logic [2:0] vp; logic [31:0] rng;
    dem_phase_gen dut(.clk(clk), .reset_n(rstn), .sample_adv(adv),
                      .reserved_o(res), .h_phase_o(hp), .v_phase_o(vp), .rng_o(rng));
    always #5 clk = ~clk;
    integer n;
    initial begin
        repeat(3) @(negedge clk); rstn = 1;
        for (n = 0; n < 17280; n = n + 1) begin
            @(negedge clk); adv = 1;
            @(posedge clk); #1;
            $display("%0d %0d %0d %0d", n, res, hp, vp);
        end
        @(negedge clk); adv = 0;
        $finish;
    end
endmodule
