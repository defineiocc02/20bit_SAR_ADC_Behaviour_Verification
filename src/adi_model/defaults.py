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

from adi_model.calibration.weight_calibration import CalibrationSpec
from adi_model.contracts.fixed_point import CodeStream, FixedPointFormat, FixedPointReconstructor
from adi_model.control.dem import SamplingConnections, SplitSwitchGeometry
from adi_model.control.dem3d import fig11_masks
from adi_model.control.dither import (
    FIG19_STEP_Q,
    FIG19_FRACTIONAL_WEIGHTS_Q,
    Fig19DitherProgram,
    Fig19StaticSpec,
    complete_flash3_sar6_prefix_table,
)
from adi_model.control.dither_port import DitherPort
from adi_model.control.timing import SlicePlan
from adi_model.simulation.config import Config, ConfigError
from adi_model.simulation.pipeline import run_pipeline
from adi_model.simulation.serialization import result_document
from adi_model.simulation.sim import SimResult
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.dac_arch import NominalCodebook, SplitChip
from adi_model.topology.capacitors import MAIN_WEIGHTS, SUB_WEIGHTS
from adi_model.topology.mapper import Mapper

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

    n_main: int = 17
    n_sub: int = 5
    # 2026-11-13：按 docs/decisions/20261112_fig11_unification.md §5.1 对齐到 Fig.11 17/5。
    #   main = 15×2C + 1C(B4) + 1C(S4) = 32C；sub = 8+4+2+1 = 15C；bridge = 1C
    #   β = Cb/(Cb+ΣB) = 1/16；c_slice = 32 + 15/16 C = 32.9375C
    #   unit = 20.5 pF / (8 × 32.9375) = 77.799 fF（0.0778 pF，与专利 col.23 示例同量级）
    #   旧值 40.04 fF / 45.76 fF 对应 63+8（n_main=63、n_sub=8），与 R7 后的权重表不同源。
    unit_cap_f: float = 7.779886183991967e-14
    bridge_cap_f: float = 7.779886183991967e-14
    feedback_cap_f: float = 6.40625e-13
    main_parasitic_f: float = 0.0
    sub_parasitic_f: float = 0.0
    sadc_cap_f: float = 2.5625e-12
    # 14 bit，与窗口 [-0.0375, +0.4125] 配套：ADR-0003 的 `paper_literal` 行即
    # "9 | 1 | 0b | 14 bit，[-0.0375, +0.4125] V"。此前误写成 12 bit 却沿用同一窗口，
    # 结果二级量化 LSB 折到输入 = 0.60 LSB20，**单这一项就吃不满 20 bit 目标**（需 ≤0.5）。
    # 14 bit 时该值 = 0.15 LSB20 ✓。
    adc2_n_bits: int = 14
    adc2_v_min: float = -0.037500000000000006
    adc2_v_max: float = 0.41250000000000003
    endpoint_policy: str = "half_open"
    # RA 输出摆幅 = ADC2 满窗宽度（0.45 V）。依据 ADR-0003 的 paper_literal 行：
    #   adc2_v_min=-0.0375, adc2_v_max=0.4125, ra_v_clip=0.45
    # 0.45 恰好等于窗口宽度 0.4125-(-0.0375)，即"RA 摆幅与 ADC2 满窗相配"。
    # 此前写 0.5 比窗口还大 ⇒ RA 永远先撞窗口、clip 形同虚设（dither 时 ra_sat 42/64）。
    ra_clip_v: float = 0.45
    temperature_k: float = 300.0
    chi: float = 2.0
    ra_noise_rms_v: float = 0.001
    # 单位电容失配 σ = 1e-4（相对）。依据：用论文 [00] 标题的两项独立指标反解——
    #   DR 94.2 dB、NSD 9.3 nV/rtHz
    # 实测扫描（1024 点动态，失配+噪声）：
    #   σ=0      : DR 95.34 dB, NSD  8.10 nV/rtHz
    #   σ=5e-5   : DR 95.04 dB, NSD  8.22
    #   σ=1e-4   : DR 94.33 dB, NSD  8.55   ← 两个指标同时命中
    #   σ=2e-4   : DR 92.32 dB, NSD  9.74
    #   σ=1e-3   : DR 81.31 dB, NSD 27.72   ← 原值，与论文差 12.9 dB
    # 原值 1e-3 是早前的工程假设、无原件依据；现改为由两个独立指标反解出的 1e-4。
    # 注意：这是**反解值不是披露值**，来源与推导记在
    # docs/mechanisms/paper_metrics_calibration.md。
    unit_mismatch_std: float = 1e-4
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
        # 2026-11-13：默认链已从 63+8 分段阵列切到专利 Fig.11 的 17/5 阵列。
        # 守卫从"码数 = (n_main+1)*n_sub"改成"静态 17/5"：前者是 63+8 的专用码本律，
        # 后者才能与 fig11_masks 的硬约束（n_main==17、n_sub==5）保持一致。
        # 为什么不再允许任意几何：码→掩码的分解（B0..B3 → 15 个 2C、B4 → 1C、
        # B5..B8 → 子阵列二进制）只在 17/5 上有原件依据（US10511316B2 FIG.11/17/19/20），
        # 换成别的几何就必须重写解码，而不是只改两个整数。
        if (self.n_main, self.n_sub) != (17, 5):
            raise ConfigError(
                "the default decoder implements the Fig.11 17/5 array only; "
                "a different geometry needs its own explicit decoder"
            )
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
        # 单位约定：`unit_cap_f` 是图上最小的那个 **C**（不是"一个单元"）。
        #   Fig.11 每片：主单元 15×2C + 1C(B4) + 1C(S4) = 32C
        #                子单元 8C+4C+2C+1C = 15C（跨桥后只有这 4 个可切换单元）
        #                桥 Cb = 1C
        # 2026-11-13 修正：原式把 n_sub（单元个数）直接乘 1C，只在"每单元恰好 1C"
        # 的 63+8 池里成立；17/5 的子单元是 8/4/2/1C，必须按**权重和**算，
        # 否则 β 会从 1/16 变成 1/6、c_slice 也一并错。
        sub_units_c = sum(SUB_WEIGHTS)           # 15
        # 名义分母：主段 15 个 2C(30C) + B4 的 1C + S4 的 1C = **32C**。
        # 端点闭合判据（drive(511) == cn）枚举后只有这一组自洽：
        #   主段 15×2C、B4 步长 1C、S4 计入分母 → cn = 31 + β·15 = drive(511) ✓
        # 若 S4 不计入分母，511 码只到 +2.818 V；若 B4 步长取 2C，码本在 B4 处非单调。
        # 分母只含参与码的 16 个主单元：15×2C + B4 1C = 31C（S4 属采样态，不参与驱动）。
        # 端点闭合判据：drive(511) = 30C + 1C + β·15C = 31.9375 = cn ⇒ v(511) = +v_fs。
        main_units_c = sum(MAIN_WEIGHTS[:15]) + MAIN_WEIGHTS[15]  # 31C
        beta = p.bridge_cap_f / (
            p.bridge_cap_f + sub_units_c * p.unit_cap_f + p.sub_parasitic_f
        )
        c_slice = p.unit_cap_f * (main_units_c + beta * sub_units_c)
        self.nominal_beta = beta
        self.nominal_active_cap_f = cfg.n_active * c_slice
        self.nominal_gain = self.nominal_active_cap_f / p.feedback_cap_f
        # 参数自洽性：二级 ADC 能否装下 dither 的提前量。这不是错误检查，
        # 而是一条**可读结论**——不自洽时会在 inference 表里显式列出缺口，
        # 避免读者从若干 dither 失败用例反推"到底是哪两个参数打不过"。
        from adi_model.simulation.parameter_consistency import check_dither_fits_stage2

        self.dither_stage2_consistency = check_dither_fits_stage2(
            v_fs=cfg.v_fs,
            gain=self.nominal_gain,
            adc2_v_min=cfg.adc2_v_min,
            adc2_v_max=cfg.adc2_v_max,
            upper_code_max=5,
            adc2_n_bits=cfg.adc2_n_bits,
        )
        coarse = np.arange(512, dtype=np.int64)
        # ==== 码本律（2026-11-13 最终版，依据 US10511316B2 FIG.11/17/19/20 + ISO 端点闭合）====
        # 位分配（9 位 = 主段 5 位 + 子阵列 4 位，B0 为 MSB）：
        #   B0..B4 : 主阵列 5 位主码 P = 0..31，每级 1C 当量（15 个 2C + 2 个 1C 可合成任意 0..31）
        #   B5..B8 : 子阵列 8C/4C/2C/1C 二进制，经 Cb 衰减 β
        # 分母 cn = 31 + β·15 C（主段满码 31C + 子阵列全接 β·15C）。
        # 为什么是 31 而不是 32：主码只有 5 位（0..31），满码只能选 31C；而 32C 那个
        # "32 级"要求 6 位主码，会把 9 位挤成 10 位。端点闭合判据（drive(511)==cn）
        # 验证：31 + 15/16 = cn → x(511) = +v_fs 精确成立。
        # 旧实现 `c // n_sub + beta * (c % n_sub)` 是 63+8 的律，套到 17/5 上会把
        # c=511 算成 31.19 V（v_fs=3 V），已删除。
        main_code = coarse >> 4                      # B0..B4：0..31
        main_twos = np.minimum(main_code // 2, 15)   # 用满 15 个 2C
        main_ones = np.minimum(main_code - 2 * main_twos, 2)  # 剩余用 2 个 1C(B4/S4) 补
        main_units = 2 * main_twos + main_ones
        sub_bits = np.stack([(coarse >> i) & 1 for i in (3, 2, 1, 0)], axis=1)
        sub_units = sub_bits @ np.array([8.0, 4.0, 2.0, 1.0])  # B5..B8
        drive_units = main_units.astype(float) + beta * sub_units
        drive_weight = p.unit_cap_f * drive_units
        voltage = -cfg.v_fs + 2 * cfg.v_fs * drive_weight / c_slice
        self.codebook = NominalCodebook(0, 511, voltage)
        # ==== SADC 阈值必须与码本同源（2026-11-13 修正）====
        # 原来阈值是 linspace(-v_fs, +v_fs, 513) 的等距表，而码本是 Fig.11 实测的
        # （每步 31.9375/512 C 而不是满幅 1/512），两者在中间码相差近 2 个一级 LSB。
        # 后果：粗码判决与 RDAC 电荷不同源 —— 理想配置也出现 5.7% 增益误差、
        # −0.171 V 直流偏置、A0 的 INL 达 28.6 LSB20。
        # 现在阈值直接取码本电压（512 个），末位补一个上边界，保证：
        #   SADC 判码 k  ⇔  x ∈ [v(k), v(k+1))，与 RDAC 用同一张表。
        thresholds = np.empty(513, dtype=float)
        thresholds[:512] = voltage
        thresholds[512] = 2.0 * voltage[-1] - voltage[-2]
        if "sadc_thresholds_v" not in self._config_overrides:
            cfg = replace(cfg, sadc_thresholds_v=tuple(thresholds))
            self.cfg = cfg
        self.mapper = Mapper(coarse, nominal_codebook=self.codebook)
        # 名义解码权重必须与**器件电容**同源：主单元 15×2C + 1C + 1C(S4)，
        # 子单元 β×8/4/2/1，末位哑单元权重 0（它从不被选中）。
        # 2026-11-13 修正：原来每个单元都按 1C 给权重，在 63+8（全 1C 单元）下成立，
        # 换到 17/5 后权重比应为 2:1 与 8:4:2:1，否则名义重构分辨率直接错位
        # （实测零输入时 vd 偏 0.09 V）。
        # 主阵列 17 列里，S4（最后一列 1C）**从不被码选中**，与子阵列哑位同类：
        # 它在 known_voltage_term 里恒为 -V_D，若给真实权重就会注入固定偏置。
        # 2026-11-13：权重要求 Σ = 31 + β·15 = 31.9375 C（与码本分母 cn 同源），
        # 而 MAIN_WEIGHTS 的和是 32C（含 S4）—— 直接相乘会让 Σ 偏大 1C，
        # 使 vd0 从 -0.0059 偏到 -0.0968、增益误差 -2.88%。故把 S4 权重置为极小。
        _tiny = 2.0 / 2**p.format.weight_fraction_bits
        main_w_eff = np.asarray(MAIN_WEIGHTS, dtype=float).copy()
        if main_w_eff.size >= 17:
            main_w_eff[16] = 0.0  # S4：不参与码
        main_w_norm = main_w_eff * p.unit_cap_f / p.feedback_cap_f
        main_w_norm = np.where(main_w_norm > 0, main_w_norm, _tiny)
        sub_switched = np.asarray(SUB_WEIGHTS, dtype=float)
        # 哑单元（物理第 5 列）权重必须是**极小正值**，不能用"与最小子单元同值"。
        # 2026-11-13：早前用 sub_switched.min()（=1C 的 0.121）填占位，理由是
        # "它从不被掩码选中，任何正值都等价"。这条推理**是错的**：
        # 定点重建的 known_voltage_term 对**全部单元**都算 V_D（±v_fs），
        # 权重 0.121 × (±3 V) 会注入 ±0.36 V 的虚假项，使 Σw·a·V_D/gain
        # 从 −0.0059 变成 −0.1823 —— 正是定点输出 −0.1766 V 偏置的来源。
        # 现在取 1e-9 量级：定点格式仍见"严格为正"，但电学贡献可忽略。
        # 取 Q30 能表示的最小正值之上一点（1/2^30 ≈ 9.3e-10），
        # 保证量化后仍 >=1 而电学贡献比最小真实权重小 8 个数量级。
        dummy = np.full(p.n_sub - sub_switched.size, 2.0 / 2**p.format.weight_fraction_bits)
        sub_w_norm = np.concatenate(
            [beta * sub_switched * p.unit_cap_f / p.feedback_cap_f, dummy]
        )
        weights = np.concatenate(
            (
                np.broadcast_to(main_w_norm, (cfg.n_slices, p.n_main)),
                np.broadcast_to(sub_w_norm, (cfg.n_slices, p.n_sub)),
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
        # 器件电容必须按原件权重缩放：主单元 15×2C + 1C + 1C(S4)，
        # 子单元 8C/4C/2C/1C（末位 1C 恒接低轨，保留物理位置但只作哑单元）。
        # 2026-11-13 修正：R7 把权重表改成 4 项后，这里若仍按"每单元 1C"生成，
        # 芯片子阵列总和只有 5C（而非 15C），β 会从 1/16 变成 1/6，
        # 码本分母与器件电容不同源——零输入时 RDAC 输出偏 0.084 V（实测）。
        main_w = np.asarray(MAIN_WEIGHTS, dtype=float)
        # 子阵列物理 5 列：前 4 列是可切换单元（8C/4C/2C/1C），第 5 列是**恒接低轨的哑单元**。
        # 哑单元在物理上是一块真电容，且**接在 sub 节点与低轨之间**（所以进 β 分母会改变 β），
        # 但它从不被掩码选中（fig11_masks 的末位恒低），因此对码本没有贡献。
        # 端点闭合判定（drive(511) == c_slice）要求 β 分母只含 4 个可切换单元，
        # 故建模方式：5 列都保留（物理位宽一致），但哑列的**电学贡献**通过
        # `sub_switched_units_c` 从系数计算中排除（见下方 beta / c_slice）。
        # 子阵列只装配 4 列（8C/4C/2C/1C = 15C）。FIG.11 的 5 个电容里，
        # 第 5 个 C 画在**虚线框**内、与第 4 个并列 —— 虚线是"可选/未装配"的常规含义，
        # 且它从未被任何码选中。若装配它，物理 β 会变成 1/17，与码本律唯一允许的
        # β=1/16 冲突（见 docs/mechanisms/p1_gaps_20261113.md §12 的 511-码步推导）。
        # 端口位宽仍是 geometry.n_sub=5（RTL 不变），器件只装 4 块。
        sub_w = np.asarray(SUB_WEIGHTS, dtype=float)
        assert main_w.size == p.n_main, "主阵列单元数须与权重表一致"
        assert sub_w.size == p.n_sub - 1, "子阵列装配列数 = 物理位宽 - 1 个虚线占位"
        sub = p.unit_cap_f * sub_w[None, :] * (
            1 + fabrication.normal(0, p.unit_mismatch_std, (cfg.n_slices, sub_w.size))
        )
        main = p.unit_cap_f * main_w[None, :] * (
            1 + fabrication.normal(0, p.unit_mismatch_std, (cfg.n_slices, p.n_main))
        )
        # 桥接电容 = 1C（图上就是一个 C），不按 n_sub 缩放。
        bridge = p.unit_cap_f * (1 + fabrication.normal(0, p.bridge_mismatch_std, cfg.n_slices))
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

    @staticmethod
    def state_estimated_gain(raw) -> float | None:
        """读出本笔使用的物理增益（RA 的 G_phys）；不可用时返回 None。

        定点重建需要它来把 fine 换算到权重和口径。缺失时返回 None（保持旧行为），
        不猜一个默认值。
        """
        state = getattr(raw, "state", None)
        value = getattr(state, "estimated_gain", None)
        if value is None:
            return None
        gain = float(value)
        return gain if gain > 0 else None

    def inference_table(self) -> dict:
        """返回当前有效值、默认依据与覆盖标记，不给制造真值分配披露等级。"""
        p, cfg = self.parameters, self.cfg
        rows = {}

        def row(name, value, grade, reason, unit=""):
            rows[name] = {"value": value, "grade": grade, "reason": reason, "unit": unit}

        report = self.dither_stage2_consistency
        row(
            "stage2_capacity_vs_dither",
            {
                "adc2_coverage_lsb1": round(report.capacity.coverage_lsb1, 4),
                "dither_need_lsb1": report.needed_lsb1,
                "deficit_lsb1": round(report.deficit_lsb1, 4),
                "sufficient": report.sufficient,
            },
            "DERIVED_FROM_CURRENT_PARAMETERS",
            report.summary(),
        )
        for name, value, default in (
            ("fs_hz", cfg.fs, 40e6),
            ("input_differential_peak_v", cfg.v_fs, 3.0),
            ("stage1_bits", cfg.b1, 9),
            ("output_bits", cfg.n_bits_target, 20),
            # ⚠️ 本行记的是**设计目标 g0**（论文给出的名义增益 32），
            # 不是实际算出的增益。实际 `DefaultModel.nominal_gain` 是
            # Cnorm/Cf = 19.878 pF / 0.640625 pF = **31.028**——因为 17/5 的
            # cn = 31.9375C 而非 32C。两者都"对"，但不是同一个量。
            # 用 32 的地方（例如定点重建的物理增益）必须用实际值，见
            # `docs/ENGINEERING_METRICS_20261113.md` §12.3。
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
                "step_q": FIG19_STEP_Q,
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
        dither_mode: str = "code_shift", ordinary_az=None,
    ) -> DefaultRun:
        """以正常用户波形和N运行，并用独立名义定点系数输出20bit码。

        Args:
            input_fn: t[s]→差分输入[V]；正常范围[-3,+3)，越界仍保留flags。
            n_samples: 正整数raw数。
            run_id: 同一制造chip上的可重放计划/噪声编号。
            dither_words: 可选(N,)显式六位字，启用Fig19静态等效端口；不生成隐藏PRNG。
                v1要求调用方明确关闭噪声/动态/DEM，建议先用±2.0V验证；不夹非法早prefix。
            dither_mode: `code_shift`（v1：RDAC 码整体减 U，要求均匀码本）
                或 `charge_port`（v2：码不动，upper 段折算成残差节点注入，
                可与分段码本/失配/噪声/DEM 同用；仍不含物理 dither 电容网络）。
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
            from adi_model.behavioral.ra_az import apply_ordinary_az
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
        dither_port = None
        mapper = self.mapper
        if dither_words is not None:
            if self._config_overrides.get("dither_enable") is False:
                raise ConfigError(
                    "explicit dither words conflict with dither_enable=False override"
                )
            if self.parameters.format.voltage_fraction_bits != 32:
                raise ConfigError("Fig19 static v1 shared digital interface requires explicit Q32")
            if dither_mode not in ("code_shift", "charge_port"):
                raise ConfigError("dither_mode must be 'code_shift' or 'charge_port'")
            spec = Fig19StaticSpec(
                v_fs=cfg.v_fs,
                step_q=FIG19_STEP_Q,
                voltage_fraction_bits=32,
                fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
                equivalent_polarity="subtract",
                provenance=FIG19_BINDING_REASON,
            )
            cfg = replace(cfg, dither_enable=True)
            if dither_mode == "code_shift":
                program = Fig19DitherProgram.from_words(dither_words, spec=spec)
                mapper = Mapper(
                    np.arange(512, dtype=np.int64),
                    nominal_codebook=self.codebook,
                    event_codes_by_coarse=complete_flash3_sar6_prefix_table(),
                )
            else:
                # 独立电荷口：保留实际分段码本与 Mapper，只把 dither 折算成电荷。
                # c_lsb 必须与**码本律同源**：码本 x(c) = -v_fs + 2·v_fs·drive/cn，
                # 每码步长 = 2·v_fs·(1/16)/cn 对应驱动单位 β/16 = 1/256 C。
                # 因此一个 LSB 的等效电容 = cn/512（cn = 31 + β·15 = 31.9375 C）。
                # 2026-11-13 修正：原式取"全部 18 片并联和 /512"，比每片口径大 18×8 倍，
                # dither 电荷被放大后把残差推出 RA 摆幅（实测均值误差曾达 2.8e6 LSB20）。
                params = self.parameters
                sub_switched = sum(SUB_WEIGHTS)
                beta_nominal = params.bridge_cap_f / (
                    params.bridge_cap_f
                    + sub_switched * params.unit_cap_f
                    + params.sub_parasitic_f
                )
                c_slice_nominal = params.unit_cap_f * (
                    (sum(MAIN_WEIGHTS[:15]) + MAIN_WEIGHTS[15]) + beta_nominal * sub_switched
                )
                dither_port = DitherPort.from_words(
                    dither_words,
                    spec=spec,
                    c_lsb_f=c_slice_nominal / 512.0,
                    reference_swing_v=2.0 * cfg.v_fs,
                )
        elif cfg.dither_enable:
            raise ConfigError("dither_enable requires explicit per-raw six-bit dither_words")
        pool = PhysicalSlicePool(
            cfg,
            chip=self.chip,
            nominal_codebook=self.codebook,
            mask_binding=fig11_masks,
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
            dither_port=dither_port,
        )
        assert raw.digital_observation is not None
        # 定点重建的分母是 Σw（码本口径 31.9375C），而 fine 由 RA 的物理增益
        # G_phys（采样口径 32.94C）产生。把 G_phys 显式传入，让解码器把 fine
        # 换算到 Σw 口径 —— 否则残差项被放大约 3.1%，误差地板 21 → 576 LSB20。
        codes = self.nominal_decoder.reconstruct(
            raw.digital_observation, physical_gain=self.state_estimated_gain(raw)
        )
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
    dither_mode: str = "code_shift",
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
        dither_mode: `code_shift`（v1，要求均匀码本）或 `charge_port`（v2，码不动）。
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
        dither_mode=dither_mode,
        ordinary_az=ordinary_az,
    )
