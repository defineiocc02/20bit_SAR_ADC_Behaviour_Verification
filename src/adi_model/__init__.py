"""adi_model -- ADI ISSCC2024 9.8（20b 40MS/s 精度 SAR）两级残差结构的行为仿真平台。

一句话模型：**用固定的物理电容阵列产生真实误差，用可替换的数字映射算法决定
如何使用阵列，最后通过残差重构观察误差究竟去了哪里。**

三条底线
--------
1. **物理失配固定**：chip.C_true 每颗虚拟芯片只生成一次，整段仿真不变；
   采样热噪声按采样事件更新，且同一实现贯穿该样本的后续处理。
2. **数字映射守恒**：对同一逻辑码，任意 DEM 状态下 DAC_nominal 必须相同。
   本实现只做等权单位置换，守恒是结构性保证。
3. **噪声相关性真实**：同一个 n_R 同时进入残差通路与 KTC 观测通路；
   两条放大通路分别检查摆幅后再相减。

来源边界
--------
* 来自 [00_1]ISSCC2024_ppt.pdf：fs=40MS/s、差分 ±3V、20b/94.6dB DR、
  8.8nV/rtHz、18 slice 池（8+8+2 spare）、3 维 DEM、RDAC 20.5pF、
  SADC/RDAC >12b AC matching、G0=32（PPT 图标注）。
* 来自论文 [00] 正文（2026-09-10 逐字审计补充）：~0.6ps timing mismatch、
  交织误差四件套（offset/gain/timing/bandwidth）、动态参考缓冲 65% RA 相、
  dither 量程量化器→RDAC 增强 2b、"9b quantization in the first stage"
  （口径读法见 config.py 头注释）。
* **不是** ADI 已公开实现的模块：KTC 校正支路（本次要研究的扩展）。
* 探索性模型参数（需 PDK 替换）：mismatch_sigma0、cap_scale、ktc_gain_n、
  ktc_noise_n、c_sadc、chi。

文档标准（全库强制，改代码前先读本节）
----------------------------------------
每个模块的 docstring 必须含五要素，缺一即为不达标：
1. **职责一句话**：本模块做什么、不做什么（边界）。
2. **物理模型与文献出处**：公式给到论文/PPT/专利条款号；
   行为近似要声明近似层级（如 "一阶 RC、聚合口径"）。
3. **单位契约**：每个对外量的单位（V / F / s / 单位当量 / 无量纲），
   以及输入参考 vs 输出口径的归属。
4. **参数来源分级**：[披露]（论文/PPT 原文）/ [拟合]（复现基准标定）/
   [假设]（工艺估算，不得用于良率结论）——禁止混用、禁止反推值当证据。
5. **契约与不变量 / 适用域**：谁的状态被改（Side effects）、什么场景
   数值会失效、哪些历史教训"勿回退"。

公开函数/类的 docstring 必须有 Args/Returns/Side effects；内联注释写
"为什么"而不是"做了什么"。改动共享函数后必须跑全链路 run_all.py
（只冒烟本模块测不到其它模块的引用路径——d_new 事故的教训）。

用法
----
    from adi_model import Config, run_sim, sine_input, experiments
    cfg = Config(dem_enable=True)
    res = run_sim(cfg, sine_input(2.7, 1.0e6), 2**15)
"""

