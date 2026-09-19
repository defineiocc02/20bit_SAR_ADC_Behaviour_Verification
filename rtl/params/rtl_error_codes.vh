// Static interface constants, independent of generated rtl_params.vh.
// Include INSIDE each module: intentionally no global include guard because
// these localparams belong to each module scope.
localparam logic [31:0] ERR_NONE          = 32'd0;
localparam logic [31:0] ERR_W_RANGE       = 32'd1;
localparam logic [31:0] ERR_W_SUM         = 32'd2;
localparam logic [31:0] ERR_RANGE_EMPTY   = 32'd3;
localparam logic [31:0] ERR_V_RANGE       = 32'd4;
localparam logic [31:0] ERR_CFG_WRITE     = 32'd5;
localparam logic [31:0] ERR_INCOMPLETE    = 32'd6;
localparam logic [31:0] ERR_SADC_NOT_READY = 32'd7;
localparam logic [31:0] ERR_ADC2_NOT_READY = 32'd8;
localparam logic [31:0] ERR_RECON_BUSY    = 32'd9;
localparam logic [31:0] ERR_DITHER_MODE   = 32'd10;
localparam logic [31:0] ERR_RDAC_RANGE    = 32'd11;
