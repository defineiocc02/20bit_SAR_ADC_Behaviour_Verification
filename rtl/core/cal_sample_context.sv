// Two distinct sample contexts: the current RDAC residue and the previous
// residue now held by ADC2. Capture physical switch decisions, not live DEM
// state or current bank identity. Coefficients are locked for the entire epoch.
`include "rtl_params.vh"
module cal_sample_context (
    input wire clk,rst_n,enable,residue_capture,quiet_sample,current_valid,
    input wire [31:0] current_id,
    input wire [N_ACTIVE-1:0][4:0] current_slices,
    input wire [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] current_main,
    input wire [N_ACTIVE-1:0][N_UNIT_SUB-1:0] current_sub,
    input wire [2*DITHER_UNITS_RANGE-1:0] current_rails,
    input wire current_sampling,
    input wire signed [63:0] current_injection,
    input wire analog_bad,current_bad,
    output logic fine_valid,
    output logic [31:0] fine_id,
    output logic [N_ACTIVE-1:0][4:0] fine_slices,
    output logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] fine_main,
    output logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0] fine_sub,
    output logic [2*DITHER_UNITS_RANGE-1:0] fine_rails,
    output logic fine_sampling,fine_analog_bad,
    output logic signed [63:0] fine_injection
);
  // Packed packet layout is local; named ports preserve independent debug.
  localparam int CW=32+int'(N_ACTIVE)*(5+int'(N_UNIT_MAIN)+int'(N_UNIT_SUB))+
                    2*int'(DITHER_UNITS_RANGE)+1+64;
  logic [CW-1:0] residue;
  logic residue_valid,residue_bad;
  always_ff @(posedge clk) begin
    if(!rst_n || !enable) begin
      residue<='0;residue_valid<=0;residue_bad<=0;fine_valid<=0;fine_id<='0;
      fine_slices<='0;fine_main<='0;fine_sub<='0;fine_rails<='0;
      fine_sampling<=0;fine_injection<='0;fine_analog_bad<=0;
    end else begin
      if(residue_capture) begin
        residue_valid<=current_valid;residue_bad<=current_bad;
        residue<={current_id,current_slices,current_main,current_sub,
                  current_rails,current_sampling,current_injection};
      end
      if(quiet_sample) begin
        fine_valid<=residue_valid;
        {fine_id,fine_slices,fine_main,fine_sub,fine_rails,fine_sampling,fine_injection}<=residue;
        fine_analog_bad<=analog_bad || residue_bad;
        residue_valid<=0;
      end
    end
  end
endmodule
