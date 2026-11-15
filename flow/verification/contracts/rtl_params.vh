// Explicit Config/format/binding inputs. No device or physical-mapper defaults.
// Source macro/timing correspondence is not certified by this header.
`ifndef RTL_PARAMS_VH
`define RTL_PARAMS_VH
localparam int unsigned W_FRAC = 32'd30;
localparam int unsigned W_BITS = 32'd48;
localparam int unsigned V_FRAC = 32'd32;
localparam int unsigned V_BITS = 32'd64;
localparam int unsigned ACC_BITS = 32'd96;
localparam int unsigned OUT_BITS = 32'd20;
localparam int unsigned N_SLICES = 32'd18;
localparam int unsigned N_ACTIVE = 32'd8;
localparam int unsigned N_UNIT_MAIN = 32'd63;
localparam int unsigned N_UNIT_SUB = 32'd8;
localparam int unsigned N_UNIT_TOTAL = 32'd71;
localparam int unsigned B1 = 32'd9;
localparam int unsigned ADC2_BITS = 32'd12;
localparam int unsigned PHASES = 32'd16;
localparam int unsigned CONTROL_WORD_BITS = 32'd6;
localparam int unsigned STATE_BITS = 32'd1;
localparam int unsigned RECON_UNROLL = 32'd7;
localparam int unsigned POOL_SHUFFLE_SEED = 32'd1831565813;
localparam int unsigned POOL_SHUFFLE_ENABLE = 32'd1;
localparam int unsigned SOURCE_DEM_REQUEST = 32'd0;
localparam int unsigned SOURCE_DITHER_REQUEST = 32'd1;
localparam int unsigned CAL_OBS_BITS = 32'd733;
`endif
