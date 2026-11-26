# ==============================================================================
# 文件名     : __init__.py
# 模块名     : ad4080_otp_cal
# 版本       : v1.0
# 功能       : 统一导出 AD4080 360 物理权重正弦标定、频谱分析与 Q30 OTP 打包接口。
# 设计说明   : 保持顶层 API 简洁稳定，外部脚本可直接 import 本包完成校准与烧录流生成。
# 变更记录   :
#   2026-11-26  v1.0  初版发布。
# ==============================================================================
"""AD4080 18 片（360 物理权重）正弦前景标定与 Q30 OTP 打包工具包。"""

# ==============================================================================
# 1. 导出架构常量、前向采集与 wsum_v21 同口径重构接口
# ==============================================================================
from .architecture import (
    ADC_BITS,
    NUM_SLICES,
    ACTIVE_SLICES,
    MAIN_PER_SLICE,
    SUB_PER_SLICE,
    WEIGHTS_PER_SLICE,
    TOTAL_WEIGHTS,
    Q30_SCALE,
    nominal_weights_18x20,
    inject_capacitor_mismatch,
    simulate_ad4080_capture,
    reconstruct_wsum_v21,
)
# ==============================================================================
# 2. 导出 360 物理权重正弦前景标定、频谱评估与 Q30 OTP 打包接口
# ==============================================================================
from .sine_calibration import (
    SineCalibrationResult,
    calibrate_360_weights_sine,
)
from .spectrum import (
    SpectrumMetrics,
    find_coherent_frequency,
    analyze_spectrum,
    has_adctoolbox,
)
from .otp_packer import (
    OTPWeightImage,
    quantize_weights_q30,
    dequantize_weights_q30,
    crc24_compute,
    pack_otp_image,
    verify_otp_image,
    export_otp_artifacts,
)

__all__ = [
    "ADC_BITS",
    "NUM_SLICES",
    "ACTIVE_SLICES",
    "MAIN_PER_SLICE",
    "SUB_PER_SLICE",
    "WEIGHTS_PER_SLICE",
    "TOTAL_WEIGHTS",
    "Q30_SCALE",
    "nominal_weights_18x20",
    "inject_capacitor_mismatch",
    "simulate_ad4080_capture",
    "reconstruct_wsum_v21",
    "SineCalibrationResult",
    "calibrate_360_weights_sine",
    "SpectrumMetrics",
    "find_coherent_frequency",
    "analyze_spectrum",
    "has_adctoolbox",
    "OTPWeightImage",
    "quantize_weights_q30",
    "dequantize_weights_q30",
    "crc24_compute",
    "pack_otp_image",
    "verify_otp_image",
    "export_otp_artifacts",
]
