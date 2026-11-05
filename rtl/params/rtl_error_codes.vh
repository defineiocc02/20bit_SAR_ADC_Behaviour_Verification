// Static interface constants, independent of generated rtl_params.vh. Values are the externally
// visible error ABI: do not renumber during cleanup. These are error codes, not the per-transaction
// dout_flags bit positions. The top-level priority encoder and individual producers define
// persistence; declaring a code here does not imply that status_regs latches the code. ERR_NONE is
// zero; nonzero codes identify configuration or protocol failure.
// Include INSIDE each module: this enumeration has module-local scope. Its shared values do not
// imply that every consumer produces every error.
typedef enum logic [31:0] {
  ERR_NONE = 32'd0,
  ERR_W_RANGE = 32'd1,
  ERR_W_SUM = 32'd2,
  ERR_RANGE_EMPTY = 32'd3,
  ERR_V_RANGE = 32'd4,
  ERR_CFG_WRITE = 32'd5,
  ERR_INCOMPLETE = 32'd6,
  ERR_SADC_NOT_READY = 32'd7,
  ERR_ADC2_NOT_READY = 32'd8,
  ERR_RECON_BUSY = 32'd9,
  // Code10 was retired with the scalar-dither mode interface; do not reuse it.
  ERR_RDAC_RANGE = 32'd11,
  ERR_CAAZ_PAIR = 32'd12,
  ERR_SOURCE_BINDING = 32'd13
} rtl_error_code_t;
