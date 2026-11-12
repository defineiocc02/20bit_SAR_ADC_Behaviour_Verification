"""split ADC的冻结系数装载与整数重构。

输入为数字观测、名义电压栅格和冻结有效C/Cf系数，不读模拟输出或未知输入。电压寄存器使用归一化V/Vfs，
权重为正的有效C/Cf；分数位、存储位宽和受检累加位宽由调用者显式提供。
最终20bit offset-binary采用[-Vfs,+Vfs)半开midrise箱，floor确定输出码，voltage属性只解码箱中心[输入V]作诊断。

系数量化使用ties-to-even；模拟越界与最终数字裁剪分别保留。20bit码宽不代表实测20bit线性度。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from adi_model._arrays import readonly as _readonly
from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation, FrozenCalibration, _terms


def round_even_divide(n: int, d: int) -> int:
    """对有符号整数比就近舍入，正好居中时取偶数。"""
    if d <= 0:
        raise ValueError("denominator must be positive")
    q, r = divmod(n, d)
    return q + int(2 * r > d or (2 * r == d and q % 2 != 0))


@dataclass(frozen=True)
class FixedPointFormat:
    """显式整数算术约定，包含有符号电压寄存器与累加器位宽。"""

    weight_fraction_bits: int
    voltage_fraction_bits: int
    voltage_bits: int
    coefficient_bits: int
    accumulator_bits: int
    output_bits: int = 20

    def __post_init__(self):
        """拒绝不支持或含义不明确的整数格式。"""
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not (
            self.weight_fraction_bits < self.coefficient_bits <= 62
            and self.voltage_fraction_bits <= 48
            and self.voltage_fraction_bits < self.voltage_bits <= 64
            and 8 <= self.accumulator_bits <= 256
            and self.output_bits == 20
        ):
            raise ValueError("unsupported fixed-point format")


@dataclass(frozen=True)
class CodeStream:
    """最终 offset-binary 码，以及分别保留的模拟越界和数字裁剪标志。"""

    code: np.ndarray
    clipped_low: np.ndarray
    clipped_high: np.ndarray
    analog_overflow: np.ndarray
    v_fs: float
    output_bits: int
    peak_accumulator_bits: int

    @property
    def voltage(self) -> np.ndarray:
        """将最终码解码为输入等效 midrise 箱中心电压 [V]。"""
        return self.v_fs * (2 * (self.code + 0.5) / 2**self.output_bits - 1)


@dataclass(frozen=True)
class FixedPointReconstructor:
    """冻结后的量化系数；记录处理采用整数算术。"""

    spec: CalibrationSpec
    format: FixedPointFormat
    weights_q: np.ndarray
    offset_q: int
    adc2_min_q: int
    adc2_max_q: int

    def __post_init__(self):
        """检查寄存器数值和受限的 int64 掩码求和阶段。"""
        if not isinstance(self.format, FixedPointFormat):
            raise ValueError("an explicit FixedPointFormat is required for integer reconstruction")
        if (
            not np.isfinite(self.spec.v_fs)
            or self.spec.v_fs <= 0
            or not np.isfinite(self.spec.adc2_v_min)
            or not np.isfinite(self.spec.adc2_v_max)
            or self.spec.adc2_v_min >= self.spec.adc2_v_max
        ):
            raise ValueError(
                "register image requires a positive finite full-scale voltage "
                "and finite increasing ADC2 voltage bounds"
            )
        if type(self.spec.adc2_n_bits) is not int or not 1 <= self.spec.adc2_n_bits <= 30:
            raise ValueError("register image ADC2 width must be an integer in [1, 30]")
        w = np.asarray(self.weights_q)
        if (
            w.shape != self.spec.shape
            or not np.issubdtype(w.dtype, np.integer)
            or np.any(w <= 0)
            or np.any(w >= 2 ** (self.format.coefficient_bits - 1))
            or sum(map(int, w.flat)) >= 2**60
        ):
            raise ValueError("weights must fit positive signed coefficient and mask-sum registers")
        if any(type(v) is not int for v in (self.offset_q, self.adc2_min_q, self.adc2_max_q)):
            raise ValueError("voltage registers must contain integers")
        if any(
            not -(2 ** (self.format.voltage_bits - 1)) <= x < 2 ** (self.format.voltage_bits - 1)
            for x in (self.offset_q, self.adc2_min_q, self.adc2_max_q)
        ):
            raise ValueError("voltage coefficients exceed their signed register width")
        if self.adc2_min_q >= self.adc2_max_q:
            raise ValueError("quantized backend range is empty")
        object.__setattr__(self, "weights_q", _readonly(w, np.int64))

    @classmethod
    def from_weights(cls, spec, weights, offset_v, *, format: FixedPointFormat):
        """一次量化名义或独立校准系数，居中时取偶数。"""
        if not isinstance(format, FixedPointFormat):
            raise ValueError("an explicit FixedPointFormat is required to quantize coefficients")
        fmt = format
        w = np.asarray(weights, dtype=float)
        scale = 2**fmt.weight_fraction_bits
        with np.errstate(over="ignore", invalid="ignore"):
            quantized_w = np.rint(w * scale)
        if (
            np.any(~np.isfinite(quantized_w))
            or np.any(quantized_w <= 0)
            or np.any(quantized_w >= 2 ** (fmt.coefficient_bits - 1))
        ):
            raise ValueError("weight values cannot be represented")
        v = np.asarray([offset_v, spec.adc2_v_min, spec.adc2_v_max]) / spec.v_fs
        with np.errstate(over="ignore", invalid="ignore"):
            quantized_v = np.rint(v * 2**fmt.voltage_fraction_bits)
        if np.any(~np.isfinite(quantized_v)):
            raise ValueError("invalid normalized voltage coefficients")
        q = [int(value) for value in quantized_v]
        return cls(spec, fmt, quantized_w.astype(np.int64), *q)

    @classmethod
    def from_result(cls, result, *, format: FixedPointFormat, calibration=None):
        """从显式提供或已经冻结的拟合模型建立寄存器。

        缺少拟合模型时拒绝。名义对照须由调用方把已声明的系数矩阵显式传给 from_weights。
        """
        data = DigitalObservation.from_result(result)
        model = result.state.weight_calibration if calibration is None else calibration
        if not isinstance(model, FrozenCalibration):
            raise ValueError(
                "integer reconstruction requires frozen effective C/Cf coefficients; "
                "supply calibration or explicitly construct nominal registers with from_weights"
            )
        return cls.from_frozen_calibration(data.spec, model, format=format)

    @classmethod
    def from_frozen_calibration(
        cls,
        spec: CalibrationSpec,
        model: FrozenCalibration,
        *,
        format: FixedPointFormat,
    ):
        """仅在完整解码规格匹配时量化外部拟合系数。"""
        if model.spec != spec:
            raise ValueError(
                "frozen calibration specification differs from the digital configuration"
            )
        return cls.from_weights(spec, model.weights, model.offset_v, format=format)

    def reconstruct(self, data: DigitalObservation) -> CodeStream:
        """从显式绑定的数字 a 和电压项重构整数码。

        每个已知逐单元电压项先按声明的归一化电压寄存器格式量化，居中时取偶数。
        随后用 Python 整数完成乘加，并在 floor 除法和最终饱和之前检查声明的累加范围；
        不推断任何物理掩码。
        """
        data.validate()
        if data.spec != self.spec:
            raise ValueError("digital record does not match the frozen register interface")
        n = len(data.adc2_code)
        output: np.ndarray = np.empty(n, dtype=np.int64)
        low: np.ndarray = np.zeros(n, bool)
        high: np.ndarray = np.zeros(n, bool)
        vscale, wscale = 2**self.format.voltage_fraction_bits, 2**self.format.weight_fraction_bits
        count = 2**self.format.output_bits
        limit = 2 ** (self.format.accumulator_bits - 1)
        voltage_limit = 2 ** (self.format.voltage_bits - 1)
        peak = 1
        for start in range(0, n, 256):
            stop = min(start + 256, n)
            ids, signal, known_term = _terms(data, start, stop)
            with np.errstate(over="ignore", invalid="ignore"):
                scaled = np.rint(known_term / self.spec.v_fs * vscale)
            if (
                np.any(~np.isfinite(scaled))
                or np.any(scaled < -voltage_limit)
                or np.any(scaled >= voltage_limit)
            ):
                raise OverflowError(
                    "known digital voltage term exceeds its declared voltage register"
                )
            terms_q = scaled.astype(np.int64)
            weights = self.weights_q[ids]
            gains = np.sum(weights * signal.astype(np.int64), axis=(1, 2))
            for j in range(stop - start):
                k = start + j
                fine_q = self.adc2_min_q + round_even_divide(
                    (2 * int(data.adc2_code[k]) + 1) * (self.adc2_max_q - self.adc2_min_q),
                    2 ** (self.spec.adc2_n_bits + 1),
                )
                gain = int(gains[j])
                if gain <= 0:
                    raise ValueError("quantized signal gain must be positive")
                term_products = [
                    int(weight) * int(term)
                    for weight, term in zip(weights[j].flat, terms_q[j].flat, strict=True)
                ]
                known_sum = sum(term_products)
                fine_product = (fine_q - self.offset_q) * wscale
                numerator = fine_product - known_sum
                shifted = (numerator + gain * vscale) * count
                denominator = 2 * gain * vscale
                operands = (
                    *term_products,
                    known_sum,
                    fine_product,
                    numerator,
                    shifted,
                    denominator,
                )
                peak = max(
                    peak,
                    *((value if value >= 0 else ~value).bit_length() + 1 for value in operands),
                )
                if any(not -limit <= value < limit for value in operands):
                    raise OverflowError(
                        "fixed-point accumulator overflow; widen the declared format"
                    )
                word = shifted // denominator
                low[k], high[k] = word < 0, word >= count
                output[k] = min(max(word, 0), count - 1)
        return CodeStream(
            _readonly(output, np.int64),
            _readonly(low, bool),
            _readonly(high, bool),
            _readonly(data.overflow, bool),
            self.spec.v_fs,
            self.format.output_bits,
            peak,
        )

    def to_dict(self) -> dict:
        """导出带明确单位和格式版本的标准 JSON 寄存器映像。"""
        return {
            "schema_version": 1,
            "voltage_unit": "normalized V/Vfs",
            "spec": asdict(self.spec),
            "format": asdict(self.format),
            "weights_q": self.weights_q.tolist(),
            "offset_q": self.offset_q,
            "adc2_min_q": self.adc2_min_q,
            "adc2_max_q": self.adc2_max_q,
        }

    @classmethod
    def from_dict(cls, value: dict):
        """恢复并检查寄存器映像，不访问物理模型对象。"""
        data = dict(value)
        if (
            data.pop("schema_version", None) != 1
            or data.pop("voltage_unit", None) != "normalized V/Vfs"
        ):
            raise ValueError("unknown fixed-point register schema or units")
        data["spec"] = CalibrationSpec(**data["spec"])
        data["format"] = FixedPointFormat(**data["format"])
        data["weights_q"] = np.asarray(data["weights_q"])
        return cls(**data)
