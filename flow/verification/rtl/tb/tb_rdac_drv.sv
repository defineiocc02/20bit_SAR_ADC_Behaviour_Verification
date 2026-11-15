// Verification testbench for rdac_drv — registered physical switch fanout.
//
// Contract: verification/contracts/digital_interfaces.yaml :: rdac_drv
//           verification/contracts/timing.yaml :: rdac_drv
// Output:   rdac_drv.out, one line per vector:
//             "<slice_sel> <main_sw> <sub_sw> <retired_cycle>"
//
// Sampling is done one delta after the accepting edge, which is what the
// contract's "load is captured on posedge" means. The bench records the cycle
// at which the value appeared so the scoreboard can catch a retime.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_rdac_drv;
  localparam int MAXV = 256;
  localparam int N_ACT = int'(N_ACTIVE);
  localparam int N_MAIN = int'(N_UNIT_MAIN);
  localparam int N_SUB = int'(N_UNIT_SUB);
  localparam int N_SLICE = int'(N_SLICES);

  logic [N_ACT*5-1:0]  id_mem   [0:MAXV-1];
  logic [N_ACT*N_MAIN-1:0] main_mem [0:MAXV-1];
  logic [N_ACT*N_SUB-1:0]  sub_mem  [0:MAXV-1];
  logic [0:0] load_mem [0:MAXV-1];
  logic [0:0] rst_mem  [0:MAXV-1];

  logic clk = 1'b0;
  logic rst_n, load;
  logic [N_ACT-1:0][4:0] slice_id;
  logic [N_ACT-1:0][N_MAIN-1:0] main_on;
  logic [N_ACT-1:0][N_SUB-1:0] sub_on;
  logic [N_SLICE-1:0] slice_sel;
  logic [N_SLICE-1:0][N_MAIN-1:0] main_sw;
  logic [N_SLICE-1:0][N_SUB-1:0] sub_sw;

  integer n_vectors, fd, i;
  integer cycle = 0;

  always #5 clk = ~clk;

  rdac_drv dut (
      .clk(clk), .rst_n(rst_n), .load(load),
      .slice_id(slice_id), .main_on(main_on), .sub_on(sub_on),
      .slice_sel(slice_sel), .main_sw(main_sw), .sub_sw(sub_sw)
  );

  function automatic logic [N_SLICE*N_MAIN-1:0] pack_main();
    integer s, b;
    begin
      pack_main = '0;
      for (s = 0; s < N_SLICE; s++)
        for (b = 0; b < N_MAIN; b++)
          pack_main[s*N_MAIN + b] = main_sw[s][b];
    end
  endfunction

  function automatic logic [N_SLICE*N_SUB-1:0] pack_sub();
    integer s, b;
    begin
      pack_sub = '0;
      for (s = 0; s < N_SLICE; s++)
        for (b = 0; b < N_SUB; b++)
          pack_sub[s*N_SUB + b] = sub_sw[s][b];
    end
  endfunction

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 1;
    $readmemh("vectors/slice_id.mem", id_mem);
    $readmemh("vectors/main_on.mem", main_mem);
    $readmemh("vectors/sub_on.mem", sub_mem);
    $readmemh("vectors/load.mem", load_mem);
    $readmemh("vectors/rst_n.mem", rst_mem);

    fd = $fopen("rdac_drv.out", "w");
    if (fd == 0) $fatal(1, "cannot open rdac_drv.out");

    rst_n = 1'b1;
    load = 1'b0;
    slice_id = '0;
    main_on = '0;
    sub_on = '0;
    @(negedge clk);

    for (i = 0; i < n_vectors; i = i + 1) begin
      @(negedge clk);
      rst_n = rst_mem[i][0];
      load = load_mem[i][0];
      slice_id = id_mem[i];
      main_on = main_mem[i];
      sub_on = sub_mem[i];
      @(posedge clk);
      #1;
      cycle = cycle + 1;
      $fdisplay(fd, "%0h %0h %0h %0d", slice_sel, pack_main(), pack_sub(), cycle);
    end
    $fclose(fd);
    $display("RDAC_DRV_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
