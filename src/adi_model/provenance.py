"""对具体数值作来源分级，不提供物理默认值或反向拟合。

目标架构只有少量公开常量。未公开的部件值与模型选择仍是显式提供的工程输入。None 表示未绑定，
本模块既不解析它，也不构造 Config 默认值作为证据；含假设或拟合依赖的代数推导保留相应等级。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any, Generic, TypeVar

__all__ = [
    "PARAM_GRADES",
    "SOURCE_VALUES",
    "Graded",
    "GradingError",
    "SourceGrade",
    "annotate_config",
    "audit_provenance",
    "grade_of",
]

T = TypeVar("T")


class SourceGrade(enum.Enum):
    """数值的来源等级；工程推算不会自动产生原文证据。"""

    DISCLOSED = "disclosed"
    DERIVED = "derived"
    FITTED = "fitted"
    ASSUMED = "assumed"
    RESEARCH_EXTENSION = "research_extension"

    @property
    def quotable(self) -> bool:
        """标识来源支持的等级；具体数值仍须通过 Graded.require 检查。"""
        return self in (SourceGrade.DISCLOSED, SourceGrade.DERIVED)

    @property
    def label_zh(self) -> str:
        """返回统一的中文证据等级标签。"""
        return {
            SourceGrade.DISCLOSED: "[披露]",
            SourceGrade.DERIVED: "[推导]",
            SourceGrade.FITTED: "[拟合]",
            SourceGrade.ASSUMED: "[假设]",
            SourceGrade.RESEARCH_EXTENSION: "[研究扩展]",
        }[self]


class GradingError(RuntimeError):
    """缺失数值或当前有效证据等级不足以支持所请求结论时的异常。"""


def _derived_grade(grades: tuple[SourceGrade, ...]) -> SourceGrade:
    for grade in (SourceGrade.RESEARCH_EXTENSION, SourceGrade.ASSUMED, SourceGrade.FITTED):
        if grade in grades:
            return grade
    return SourceGrade.DERIVED


@dataclass(frozen=True)
class Graded(Generic[T]):
    """声明的数值、单位与依赖项的有效证据等级。"""

    value: T
    grade: SourceGrade
    source: str
    note: str = ""
    unit: str = ""
    overridden: bool = False
    dependencies: tuple[SourceGrade, ...] = ()

    def __post_init__(self):
        """检查来源明确，且依赖项的证据等级类型正确。"""
        if not isinstance(self.grade, SourceGrade) or not self.source.strip():
            raise ValueError("a graded value requires a source grade and a nonempty origin")
        if any(not isinstance(grade, SourceGrade) for grade in self.dependencies):
            raise ValueError("dependency evidence must use SourceGrade values")

    @property
    def effective_grade(self) -> SourceGrade:
        """对覆盖值降级，并传播假设、拟合与研究扩展依赖。"""
        if self.overridden and self.grade in (SourceGrade.DISCLOSED, SourceGrade.DERIVED):
            return SourceGrade.ASSUMED
        if self.dependencies:
            dependency_grade = _derived_grade(self.dependencies)
            return _derived_grade((self.grade, dependency_grade))
        return self.grade

    def require(self, *allowed: SourceGrade) -> T:
        """仅在数值已绑定且有效等级获准时返回数值。"""
        if self.value is None:
            raise GradingError(
                f"unbound value from {self.source!r}; no physical default is supplied"
            )
        if self.effective_grade not in allowed:
            raise GradingError(
                f"value graded {self.effective_grade.name} from {self.source!r}; "
                f"allowed grades are {[grade.name for grade in allowed]}"
            )
        return self.value

    def map(
        self, fn, *, source: str, unit: str, dependencies: tuple[Graded[Any], ...]
    ) -> Graded[Any]:
        """以声明的公式、单位和全部附加依赖推导数值。

        数值函数是调用方提供的计算，不是原文证据。额外自由参数必须列入 dependencies；
        没有其他输入时显式传入空 tuple。
        """
        if self.value is None or any(value.value is None for value in dependencies):
            raise GradingError("cannot derive a quantity from an unbound input")
        grades = (self.effective_grade, *(value.effective_grade for value in dependencies))
        return Graded(fn(self.value), SourceGrade.DERIVED, source, unit=unit, dependencies=grades)


_G = SourceGrade.DISCLOSED
_A = SourceGrade.ASSUMED

# 原文字面数值独立于任何 Config 实例或历史默认值。
SOURCE_VALUES = MappingProxyType(
    {
        "fs": 40e6,
        "n_bits_target": 20,
        "v_fs": 3.0,
        "b1": 9,
        "g0": 32.0,
        "n_slices": 18,
        "n_active": 8,
    }
)

PARAM_GRADES: dict[str, tuple[SourceGrade, str]] = {
    "fs": (_G, "ISSCC2024 paper p1: 40 MS/s raw conversion rate"),
    "n_bits_target": (_G, "ISSCC2024 paper p1: 20-bit output target, not ENOB"),
    "v_fs": (_G, "ISSCC2024 paper p1: 6 Vpp differential input, peak 3 V"),
    "b1": (_G, "ISSCC2024 paper p1 and slides9: 9 unknown-input decision bits"),
    "g0": (_G, "ISSCC2024 Fig9.8.1/slides9: nominal RA gain 32"),
    "n_slices": (_G, "ISSCC2024 paper p1: pool of 18 RDAC slices"),
    "n_active": (_G, "ISSCC2024 paper p1: 8 converting and 8 acquiring slices"),
    "execution_profile": (
        _A,
        "explicit numerical execution interface, not a silicon clock disclosure",
    ),
    "dac_n_main": (_A, "explicit physical geometry; capacitor count is not disclosed"),
    "dac_n_sub": (_A, "explicit physical geometry; capacitor count is not disclosed"),
    "sadc_thresholds_v": (_A, "explicit SADC input-node threshold vector [V]"),
    "sadc_endpoint_policy": (_A, "explicit SADC endpoint convention"),
    "adc2_n_bits": (_A, "explicit backend width; not derived from output20 or RA span"),
    "adc2_v_min": (_A, "explicit ADC2 RA-output lower voltage bound [V]"),
    "adc2_v_max": (_A, "explicit ADC2 RA-output upper voltage bound [V]"),
    "adc2_codebook": (_A, "explicit backend quantizer model, not a disclosed circuit"),
    "adc2_endpoint_policy": (_A, "explicit backend endpoint convention"),
    "ra_v_clip": (_A, "explicit RA-output clipping voltage [V]"),
    "ra_enable_noise": (_A, "explicit model activation"),
    "ra_out_noise_rms": (_A, "explicit RA-output noise [Vrms]; never reverse-fitted from DR"),
    "ra_noise_distribution": (_A, "explicit RA-output noise law"),
    "dem_enable": (_A, "explicit request for an actually supplied physical mapping"),
    "dither_enable": (
        _A,
        "explicit lower/upper program required; unbound or unsupported paths reject; "
        "profile assumptions are graded separately",
    ),
    "c_sadc": (_A, "explicit SADC sampling capacitance [F]"),
    "sampling_tau_mismatch": (_A, "explicit first-order SADC/RDAC response difference [s]"),
    "enable_sampling_noise": (_A, "explicit sampling-noise activation"),
    "sampling_noise_distribution": (_A, "explicit sampling-noise law"),
    "temperature_k": (_A, "explicit temperature [K]"),
    "chi": (_A, "explicit sampling-noise coefficient; no assumed differential factor"),
    "driver_noise_rms": (_A, "explicit input-source noise [Vrms]"),
    "driver_noise_distribution": (_A, "explicit input-source noise law"),
    "seed": (_A, "explicit numerical reproducibility input, not a device value"),
    "dyn_input_settling": (_A, "explicit dynamic-input activation"),
    "input_network": (_A, "explicit passive topology and numerical controls"),
    "dyn_r_source": (_A, "explicit source resistance [ohm]"),
    "dyn_r_on": (_A, "explicit switch resistance [ohm]"),
    "dyn_ron_code_coeff": (_A, "explicit signal-dependent switch-resistance law"),
    "dyn_ref_settling": (_A, "explicit reference-recovery activation"),
    "conversion": (_A, "explicit RA/ADC2 timing and electrical parameters"),
    "dyn_c_decouple": (_A, "explicit reference decoupling capacitance [F]"),
    "dyn_tau_ref": (_A, "explicit reference recovery time constant [s]"),
    "dyn_crosstalk": (_A, "explicit crosstalk activation"),
    "dyn_c_xtalk_common": (_A, "explicit common coupling capacitance [F]"),
    "dyn_c_xtalk_unit": (_A, "explicit per-unit coupling capacitance [F]"),
    "dyn_v_digital": (_A, "explicit digital switching voltage [V]"),
}

PARAM_UNITS = {
    "fs": "Hz",
    "n_bits_target": "bit",
    "v_fs": "V",
    "b1": "bit",
    "g0": "V/V",
    "n_slices": "count",
    "n_active": "count",
    "execution_profile": "model",
    "dac_n_main": "count",
    "dac_n_sub": "count",
    "sadc_thresholds_v": "V",
    "sadc_endpoint_policy": "convention",
    "adc2_n_bits": "bit",
    "adc2_v_min": "V",
    "adc2_v_max": "V",
    "adc2_codebook": "model",
    "adc2_endpoint_policy": "convention",
    "ra_v_clip": "V",
    "ra_enable_noise": "bool",
    "ra_out_noise_rms": "Vrms",
    "ra_noise_distribution": "model",
    "dem_enable": "bool",
    "dither_enable": "bool",
    "c_sadc": "F",
    "sampling_tau_mismatch": "s",
    "enable_sampling_noise": "bool",
    "sampling_noise_distribution": "model",
    "temperature_k": "K",
    "chi": "ratio",
    "driver_noise_rms": "Vrms",
    "driver_noise_distribution": "model",
    "seed": "integer",
    "dyn_input_settling": "bool",
    "input_network": "explicit parameters",
    "dyn_r_source": "ohm",
    "dyn_r_on": "ohm",
    "dyn_ron_code_coeff": "ratio",
    "dyn_ref_settling": "bool",
    "conversion": "explicit parameters",
    "dyn_c_decouple": "F",
    "dyn_tau_ref": "s",
    "dyn_crosstalk": "bool",
    "dyn_c_xtalk_common": "F",
    "dyn_c_xtalk_unit": "F",
    "dyn_v_digital": "V",
}


def grade_of(name: str) -> tuple[SourceGrade, str]:
    """返回已登记字段的来源；未知字段抛出 KeyError。"""
    return PARAM_GRADES[name]


def _equal(a: Any, b: Any) -> bool:
    try:
        return bool(a == b)
    except (TypeError, ValueError):
        return False


def annotate_config(cfg: Any) -> dict[str, Graded[Any]]:
    """标注当前值，不解析 None，也不构造默认配置作为依据。"""
    result = {}
    for item in fields(cfg):
        name = item.name
        value = getattr(cfg, name)
        grade, source = PARAM_GRADES.get(name, (_A, "UNREGISTERED"))
        overridden = name in SOURCE_VALUES and not _equal(value, SOURCE_VALUES[name])
        result[name] = Graded(
            value, grade, source, unit=PARAM_UNITS.get(name, "unregistered"), overridden=overridden
        )
    return result


def audit_provenance(cfg: Any) -> dict[str, Any]:
    """报告当前来源与未绑定字段，不充当消费者合法性门禁。

    允许不完整的 Config 对象。每个已选择的数值或物理消费者负责自己的必填输入检查；
    本模块不把未启用的可选字段变成人为要求。
    """
    annotations = annotate_config(cfg)
    counts = {grade.value: 0 for grade in SourceGrade}
    for value in annotations.values():
        counts[value.effective_grade.value] += 1
    unregistered = sorted(
        name for name, value in annotations.items() if value.source == "UNREGISTERED"
    )
    return {
        "counts": counts,
        "ungraded": unregistered,
        "unbound": sorted(name for name, value in annotations.items() if value.value is None),
        "assumed_supplied": sorted(
            name
            for name, value in annotations.items()
            if value.value is not None and value.effective_grade is _A
        ),
        "fitted_supplied": sorted(
            name
            for name, value in annotations.items()
            if value.value is not None and value.effective_grade is SourceGrade.FITTED
        ),
        "overridden": sorted(name for name, value in annotations.items() if value.overridden),
        "verdict": "registered origins; consumer validity checked separately"
        if not unregistered
        else "FAIL: unregistered fields",
    }