from . import experiments
from .adc2 import ADC2
from .aux_input import AUX_GRADES, AUX_MODES, AuxInputStage, build_stage
from .calib import (
    apply_weight_correction,
    calibrate_unit_weights,
    null_space_projection_error,
    observability,
    required_samples,
    ridge_fit,
    use_matrix,
)
from .chip import Chip, build_chip, rescale_chip
from .config import (
    K_B,
    LEGAL_VALUES,
    TEMP_K,
    Config,
    ConfigError,
    dac_error_scaling_note,
    mismatch_from_pdk,
    noise_budget,
    resolve_ra_noise,
)
from .dac_arch import SplitChip, SplitDAC, build_split_chip, split_chip_draw
from .dynamics import (
    apply_dynamics,
    crosstalk_error,
    input_settling_eps,
    ref_settling_error,
    switching_activity,
)
from .interleave_tracking import (
    TRACK_GRADES,
    TRACK_MODES,
    InterleavedSAR,
    TrackPolicy,
    TrackRunResult,
    filter_bw_absolute,
    filter_bw_relative,
    noise_ratio_from_bw,
)
from .ktc import KTCBranch
from .mapper import DitherState, Mapper, SwitchCommand, dem_state_sequence, make_dither_state
from .metrics import inl_from_mean_error, sine_fit_metrics, spectrum_dbfs, static_test, summarize
from .provenance import (
    PARAM_GRADES,
    Graded,
    GradingError,
    SourceGrade,
    annotate_config,
    audit_provenance,
    grade_of,
)
from .ra import ResidueAmplifier
from .rdac import RDAC, DACLut
from .reconstruction import Calibrator, DigitalState, initialize_state, reconstruct
from .ref_track import (
    REF_GRADES,
    RefTrackConfig,
    RefTrackRun,
    RefTrackSim,
    reference_precision_bits,
)
from .sadc import SADC
from .sampler import (
    SampleBatch,
    capture,
    dc_input,
    input_derivative,
    sine_input,
    spectral_derivative,
)
from .scheduler import Allocation, Scheduler
from .sim import SimResult, run_sim, run_with_calibration
from .sim_split import run_sim_split
from .slice_pool import PhysicalSlicePool, SlicePlan, check_causality

__all__ = [
    "Config",
    "ConfigError",
    "LEGAL_VALUES",
    "K_B",
    "TEMP_K",
    "noise_budget",
    "resolve_ra_noise",
    "mismatch_from_pdk",
    "dac_error_scaling_note",
    "Chip",
    "build_chip",
    "rescale_chip",
    "Scheduler",
    "Allocation",
    "SampleBatch",
    "capture",
    "dc_input",
    "sine_input",
    "input_derivative",
    "spectral_derivative",
    "SADC",
    "Mapper",
    "SwitchCommand",
    "DitherState",
    "dem_state_sequence",
    "make_dither_state",
    "RDAC",
    "DACLut",
    "ResidueAmplifier",
    "KTCBranch",
    "ADC2",
    "Calibrator",
    "DigitalState",
    "initialize_state",
    "reconstruct",
    "sine_fit_metrics",
    "spectrum_dbfs",
    "static_test",
    "inl_from_mean_error",
    "summarize",
    "SimResult",
    "run_sim",
    "run_with_calibration",
    "experiments",
    "SplitChip",
    "SplitDAC",
    "build_split_chip",
    "split_chip_draw",
    "apply_dynamics",
    "input_settling_eps",
    "ref_settling_error",
    "crosstalk_error",
    "switching_activity",
    "use_matrix",
    "observability",
    "ridge_fit",
    "calibrate_unit_weights",
    "apply_weight_correction",
    "required_samples",
    "null_space_projection_error",
    "run_sim_split",
    # --- provenance: machine-checkable source grading (the audit's fix) -----
    "SourceGrade",
    "Graded",
    "GradingError",
    "PARAM_GRADES",
    "grade_of",
    "annotate_config",
    "audit_provenance",
    # --- physical slice pool: cross-cycle causal interleaving ---------------
    "PhysicalSlicePool",
    "SlicePlan",
    "check_causality",
    # --- patent mechanisms [12]/[13]/[14]: standalone technique models ------
    "TrackPolicy",
    "TrackRunResult",
    "InterleavedSAR",
    "TRACK_GRADES",
    "TRACK_MODES",
    "filter_bw_relative",
    "filter_bw_absolute",
    "noise_ratio_from_bw",
    "AuxInputStage",
    "build_stage",
    "AUX_GRADES",
    "AUX_MODES",
    "RefTrackConfig",
    "RefTrackRun",
    "RefTrackSim",
    "REF_GRADES",
    "reference_precision_bits",
]
