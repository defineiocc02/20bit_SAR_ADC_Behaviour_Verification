`timescale 1ns/1ps
// 独立反例检查缺失电气、指令格式与寄存器元数据绑定时的拒绝行为。
module spi_unbound_contract_tb;
  logic clk=0,rst_n=0,cs_n=1,sclk=1,sdi=0,interface_ready=1,short_instr=0;
  logic address_valid=1,read_only=0,access_supported=1;
  wire sdo,sdo_oe,we;wire[15:0]addr,wdata;wire[1:0]write_bytes;
  wire crc_err_pulse,clk_cnt_err_pulse,partial_err_pulse,addr_err_pulse,readonly_err_pulse,not_ready_err_pulse,cfg_reject;
  wire[3:0]cfg_reject_reason;int checks=0,frames=0,rejects=0,writes=0,reason_count[16];
  always #5 clk=~clk;
  spi_slave dut(.clk(clk),.rst_n(rst_n),.cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),.interface_ready(interface_ready),
    .crc_en(1'b0),.send_status(1'b0),.single_inst(1'b1),.short_instr(short_instr),.addr_asc(1'b0),.strict_access(1'b1),.status_long(16'd0),.status_short(8'd0),
    .addr(addr),.wdata(wdata),.write_bytes(write_bytes),.we(we),.rdata(16'h5a3c),.entity_bytes(2'd1),.address_valid(address_valid),.read_only(read_only),.access_supported(access_supported),.access_reject_reason(4'd1),
    .crc_err_pulse(crc_err_pulse),.clk_cnt_err_pulse(clk_cnt_err_pulse),.partial_err_pulse(partial_err_pulse),.addr_err_pulse(addr_err_pulse),.readonly_err_pulse(readonly_err_pulse),.not_ready_err_pulse(not_ready_err_pulse),.cfg_reject(cfg_reject),.cfg_reject_reason(cfg_reject_reason));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"SPI_UNBOUND: %s",why);endtask
  always @(posedge clk)begin #1;if(rst_n)begin if(we)writes++;if(cfg_reject)begin rejects++;reason_count[cfg_reject_reason]++;end end end
  task automatic byte_send(input logic[7:0]value);
    for(int i=7;i>=0;i--)begin sdi=value[i];#80;sclk=0;#80;sclk=1;#80;end
  endtask
  task automatic rejected_frame(input bit read_request,input int reason);
    int old;old=reason_count[reason];@(negedge clk);cs_n=0;repeat(8)@(negedge clk);
    if(!short_instr)begin byte_send(read_request?8'h80:0);byte_send(8'h0a);end else byte_send(8'h0a);
    byte_send(8'ha5);cs_n=1;sclk=1;repeat(8)@(negedge clk);
    require(reason_count[reason]==old+1&&writes==0&&!sdo_oe,"unbound/unsupported frame accepted or duplicated reject");frames++;
  endtask
  initial begin
    for(int i=0;i<16;i++)reason_count[i]=0;
    repeat(3)@(negedge clk);rst_n=1;
    rejected_frame(0,1); // P_WRITE_SDO_DRIVE 未绑定。
    short_instr=1;rejected_frame(0,1);short_instr=0; // P_SHORT_ADDR_BITS 未绑定。
    interface_ready=0;rejected_frame(0,8);interface_ready=1;
    access_supported=0;rejected_frame(1,1);access_supported=1;
    address_valid=0;rejected_frame(1,3);address_valid=1;
    read_only=1;rejected_frame(0,4);
    require(frames==6&&rejects==6&&reason_count[1]==3&&reason_count[8]==1&&reason_count[3]==1&&reason_count[4]==1,"named negative coverage");
    $display("SPI_UNBOUND_CONTRACT_COMPLETE frames=%0d rejects=%0d missing_bindings=%0d not_ready=%0d invalid_address=%0d readonly=%0d write_requests=%0d checks=%0d",frames,rejects,reason_count[1],reason_count[8],reason_count[3],reason_count[4],writes,checks);$finish;
  end
  initial begin #100000;$fatal(1,"unbound SPI watchdog");end
endmodule
