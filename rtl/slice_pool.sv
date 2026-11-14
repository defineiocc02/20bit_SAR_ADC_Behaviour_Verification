// D1 调度：第 s 笔样本的 8 个采集片 ID = (8 + 8s + i) mod 18。
// 输出打包为 [44:0]，每 5 位一个片 ID（iverilog 不支持 always_comb 驱动
// 非打包数组输出端口）。
module slice_pool (
    input  logic [7:0]  sample_i,
    output logic [44:0] sid_o
);
    genvar i;
    generate
        for (i = 0; i < 8; i++) begin : g_id
            assign sid_o[i*5 +: 5] = (5'd8 + (sample_i * 8) % 18 + i) % 18;
        end
    endgenerate
endmodule
