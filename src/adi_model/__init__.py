# =============================================================================
# 文件名     : __init__.py
# 模块名     : adi_model
# 版本       : v1.0
# 功能       : 统一导出模型、组件、合同数据类型及测量入口。
#
# 设计说明   :
#   * 顶层只组织公共绑定。组件依赖实际参数，在对应消费者中检查输入。
#   * 在顶层导出的 DigitalObservation 与 FrozenCalibration 属于通用合同。
#     拟合专用的同名结构应从 calibration.weight_calibration 导入，以免
#     把事务观测与权重拟合的样本结构混用。
#   * 论文来源值与项目的模型参数分开导出。来源引用不构成本次仿真的输入。
# -----------------------------------------------------------------------------
# 变更记录   : 去掉遮蔽合同类型的重复导入，保持原有顶层绑定。
# =============================================================================

"""原文约束下的 SAR ADC 组件与数字观测接口。

导入包只加载组件定义，不创建芯片或执行实验。原文未披露的物理输入由实际消费者显式检查。
来源元数据、配置合法性、数值模型与 RTL 证据分别报告；配置输入检查成功不代表电路或芯片性能已经复现。
"""

# =============================================================================
# 1. 公共绑定
# =============================================================================
# 这里使用包限定名直接绑定对象，数值实现仍定义在子模块里。
# 调试时如需访问未列出的组件，应从定义它的子模块导入，不依赖偶然出现的顶层名字。
from adi_model.behavioral.adc2 import (
    ADC2,
)
from adi_model.behavioral.charge_ref import (
    ref_ra_charge,
    ref_ra_charge_nodal,
)
from adi_model.behavioral.conversion import (
    ConversionEngine,
    ConversionParameters,
    ConversionResult,
    response_interval,
)
from adi_model.behavioral.dynamics import (
    crosstalk_error,
    switching_activity,
)
from adi_model.behavioral.input_network import (
    InputNetworkParameters,
    OffsetWaveform,
    PassiveTrackingNetwork,
    track_interval,
)
from adi_model.behavioral.low_frequency_noise import (
    BandLimitedFlicker,
)
from adi_model.behavioral.pretracking import (
    DecisionRecord,
    InputAssistTrace,
    PretrackPrediction,
    QuantizedPretracker,
)
from adi_model.behavioral.ra import (
    ResidueAmplifier,
)
from adi_model.behavioral.ra_az import (
    OrdinaryAZConfig,
    OrdinaryAZResult,
    apply_ordinary_az,
)
from adi_model.behavioral.reference_charge import (
    ReferenceProgram,
    physical_reference_program,
    sampled_bottom_voltages,
)
from adi_model.behavioral.sadc import (
    SADC,
    build_first_stage_quantizer,
)
from adi_model.behavioral.sampler import (
    AnalyticInput,
    SampleCapture,
    capture,
    dc_input,
    input_derivative,
    sine_input,
    spectral_derivative,
)
from adi_model.calib import (
    Observability,
    group_aggregate,
    observability,
    required_samples,
)
from adi_model.calibration.reconstruction import (
    DigitalState,
    initialize_state,
    reconstruct,
)
from adi_model.calibration.weight_calibration import (
    CalibrationSpec,
    CalibrationUnidentifiableError,
    apply_frozen_calibration,
    fit_unit_weights,
)
from adi_model.contracts import (
    ADI_40M,
    DEFAULT_PROFILE_ID,
    ENG_10M,
    PATENT_FIG19,
    PROFILES,
    AcquisitionCommand,
    DigitalObservation,
    EventRecord,
    FrozenCalibration,
    PlantTrace,
    Profile,
    SampleContext,
    SwitchCommand,
    TopologySpec,
    assert_caaz_free,
    load_profile,
)
from adi_model.contracts.fixed_point import (
    CodeStream,
    FixedPointFormat,
    FixedPointReconstructor,
    round_even_divide,
)
from adi_model.control.dem import (
    REF_HIGH,
    REF_LOW,
    SIGNAL,
    SamplingConnections,
    SplitSwitchCommand,
    SplitSwitchGeometry,
    split_switch_command,
)
from adi_model.control.timing import (
    ScheduleBinding,
    SlicePlan,
    build_slice_plan,
)
from adi_model.defaults import DefaultModel, DefaultParameters, DefaultRun, default_model, simulate
from adi_model.metrics.benchmarks import (
    PAPER_BENCHMARK,
    SLIDES_BENCHMARK,
    PublishedBenchmark,
)
from adi_model.metrics.metrics import (
    inl_from_mean_error,
    integrate_noise_band,
    power_spectral_density,
    reference_precision_bits,
    sine_fit_metrics,
    spectrum_dbfs,
    static_test,
    summarize,
)
from adi_model.provenance import (
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
from adi_model.rtl_observation import (
    observations_from_rtl,
)
from adi_model.simulation.config import (
    Config,
    ConfigError,
)
from adi_model.simulation.pipeline import (
    run_pipeline,
)
from adi_model.simulation.scheduler import (
    Allocation,
    Scheduler,
    make_scheduler,
)
from adi_model.simulation.sim import (
    SimResult,
)
from adi_model.simulation.slice_pool import (
    PhysicalSlicePool,
    check_causality,
)
from adi_model.topology.dac_arch import (
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
from adi_model.topology.mapper import (
    MappedCodes,
    Mapper,
)

# =============================================================================
# 2. 公开入口
# =============================================================================
# 此清单用于明确用户可从顶层取得的对象。阅读属性单位和输入有效域时，
# 以对应子模块的合同和函数说明为准，避免把一个同名动作套用到另一条仿真链。
__all__ = [
    # 默认 bundle 由调用者用显式输入信号、点数和 seed 建立。
    # 默认入口给出名义模型，定制多个实验条件时应保留各次配置与结果身份。
    # 功率密度和积分噪声使用不同带宽口径，摘录结果时应带单位与采样区间。
    "DefaultParameters",
    "DefaultModel",
    "DefaultRun",
    "default_model",
    "simulate",
    "power_spectral_density",
    "integrate_noise_band",
    "PARAM_UNITS",
    # Profile 保存工程目标与来源归属。事务合同保存样本 ID 与事件身份，
    # 这些记录可跨模型或工具比对，仍需要具体执行路径实现对应逻辑。
    # 默认 profile 不替用户补造未提供的物理开关网络。
    "ADI_40M",
    "DEFAULT_PROFILE_ID",
    "ENG_10M",
    "PATENT_FIG19",
    "PROFILES",
    "AcquisitionCommand",
    "DigitalObservation",
    "EventRecord",
    "FrozenCalibration",
    "PlantTrace",
    "Profile",
    "SampleContext",
    "SwitchCommand",
    "TopologySpec",
    "assert_caaz_free",
    "load_profile",
    # 普通 AZ 是可配置的模拟残差处理。基线合同和研究型斩波扩展分开，
    # 组合实验应写明是否启用底层 AZ 以及使用哪条观测路径。
    # SADC 与 ADC2 各有量化域。调用方不能直接交换两级原码。
    "OrdinaryAZConfig",
    "OrdinaryAZResult",
    "apply_ordinary_az",
    "ADC2",
    "Config",
    "ConfigError",
    "SADC",
    "build_first_stage_quantizer",
    # 拟合函数依赖专门的批观测、可辨识性与权重规格。
    # 保留历史导出清单的名字和次序。两处重复列出的同名类型均指向上面的
    # 合同绑定；拟合调用方仍应按照 weight_calibration 参数类型准备记录。
    # 当训练矩阵不足以分离权重时，对外保留不可辨识错误而不返回名义拟合。
    "CalibrationSpec",
    "DigitalObservation",
    "FrozenCalibration",
    "CalibrationUnidentifiableError",
    "fit_unit_weights",
    "apply_frozen_calibration",
    # 定点层需要显式的位数和刻度。舍入与裁剪的边界由格式或重构器处理，
    # 输出码以及越界标志都应保留，用于和 RTL 对齐。
    # 浮点重构及状态初始化是独立入口。比对两条路径时必须使用同一批观测。
    "FixedPointFormat",
    "FixedPointReconstructor",
    "CodeStream",
    "round_even_divide",
    "DigitalState",
    "initialize_state",
    "reconstruct",
    # 可观测性与所需样本数是拟合设计诊断，不能当作拟合后的性能结果。
    # 电荷与 RTL 观测转换是对接入口。应用算法还需要把实际片号、掩码
    # 及该笔余差保存在同一记录中，以防拿到别的转换周期的数据。
    "Observability",
    "observability",
    "group_aggregate",
    "required_samples",
    "ref_ra_charge",
    "ref_ra_charge_nodal",
    "observations_from_rtl",
    # 指标函数按输入记录与统计窗口计算。引用 benchmark 时单列发表的
    # 数字；复现报告应注明模型、波形、采样和校准条件。
    # 来源分级约束对外声明，记录为未知项的参数不因执行成功自动变成已披露。
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
    # 放大器、噪声源和物理电容池是可独立验证的组件。名义码本标记
    # 理想的粗估计；实际电荷应按本次采用的开关状态与每片权重计算。
    # 采样态使用实际连接，转换态使用掩码。混用两套口径会改动余差归一化。
    "ResidueAmplifier",
    "BandLimitedFlicker",
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
    # SIGNAL、REF_LOW 和 REF_HIGH 编码底板连接。切片计划决定每笔
    # 使用哪组物理片；几何、连接与计划需分别传入，不能从一个掩码推断全部。
    # 同一笔片号约束采集和后续转换，check_causality 用于核对显式轨迹。
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
    # 跟踪、比较与参考程序有各自的输入状态和参数。调整参考序列时，
    # 应保留真实决定事件与采样状态；数字指令只可使用其时刻已决出的信息。
    # 这些组件导出不表示所有动态机制已被默认链自动启用。
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
    # 流水线执行已装配的模型，输出包含每笔关联身份与节点记录。
    # 对分析输入使用已知导数。频谱估计的导数需要适用的采样窗口，
    # 端点或非周期场景仍由调用者核对边界条件。
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
