`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : store_tb.sv
// 模块名     : store_tb
// 版本       : v1.0
// 功能       : 以线性地址记分板核对全深度存储和跨银行的逐拍响应。
// 设计说明   : 上层 FIFO 控制涉及另一条接口；此台只验证宏端口和容量。
// 变更记录   : 加入中途复位、反序遍历和相邻周期换银行。
// =============================================================================
module store_tb;
    reg clk=0, rst_n=0, req=0, wr=0;
    reg [13:0] addr=0;
    reg [19:0] din=0;
    wire [19:0] dout;
    wire valid;
    reg [19:0] reference [0:16383];  // 独立平坦数组，不复用被测的银行译码
    integer checks=0, i;
    always #5 clk=~clk;
    result_store dut(.clk(clk), .rst_n(rst_n), .req_i(req), .write_i(wr),
        .addr_i(addr), .data_i(din), .data_o(dout), .read_valid_o(valid));

    // 1. 先在下降沿驱动，上升沿之后取样，从而区分同步读返回和即时地址旁路。
    task automatic cycle(input bit en, input bit we, input logic [13:0] a,
                         input logic [19:0] data);
        begin
            @(negedge clk); req=en; wr=we; addr=a; din=data;
            @(posedge clk); #1;
            if (valid !== (rst_n && en && !we)) $fatal(1,"VALID addr=%0d",a);
            if (rst_n && en && we) reference[a]=data;
            if (rst_n && en && !we && dout !== reference[a])
                $fatal(1,"DATA addr=%0d want=%h got=%h",a,reference[a],dout);
            checks++;
        end
    endtask
    initial begin
        cycle(0,0,0,0); rst_n=1;
        // 写满后以与写入顺序无关的置换读回。0、4095/4096 和 16383 均在其中。
        for (i=0;i<16384;i++) cycle(1,1,14'(i),20'((i*40503)^20'ha35d7));
        for (i=0;i<16384;i++) cycle(1,0,14'((i*4051+37)%16384),'0);
        // 空拍、读写交替和复位时的写请求不能制造返回，也不能破坏原银行。
        cycle(0,0,14'h3fff,0); cycle(1,1,14'h2000,20'h80001); cycle(1,0,14'h2000,0);
        @(negedge clk); rst_n=0; req=1; wr=1; addr=14'h2000; din=20'h0;
        @(posedge clk); #1; if(valid!==0) $fatal(1,"RESET");
        @(negedge clk); req=0; rst_n=1;
        for (i=16383;i>=0;i--) cycle(1,0,14'(i),0);
        $display("PASS result_store depth=16384 width=20 banks=4 checked_cycles=%0d",checks);
        $finish;
    end
endmodule
`default_nettype wire
