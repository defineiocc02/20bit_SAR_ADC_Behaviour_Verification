"""低层源架构配置、显式运行输入与缺项检查。

Config() 是可检查的不完整 9 bit 配置，不是已复原的原芯片。
本模块不为未公开的 ADC2 码制、窗口、SADC 阈值或电气参数填物理默认值；
高层工程推断集中在 defaults 模块。
实际 CDAC、桥接/反馈电容、逐 slice 状态及码到开关绑定由显式物理对象持有，数字推理不得读取其真值。

v_fs 为差分输入峰值 [V]，SADC 阈值位于其输入节点 [V]；ADC2 窗口与 RA 限幅位于 RA 输出节点 [V]。
时间为 s、电阻为 ohm、电容为 F、噪声幅度为 Vrms。
旧 7/6 bit、unary、granularity 与逐样本固定 4x dither 不属于本接口。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from numbers import Integral, Real
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, TypeGuard

import numpy as np

if TYPE_CHECKING:
    from adi_model.conversion import ConversionParameters
    from adi_model.input_network import InputNetworkParameters

K_B = 1.380_649e-23  # 精确定义的玻尔兹曼常数[J/K]；温度不是常量默认值。

# 只读来源/历史指标，不参与forward-model参数推算。不同测量版本不互相替代。
SOURCE_REFERENCE_VALUES = MappingProxyType(
    {
        "target_dr_db": (94.6, "dB", "historical model target; not a device noise source"),
        "ra_autozero_cost_db": (1.6, "dB", "ISSCC2024 slides34-35 benchmark annotation"),
        "dither_enhancement_bits": (2, "bit", "ISSCC2024 range statement; not dR/dQ"),
        "rdac_sampling_capacitance_f": (
            20.5e-12,
            "F",
            "ISSCC2024 sampling-capacitance report; physical node/geometry binding required",
        ),
    }
)


class ConfigError(ValueError):
    """缺少已选路径输入、非法数值或未实现绑定时的入口异常。"""


class PhysicalGeometry(Protocol):
    """仅供配置核对的维数，不暴露电容或每笔增益真值。"""

    @property
    def n_main(self) -> int:
        """主阵列物理单位数。"""
        ...

    @property
    def n_sub(self) -> int:
        """子阵列物理单位数。"""
        ...

    @property
    def n_slices(self) -> int:
        """物理slice总数。"""
        ...

    @property
    def n_active(self) -> int:
        """每笔实际转换slice数。"""
        ...


def _finite_real(value: object) -> TypeGuard[float]:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _integer(value: object, lower: int, upper: int | None = None) -> TypeGuard[int]:
    return (
        isinstance(value, Integral)
        and not isinstance(value, bool)
        and int(value) >= lower
        and (upper is None or int(value) <= upper)
    )


@dataclass
class Config:
    """源目标的运行输入；关闭机制不要求未消费的电气参数。

    Attributes:
        fs: raw 采样率 [Hz]，属于源架构常量。
        n_bits_target: 源目标输出字宽，不表示 ADC2 位数或 ENOB。
        v_fs: 外部差分输入峰值 [V]。
        b1: 对未知输入的 SADC 决策位数，已知 dither 不增加信息位。
        g0: 源名义残差增益，不能替代实际 Csignal/Cfeedback。
        n_slices: 源架构的物理 slice 池大小。
        n_active: 每笔转换的源架构活动片数。
        execution_profile: sample_domain 或 rtl16 执行接口，后者不提供模拟参数。
        dac_n_main: 可选显式主阵列维数，实际 geometry 可满足缺项。
        dac_n_sub: 可选显式子阵列维数，实际 geometry 可满足缺项。
        sadc_thresholds_v: 513 个递增的实际 SADC 节点阈值 [V]，不从 RDAC 真值生成。
        sadc_endpoint_policy: 显式 closed 或 half_open 端点约定。
        adc2_n_bits: 显式后端位数。
        adc2_v_min: 显式 ADC2 输入窗口下界 [RA 输出节点 V]。
        adc2_v_max: 显式 ADC2 输入窗口上界 [RA 输出节点 V]。
        adc2_codebook: 显式静态码制，当前只实现 uniform_midrise。
        adc2_endpoint_policy: 显式后端端点约定。
        ra_v_clip: 必须明确给出的 RA 输出对称限幅 [V]。
        dem_enable: 请求实际绑定的 DEM，物理池负责拒绝缺少映射的请求。
        dither_enable: 请求显式lower/upper program；运行时缺绑定即拒绝。
            当前仅支持单独验收的Fig19静态等效端口，不代表完整原芯片电路。

    其余可选字段的单位见字段注释。None 不产生随机器、旧预算或理想器件替身；构造不补物理输入，
    运行前由入口调用 check_legal。来源为 ISSCC2024 9.8 与既有源清理约定，未知电气值由调用方声明。
    """

    fs: float = 40e6  # raw采样率[Hz]。
    n_bits_target: int = 20  # 输出字宽；不是ADC2位数。
    v_fs: float = 3.0  # 外部差分输入峰值[V]。
    b1: int = 9  # 对未知输入的决策位数；dither不增加信息位。
    g0: float = 32.0  # 来源名义残差增益[V/V]。
    n_slices: int = 18
    n_active: int = 8
    execution_profile: str = "sample_domain"
    dac_n_main: int | None = None
    dac_n_sub: int | None = None

    sadc_thresholds_v: tuple[float, ...] | None = None
    sadc_endpoint_policy: str | None = None
    adc2_n_bits: int | None = None
    adc2_v_min: float | None = None
    adc2_v_max: float | None = None
    adc2_codebook: str | None = None
    adc2_endpoint_policy: str | None = None
    ra_v_clip: float | None = None
    ra_enable_noise: bool = False
    ra_out_noise_rms: float | None = None  # RA输出节点独立白噪声[Vrms]。
    ra_noise_distribution: str | None = None
    dem_enable: bool = False
    dither_enable: bool = False

    c_sadc: float | None = None  # SADC采样电容[F]，仅噪声/动态输入路径需要。
    sampling_tau_mismatch: float | None = None  # 可选一阶响应误差Δtau[s]。
    enable_sampling_noise: bool = False
    sampling_noise_distribution: str | None = None
    temperature_k: float | None = None
    chi: float | None = None  # 明确声明的kT/C系数，不默认差分系数2。
    driver_noise_rms: float | None = None  # None关闭；显式源输入噪声[Vrms]。
    driver_noise_distribution: str | None = None
    seed: int | None = None  # 仅实际生成随机样本时需要。

    dyn_input_settling: bool = False
    input_network: InputNetworkParameters | None = None
    dyn_r_source: float | None = None  # [ohm]。
    dyn_r_on: float | None = None  # [ohm]。
    dyn_ron_code_coeff: float | None = None  # Ron=Ron0*(1+rho*(Vin/Vfs)^2)。
    dyn_ref_settling: bool = False
    conversion: ConversionParameters | None = None
    dyn_c_decouple: float | None = None  # [F]。
    dyn_tau_ref: float | None = None  # [s]。
    dyn_crosstalk: bool = False
    dyn_c_xtalk_common: float | None = None  # [F]。
    dyn_c_xtalk_unit: float | None = None  # [F]。
    dyn_v_digital: float | None = None  # [V]。

    @classmethod
    def rtl_structural(cls, **values) -> Config:
        """声明rtl16执行接口，保留缺项供显式补全。

        Args:
            **values: 本schema中的显式输入；不得指定另一execution_profile。

        Returns:
            不自动填ADC2、物理几何或模拟时序的Config。

        Side effects: 无；构造不等于通过完整运行合法性检查。
        """
        if "execution_profile" in values and values["execution_profile"] != "rtl16":
            raise ConfigError("rtl_structural requires execution_profile='rtl16'")
        return cls(**{**values, "execution_profile": "rtl16"})

    def _architecture_violations(self) -> list[str]:
        bad = []
        for name in ("fs", "v_fs", "g0"):
            value = getattr(self, name)
            if not _finite_real(value) or value <= 0:
                bad.append(f"{name} must be finite and positive")
        for name, expected in (("n_bits_target", 20), ("b1", 9), ("n_slices", 18), ("n_active", 8)):
            if not _integer(getattr(self, name), expected, expected):
                bad.append(f"{name} must be {expected} for the source target")
        if self.execution_profile not in ("sample_domain", "rtl16"):
            bad.append("execution_profile must be sample_domain or rtl16")
        return bad

    def adc2_violations(self) -> list[str]:
        """检查ADC2自身合同，不要求未消费的电阻、电容或其他块输入。"""
        bad = []
        for name, unit in (
            ("adc2_n_bits", "bit"),
            ("adc2_v_min", "V"),
            ("adc2_v_max", "V"),
            ("adc2_codebook", "model"),
            ("adc2_endpoint_policy", "convention"),
        ):
            if getattr(self, name) is None:
                bad.append(f"missing {name} [{unit}] consumed by ADC2")
        if self.adc2_n_bits is not None and not _integer(self.adc2_n_bits, 1, 30):
            bad.append("adc2_n_bits must be an integer in [1, 30] for this numerical kernel")
        for name in ("adc2_v_min", "adc2_v_max"):
            if getattr(self, name) is not None and not _finite_real(getattr(self, name)):
                bad.append(f"{name} must be finite [V]")
        if (
            _finite_real(self.adc2_v_min)
            and _finite_real(self.adc2_v_max)
            and self.adc2_v_min >= self.adc2_v_max
        ):
            bad.append("adc2_v_min must be strictly below adc2_v_max [V]")
        if (
            not bad
            and self.adc2_n_bits is not None
            and self.adc2_v_min is not None
            and self.adc2_v_max is not None
        ):
            step = (float(self.adc2_v_max) - float(self.adc2_v_min)) / 2**self.adc2_n_bits
            if not math.isfinite(step) or step <= 0:
                bad.append(
                    "ADC2 window/bit count must yield a finite representable positive step [V]"
                )
        if self.adc2_codebook is not None and self.adc2_codebook != "uniform_midrise":
            bad.append("adc2_codebook currently supports only uniform_midrise")
        if self.adc2_endpoint_policy is not None and self.adc2_endpoint_policy not in (
            "closed",
            "half_open",
        ):
            bad.append("adc2_endpoint_policy must be closed or half_open")
        return bad

    def sadc_violations(self) -> list[str]:
        """检查显式9bit静态SADC阈值，不从RDAC端点或真值补阈值。"""
        bad = []
        if self.sadc_thresholds_v is None:
            bad.append("missing sadc_thresholds_v [V] consumed by SADC")
        else:
            try:
                if np.iscomplexobj(self.sadc_thresholds_v):
                    raise ValueError("complex thresholds are not physical node voltages")
                thresholds: np.ndarray = np.asarray(self.sadc_thresholds_v, dtype=float)
            except (TypeError, ValueError, OverflowError):
                bad.append("sadc_thresholds_v must be a finite real threshold vector [V]")
            else:
                if (
                    thresholds.shape != (513,)
                    or not np.all(np.isfinite(thresholds))
                    or np.any(thresholds[1:] <= thresholds[:-1])
                ):
                    bad.append("sadc_thresholds_v needs 513 finite strictly increasing values [V]")
        if self.sadc_endpoint_policy is None:
            bad.append("missing sadc_endpoint_policy [convention] consumed by SADC")
        elif self.sadc_endpoint_policy not in ("closed", "half_open"):
            bad.append("sadc_endpoint_policy must be closed or half_open")
        return bad

    def legality_violations(self, *, geometry: PhysicalGeometry | None = None) -> list[str]:
        """先报告缺项和类型，再检查已激活路径；不进行物理派生。

        Args:
            geometry: 实际物理对象的四个整数维数；可满足未填的Config几何。

        Returns:
            可定位到字段与消费者的错误列表；不修改Config或物理对象。
        """
        bad = self._architecture_violations() + self.adc2_violations() + self.sadc_violations()
        for name in (
            "ra_enable_noise",
            "dem_enable",
            "dither_enable",
            "enable_sampling_noise",
            "dyn_input_settling",
            "dyn_ref_settling",
            "dyn_crosstalk",
        ):
            if not isinstance(getattr(self, name), bool):
                bad.append(f"{name} must be bool")
        for name in ("dac_n_main", "dac_n_sub"):
            supplied = getattr(self, name)
            attribute = "n_main" if name == "dac_n_main" else "n_sub"
            actual = getattr(geometry, attribute, None) if geometry is not None else None
            if supplied is None and actual is None:
                bad.append(f"missing {name} [count] consumed by the physical geometry")
            elif supplied is not None and not _integer(supplied, 1):
                bad.append(f"{name} must be a positive integer")
            if actual is not None and not _integer(actual, 1):
                bad.append(f"geometry.{attribute} must be a positive integer")
            if supplied is not None and actual is not None and supplied != actual:
                bad.append(f"{name} conflicts with geometry.{attribute}")
        if geometry is not None:
            for name in ("n_slices", "n_active"):
                if not _integer(getattr(geometry, name, None), 1) or getattr(
                    geometry, name
                ) != getattr(self, name):
                    bad.append(f"geometry.{name} must match Config.{name}")

        def required_real(name: str, unit: str, consumer: str, *, zero: bool = False) -> None:
            value = getattr(self, name)
            if value is None:
                bad.append(f"missing {name} [{unit}] consumed by {consumer}")
            elif not _finite_real(value) or (value < 0 if zero else value <= 0):
                sign = "nonnegative" if zero else "positive"
                bad.append(f"{name} must be finite and {sign} [{unit}]")

        required_real("ra_v_clip", "V", "RA output clipping")
        for enabled, amplitude, law, consumer in (
            (
                self.ra_enable_noise is True,
                "ra_out_noise_rms",
                "ra_noise_distribution",
                "RA output white noise",
            ),
            (
                self.driver_noise_rms is not None or self.driver_noise_distribution is not None,
                "driver_noise_rms",
                "driver_noise_distribution",
                "driver noise",
            ),
        ):
            if enabled:
                required_real(amplitude, "Vrms", consumer, zero=True)
                if getattr(self, law) != "gaussian_white":
                    bad.append(f"{law} must explicitly be gaussian_white for {consumer}")
        random_active = (
            self.ra_enable_noise is True
            or self.enable_sampling_noise is True
            or self.driver_noise_rms is not None
            or self.driver_noise_distribution is not None
        )
        if random_active and not _integer(self.seed, 0):
            bad.append(
                "missing or invalid seed [nonnegative integer] consumed by active random sources"
            )
        if self.enable_sampling_noise is True:
            if self.sampling_noise_distribution != "gaussian_white":
                bad.append("sampling_noise_distribution must explicitly be gaussian_white")
            required_real("temperature_k", "K", "sampling kT/C noise")
            required_real("chi", "ratio", "sampling kT/C noise")
        if self.enable_sampling_noise is True or self.dyn_input_settling is True:
            required_real("c_sadc", "F", "SADC noise/input network")
        if self.sampling_tau_mismatch is not None and not _finite_real(self.sampling_tau_mismatch):
            bad.append("sampling_tau_mismatch must be finite [s]")
        if self.input_network is not None and self.dyn_input_settling is not True:
            from adi_model.input_network import InputNetworkParameters

            # 未启用的电气数值可缺省；已提供的块对象仍须符合消费者的类型合同。
            if not isinstance(self.input_network, InputNetworkParameters):
                bad.append("input_network must be InputNetworkParameters")
        if self.dyn_input_settling is True:
            for name, unit, zero in (
                ("dyn_r_source", "ohm", True),
                ("dyn_r_on", "ohm", False),
                ("dyn_ron_code_coeff", "ratio", True),
            ):
                required_real(name, unit, "dynamic input acquisition", zero=zero)
            if self.input_network is None:
                bad.append(
                    "missing input_network [explicit parameters] consumed by dynamic input acquisition"
                )
            else:
                from adi_model.input_network import InputNetworkParameters

                if not isinstance(self.input_network, InputNetworkParameters):
                    bad.append("input_network must be InputNetworkParameters")
                else:
                    try:
                        self.input_network.require_tracking()
                    except ValueError as error:
                        bad.append(str(error))
        if self.dyn_ref_settling is True:
            required_real("dyn_c_decouple", "F", "reference recovery")
            required_real("dyn_tau_ref", "s", "reference recovery")
        if self.conversion is not None:
            from adi_model.conversion import ConversionParameters

            if not isinstance(self.conversion, ConversionParameters):
                bad.append("conversion must be ConversionParameters")
            else:
                try:
                    self.conversion.require_runtime(
                        fs=self.fs, reference_active=self.dyn_ref_settling
                    )
                except ValueError as error:
                    bad.append(str(error))
        if self.dyn_ref_settling is True and self.conversion is None:
            bad.append("missing conversion [explicit timing] consumed by reference recovery")
        if self.dyn_crosstalk is True:
            required_real("dyn_c_xtalk_common", "F", "digital crosstalk", zero=True)
            required_real("dyn_c_xtalk_unit", "F", "digital crosstalk", zero=True)
            required_real("dyn_v_digital", "V", "digital crosstalk")
        return bad

    def check_legal(self, *, geometry: PhysicalGeometry | None = None) -> None:
        """拒绝缺项或非法配置；此检查在任何整链派生之前执行。

        Args:
            geometry: 可选实际物理对象维数，不包含估计器可访问的电容真值。

        Raises:
            ConfigError: 缺少被消费输入、非法字段或未实现机制。

        Side effects: 无；不会填值、关闭功能或降级到另一profile。
        """
        bad = self.legality_violations(geometry=geometry)
        if bad:
            raise ConfigError("configuration cannot run:\n  - " + "\n  - ".join(bad))

    @property
    def lsb_target(self) -> float:
        """外部目标码步长2*Vfs/2**20[V]，不代表物理分辨能力。"""
        self._check_architecture()
        return 2.0 * self.v_fs / 2**self.n_bits_target

    @property
    def delta1(self) -> float:
        """名义均匀512区间的步长[V]，不代替显式SADC/CDAC阈值。"""
        self._check_architecture()
        return 2.0 * self.v_fs / 2**self.b1

    @property
    def stage1_levels(self) -> int:
        """9个未知输入决策位对应的区间数512，不增加已知dither信息位。"""
        self._check_architecture()
        return 2**self.b1

    @property
    def delta2(self) -> float:
        """显式uniform-midrise ADC2的RA输出节点码步长[V]。"""
        bad = self.adc2_violations()
        if bad:
            raise ConfigError("; ".join(bad))
        assert self.adc2_v_min is not None and self.adc2_v_max is not None
        assert self.adc2_n_bits is not None
        return (self.adc2_v_max - self.adc2_v_min) / 2**self.adc2_n_bits

    def _check_architecture(self) -> None:
        bad = self._architecture_violations()
        if bad:
            raise ConfigError("; ".join(bad))

    def validate(self, verbose: bool = True, *, geometry: PhysicalGeometry | None = None) -> dict:
        """检查完整输入并报告码步长，不反推电容、噪声、增益或余量。

        Args:
            verbose: 是否打印显式数值报告。
            geometry: 物理对象四个整数维数，可满足几何缺项。

        Returns:
            (数值,门限,是否通过,单位)字典；不包含未运行的模拟性能结论。

        Raises:
            ConfigError: 检查在任何数值派生前拒绝缺项。
        """
        self.check_legal(geometry=geometry)
        checks = {
            "SADC decision intervals": (float(self.stage1_levels), 512.0, True, "count"),
            "target output step": (self.lsb_target, None, True, "V"),
            "ADC2 code step": (self.delta2, None, True, "V at RA output"),
        }
        if verbose:
            for name, (value, _limit, _passed, unit) in checks.items():
                print(f"{name}: {value:.12g} [{unit}]")
        return checks

    def to_dict(self) -> dict:
        """导出含None的显式输入；不以来源指标填补缺项。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict) -> Config:
        """恢复可不完整的配置，未知字段由构造器拒绝。

        Args:
            values: 本schema字段及SI数值；嵌套参数必须由调用者显式给出。

        Returns:
            独立Config；运行入口仍必须调用check_legal。

        Side effects: 不填旧夹具、不推模拟值、不执行运行。
        """
        params = dict(values)
        if isinstance(params.get("input_network"), dict):
            from adi_model.input_network import InputNetworkParameters

            params["input_network"] = InputNetworkParameters(**params["input_network"])
        if isinstance(params.get("conversion"), dict):
            from adi_model.conversion import ConversionParameters

            params["conversion"] = ConversionParameters(**params["conversion"])
        if params.get("sadc_thresholds_v") is not None:
            params["sadc_thresholds_v"] = tuple(params["sadc_thresholds_v"])
        return cls(**params)
