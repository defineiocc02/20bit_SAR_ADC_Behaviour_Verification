// =============================================================================
// 文件名     : files_ad4080_silicon_shell.f
// 功能       : AD4080 外围硬件边界闭环封装清单（CRC24 / OTP / SRAM / LVDS PHY）。
// 设计说明   : 单文件单模块，独立于冻结的 rtl/ad4080_periph/ 清单，供流片装配与 CI 检查。
// =============================================================================
rtl/top/ad4080_crc24_service.sv
rtl/top/ad4080_otp_macro_adapter.sv
rtl/top/ad4080_sram_bank_foundry_wrap.sv
rtl/top/ad4080_result_store_foundry.sv
rtl/top/ad4080_lvds_phy_wrapper.sv
