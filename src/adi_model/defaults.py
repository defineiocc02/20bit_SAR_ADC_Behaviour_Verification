"""集中、可覆盖且标明推断来源的工程默认入口。

本默认层按用户授权提供工程参数；底层 Config 与物理算子仍检查显式缺项。20 bit 表示输出码宽，
不表示 20 bit ENOB。数字系数只来自名义设计，不读取制造后的电容，
也不伪造 FrozenCalibration、训练样本数或拟合诊断。验证只覆盖声明的工程样本域，不能外推原芯片性能。

参考 ±3 V 是差分等效坐标，不是晶体管单端负电源。基础整数译码、随机资源计划和热噪声相位假设，
不是原芯片公开的完整控制器、3DDEM 或开关网络。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from numbers import Real

import numpy as np

from .config import Config, ConfigError
from .dac_arch import NominalCodebook, SplitChip
from .dem import SamplingConnections, SplitSwitchCommand, SplitSwitchGeometry
from .dither import (
    FIG19_FRACTIONAL_WEIGHTS_Q,
    Fig19DitherProgram,
    Fig19StaticSpec,
    complete_flash3_sar6_prefix_table,
)
from .fixed_point import CodeStream, FixedPointFormat, FixedPointReconstructor
from .mapper import Mapper
from .pipeline import run_pipeline
from .serialization import result_document
from .sim import SimResult
from .slice_pool import PhysicalSlicePool
from .timing import SlicePlan
from .weight_calibration import CalibrationSpec

DEFAULT_SEED = 20261105
AMENDMENT_SHA256 = "d709a5f2ad657cd0990ea4bc8b7e2a5889a055be2a0793ad898190d26ae6a4da"
FIG19_BINDING_REASON = (
    "US10505561 Fig19 seven-slice example interpreted as common voltage ports on this 18/8 model; "
    "Q32 ratio/sign/target binding are explicit engineering assumptions, not fabricated-chip disclosure"
)


def _positive(value, name: str, *, zero: bool = False) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ConfigError(f"{name} must be a finite real value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ConfigError(f"{name} must be a finite real value") from error
    if not math.isfinite(number):
        raise ConfigError(f"{name} must be a finite real value")
    if number < 0 if zero else number <= 0:
        raise ConfigError(f"{name} must be {'nonnegative' if zero else 'positive'}")
    return number


def _seed(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | np.integer) or value < 0:
        raise ConfigError(f"{name} must be a nonnegative integer")
    return int(value)


@dataclass(frozen=True)
class DefaultParameters:
    """默认名义值与工程试验假设；覆盖一项不会暗中重推其他物理输入。

    默认 Cu/Cb/Cf 基于 20.5 pF 节点解释、63+8 几何、beta=1/8 与 Gnom=32 推导。
    替换其中一项即定义自己的名义网络，其余值保持明确、可见；不声称覆盖后的网络仍等于原芯片。

    电容为 F，temperature_k 为 K，ra_noise_rms_v 位于 RA 输出节点，单位为 Vrms；
    失配 std 为相对标准差。底层电荷与译码器使用该名义规格，位宽仍只是整数算术约定。
    """

    n_main: int = 63
    n_sub: int = 8
    unit_cap_f: float = 4.00390625e-14
    bridge_cap_f: float = 4.575892857142857e-14
    feedback_cap_f: float = 6.40625e-13
    main_parasitic_f: float = 0.0
    sub_parasitic_f: float = 0.0
    sadc_cap_f: float = 2.5625e-12
    adc2_n_bits: int = 12
    adc2_v_min: float = -0.037500000000000006
    adc2_v_max: float = 0.41250000000000003
    endpoint_policy: str = "half_open"
    ra_clip_v: float = 0.5
    temperature_k: float = 300.0
    chi: float = 2.0
    ra_noise_rms_v: float = 0.001
    unit_mismatch_std: float = 0.001
    bridge_mismatch_std: float = 0.001
    feedback_mismatch_std: float = 0.0
    format: FixedPointFormat = field(
        default_factory=lambda: FixedPointFormat(30, 32, 64, 48, 96, 20)
    )

    def __post_init__(self):
        """检查固定整数几何和会实际生成器件的显式参数。"""
        for name in ("n_main", "n_sub"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ConfigError(f"{name} must be a positive integer")
        if (self.n_main + 1) * self.n_sub != 512:
            raise ConfigError("the default segmented decoder needs (n_main+1)*n_sub=512")
        for name in ("unit_cap_f", "bridge_cap_f", "feedback_cap_f"):
            _positive(getattr(self, name), name)
        for name in (
            "main_parasitic_f",
            "sub_parasitic_f",
            "unit_mismatch_std",
            "bridge_mismatch_std",
            "feedback_mismatch_std",
        ):
            _positive(getattr(self, name), name, zero=True)
        if not isinstance(self.format, FixedPointFormat):
            raise ConfigError("format must be an explicit FixedPointFormat")


@dataclass(frozen=True)
class DefaultRun:
    """默认运行的raw、20bit码与推断表；codes保留模拟/数字裁剪标志。"""

    raw: SimResult
    codes: CodeStream
    metadata: dict


def _segmented_mask(geometry, codes, states, slice_ids) -> SplitSwitchCommand:
    if np.any(states != 0):
        raise ConfigError("the inferred basic decoder has no source-validated unit DEM states")
    main_count, sub_count = codes // geometry.n_sub, codes % geometry.n_sub
    main = np.arange(geometry.n_main)[None, None, :] < main_count[:, None, None]
    sub = np.arange(geometry.n_sub)[None, None, :] < sub_count[:, None, None]
    shape = (len(codes), geometry.n_active)
    return SplitSwitchCommand(
        np.broadcast_to(main, (*shape, geometry.n_main)).copy(),
        np.broadcast_to(sub, (*shape, geometry.n_sub)).copy(),
    )


def _all_signal(geometry, sample_ids, slice_ids, control) -> SamplingConnections:
    if control is not None:
        raise ConfigError(
            "default acquisition is explicitly allSIGNAL without hidden dither control"
        )
    return SamplingConnections(
        np.zeros((*slice_ids.shape, geometry.n_main), dtype=np.int64),
        np.zeros((*slice_ids.shape, geometry.n_sub), dtype=np.int64),
    )


def _schedule(seed: int):
    def binding(n: int, n_slices: int, n_active: int) -> SlicePlan:
        generator = np.random.Generator(np.random.PCG64(seed))
        conv = np.empty((n, n_active), dtype=np.int64)
        acq = np.empty_like(conv)
        group = np.arange(n_active, dtype=np.int64)
        for index in range(n):
            conv[index] = group
            idle = np.setdiff1d(np.arange(n_slices, dtype=np.int64), group)
            group = generator.choice(idle, size=n_active, replace=False)
            acq[index] = group
        return SlicePlan(
            conv,
            acq,
            np.arange(n, dtype=np.int64) - 1,
            np.ones(n, dtype=bool),
            "INFERRED uniform8-of-idle10; not original-chip PRNG",
            {"seed": seed},
        )

    return binding


class DefaultModel:
    """同一制造实现上的可重放默认模型；run_id分开计划与噪声。

    仅制造流读nominal值并生成actual器件。nominal_decoder在制造抽样之前从名义电容构造；
    它不是拟合模型。unit/bridge sigma为工程压力，非PDK参数。
    """

    def __init__(self, *, seed: int, parameters: DefaultParameters, config_overrides: dict | None):
        """建立集中默认bundle，不启动训练或波形仿真。"""
        self.seed = _seed(seed, "seed")
        if not isinstance(parameters, DefaultParameters):
            raise ConfigError("parameters must be DefaultParameters")
        self.parameters = parameters
        p = parameters
        self._config_overrides = {} if config_overrides is None else dict(config_overrides)
        if "seed" in self._config_overrides:
            raise ConfigError(
                "use the top-level seed/run_id instead of overriding the partitioned noise seed"
            )
        cfg = Config(
            dac_n_main=p.n_main,
            dac_n_sub=p.n_sub,
            sadc_thresholds_v=tuple(np.linspace(-3.0, 3.0, 513)),
            sadc_endpoint_policy=p.endpoint_policy,
            adc2_n_bits=p.adc2_n_bits,
            adc2_v_min=p.adc2_v_min,
            adc2_v_max=p.adc2_v_max,
            adc2_codebook="uniform_midrise",
            adc2_endpoint_policy=p.endpoint_policy,
            ra_v_clip=p.ra_clip_v,
            ra_enable_noise=True,
            ra_out_noise_rms=p.ra_noise_rms_v,
            ra_noise_distribution="gaussian_white",
            c_sadc=p.sadc_cap_f,
            enable_sampling_noise=True,
            sampling_noise_distribution="gaussian_white",
            temperature_k=p.temperature_k,
            chi=p.chi,
            seed=self._child_seed(2, 0),
            dem_enable=False,
            dither_enable=False,
        )
        cfg = replace(cfg, **self._config_overrides)
        _positive(cfg.v_fs, "v_fs")
        if "v_fs" in self._config_overrides and "sadc_thresholds_v" not in self._config_overrides:
            cfg = replace(cfg, sadc_thresholds_v=tuple(np.linspace(-cfg.v_fs, cfg.v_fs, 513)))
        if cfg.dem_enable:
            raise ConfigError(
                "the inferred basic decoder has no implemented source unit DEM binding"
            )
        if (
            cfg.dyn_input_settling
            or cfg.dyn_ref_settling
            or cfg.dyn_crosstalk
            or cfg.conversion is not None
        ):
            raise ConfigError(
                "default_model is a static baseline; detailed dynamics need explicit run_pipeline circuit inputs"
            )
        geometry = SplitSwitchGeometry(p.n_main, p.n_sub, cfg.n_slices, cfg.n_active, 0, 511)
        cfg.check_legal(geometry=geometry)
        self.cfg = cfg
        self.geometry = geometry
        beta = p.bridge_cap_f / (p.bridge_cap_f + p.n_sub * p.unit_cap_f + p.sub_parasitic_f)
        c_slice = p.unit_cap_f * (p.n_main + beta * p.n_sub)
        self.nominal_beta = beta
        self.nominal_active_cap_f = cfg.n_active * c_slice
        self.nominal_gain = self.nominal_active_cap_f / p.feedback_cap_f
        coarse = np.arange(512, dtype=np.int64)
        drive_weight = p.unit_cap_f * (coarse // p.n_sub + beta * (coarse % p.n_sub))
        voltage = -cfg.v_fs + 2 * cfg.v_fs * drive_weight / c_slice
        self.codebook = NominalCodebook(0, 511, voltage)
        self.mapper = Mapper(coarse, nominal_codebook=self.codebook)
        weights = np.concatenate(
            (
                np.full((cfg.n_slices, p.n_main), p.unit_cap_f / p.feedback_cap_f),
                np.full((cfg.n_slices, p.n_sub), beta * p.unit_cap_f / p.feedback_cap_f),
            ),
            axis=1,
        )
        self.nominal_decoder = FixedPointReconstructor.from_weights(
            CalibrationSpec.from_config(cfg, geometry=geometry),
            weights,
            0.0,
            format=p.format,
        )
        # 先构造名义数字模型，再抽取制造数据，避免实际电容进入名义系数。
        fabrication = np.random.Generator(
            np.random.PCG64(np.random.SeedSequence(self.seed, spawn_key=(0,)))
        )
        main = p.unit_cap_f * (
            1 + fabrication.normal(0, p.unit_mismatch_std, (cfg.n_slices, p.n_main))
        )
        sub = p.unit_cap_f * (
            1 + fabrication.normal(0, p.unit_mismatch_std, (cfg.n_slices, p.n_sub))
        )
        bridge = p.bridge_cap_f * (1 + fabrication.normal(0, p.bridge_mismatch_std, cfg.n_slices))
        feedback = p.feedback_cap_f
        if p.feedback_mismatch_std:
            feedback *= 1 + fabrication.normal(0, p.feedback_mismatch_std)
        self.chip = SplitChip(
            main,
            sub,
            bridge,
            np.full(cfg.n_slices, p.sub_parasitic_f),
            np.full(cfg.n_slices, p.main_parasitic_f),
            feedback,
        )

    def _child_seed(self, branch: int, run_id: int) -> int:
        sequence = np.random.SeedSequence(self.seed, spawn_key=(branch, run_id))
        return int(sequence.generate_state(1, dtype=np.uint64)[0])

    def inference_table(self) -> dict:
        """返回当前有效值、默认依据与覆盖标记，不给制造真值分配披露等级。"""
        p, cfg = self.parameters, self.cfg
        rows = {}

        def row(name, value, grade, reason, unit=""):
            rows[name] = {"value": value, "grade": grade, "reason": reason, "unit": unit}

        for name, value, default in (
            ("fs_hz", cfg.fs, 40e6),
            ("input_differential_peak_v", cfg.v_fs, 3.0),
            ("stage1_bits", cfg.b1, 9),
            ("output_bits", cfg.n_bits_target, 20),
            ("nominal_residue_gain", cfg.g0, 32.0),
            ("n_slices", cfg.n_slices, 18),
            ("n_active", cfg.n_active, 8),
        ):
            row(
                name,
                value,
                "DISCLOSED" if value == default else "USER_OVERRIDE",
                "ISSCC2024 source anchor; overrides are a custom model",
                {
                    "fs_hz": "Hz",
                    "input_differential_peak_v": "V",
                    "stage1_bits": "bit",
                    "output_bits": "bit",
                    "nominal_residue_gain": "V/V",
                    "n_slices": "count",
                    "n_active": "count",
                }[name],
            )
        row(
            "active_capacitance_anchor_f",
            20.5e-12,
            "DISCLOSED_ANCHOR_WITH_NODE_ASSUMPTION",
            "reported20.5pF mapped to nominal Cnorm, not an extracted input-load network",
            "F",
        )
        row("n_main", p.n_main, "ASSUMED_GEOMETRY", "one explicit512-code segmented realization")
        row("n_sub", p.n_sub, "ASSUMED_GEOMETRY", "not disclosed fabricated unit count")
        row(
            "unit_cap_f",
            p.unit_cap_f,
            "DERIVED_WITH_ASSUMPTION",
            "default20.5pF/[8*(63+8/8)]; independent overrides remain explicit",
            "F",
        )
        row(
            "bridge_cap_f",
            p.bridge_cap_f,
            "DERIVED_WITH_ASSUMPTION",
            "default8*Cu/7 with zero subnode parasitic",
            "F",
        )
        row(
            "feedback_cap_f",
            p.feedback_cap_f,
            "DERIVED_WITH_ASSUMPTION",
            "default20.5pF/32",
            "F",
        )
        row(
            "beta_nominal",
            self.nominal_beta,
            "DERIVED_FROM_NOMINAL_INPUTS",
            "Cb/(Cb+Nsub*Cu+Cp_sub)",
        )
        row(
            "nominal_active_Cnorm_f",
            self.nominal_active_cap_f,
            "DERIVED_FROM_NOMINAL_INPUTS",
            "8*(A+beta*B); not physical load or noise capacitance",
            "F",
        )
        row(
            "nominal_capacitance_gain",
            self.nominal_gain,
            "DERIVED_FROM_NOMINAL_INPUTS",
            "Cnorm_nom/Cf_nom",
        )
        for name in ("main_parasitic_f", "sub_parasitic_f"):
            row(
                name,
                getattr(p, name),
                "NOMINAL_IDEALIZATION_NOT_DEVICE_MEASUREMENT",
                "default0 is not device measurement",
                "F",
            )
        row("sadc_cap_f", cfg.c_sadc, "DERIVED_MATCHED_SLICE_ASSUMPTION", "default20.5pF/8", "F")
        thresholds = np.asarray(cfg.sadc_thresholds_v, dtype=float)
        threshold_deviation_v = float(
            np.max(np.abs(thresholds - np.linspace(-cfg.v_fs, cfg.v_fs, 513)))
        )
        row(
            "sadc_thresholds",
            {
                "edge_count": len(thresholds),
                "first_v": float(thresholds[0]),
                "last_v": float(thresholds[-1]),
            },
            "IDEAL_QUANTIZER_ASSUMPTION",
            "default uniform -Vfs..+Vfs; explicit overrides are retained in config_overrides",
            "V at SADC input",
        )
        row(
            "endpoint_policy", cfg.adc2_endpoint_policy, "NUMERICAL_CONVENTION", "half_open default"
        )
        row("adc2_codebook", cfg.adc2_codebook, "IDEAL_QUANTIZER_ASSUMPTION", "uniform_midrise")
        row(
            "adc2_n_bits",
            cfg.adc2_n_bits,
            "INFERRED_RESOLUTION_BUDGET",
            "default12b; no targetDR back-solving",
        )
        row(
            "adc2_window_v",
            [cfg.adc2_v_min, cfg.adc2_v_max],
            "INFERRED_HEADROOM",
            "default[-0.1,+1.1]*32*(6/512)",
            "V at RA output",
        )
        row(
            "ra_clip_v",
            cfg.ra_v_clip,
            "ASSUMED_SWING",
            "default0.5V is not disclosed device swing",
            "V",
        )
        row("temperature_k", cfg.temperature_k, "ASSUMED_TEST_CONDITION", "default300K", "K")
        row("chi", cfg.chi, "ASSUMED_DIFFERENTIAL_NOISE_MODEL", "default2")
        row(
            "ra_noise_rms_v",
            cfg.ra_out_noise_rms,
            "ASSUMED_NOISE_BUDGET",
            "rounded1mV output budget, not inferred measured component",
            "Vrms",
        )
        row(
            "noise_distribution",
            "gaussian_white",
            "MODELING_ASSUMPTION",
            "independent sampled sources",
        )
        row(
            "sampling_Ceq",
            "Cnorm^2/sum(Cmain+beta^2*Csub)",
            "ASSUMED_SAMPLING_CHARGE_MODEL",
            "independent main/sub sampled charge; actual values used only by plant",
        )
        for name in ("unit_mismatch_std", "bridge_mismatch_std", "feedback_mismatch_std"):
            row(
                name,
                getattr(p, name),
                "NOMINAL_BASELINE"
                if name == "feedback_mismatch_std" and getattr(p, name) == 0
                else "ASSUMED_SENSITIVITY",
                "iid relative Gaussian stress when nonzero; not PDK data",
                "relative RMS",
            )
        row(
            "sadc_static_mismatch",
            threshold_deviation_v,
            "NOMINAL_BASELINE",
            "max absolute configured threshold deviation from uniform[-Vfs,+Vfs]; not a random mismatch sigma",
            "V at SADC input",
        )
        row(
            "reference_coordinates",
            [cfg.v_fs, -cfg.v_fs],
            "DIFFERENTIAL_EQUIVALENT_COORDINATE",
            "not single-ended transistor pin voltages",
            "V",
        )
        row(
            "sampled_reset",
            [0.0, 0.0],
            "ASSUMED_STATIC_RESET",
            "differential top zero; sub coordinate cancels statically",
            "V",
        )
        row(
            "base_decoder",
            "main=k//Nsub,sub=k%Nsub; canonical integer masks",
            "INFERRED_BASIC_DECODER",
            "not source3DDEM or binary-unary bridging",
        )
        row(
            "schedule",
            "uniform8-of-idle10 with explicit seed",
            "BEHAVIORAL_SCHEDULING_ASSUMPTION",
            "causal18/8/8/2; not original-chip PRNG",
        )
        row(
            "fixed_point",
            asdict(p.format),
            "ENGINEERING_ARITHMETIC_CHOICE",
            "Q30/Q32/64/48/96/20 default; not paper format",
        )
        row(
            "fig19_optional_static_binding",
            {
                "voltage_fraction_bits": 32,
                "step_q": 2**24,
                "fractional_lower_weights_q": list(FIG19_FRACTIONAL_WEIGHTS_Q),
                "equivalent_polarity": "subtract",
            },
            "ASSUMED_TARGET_PORT_EQUIVALENCE",
            FIG19_BINDING_REASON,
        )
        row(
            "seed",
            self.seed,
            "NUMERICAL_REPRODUCIBILITY_CONTROL",
            "SeedSequence fabrication/schedule/noise partition",
        )
        row(
            "sadc_endpoint_policy",
            cfg.sadc_endpoint_policy,
            "NUMERICAL_CONVENTION",
            "half_open default",
        )
        default_parameters = asdict(DefaultParameters())
        parameter_rows = {
            "format": "fixed_point",
            "adc2_v_min": "adc2_window_v",
            "adc2_v_max": "adc2_window_v",
        }
        for name, value in asdict(p).items():
            key = parameter_rows.get(name, name)
            if value != default_parameters[name] and key in rows:
                rows[key].setdefault("default_grade", rows[key]["grade"])
                rows[key]["grade"] = "USER_OVERRIDE"
        config_rows = {
            "c_sadc": "sadc_cap_f",
            "sadc_thresholds_v": "sadc_thresholds",
            "sadc_endpoint_policy": "sadc_endpoint_policy",
            "adc2_n_bits": "adc2_n_bits",
            "adc2_v_min": "adc2_window_v",
            "adc2_v_max": "adc2_window_v",
            "adc2_endpoint_policy": "endpoint_policy",
            "adc2_codebook": "adc2_codebook",
            "ra_v_clip": "ra_clip_v",
            "temperature_k": "temperature_k",
            "chi": "chi",
            "ra_out_noise_rms": "ra_noise_rms_v",
        }
        for field_name in self._config_overrides:
            override_row = config_rows.get(field_name)
            if override_row is not None and override_row in rows:
                rows[override_row].setdefault("default_grade", rows[override_row]["grade"])
                rows[override_row]["grade"] = "USER_OVERRIDE"
                rows[override_row]["override_field"] = field_name
        if "sadc_thresholds_v" in self._config_overrides:
            rows["sadc_static_mismatch"].update(
                default_grade="NOMINAL_BASELINE",
                grade="USER_OVERRIDE",
                override_field="sadc_thresholds_v",
            )
        return rows

    def simulate(
        self, input_fn, n_samples: int, *, run_id: int = 0, dither_words=None,
        ordinary_az=None,
    ) -> DefaultRun:
        """以正常用户波形和N运行，并用独立名义定点系数输出20bit码。

        Args:
            input_fn: t[s]→差分输入[V]；正常范围[-3,+3)，越界仍保留flags。
            n_samples: 正整数raw数。
            run_id: 同一制造chip上的可重放计划/噪声编号。
            dither_words: 可选(N,)显式六位字，启用Fig19静态等效端口；不生成隐藏PRNG。
                v1要求调用方明确关闭噪声/动态/DEM，建议先用±2.0V验证；不夹非法早prefix。
            ordinary_az: 可选 OrdinaryAZConfig；None 表示不施加。启用时每笔独立
                计算 AZ 误差并送入 RA 输入，N 笔仍输出 N 笔，不做偶奇配对。

        Returns:
            DefaultRun，含CodeStream、raw与本次推断/启用/未建机制表。

        Side effects: 不改制造值或系数；同一seed/run_id每次重新建立电气状态。

        Source: 用户授权的工程默认推断；不宣称原芯片或PDK性能复现。
        """
        run_id = _seed(run_id, "run_id")
        if not callable(input_fn):
            raise ConfigError("input_fn must be a callable t[s] to input[V] waveform")
        if type(n_samples) is not int or not 1 <= n_samples <= 0xFFFF_FFFF:
            raise ConfigError("n_samples must be a positive uint32-range count")
        schedule_seed, noise_seed = self._child_seed(1, run_id), self._child_seed(2, run_id)
        cfg = replace(self.cfg, seed=noise_seed)
        az_error_v = None
        if ordinary_az is not None:
            from .behavioral.ra_az import apply_ordinary_az
            if not ordinary_az.enabled:
                raise ConfigError("ordinary_az must be enabled to be applied")
            # 普通 AZ 逐笔刷新：保持时间从本笔自己的 AZ 时刻算起，近似为 0。
            # 早期版本传的是绝对记录时间 n/fs，会凭空制造一条随记录增长的 droop。
            hold_s = np.zeros(n_samples, dtype=float)
            az = apply_ordinary_az(
                np.zeros(n_samples), np.arange(n_samples, dtype=np.int64),
                ordinary_az, hold_time_s=hold_s,
            )
            az_error_v = az.residual_v
        program = None
        mapper = self.mapper
        if dither_words is not None:
            if self._config_overrides.get("dither_enable") is False:
                raise ConfigError(
                    "explicit dither words conflict with dither_enable=False override"
                )
            if self.parameters.format.voltage_fraction_bits != 32:
                raise ConfigError("Fig19 static v1 shared digital interface requires explicit Q32")
            program = Fig19DitherProgram.from_words(
                dither_words,
                spec=Fig19StaticSpec(
                    v_fs=cfg.v_fs,
                    step_q=2**24,
                    voltage_fraction_bits=32,
                    fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
                    equivalent_polarity="subtract",
                    provenance=FIG19_BINDING_REASON,
                ),
            )
            cfg = replace(cfg, dither_enable=True)
            mapper = Mapper(
                np.arange(512, dtype=np.int64),
                nominal_codebook=self.codebook,
                event_codes_by_coarse=complete_flash3_sar6_prefix_table(),
            )
        elif cfg.dither_enable:
            raise ConfigError("dither_enable requires explicit per-raw six-bit dither_words")
        pool = PhysicalSlicePool(
            cfg,
            chip=self.chip,
            nominal_codebook=self.codebook,
            mask_binding=_segmented_mask,
            acquisition_binding=_all_signal,
            schedule_binding=_schedule(schedule_seed),
        )
        noise_cap = None
        if cfg.enable_sampling_noise:
            plan = pool.plan(n_samples)
            ids = plan.conv
            main, sub = self.chip.C_main[ids], self.chip.C_sub[ids]
            beta = self.chip.C_bridge[ids] / (
                self.chip.C_bridge[ids] + sub.sum(axis=-1) + self.chip.c_p_sub[ids]
            )
            norm = (main.sum(axis=-1) + beta * sub.sum(axis=-1)).sum(axis=-1)
            charge_variance_factor = (main.sum(axis=-1) + beta**2 * sub.sum(axis=-1)).sum(axis=-1)
            noise_cap = norm**2 / charge_variance_factor
        raw = run_pipeline(
            cfg,
            input_fn,
            n_samples,
            pool=pool,
            mapper=mapper,
            sample_ids=np.arange(n_samples, dtype=np.int64),
            dem_states=np.zeros(n_samples, dtype=np.int64),
            sampled_top_v=0.0,
            sampled_subnode_v=0.0,
            reference_high_v=cfg.v_fs,
            reference_low_v=-cfg.v_fs,
            nominal_reference_high_v=cfg.v_fs,
            nominal_reference_low_v=-cfg.v_fs,
            static_charge_domain=True,
            rdac_noise_cap_f=noise_cap,
            ra_input_error_v=az_error_v,
            dither_program=program,
        )
        assert raw.digital_observation is not None
        codes = self.nominal_decoder.reconstruct(raw.digital_observation)
        metadata = {
            "profile": "INFERRED_STATIC_DEFAULT_V1",
            "amendment_sha256": AMENDMENT_SHA256,
            "source_reproduction": False,
            "digital_coefficients": "NOMINAL_NOT_FITTED; nominalC/Cf only",
            "output_bits": 20,
            "n_samples": n_samples,
            "precision_claim": "code width only; no20bit ENOB or silicon claim",
            "seed": self.seed,
            "run_id": run_id,
            "seed_partition": {
                "fabrication_spawn_key": [0],
                "schedule_spawn_key": [1, run_id],
                "noise_spawn_key": [2, run_id],
                "schedule_seed": schedule_seed,
                "noise_seed": noise_seed,
            },
            "enabled": {
                "sampling_noise": cfg.enable_sampling_noise,
                "driver_noise": cfg.driver_noise_rms is not None and cfg.driver_noise_rms > 0,
                "sadc_response_approximation": cfg.sampling_tau_mismatch not in (None, 0),
                "ra_output_noise": cfg.ra_enable_noise,
                "unit_mismatch": self.parameters.unit_mismatch_std != 0,
                "bridge_mismatch": self.parameters.bridge_mismatch_std != 0,
                "slice_resource_randomization": True,
                "basic_segmented_decoder": True,
                "unit_dem": False,
                "source_dither": program is not None,
                "feedback_mismatch": self.parameters.feedback_mismatch_std != 0,
                "ordinary_az": ordinary_az is not None and ordinary_az.enabled,
            },
            "unmodeled": [
                "complete source lower/upper capacitor/switch network",
                "source3D unit DEM",
                "input RC and chop switching",
                "physical-slice timing/bandwidth/offset mismatch",
                "RA/ADC2 nonlinearity",
                "reference dynamics",
                "FGA/CBIAS transistor noise transfer",
                "bridge/reset sampling-noise covariance",
                "PDK/layout/signoff",
            ],
            "inferences": self.inference_table(),
            "ordinary_az": None if ordinary_az is None else {
                "enabled": ordinary_az.enabled,
                "offset_v": ordinary_az.offset_v,
                "noise_v": ordinary_az.noise_v,
                "droop_v_per_s": ordinary_az.droop_v_per_s,
                "seed": ordinary_az.seed,
                "samples": n_samples,
                "pairing": "none; N->N full rate",
            },
            "dither_program": None
            if program is None
            else {
                **program.spec.to_dict(),
                "word_count": len(program.words),
                "lower_max_q": int(program.lower_q.max()),
                "total_max_q": int(program.total_q.max()),
                "word_source": "explicit caller per-raw six-bit words",
                "prefix_binding": "current complete-valid Flash3/SAR6 table; seven events including repeats",
            },
            "config_overrides": {name: cfg.to_dict()[name] for name in self._config_overrides},
            "raw_invalid": int(np.count_nonzero(codes.analog_overflow)),
            "final_clip_low": int(np.count_nonzero(codes.clipped_low)),
            "final_clip_high": int(np.count_nonzero(codes.clipped_high)),
            "numpy_version": np.__version__,
        }
        return DefaultRun(raw, codes, result_document(metadata))


def default_model(
    *,
    seed: int = DEFAULT_SEED,
    parameters: DefaultParameters | None = None,
    config_overrides: dict | None = None,
) -> DefaultModel:
    """构造可运行的集中默认模型，未提供的合理值均在推断表中可见。

    Args:
        seed: 数值重放种子，不是工艺参数。
        parameters: 可覆盖的明确名义值/压力/格式，None选集中工程默认。
        config_overrides: 显式Config字段覆盖；不支持旧架构/未实现功能回退。

    Returns:
        DefaultModel；制造值只影响plant，名义系数不由它们生成。

    Side effects: 消费独立制造SeedSequence一次，不训练、不运行输入波形。
    """
    return DefaultModel(
        seed=seed,
        parameters=DefaultParameters() if parameters is None else parameters,
        config_overrides=config_overrides,
    )


def simulate(
    input_fn,
    n_samples: int,
    *,
    seed: int = DEFAULT_SEED,
    parameters: DefaultParameters | None = None,
    config_overrides: dict | None = None,
    run_id: int = 0,
    dither_words=None,
    ordinary_az=None,
) -> DefaultRun:
    """只给波形与N即可得到20bit码及推断记录的正常用户入口。

    Args:
        input_fn: t[s]→差分输入[V]的可调用波形。
        n_samples: 正整数raw数。
        seed: 制造/计划/噪声的根数值种子。
        parameters: 可覆盖的集中工程假设。
        config_overrides: 明确Config字段覆盖，不恢复被删除算法。
        run_id: 同一制造根种子下独立且可重放的计划/噪声编号。
        dither_words: 可选显式六位字；只启用有明确假设、范围拒绝和真实采样注入的Fig19静态等效模型。
        ordinary_az: 可选普通逐笔 RA AZ 配置；None=不施加，N→N。

    Returns:
        DefaultRun；codes.code是20bit offset-binary整数，保留全部flags。

    Side effects: 建立独立默认模型并运行一次，不拟合系数或改变全局状态。
    """
    return default_model(
        seed=seed, parameters=parameters, config_overrides=config_overrides
    ).simulate(
        input_fn,
        n_samples,
        run_id=run_id,
        dither_words=dither_words,
        ordinary_az=ordinary_az,
    )
