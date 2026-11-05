//=============================================================================
// @file    result_fifo.sv
// @brief   带保护的队列 RAM 与互斥快照读口。
// @details clear 只复位逻辑指针，不清零 RAM。快照和队列共用一个寄存读口，
//          请求冲突时拒绝访问；快照不能与队列读写同时接受。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.56/63 的存储使用方式；队列运算为工程实现。
// 时钟域: clk，单一数字时钟域。
// 复位: rst_n 复位控制；clear 复位指针；RAM 与保持的读数据均不初始化。
// 延迟: 一次寄存 RAM 读取；仅在 rd_valid 有效时解释保持的数据。
// 吞吐: 每个 clk 最多各一次队列写和队列读；快照访问独占读写接口。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 仅数字 RAM 抽象；SRAM 映射、读写冲突行为与目标工艺时序需另行验证。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none
module result_fifo #(
    parameter int DATA_W=20,  // DATA_W: 存储字位宽，须大于等于 1 bit。
    parameter int DEPTH=16384,  // DEPTH: 存储字数，须为不小于 2 的二次幂。
    parameter int P_AF_MARGIN=16,  // P_AF_MARGIN: 近满余量，范围 1..DEPTH-1 个字。
    parameter int P_AE_LEVEL=16  // P_AE_LEVEL: 近空阈值，范围 0..DEPTH-1 个字。
) (
    input logic clk, rst_n, clear,
    input logic wr_en, overwrite_en,
    input logic [DATA_W-1:0] wr_data,
    input logic rd_en,
    input logic snapshot_rd_en,
    input logic [$clog2(DEPTH)-1:0] snapshot_addr,
    output logic [DATA_W-1:0] rd_data,
    output logic rd_valid,
    output logic full, empty, almost_full, almost_empty,
    output logic overflow, underflow, access_reject
);
    localparam int MEM_AW=$clog2(DEPTH);
    localparam int PTR_W=MEM_AW+1;
    localparam logic [PTR_W-1:0] DEPTH_C=PTR_W'(DEPTH);
    logic [PTR_W-1:0] wr_ptr, rd_ptr;
    wire [PTR_W-1:0] count=wr_ptr-rd_ptr;
    wire collision=snapshot_rd_en && (wr_en || rd_en);
    wire write_fire=rst_n && !clear && !collision && wr_en && (!full || overwrite_en);
    wire read_fire=rst_n && !clear && !collision && rd_en && !empty;
    wire snapshot_fire=rst_n && !clear && !collision && snapshot_rd_en;
    (* ram_style="block" *) logic [DATA_W-1:0] mem [0:DEPTH-1];
    initial begin
        if (DATA_W<1 || DEPTH<2 || (DEPTH&(DEPTH-1))!=0)
            $fatal(1,"result_fifo: positive width and power-of-two depth >=2 required");
        if (P_AF_MARGIN<1 || P_AF_MARGIN>=DEPTH || P_AE_LEVEL<0 || P_AE_LEVEL>=DEPTH)
            $fatal(1,"result_fifo: almost-full/empty thresholds are outside the RAM");
    end
    assign empty=count==0;
    assign full=count==DEPTH_C;
    assign almost_full=count>=PTR_W'(DEPTH-P_AF_MARGIN);
    assign almost_empty=count<=PTR_W'(P_AE_LEVEL);

    always_ff @(posedge clk) begin
        if (write_fire) mem[wr_ptr[MEM_AW-1:0]]<=wr_data;
        rd_valid<=read_fire || snapshot_fire;
        if (snapshot_fire) rd_data<=mem[snapshot_addr];
        else if (read_fire) rd_data<=mem[rd_ptr[MEM_AW-1:0]];
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin wr_ptr<='0; rd_ptr<='0; end
        else if (clear) begin wr_ptr<='0; rd_ptr<='0; end
        else begin
            if (write_fire) wr_ptr<=wr_ptr+PTR_W'(1);
            if (read_fire) rd_ptr<=rd_ptr+PTR_W'(1);
            else if (write_fire && full && overwrite_en) rd_ptr<=rd_ptr+PTR_W'(1);
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin overflow<=1'b0; underflow<=1'b0; access_reject<=1'b0; end
        else begin
            access_reject<=!clear && collision;
            if (!clear && !collision) begin
                if (wr_en && full && !overwrite_en) overflow<=1'b1;
                if (rd_en && empty) underflow<=1'b1;
            end
        end
    end
`ifdef FORMAL
    always_comb begin
        assert(count<=DEPTH_C);
        assert(!(write_fire && full && !overwrite_en));
        assert(!(read_fire && empty));
        assert(!(snapshot_fire && (write_fire || read_fire)));
    end
`endif
endmodule
`default_nettype wire
