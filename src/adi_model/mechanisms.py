"""ADI 公开机制的可复用内核入口。

这些模块从 20b10mtask 迁入，默认全关，供 profile/实验显式开启。
它们**尚未**接入 `DefaultModel.simulate` 的主链；接入属于 W6 的后续工作，
不能因为本文件存在就宣称主链已复现。
"""

from adi_model.impairments import ImpairmentSet, all_of, only
from adi_model.noise import FrontEndNoise, NoiseConfig, flicker, front_end_noise, thermal_sigma, white
from adi_model.reference import (
    ReferenceConfig,
    dynamic_reference,
    precharge_fraction,
    ra_transfer,
    required_tau,
    settle_error,
)
from adi_model.timing_jitter import (
    TimingConfig,
    aperture_jitter,
    dac_pole_attenuation,
    jitter_limited_snr,
    pole_from_bandwidth_multiple,
    sine_slope,
)
from adi_model.transients import CoupledNode, DynamicConfig, SineDrive, rail_recovery, ra_endpoint

__all__ = [
    "CoupledNode",
    "DynamicConfig",
    "FrontEndNoise",
    "ImpairmentSet",
    "NoiseConfig",
    "ReferenceConfig",
    "SineDrive",
    "TimingConfig",
    "all_of",
    "aperture_jitter",
    "dac_pole_attenuation",
    "dynamic_reference",
    "flicker",
    "front_end_noise",
    "jitter_limited_snr",
    "only",
    "pole_from_bandwidth_multiple",
    "precharge_fraction",
    "ra_endpoint",
    "ra_transfer",
    "rail_recovery",
    "required_tau",
    "settle_error",
    "sine_slope",
    "thermal_sigma",
    "white",
]
