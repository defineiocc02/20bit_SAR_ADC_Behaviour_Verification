`timescale 1ns/1ps
// 检查未选中时钟、字节/CRC 中止、复位取消、状态位序与流式地址，断言不完整帧不提交。
module spi_abort_contract_tb;
  logic clk=0;
  always #5 clk=~clk;
  logic rst_n=0,cs_n=1,sclk=1,sdi=0;
  logic crc_en=0,send_status=0,single_inst=0,short_instr=0,addr_asc=0;
  logic [7:0] status_byte=8'ha6,rdata=8'h5a;
  wire sdo,sdo_oe,we,crc_err_pulse,clk_cnt_err_pulse;
  wire [15:0] addr;wire[7:0]wdata;
  spi_slave dut(.*);
  int checks=0,commits=0,frame_errors=0,crc_errors=0;
  logic [15:0] seen_addr[16];logic[7:0]seen_data[16];
  task automatic require(input bit condition,input string message);
    checks++;
    if(!condition)$fatal(1,"SPI contract: %s",message);
  endtask
  always @(posedge clk)begin
    if(!rst_n || dut.cs_s1)require(!we,"write while reset or synchronized CS inactive");
    if(rst_n && we)begin
      seen_addr[commits]=addr;seen_data[commits]=wdata;commits++;
    end
    if(rst_n && clk_cnt_err_pulse)frame_errors++;
    if(rst_n && crc_err_pulse)crc_errors++;
  end
  task automatic tick(input int count);
    repeat(count)begin @(negedge clk);#1;end
  endtask
  task automatic exchange_bit(input bit value,output bit sampled);
    @(negedge clk);#1;sclk=0;sdi=value;
    tick(5);sclk=1;sampled=sdo;tick(5);
  endtask
  task automatic send_word(input logic[15:0] word_value,input int count,output logic[15:0] observed);
    bit sampled;
    observed=0;
    for(int k=count-1;k>=0;k--)begin
      exchange_bit(word_value[k],sampled);observed={observed[14:0],sampled};
    end
  endtask
  task automatic select_device();cs_n=0;sclk=1;tick(6);endtask
  task automatic deselect_device();cs_n=1;tick(6);endtask
  function automatic logic[7:0] crc_byte(input logic[7:0] initial_crc,input logic[7:0] value);
    logic[7:0] c;
    c=initial_crc;
    for(int k=7;k>=0;k--)c={c[6:0],1'b0}^((c[7]^value[k])?8'h07:8'h00);
    return c;
  endfunction
  logic[15:0] ignored,observed;
  logic[7:0] expected_crc;
  int old_count,old_errors;
  initial begin
    tick(3);rst_n=1;tick(6);
    send_word(16'ha55a,16,ignored);
    require(commits==0,"unselected clocks changed registers");
    select_device();send_word(16'h000a,16,ignored);send_word(16'h005c,8,ignored);deselect_device();
    require(commits==1 && seen_addr[0]==16'h000a && seen_data[0]==8'h5c,"normal byte not committed exactly once");
    old_errors=frame_errors;
    select_device();send_word(16'h000a,16,ignored);send_word(16'h0055,7,ignored);deselect_device();
    require(frame_errors==old_errors+1,"partial payload not reported");
    require(dut.phase==0 && dut.bitcnt==0,"abort left frame state active");
    send_word(16'h5aa5,16,ignored);
    require(commits==1,"shared SCLK committed an aborted byte");
    select_device();send_word(16'h0010,16,ignored);send_word(16'h00a3,8,ignored);deselect_device();
    require(commits==2 && seen_addr[1]==16'h0010 && seen_data[1]==8'ha3,"new frame inherited aborted state");
    select_device();send_word(16'h001b,16,ignored);send_word(16'h0055,7,ignored);
    rst_n=0;tick(2);send_word(16'h0007,3,ignored);cs_n=1;tick(3);rst_n=1;tick(6);
    require(commits==2,"reset committed a partial byte");
    select_device();send_word(16'h001b,16,ignored);send_word(16'h0037,8,ignored);deselect_device();
    require(commits==3 && seen_data[2]==8'h37,"post-reset frame incorrect");
    crc_en=1;
    expected_crc=crc_byte(crc_byte(crc_byte(8'ha5,8'h00),8'h0a),8'hc3);
    select_device();send_word(16'h000a,16,ignored);send_word(16'h00c3,8,ignored);send_word({8'd0,expected_crc},8,ignored);deselect_device();
    require(commits==4 && seen_data[3]==8'hc3,"good CRC did not commit once");
    expected_crc=crc_byte(crc_byte(crc_byte(8'ha5,8'h00),8'h0a),8'h4d)^8'h01;
    old_errors=crc_errors;
    select_device();send_word(16'h000a,16,ignored);send_word(16'h004d,8,ignored);send_word({8'd0,expected_crc},8,ignored);deselect_device();
    require(commits==4 && crc_errors==old_errors+1,"bad CRC committed or error missing");
    expected_crc=crc_byte(crc_byte(crc_byte(8'ha5,8'h00),8'h0a),8'h72);
    select_device();send_word(16'h000a,16,ignored);send_word(16'h0072,8,ignored);send_word({8'd0,expected_crc},7,ignored);deselect_device();
    send_word(16'h96a5,16,ignored);
    require(commits==4 && dut.phase==0 && dut.bitcnt==0,"aborted CRC frame remained active");
    crc_en=0;send_status=1;short_instr=0;
    select_device();send_word(16'h800a,16,observed);
    $display("SPI_STATUS_LONG observed=%04h expected=a6a6",observed);
    require(observed==16'ha6a6,"long instruction status bit order incorrect");
    send_word(16'd0,8,observed);require(observed[7:0]==8'h5a,"read payload changed");deselect_device();
    short_instr=1;
    select_device();send_word(16'h008a,8,observed);
    require(observed[7:0]==8'ha6,"short instruction status bit order incorrect");
    send_word(16'd0,8,observed);require(observed[7:0]==8'h5a,"short read payload changed");deselect_device();
    short_instr=0;send_status=0;addr_asc=1;
    select_device();send_word(16'h000a,16,ignored);send_word(16'h0069,8,ignored);send_word(16'h0096,8,ignored);deselect_device();
    require(commits==6 && seen_addr[4]==16'h000a && seen_addr[5]==16'h000b && seen_data[4]==8'h69 && seen_data[5]==8'h96,"streaming address/data regression");
    $display("SPI_ABORT_CONTRACT_COMPLETE checks=%0d commits=%0d aborted_frame_errors=%0d crc_errors=%0d",checks,commits,frame_errors,crc_errors);
    $display("SPI_ABORT_CONTRACT_TB PASS");$finish;
  end
  initial begin #500000;$fatal(1,"SPI watchdog");end
endmodule
