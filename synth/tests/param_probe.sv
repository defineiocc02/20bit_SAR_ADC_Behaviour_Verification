// Generic numerical parameter-elaboration fixture; no ADC or technology values.
// Record the caller's P_WIDTH/source hash with any later synthesis evidence.
// The one-bit default does not prove that a parameter override was consumed.
`timescale 1ns/1ps
`default_nettype none
module param_probe #(
    parameter int P_WIDTH = 1
) (
    input logic clk,
    input logic rst_n,
    input logic [P_WIDTH-1:0] a,
    input logic [P_WIDTH-1:0] b,
    output logic [P_WIDTH-1:0] y
);
    initial begin
        if (P_WIDTH < 1) $fatal(1, "param_probe: positive width required");
    end
    always_ff @(posedge clk) begin
        if (!rst_n) y <= '0;
        else y <= a ^ b;
    end
endmodule
`default_nettype wire
