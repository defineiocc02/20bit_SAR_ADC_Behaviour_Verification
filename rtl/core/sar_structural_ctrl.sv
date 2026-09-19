// Source-aligned digital macro controls, with explicit implementation choices:
// dual seeded binary SARs, shared 3-bit flash, causal 8/18 allocation, and one
// shared backend converting the PREVIOUS residue. See ADR0018 for disclosure
// limits, physical unit mapping and timing. All comparator inputs share clk;
// the analog macro must meet setup/hold and assert valid after settling.
`include "rtl_params.vh"
module sar_structural_ctrl #(
    parameter int P_REF_ON=10,
    parameter int P_RESIDUE_CAPTURE=9,
    parameter logic [31:0] P_SHUFFLE_SEED=32'h6d2b79f5
) (
    input wire clk,rst_n,enable,dem_en,bridge_en,sampling_en,quantizer_en,
    input wire [6:0] flash_therm,
    input wire flash_valid,
    input wire [1:0] coarse_cmp_valid,coarse_cmp_ge,
    input wire fine_cmp_valid,fine_cmp_ge,
    input wire signed [63:0] injection_q,
    input wire analog_bad,recon_busy,
    output wire [3:0] phase,
    output wire quiet_sample,tp_clock,ra_az,ra_amplify,ref_precharge,ref_accurate,
    output wire [17:0] acquiring_mask,converting_mask,
    output wire [17:0] aux_charge_enable,hold_low_enable,
    output wire [1:0] coarse_compare_enable,
    output logic [1:0] coarse_acquire_enable,
    output wire flash_acquire_enable,fine_acquire_enable,
    output wire [1:0][8:0] coarse_trial,
    output wire [1:0][7:0] quantizer_dither,
    output wire [3:0] acquisition_dither_rails,
    output wire [11:0] fine_trial,
    output wire fine_compare_enable,
    output wire flash_sample,
    output wire [17:0] slice_sel,
    output wire [17:0][62:0] main_sw,
    output wire [17:0][7:0] sub_sw,
    output wire [17:0][3:0] dither_sw,
    output logic sw_valid,
    output wire recon_start,
    output wire [31:0] context_id,
    output wire [7:0][4:0] context_slices,
    output wire [7:0][62:0] context_main,
    output wire [7:0][7:0] context_sub,
    output wire [3:0] context_rails,
    output wire context_sampling,context_analog_bad,
    output wire signed [63:0] context_injection,
    output logic [11:0] fine_code,
    output logic [31:0] error_code
);
  `include "rtl_error_codes.vh"
  wire advance,capture;
  analog_phase_ctrl #(.P_REF_ON(P_REF_ON),.P_CAPTURE(P_RESIDUE_CAPTURE)) u_phase(.clk(clk),.rst_n(rst_n),.enable(enable),.phase(phase),
    .quiet_sample(quiet_sample),.bank_advance(advance),.residue_capture(capture),
    .tp_clock(tp_clock),.ra_az(ra_az),.ra_amplify(ra_amplify),
    .ref_precharge(ref_precharge),.ref_accurate(ref_accurate));
  wire [7:0][4:0] acq_ids,conv_ids;
  wire conv_valid;
  wire [31:0] current_id;
  slice_pool_ctrl #(.P_SEED(P_SHUFFLE_SEED)) u_pool(.clk(clk),.rst_n(rst_n),.enable(enable),.advance(advance),
    .shuffle(dem_en),.acquiring_ids(acq_ids),.converting_ids(conv_ids),
    .acquiring_mask(acquiring_mask),.converting_mask(converting_mask),
    .conversion_valid(conv_valid),.sample_id(current_id));
  // Aux supplies parasitic/boost charge while acquiring (patent13 Fig2/11).
  // Optional capacitor precharge embodiments require a separate analog macro.
  assign aux_charge_enable=(enable && rst_n)?acquiring_mask:'0;
  assign hold_low_enable=(enable && rst_n)?~acquiring_mask:'1;
  assign flash_sample=quiet_sample;
  assign flash_acquire_enable=enable && rst_n && (phase>=2 || phase==0);
  assign fine_acquire_enable=enable && rst_n && (phase>=14 || phase==0);
  logic bank;
  wire [2:0] flash_code;
  sadc_enc #(.P_B1(3)) u_flash(.cmp_raw(flash_therm),.sadc_code(flash_code));
  wire [1:0] coarse_busy,coarse_done,coarse_update;
  wire [1:0][8:0] resolved;
  for(genvar b=0;b<2;b++) begin : g_coarse
    sar_trial_ctrl #(.P_BITS(9),.P_SEED_BITS(3)) u_sar(
      .clk(clk),.rst_n(rst_n),.enable(enable),
      .start(advance && flash_valid && bank==1'(b)),.cancel(phase==8),
      .seed_code({flash_code,6'b0}),.cmp_valid(coarse_cmp_valid[b]),.cmp_ge(coarse_cmp_ge[b]),
      .trial_code(coarse_trial[b]),.resolved_code(resolved[b]),.busy(coarse_busy[b]),
      .done(coarse_done[b]),.resolved_valid(coarse_update[b]),.compare_enable(coarse_compare_enable[b]));
  end
  wire fine_busy,fine_done,fine_update;
  wire [11:0] fine_resolved;
  wire context_valid;
  sar_trial_ctrl #(.P_BITS(12),.P_SEED_BITS(0)) u_fine(
    .clk(clk),.rst_n(rst_n),.enable(enable),.start(advance && context_valid),.cancel(phase==14),
    .seed_code(12'd0),.cmp_valid(fine_cmp_valid),.cmp_ge(fine_cmp_ge),
    .trial_code(fine_trial),.resolved_code(fine_resolved),.busy(fine_busy),.done(fine_done),
    .resolved_valid(fine_update),.compare_enable(fine_compare_enable));
  wire [8:0] sid_a,sid_b;
  logic [8:0] sid;
  dem_state_gen u_dem(.clk(clk),.rst_n(rst_n && enable),.dem_en(dem_en),
    .load(1'b0),.init_a(9'd0),.init_b(9'd0),.en_a(advance && !bank),.en_b(advance && bank),
    .sid_a(sid_a),.sid_b(sid_b));
  wire [62:0][5:0] main_order;
  wire [7:0][2:0] sub_order;
  dem_addr_gen u_order(.sid(sid),.main_logical(main_order),.sub_logical(sub_order));
  wire signed [7:0] random_dither;
  wire random_valid;
  logic signed [7:0] acquiring_dither,current_dither;
  logic signed [63:0] current_injection;
  dither_gen u_random(.clk(clk),.rst_n(rst_n && enable),.en(advance),
    .dither_code(random_dither),.valid(random_valid));
  for(genvar b=0;b<2;b++) begin : g_qdither
    assign quantizer_dither[b]=!quantizer_en?8'd0:
      ((bank==1'(b))?current_dither:acquiring_dither);
  end
  // For D=2: negative rails followed by positive rails, matching unit_therm.
  for(genvar d=0;d<4;d++) begin : g_acq_rails
    assign acquisition_dither_rails[d]=sampling_en && ((int'(d)-2)<int'($signed(acquiring_dither)));
  end
  wire [7:0][6:0] main_count;
  wire [7:0][2:0] sub_count;
  wire [7:0][62:0] main_on;
  wire [7:0][7:0] sub_on;
  wire [3:0] rails;
  wire rdac_over;
  wire signed [15:0] remove_dither=quantizer_en? -16'(current_dither):16'sd0;
  swap_decode u_decode(.coarse(resolved[bank]),.dither_code(remove_dither),
    .dem_en(dem_en),.bridge_en(bridge_en && sid[8]),.sid_low(sid[2:0]),
    .main_count(main_count),.sub_count(sub_count),.rdac_over(rdac_over));
  unit_therm u_units(.main_logical(main_order),.sub_logical(sub_order),
    .main_count(main_count),.sub_count(sub_count),.bank_dither(sampling_en?current_dither:8'sd0),
    .main_on(main_on),.sub_on(sub_on),.dither_rail(rails));
  wire load_rdac=enable && conv_valid && coarse_update[bank] && phase>=2 && phase<=8;
  rdac_drv u_rdac(.clk(clk),.rst_n(rst_n && enable),.load(load_rdac),.slice_id(conv_ids),
    .main_on(main_on),.sub_on(sub_on),.dither_rail(rails),.slice_sel(slice_sel),
    .main_sw(main_sw),.sub_sw(sub_sw),.dither_sw(dither_sw));
  logic coarse_ok,fine_ok;
  cal_sample_context u_context(.clk(clk),.rst_n(rst_n),.enable(enable),
    .residue_capture(capture),.quiet_sample(quiet_sample),.current_valid(conv_valid && coarse_ok),
    .current_id(current_id),.current_slices(conv_ids),.current_main(main_on),.current_sub(sub_on),
    .current_rails(rails),.current_sampling(sampling_en),.current_injection(current_injection),
    .analog_bad(analog_bad),.current_bad(rdac_over),.fine_valid(context_valid),.fine_id(context_id),
    .fine_slices(context_slices),.fine_main(context_main),.fine_sub(context_sub),
    .fine_rails(context_rails),.fine_sampling(context_sampling),
    .fine_analog_bad(context_analog_bad),.fine_injection(context_injection));
  assign recon_start=enable && phase==15 && context_valid && fine_ok && !recon_busy;
  always_ff @(posedge clk) begin
    if(!rst_n || !enable) begin
      coarse_acquire_enable<=2'b00;bank<=0;sid<='0;acquiring_dither<=0;current_dither<=0;current_injection<=0;
      coarse_ok<=0;fine_ok<=0;fine_code<=0;sw_valid<=0;error_code<=0;
    end else begin
      sw_valid<=load_rdac;
      if(quiet_sample) begin
        bank<=!bank;current_dither<=acquiring_dither;current_injection<=injection_q;
        coarse_ok<=0;fine_ok<=0;
      end
      if(advance) begin
        coarse_acquire_enable<=bank?2'b01:2'b10;
        sid<=bank?sid_b:sid_a;
        acquiring_dither<=((sampling_en || quantizer_en) && random_valid)?random_dither:8'sd0;
        if(!flash_valid) error_code<=ERR_SADC_NOT_READY;
      end
      if(phase==8) begin
        coarse_ok<=coarse_done[bank];
        if(conv_valid && !coarse_done[bank]) error_code<=ERR_SADC_NOT_READY;
      end
      if(phase==14) begin
        fine_ok<=fine_done;fine_code<=fine_resolved;
        if(context_valid && !fine_done) error_code<=ERR_ADC2_NOT_READY;
      end
      if(capture && conv_valid && rdac_over) error_code<=ERR_RDAC_RANGE;
      if(phase==15 && context_valid && fine_ok && recon_busy) error_code<=ERR_RECON_BUSY;
    end
  end
endmodule
