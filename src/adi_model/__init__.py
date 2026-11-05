"""原文约束下的 SAR ADC 组件与数字观测接口。

导入包只加载组件定义，不创建芯片或执行实验。原文未披露的物理输入由实际消费者显式检查。
来源元数据、配置合法性、数值模型与 RTL 证据分别报告；配置输入检查成功不代表电路或芯片性能已经复现。
"""

from .adc2 import (
    ADC2,
)
from .benchmarks import (
    PAPER_BENCHMARK,
    SLIDES_BENCHMARK,
    PublishedBenchmark,
)
from .calib import (
    Observability,
    group_aggregate,
    observability,
    required_samples,
)
from .charge_ref import (
    ref_ra_charge,
    ref_ra_charge_nodal,
)
from .chopping_az import (
    ChoppingAZParameters,
    ChoppingAZResult,
    pair_readout,
    run_caaz_pipeline,
)
from .config import (
    Config,
    ConfigError,
)
from .conversion import (
    ConversionEngine,
    ConversionParameters,
    ConversionResult,
    response_interval,
)
from .dac_arch import (
    DACCharge,
    NominalCodebook,
    SamplingCharge,
    SplitChip,
    SplitDAC,
    build_split_chip,
    charge_from_masks,
    charge_from_sampling_connections,
    split_capacitance_coefficients,
    topplate_voltage_from_effective_charge,
)
from .defaults import DefaultModel, DefaultParameters, DefaultRun, default_model, simulate
from .dem import (
    REF_HIGH,
    REF_LOW,
    SIGNAL,
    SamplingConnections,
    SplitSwitchCommand,
    SplitSwitchGeometry,
    split_switch_command,
)
from .dynamics import (
    crosstalk_error,
    switching_activity,
)
from .fixed_point import (
    CodeStream,
    FixedPointFormat,
    FixedPointReconstructor,
    round_even_divide,
)
from .input_network import (
    InputNetworkParameters,
    OffsetWaveform,
    PassiveTrackingNetwork,
    track_interval,
)
from .low_frequency_noise import (
    BandLimitedFlicker,
)
from .mapper import (
    MappedCodes,
    Mapper,
)
from .metrics import (
    inl_from_mean_error,
    integrate_noise_band,
    power_spectral_density,
    reference_precision_bits,
    sine_fit_metrics,
    spectrum_dbfs,
    static_test,
    summarize,
)
from .pipeline import (
    run_pipeline,
)
from .pretracking import (
    DecisionRecord,
    InputAssistTrace,
    PretrackPrediction,
    QuantizedPretracker,
)
from .provenance import (
    PARAM_GRADES,
    PARAM_UNITS,
    SOURCE_VALUES,
    Graded,
    GradingError,
    SourceGrade,
    annotate_config,
    audit_provenance,
    grade_of,
)
from .ra import (
    ResidueAmplifier,
)
from .reconstruction import (
    DigitalState,
    initialize_state,
    reconstruct,
)
from .reference_charge import (
    ReferenceProgram,
    physical_reference_program,
    sampled_bottom_voltages,
)
from .rtl_observation import (
    observations_from_rtl,
)
from .sadc import (
    SADC,
    build_first_stage_quantizer,
)
from .sampler import (
    AnalyticInput,
    SampleCapture,
    capture,
    dc_input,
    input_derivative,
    sine_input,
    spectral_derivative,
)
from .scheduler import (
    Allocation,
    Scheduler,
    make_scheduler,
)
from .sim import (
    SimResult,
)
from .slice_pool import (
    PhysicalSlicePool,
    check_causality,
)
from .timing import (
    ScheduleBinding,
    SlicePlan,
    build_slice_plan,
)
from .weight_calibration import (
    CalibrationSpec,
    CalibrationUnidentifiableError,
    DigitalObservation,
    FrozenCalibration,
    apply_frozen_calibration,
    fit_unit_weights,
)

__all__ = [
    "DefaultParameters",
    "DefaultModel",
    "DefaultRun",
    "default_model",
    "simulate",
    "power_spectral_density",
    "integrate_noise_band",
    "PARAM_UNITS",
    "ADC2",
    "Config",
    "ConfigError",
    "SADC",
    "build_first_stage_quantizer",
    "CalibrationSpec",
    "DigitalObservation",
    "FrozenCalibration",
    "CalibrationUnidentifiableError",
    "fit_unit_weights",
    "apply_frozen_calibration",
    "FixedPointFormat",
    "FixedPointReconstructor",
    "CodeStream",
    "round_even_divide",
    "DigitalState",
    "initialize_state",
    "reconstruct",
    "Observability",
    "observability",
    "group_aggregate",
    "required_samples",
    "ref_ra_charge",
    "ref_ra_charge_nodal",
    "observations_from_rtl",
    "inl_from_mean_error",
    "sine_fit_metrics",
    "spectrum_dbfs",
    "static_test",
    "summarize",
    "reference_precision_bits",
    "PublishedBenchmark",
    "PAPER_BENCHMARK",
    "SLIDES_BENCHMARK",
    "SourceGrade",
    "Graded",
    "GradingError",
    "PARAM_GRADES",
    "SOURCE_VALUES",
    "grade_of",
    "annotate_config",
    "audit_provenance",
    "ResidueAmplifier",
    "BandLimitedFlicker",
    "ChoppingAZParameters",
    "ChoppingAZResult",
    "pair_readout",
    "run_caaz_pipeline",
    "NominalCodebook",
    "SplitChip",
    "build_split_chip",
    "DACCharge",
    "SamplingCharge",
    "SplitDAC",
    "charge_from_masks",
    "charge_from_sampling_connections",
    "split_capacitance_coefficients",
    "topplate_voltage_from_effective_charge",
    "SIGNAL",
    "REF_LOW",
    "REF_HIGH",
    "SplitSwitchGeometry",
    "SplitSwitchCommand",
    "SamplingConnections",
    "split_switch_command",
    "SlicePlan",
    "ScheduleBinding",
    "build_slice_plan",
    "Allocation",
    "Scheduler",
    "make_scheduler",
    "PhysicalSlicePool",
    "check_causality",
    "InputNetworkParameters",
    "OffsetWaveform",
    "PassiveTrackingNetwork",
    "track_interval",
    "ConversionParameters",
    "ConversionResult",
    "ConversionEngine",
    "response_interval",
    "DecisionRecord",
    "PretrackPrediction",
    "InputAssistTrace",
    "QuantizedPretracker",
    "ReferenceProgram",
    "physical_reference_program",
    "sampled_bottom_voltages",
    "crosstalk_error",
    "switching_activity",
    "run_pipeline",
    "SimResult",
    "SampleCapture",
    "capture",
    "AnalyticInput",
    "dc_input",
    "sine_input",
    "input_derivative",
    "spectral_derivative",
    "Mapper",
    "MappedCodes",
]
