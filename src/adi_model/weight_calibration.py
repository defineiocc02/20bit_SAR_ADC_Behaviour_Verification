"""从显式数字连接记录外部估计静态权重。

RA 输出满足 fine = sum(w * (a*x_known + term)) + offset。a 为观测到的信号连接掩码，
term=b-VD 是由控制和显式名义参考绑定给出的已知采样/参考电压减转换参考电压 [V]。主权重为有效 C/Cf，
子权重另含静态桥接比例。a 和 term 均不得含隐藏模拟测量；本项目 SVD 方法不是原芯片披露的学习算法。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from numbers import Real
from typing import Any

import numpy as np
from scipy.linalg import svd

from ._arrays import readonly as _readonly

STATIC_OBSERVATION_DOMAIN = "static_charge_connections_v2"


@dataclass(frozen=True)
class CalibrationSpec:
    """显式数字几何与所选后端解码模型。

    uniform_midrise 表示无符号递增原始码、等宽量化箱，并在箱中心解码 [RA 输出节点 V]。
    adc2_endpoint_policy 明确选择上端点有效或标溢出；上游有效性与原始码分开保留。这些是工程假设，
    不是 ADC2 电路披露。
    """

    n_slices: int
    n_active: int
    dac_n_main: int
    dac_n_sub: int
    v_fs: float
    adc2_n_bits: int
    adc2_v_min: float
    adc2_v_max: float
    adc2_codebook: str
    adc2_endpoint_policy: str

    def __post_init__(self):
        """检查显式维数、电压尺度与后端约定。"""
        for name in ("n_slices", "n_active", "dac_n_main", "dac_n_sub", "adc2_n_bits"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be an explicitly supplied positive integer")
        if self.n_active > self.n_slices or self.adc2_n_bits > 30:
            raise ValueError("calibration geometry or backend code width is unsupported")
        if not all(
            isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v)
            for v in (self.v_fs, self.adc2_v_min, self.adc2_v_max)
        ):
            raise ValueError("Vfs and ADC2 voltage bounds must be finite [V]")
        if self.v_fs <= 0 or self.adc2_v_min >= self.adc2_v_max:
            raise ValueError("Vfs must be positive and ADC2 voltage bounds increasing [V]")
        if not math.isfinite(self.adc2_step_v) or self.adc2_step_v <= 0:
            raise ValueError(
                "the explicit ADC2 window and width must yield a finite positive step [V]"
            )
        if self.adc2_codebook != "uniform_midrise":
            raise ValueError(
                "calibration requires an explicit supported ADC2 codebook: uniform_midrise"
            )
        if self.adc2_endpoint_policy not in ("closed", "half_open"):
            raise ValueError(
                "calibration requires an explicit ADC2 endpoint policy: closed or half_open"
            )

    @classmethod
    def from_config(cls, cfg, *, geometry=None) -> CalibrationSpec:
        """只读已明确提供的名义字段，不推算缺失数值。"""
        values: dict[str, Any] = {f.name: getattr(cfg, f.name, None) for f in fields(cls)}
        if geometry is not None:
            for name, geometry_name in (
                ("n_slices", "n_slices"),
                ("n_active", "n_active"),
                ("dac_n_main", "n_main"),
                ("dac_n_sub", "n_sub"),
            ):
                declared = getattr(geometry, geometry_name, None)
                if values[name] is not None and declared != values[name]:
                    raise ValueError(f"calibration geometry conflicts with the declared {name}")
                values[name] = declared
        missing = [name for name, value in values.items() if value is None]
        if missing:
            raise ValueError(
                "calibration nominal interface requires explicit fields: " + ", ".join(missing)
            )
        return cls(**values)

    @property
    def shape(self) -> tuple[int, int]:
        """返回物理权重数组的维数，不包含电容数值。"""
        return self.n_slices, self.dac_n_main + self.dac_n_sub

    @property
    def adc2_step_v(self) -> float:
        """返回显式选定均匀后端模型的步长 [V]。"""
        return (self.adc2_v_max - self.adc2_v_min) / 2**self.adc2_n_bits


@dataclass(frozen=True)
class DigitalObservation:
    """不可变数字控制、原始码与显式绑定的已知项。

    main/sub mask 描述实际转换开关 [0/1]；
    signal_mask 和 known_voltage_term_v 的形状均为 (samples, active slices, physical units)。
    它们必须来自数字控制及声明的名义电压绑定，不以实际电容、保持电压、增益或噪声为输入。
    不从 tail 单元、DEM 状态或 dither 模式重建缺失数据。
    """

    spec: CalibrationSpec
    slice_ids: np.ndarray
    adc2_code: np.ndarray
    main_masks: np.ndarray
    sub_masks: np.ndarray
    signal_mask: np.ndarray
    known_voltage_term_v: np.ndarray
    overflow: np.ndarray
    sample_ids: np.ndarray
    static_charge_domain: bool

    def __post_init__(self):
        """按原离散或实数类型冻结数字输入快照。"""
        for name in (
            "slice_ids",
            "adc2_code",
            "main_masks",
            "sub_masks",
            "signal_mask",
            "known_voltage_term_v",
            "overflow",
            "sample_ids",
        ):
            value = np.asarray(getattr(self, name))
            object.__setattr__(self, name, _readonly(value, value.dtype))
        self.validate()

    @classmethod
    def from_result(cls, result, *, observation=None) -> DigitalObservation:
        """接收运行入口绑定的数字记录，拒绝未绑定的结果。

        运行入口或调用方须由实际控制和显式参考绑定构造记录。本方法不检查物理状态，
        也不根据历史开关假设重新生成 a/b。
        """
        data = getattr(result, "digital_observation", None) if observation is None else observation
        if not isinstance(data, cls):
            raise ValueError(
                "digital reconstruction requires explicitly bound DigitalObservation: "
                "actual conversion masks, signal_mask and known_voltage_term_v [V]"
            )
        data.validate()
        if result.adc2_code is None or not np.array_equal(data.adc2_code, result.adc2_code):
            raise ValueError("bound digital observations do not match the result's raw ADC2 codes")
        result_ids = getattr(result, "sample_id", None)
        if result_ids is not None and not np.array_equal(data.sample_ids, result_ids):
            raise ValueError("bound digital observations do not match the result's sample IDs")
        return data

    def validate(self) -> None:
        """检查实际记录维数、离散控制与电压单位。"""
        if not isinstance(self.spec, CalibrationSpec):
            raise ValueError("digital observations require an explicit CalibrationSpec")
        if type(self.static_charge_domain) is not bool:
            raise ValueError("static charge-domain validity must be explicitly declared")
        n = self.adc2_code.size
        spec = self.spec
        if n == 0 or self.adc2_code.shape != (n,):
            raise ValueError("raw ADC2 codes must be a nonempty one-dimensional record")
        if self.slice_ids.shape != (n, spec.n_active) or not np.issubdtype(
            self.slice_ids.dtype, np.integer
        ):
            raise ValueError("digital slice IDs must be an integer (samples, active slices) array")
        if np.any(self.slice_ids < 0) or np.any(self.slice_ids >= spec.n_slices):
            raise ValueError("digital slice IDs are outside the explicitly supplied geometry")
        if np.any(np.diff(np.sort(self.slice_ids, axis=1), axis=1) == 0):
            raise ValueError("duplicate physical slice in one digital allocation")
        if (
            not np.issubdtype(self.adc2_code.dtype, np.integer)
            or np.any(self.adc2_code < 0)
            or np.any(self.adc2_code >= 2**spec.adc2_n_bits)
        ):
            raise ValueError("calibration needs legal raw integer ADC2 codes")
        for mask, size in ((self.main_masks, spec.dac_n_main), (self.sub_masks, spec.dac_n_sub)):
            if mask.shape != (n, spec.n_active, size) or not np.all((mask == 0) | (mask == 1)):
                raise ValueError(
                    "actual conversion masks must be binary and match the declared geometry"
                )
        term_shape = (n, spec.n_active, spec.shape[1])
        if self.signal_mask.shape != term_shape or not np.all(
            (self.signal_mask == 0) | (self.signal_mask == 1)
        ):
            raise ValueError(
                "signal_mask must explicitly supply one binary input connection per unit"
            )
        if (
            np.iscomplexobj(self.known_voltage_term_v)
            or self.known_voltage_term_v.shape != term_shape
            or np.any(~np.isfinite(self.known_voltage_term_v))
        ):
            raise ValueError(
                "known_voltage_term_v must explicitly supply finite bound terms [V] per unit"
            )
        if self.overflow.shape != (n,) or not np.all((self.overflow == 0) | (self.overflow == 1)):
            raise ValueError("overflow must supply one digital validity flag per sample")
        if (
            self.sample_ids.shape != (n,)
            or not np.issubdtype(self.sample_ids.dtype, np.integer)
            or np.any(self.sample_ids < 0)
            or np.any(self.sample_ids > 0xFFFF_FFFF)
        ):
            raise ValueError("sample IDs must explicitly supply one uint32-valued ID per sample")
        if n > 1:
            signed_ids: np.ndarray[Any, np.dtype[np.int64]] = self.sample_ids.astype(np.int64)
            steps = np.diff(signed_ids) & 0xFFFF_FFFF
            if np.any(steps == 0) or np.any(steps > 0x7FFF_FFFF):
                raise ValueError("digital observations must preserve sample order within one epoch")

    @property
    def fine_v(self) -> np.ndarray:
        """解码显式选定的均匀 midrise 码中心 [RA 输出节点 V]。"""
        return self.spec.adc2_v_min + (self.adc2_code + 0.5) * self.spec.adc2_step_v

    @property
    def observation_domain(self) -> str:
        """标识实际 mask、a 和 term 容器的数值语义。"""
        return STATIC_OBSERVATION_DOMAIN


def _terms(data: DigitalObservation, start: int, stop: int):
    """返回已记录的 a 与 b-VD，不推断电路绑定。"""
    return (
        data.slice_ids[start:stop],
        data.signal_mask[start:stop],
        data.known_voltage_term_v[start:stop],
    )


class CalibrationUnidentifiableError(ValueError):
    """所给测量不足以辨识请求的有效权重时的异常。"""


@dataclass(frozen=True)
class FrozenCalibration:
    """冻结有效 C/Cf 与一个有效 RA 输出失调 [V]。

    标准误差以显式已知参考和静态线性传递为条件，
    不包括源幅度/时间误差、漂移、ADC2 非线性或未建立的电路动态。完整解码规格和观测域版本随拟合冻结，
    但不认证物理芯片身份；同一实例捕获仍是外部条件。
    """

    weights: np.ndarray
    offset_v: float
    standard_error: np.ndarray
    rank: int
    condition: float
    residual_rms_v: float
    training_samples: int
    training_digest: str
    spec: CalibrationSpec
    observation_domain: str

    def __post_init__(self):
        """检查拟合系数，并冻结其数值存储。"""
        if not isinstance(self.spec, CalibrationSpec):
            raise ValueError("frozen calibration requires its complete explicit CalibrationSpec")
        if self.observation_domain != STATIC_OBSERVATION_DOMAIN:
            raise ValueError("unknown frozen calibration observation domain")
        weights, se = np.asarray(self.weights), np.asarray(self.standard_error)
        if (
            np.iscomplexobj(weights)
            or weights.ndim != 2
            or not weights.size
            or np.any(~np.isfinite(weights))
            or np.any(weights <= 0)
        ):
            raise ValueError("calibrated effective weights must be finite and positive")
        if weights.shape != self.spec.shape:
            raise ValueError("frozen effective weights do not match their complete specification")
        if se.shape != (weights.size + 1,) or np.any(~np.isfinite(se)) or np.any(se < 0):
            raise ValueError("standard errors must match every effective weight and the offset")
        if self.rank != weights.size + 1 or self.training_samples <= self.rank:
            raise ValueError(
                "frozen calibration must identify all coefficients with residual degrees of freedom"
            )
        if (
            not np.all(np.isfinite([self.offset_v, self.condition, self.residual_rms_v]))
            or self.condition < 1
            or self.residual_rms_v < 0
        ):
            raise ValueError("calibration diagnostics and voltage scale must be finite and legal")
        object.__setattr__(self, "weights", _readonly(weights))
        object.__setattr__(self, "standard_error", _readonly(se))

    def reconstruct(self, data: DigitalObservation) -> np.ndarray:
        """按已记录的静态电荷方程求解未知输入 [V]。"""
        data.validate()
        if data.spec != self.spec or data.observation_domain != self.observation_domain:
            raise ValueError(
                "frozen calibration specification or observation domain does not match the digital record"
            )
        out = np.empty(len(data.adc2_code))
        for start in range(0, len(out), 256):
            stop = min(start + 256, len(out))
            ids, signal, term = _terms(data, start, stop)
            weights = self.weights[ids]
            numerator = (
                data.fine_v[start:stop] - self.offset_v - np.sum(weights * term, axis=(1, 2))
            )
            denominator = np.sum(weights * signal, axis=(1, 2))
            if np.any(denominator <= 0):
                raise ValueError("calibrated signal gain is nonpositive")
            out[start:stop] = numerator / denominator
        return out

    def to_dict(self) -> dict:
        """导出有效单位、不可变拟合值与条件性诊断。"""
        result = asdict(self)
        result["weights"] = self.weights.tolist()
        result["standard_error"] = self.standard_error.tolist()
        result["schema_version"] = 2
        result["weight_unit"] = "effective C/Cf"
        return result

    @classmethod
    def from_dict(cls, value: dict) -> FrozenCalibration:
        """恢复显式有效权重格式，不访问物理模型。"""
        values = dict(value)
        if (
            values.pop("schema_version", None) != 2
            or values.pop("weight_unit", None) != "effective C/Cf"
        ):
            raise ValueError("unknown calibrated-weight schema or units")
        if values.get("observation_domain") != STATIC_OBSERVATION_DOMAIN:
            raise ValueError("missing or unsupported frozen observation domain")
        if set(values) != {field.name for field in fields(cls)}:
            raise ValueError("schema2 contains missing or unknown frozen-calibration fields")
        specification = values.get("spec")
        if not isinstance(specification, dict) or set(specification) != {
            field.name for field in fields(CalibrationSpec)
        }:
            raise ValueError("schema2 requires the complete explicit CalibrationSpec")
        values["spec"] = CalibrationSpec(**specification)
        values["weights"] = np.asarray(values["weights"])
        values["standard_error"] = np.asarray(values["standard_error"])
        return cls(**values)


def fit_unit_weights(
    data: DigitalObservation, known_input_v, *, rank_rtol: float = 1e-10
) -> FrozenCalibration:
    """从独立已知参考拟合静态有效权重与失调。

    数值 SVD 控制是实现选择。调用方提供全部连接、电压尺度、激励与静态域声明；缺少可辨识方向时拒绝，
    不以名义先验或物理真值补足。
    """
    data.validate()
    if not data.static_charge_domain:
        raise ValueError("unit-weight training requires a controlled static charge-domain dataset")
    if np.any(data.overflow):
        raise ValueError(f"training has {np.count_nonzero(data.overflow)} clipped conversions")
    if np.iscomplexobj(known_input_v):
        raise ValueError("the independent training reference must be real [V]")
    x = np.asarray(known_input_v, dtype=float)
    n = len(data.adc2_code)
    p = int(np.prod(data.spec.shape)) + 1
    if x.shape != (n,) or np.any(~np.isfinite(x)) or not 0 < rank_rtol < 1:
        raise ValueError(
            "training reference must be finite, match the record and use legal rank tolerance"
        )
    if n <= p:
        raise CalibrationUnidentifiableError(
            f"{n} samples cannot identify {p} coefficients with residual degrees of freedom"
        )
    design = np.zeros((n, p))
    for start in range(0, n, 256):
        stop = min(start + 256, n)
        ids, signal, term = _terms(data, start, stop)
        terms = signal * x[start:stop, None, None] + term
        block = design[start:stop, :-1].reshape(stop - start, *data.spec.shape)
        for active in range(data.spec.n_active):
            block[np.arange(stop - start), ids[:, active], :] = terms[:, active]
    design[:, -1] = 1
    if np.any(~np.isfinite(design)):
        raise ValueError(
            "the explicitly supplied reference and connection terms overflowed the design matrix"
        )
    u, s, vh = svd(design, full_matrices=False, check_finite=False, lapack_driver="gesdd")
    if not (np.all(np.isfinite(u)) and np.all(np.isfinite(s)) and np.all(np.isfinite(vh))):
        raise ValueError("singular value decomposition returned non-finite factors")
    rank = int(np.count_nonzero(s > rank_rtol * s[0]))
    if rank < p:
        raise CalibrationUnidentifiableError(
            f"rank {rank}/{p}: the supplied digital connections and reference do not identify all coefficients"
        )
    theta = vh.T @ ((u.T @ data.fine_v) / s)
    residual = data.fine_v - design @ theta
    variance = float(np.dot(residual, residual) / (n - p))
    se = np.sqrt(variance * np.sum((vh.T / s) ** 2, axis=1))
    weights = theta[:-1].reshape(data.spec.shape)
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
        raise ValueError("estimated effective weights are nonpositive or non-finite")
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {"spec": asdict(data.spec), "observation_domain": data.observation_domain},
            sort_keys=True,
        ).encode()
    )
    digest.update(x.tobytes())
    for array in (
        data.slice_ids,
        data.adc2_code,
        data.main_masks,
        data.sub_masks,
        data.signal_mask,
        data.known_voltage_term_v,
        data.overflow,
        data.sample_ids,
    ):
        digest.update(array.tobytes())
    return FrozenCalibration(
        weights,
        float(theta[-1]),
        se,
        rank,
        float(s[0] / s[-1]),
        float(np.sqrt(np.mean(residual**2))),
        n,
        digest.hexdigest(),
        data.spec,
        data.observation_domain,
    )


def apply_frozen_calibration(result, calibration: FrozenCalibration):
    """先执行数字推理，再更新单列的仿真评分。"""
    out = calibration.reconstruct(DigitalObservation.from_result(result))
    if result.uncalibrated_out is None:
        result.uncalibrated_out = result.out.copy()
    result.out = out
    result.err = out - result.x_ref
    result.state.weight_calibration = calibration
    result.calibration_applied = tuple(dict.fromkeys((*result.calibration_applied, "unit_weights")))
    return result
