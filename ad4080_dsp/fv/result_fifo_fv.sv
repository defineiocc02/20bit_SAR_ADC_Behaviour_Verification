// 有界安全属性验证框架：用符号地址跟踪已写入的 RAM 字，不假定 DUT 存储器已初始化。
module result_fifo_fv(
  input logic clk,rst_n,clear,wr_en,rd_en,overwrite_en,snapshot_rd_en,
  input logic[3:0]wr_data,input logic[2:0]snapshot_addr
);
  wire[3:0]rd_data;wire rd_valid,full,empty,almost_full,almost_empty,overflow,underflow,access_reject;
  result_fifo #(.DATA_W(4),.DEPTH(8),.P_AF_MARGIN(2),.P_AE_LEVEL(1)) dut(.*);
  logic past_valid=0;
  logic[3:0]occupancy;
  logic[2:0]write_index,read_index;
  wire collision=snapshot_rd_en&&(wr_en||rd_en);
  wire write_ok=!clear&&!collision&&wr_en&&(occupancy<8||overwrite_en);
  wire read_ok=!clear&&!collision&&rd_en&&(occupancy!=0);
  wire snapshot_ok=!clear&&!collision&&snapshot_rd_en;
  (* anyconst *) logic[2:0]tracked_address;
  logic tracked_known=0;logic[3:0]tracked_word;
  always @(posedge clk)begin
    past_valid<=1;
    if(!past_valid)assume(!rst_n);
    if(!rst_n)begin occupancy<=0;write_index<=0;read_index<=0;end
    else if(clear)begin occupancy<=0;write_index<=0;read_index<=0;end
    else begin
      if(write_ok)begin
        write_index<=write_index+1'b1;
        if(write_index==tracked_address)begin tracked_known<=1;tracked_word<=wr_data;end
      end
      if(read_ok)read_index<=read_index+1'b1;
      else if(write_ok&&occupancy==8&&overwrite_en)read_index<=read_index+1'b1;
      if(write_ok&&!read_ok&&occupancy<8)occupancy<=occupancy+1'b1;
      else if(read_ok&&!write_ok)occupancy<=occupancy-1'b1;
    end
    if(past_valid&&rst_n)begin
      assert(occupancy<=8);
      assert(full==(occupancy==8));assert(empty==(occupancy==0));
      assert(almost_full==(occupancy>=6));assert(almost_empty==(occupancy<=1));
      if($past(rst_n))begin
        assert(rd_valid==$past(read_ok||snapshot_ok));
        assert(access_reject==$past(!clear&&collision));
        if($past(tracked_known&&((read_ok&&read_index==tracked_address)||(snapshot_ok&&snapshot_addr==tracked_address))))
          assert(rd_data==$past(tracked_word));
        if($past(overflow))assert(overflow);
        if($past(underflow))assert(underflow);
      end
    end
  end
endmodule
