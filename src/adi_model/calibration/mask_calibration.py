"""DAC 权值/传输函数校准：两条路径，一条被证伪、一条可用。

**逐单元权值不可辨识（已被本仓库的数值反例否掉）**：本流水线记录的 `fine_v` 是 RA
放大后的残差，表达式里 `G*C_sig*x` 与 `G*Q_DAC` 各约 96 V、相减后只剩 0.1 V。
在 1e-3 量级的抵消下，90 uV 噪声会把逐单元权值误差放大到 V 量级（实测残差 14 V、
权重出现 -43 V、理想器件也解出负值）。因此 `fit_mask_weights` 保留为**负结果证据**，
不作为可用校准路径。

**逐码修正可用**：每个粗码只要有足够样本，就能估出该码的平均输出误差；
用 `fit_code_correction` 建 512 项表，等价于论文所说的 "DAC weight correction"
在输出域的实现。

原始设计说明（保留）：

论文（ISSCC 2024 §9.8 p.3）："The DAC weight correction is done on-chip with coefficients
derived externally, suitable due to very good DAC stability."
本模块实现"externally derived"这一步：激励是已知输入，观测量是数字侧已记录的
逐单元开关 mask 与 ADC2 码。

模型（每个样本一条方程，单位 V，坐标是 ADC2 输出/RA 输出节点）：

    fine_v = a * x + sum_i b_i * m_i + offset

其中 `m_i` 是该单元本笔的开关位（1=接高参考，0=接低参考），`b_i` 是"该单元从低轨切到
高轨时在 `fine_v` 上产生的电压增量"，`a` 是输入到 `fine_v` 的等效增益。这个形式是
**线性**的，且每个 `m_i` 随码变化，所以最小二乘可以逐单元辨识；不需要把权重先验设为正值，
也不依赖任何"名义权重"。

为什么不用 `weight_calibration.fit_unit_weights`：那个拟合的观测量是
`signal_mask * x + known_voltage_term_v`（采样侧 a/b 口径），本流水线的 `signal_mask`
恒为全 1、`term` 是全轨电压，两者相减会得到病态的法方程；实测即使零失配零噪声也会
解出负权重。本模块改用 **转换 mask** 直接建模，理想器件上残差应降到数值零。

边界：只校正静态电荷域（`static_charge_domain=True` 且无溢出）；不校正参考 droop、
RA 非线性、ADC2 增益误差与温漂。系数是"外部推导"的数值，不代表片上自适应。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model._arrays import readonly
from adi_model.simulation.config import ConfigError
from adi_model.calibration.weight_calibration import DigitalObservation


def _mask_matrix(data: DigitalObservation) -> np.ndarray:
    """把 (N,A,n_main) 与 (N,A,n_sub) 的 mask 展开成按物理片的 (N, n_slices*(n_main+n_sub))。

    Args:
        data: 已校验的数字观测；同一笔内一片最多出现一次（上游已保证）。

    Returns:
        float64 (N,K) 0/1 矩阵，列顺序为 slice-major、单元-minor。

    Raises:
        ConfigError: 形状不一致或同一笔重复使用同一物理片。
    """
    main = np.asarray(data.main_masks, dtype=bool)
    sub = np.asarray(data.sub_masks, dtype=bool)
    if main.ndim != 3 or sub.ndim != 3:
        raise ConfigError("mask calibration needs (N,A,n_main) and (N,A,n_sub) masks")
    n, n_active, n_main = main.shape
    if sub.shape[:2] != (n, n_active):
        raise ConfigError("main and sub masks must share sample/active dimensions")
    n_sub = sub.shape[2]
    n_slices = int(data.slice_ids.max()) + 1 if data.slice_ids.size else 0
    if n_slices <= 0:
        raise ConfigError("mask calibration needs at least one physical slice")
    matrix = np.zeros((n, n_slices * (n_main + n_sub)), dtype=float)
    for active in range(n_active):
        ids = np.asarray(data.slice_ids[:, active], dtype=np.int64)
        if np.any(ids < 0) or np.any(ids >= n_slices):
            raise ConfigError("physical slice IDs exceed the observed pool")
        base = ids * (n_main + n_sub)
        rows = np.arange(n)
        matrix[rows[:, None], base[:, None] + np.arange(n_main)[None, :]] = main[:, active, :]
        matrix[rows[:, None], base[:, None] + n_main + np.arange(n_sub)[None, :]] = sub[
            :, active, :
        ]
    return matrix


@dataclass(frozen=True)
class MaskWeightCalibration:
    """冻结的 DAC 权值 + 输入增益 + 失调。

    Attributes:
        gain_v_per_v: `a`，输入到 `fine_v` 的等效增益（无量纲）。
        weights_v: (K,) 每个单元 mask 位的电压增量 [V]；K = n_slices*(n_main+n_sub)。
        offset_v: 公共失调 [V]。
        residual_rms_v: 训练残差 RMS [V]；理想器件应接近 0。
        n_samples: 训练样本数。
        rank: 设计矩阵秩；必须等于参数个数。
        condition: 奇异值比。
        n_main/n_sub/n_slices: 列布局，重建时必须一致。
    """

    gain_v_per_v: float
    weights_v: np.ndarray
    offset_v: float
    residual_rms_v: float
    n_samples: int
    rank: int
    condition: float
    n_main: int
    n_sub: int
    n_slices: int
    dropped_columns: int = 0

    def __post_init__(self):
        """冻结数组并检查数值合法性（不要求权重为正——符号由 mask 定义决定）。"""
        weights = np.asarray(self.weights_v, dtype=float)
        if weights.ndim != 1 or weights.size != self.n_slices * (self.n_main + self.n_sub):
            raise ConfigError("calibrated weights do not match the declared column layout")
        if np.any(~np.isfinite(weights)) or not np.isfinite(self.gain_v_per_v):
            raise ConfigError("calibrated weights must be finite")
        if self.gain_v_per_v == 0:
            raise ConfigError("calibrated input gain must be nonzero")
        for name in ("residual_rms_v", "condition"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ConfigError(f"calibration {name} must be finite nonnegative")
        object.__setattr__(self, "weights_v", readonly(weights, np.float64))

    @property
    def mask_gain_v(self) -> float:
        """一个单元接高轨相对接低轨的电压增量典型值 [V]（用于量级自检）。"""
        return float(np.median(np.abs(self.weights_v)))

    def reconstruct(self, data: DigitalObservation) -> np.ndarray:
        """按冻结系数反解输入 [V]。

        Args:
            data: 与训练同布局的数字观测。

        Returns:
            (N,) 输入电压估计。

        Raises:
            ConfigError: 布局不一致或增益非法。
        """
        matrix = _mask_matrix(data)
        if matrix.shape[1] != self.weights_v.size:
            raise ConfigError("observation mask layout differs from the frozen calibration")
        fine = np.asarray(data.fine_v, dtype=float)
        return (fine - matrix @ self.weights_v - self.offset_v) / self.gain_v_per_v

    def to_dict(self) -> dict:
        """导出可序列化系数与诊断，不写入任何物理真值。"""
        return {
            "schema": "mask_weights_v1",
            "gain_v_per_v": self.gain_v_per_v,
            "weights_v": self.weights_v.tolist(),
            "offset_v": self.offset_v,
            "residual_rms_v": self.residual_rms_v,
            "n_samples": self.n_samples,
            "rank": self.rank,
            "condition": self.condition,
            "n_main": self.n_main,
            "n_sub": self.n_sub,
            "n_slices": self.n_slices,
            "dropped_columns": self.dropped_columns,
        }


def fit_mask_weights(
    data: DigitalObservation,
    known_input_v,
    *,
    rank_rtol: float = 1e-10,
) -> MaskWeightCalibration:
    """用已知输入拟合逐单元权值。

    Args:
        data: 静态电荷域的观测；不得有溢出。
        known_input_v: (N,) 独立已知输入 [V]，必须与观测同序。
        rank_rtol: 秩判定阈值（相对最大奇异值）。

    Returns:
        MaskWeightCalibration。

    Raises:
        ConfigError: 观测不在静态域、含溢出、长度不符或设计矩阵秩不足。
    """
    data.validate()
    if not data.static_charge_domain:
        raise ConfigError("mask-weight training requires a controlled static charge domain")
    if np.any(data.overflow):
        raise ConfigError(
            f"training contains {int(np.count_nonzero(data.overflow))} clipped conversions"
        )
    x = np.asarray(known_input_v, dtype=float)
    if x.shape != (len(data.adc2_code),):
        raise ConfigError("known input must supply one value per sample")
    if np.any(~np.isfinite(x)):
        raise ConfigError("known input must be finite")
    if not 0 < rank_rtol < 1:
        raise ConfigError("rank tolerance must lie in (0,1)")

    matrix = _mask_matrix(data)
    n, k = matrix.shape
    # 恒 0 的列（子阵列末位恒接低轨）与恒 1 的列（与失调共线）都不可辨识，
    # 直接排除并令其权重为 0，而不是让最小二乘在秩亏的法方程里乱解。
    informative = np.ptp(matrix, axis=0) > 0
    dropped = int(np.count_nonzero(~informative))
    columns = matrix[:, informative]
    n_columns = columns.shape[1]
    design = np.empty((n, n_columns + 2), dtype=float)
    design[:, 0] = x
    design[:, 1 : n_columns + 1] = columns
    design[:, -1] = 1.0
    if np.any(~np.isfinite(design)):
        raise ConfigError("design matrix contains non-finite entries")
    if n <= n_columns + 2:
        raise ConfigError(
            f"{n} samples cannot identify {n_columns + 2} coefficients with residual degrees of freedom"
        )
    u, s, vh = np.linalg.svd(design, full_matrices=False)
    rank = int(np.count_nonzero(s > rank_rtol * s[0]))
    if rank < n_columns + 2:
        raise ConfigError(
            f"rank {rank}/{n_columns + 2}: masks and input do not identify every weight"
        )
    theta = vh.T @ ((u.T @ np.asarray(data.fine_v, dtype=float)) / s)
    residual = np.asarray(data.fine_v, dtype=float) - design @ theta
    n_slices = int(data.slice_ids.max()) + 1
    weights = np.zeros(k, dtype=float)
    weights[informative] = theta[1 : n_columns + 1]
    return MaskWeightCalibration(
        gain_v_per_v=float(theta[0]),
        weights_v=weights,
        dropped_columns=dropped,
        offset_v=float(theta[-1]),
        residual_rms_v=float(np.sqrt(np.mean(residual**2))),
        n_samples=n,
        rank=rank,
        condition=float(s[0] / s[-1]),
        n_main=data.main_masks.shape[2],
        n_sub=data.sub_masks.shape[2],
        n_slices=n_slices,
    )


def apply_mask_calibration(result, calibration: MaskWeightCalibration) -> dict:
    """把校准结果写到 SimResult（`out`/`err`），保留未校准值供对照。

    Args:
        result: 已完成的 SimResult（含 digital_observation）。
        calibration: 冻结系数。

    Returns:
        dict：校准前后的 RMS 误差 [V]，用于当场判断是否真的改善。

    Raises:
        ConfigError: 布局不匹配。
    """
    data = getattr(result, "digital_observation", None)
    if not isinstance(data, DigitalObservation):
        raise ConfigError("mask calibration requires a bound DigitalObservation")
    if data.main_masks.shape[2] != calibration.n_main or data.sub_masks.shape[2] != calibration.n_sub:
        raise ConfigError("observation and calibration disagree on the DAC layout")
    calibrated = calibration.reconstruct(data)
    before = np.asarray(result.out, dtype=float)
    result.uncalibrated_out = before.copy() if result.uncalibrated_out is None else result.uncalibrated_out
    result.out = calibrated
    result.err = calibrated - np.asarray(result.x_ref, dtype=float)
    result.calibration_applied = tuple(dict.fromkeys((*result.calibration_applied, "mask_weights")))
    reference = np.asarray(result.x_ref, dtype=float)
    return {
        "rms_before_v": float(np.sqrt(np.mean((before - reference) ** 2))),
        "rms_after_v": float(np.sqrt(np.mean((calibrated - reference) ** 2))),
    }


__all__ = [
    "CodeCorrection",
    "ElementWeightCalibration",
    "MaskWeightCalibration",
    "apply_mask_calibration",
    "fit_code_correction",
    "fit_element_weights",
    "fit_mask_weights",
]


@dataclass(frozen=True)
class CodeCorrection:
    """逐粗码的输出修正表（等价于 DAC 传输函数线性化）。

    为什么不是逐单元权值：本流水线记录的 `fine_v` 是 RA 放大后的**残差**，
    而残差的表达式里 `G*C_sig*x` 与 `G*Q_DAC` 各约 96 V、相减后只剩 0.1 V 量级。
    在这种 1e-3 量级的抵消下，90 uV 的噪声会把逐单元权值的估计误差放大到 V 量级，
    实测残差 14 V、权重出现 -43 V。所以逐单元辨识在本观测下**不可行**，
    而逐码修正是可辨识的：每个粗码只要被访问过，就有该码的均值误差可估。

    边界：只修正与粗码相关的分量；同一码内的斜率误差、温漂与参考 droop 不在表内。
    """

    table_v: np.ndarray
    counts: np.ndarray
    covered_codes: int
    min_count: int
    residual_rms_v: float
    n_samples: int

    def __post_init__(self):
        """校验表长一致、计数非负、未覆盖码保持 0。"""
        table = np.asarray(self.table_v, dtype=float)
        counts = np.asarray(self.counts, dtype=np.int64)
        if table.shape != counts.shape or table.ndim != 1:
            raise ConfigError("code correction table and counts must be 1-D and equal length")
        if np.any(counts < 0):
            raise ConfigError("code correction counts must be nonnegative")
        if np.any(~np.isfinite(table)):
            raise ConfigError("code correction table must be finite")
        if np.any((table != 0) & (counts == 0)):
            raise ConfigError("unvisited codes must keep a zero correction")
        object.__setattr__(self, "table_v", readonly(table, np.float64))
        object.__setattr__(self, "counts", readonly(counts, np.int64))

    def apply(self, codes, estimated_v) -> np.ndarray:
        """按码减去修正量；未覆盖码原样返回（不插值、不外推）。"""
        index = np.asarray(codes, dtype=np.int64)
        values = np.asarray(estimated_v, dtype=float)
        if index.shape != values.shape:
            raise ConfigError("codes and estimates must share shape")
        if np.any((index < 0) | (index >= self.table_v.size)):
            raise ConfigError("codes exceed the correction table")
        return values - self.table_v[index]

    def to_dict(self) -> dict:
        """导出表与覆盖统计。"""
        return {
            "schema": "code_correction_v1",
            "table_v": self.table_v.tolist(),
            "counts": self.counts.tolist(),
            "covered_codes": self.covered_codes,
            "min_count": self.min_count,
            "residual_rms_v": self.residual_rms_v,
            "n_samples": self.n_samples,
        }


def fit_code_correction(codes, estimated_v, known_input_v, *, n_codes: int = 512) -> CodeCorrection:
    """用已知输入拟合逐码修正量。

    Args:
        codes: (N,) 实际 9 位粗码。
        estimated_v: (N,) 名义解码输出 [V]。
        known_input_v: (N,) 独立已知输入 [V]。
        n_codes: 码表长度，默认 512。

    Returns:
        CodeCorrection；每个被访问过的码给出该码的平均误差。

    Raises:
        ConfigError: 长度不符、码越界或某码未被访问（不做静默补 0）。
    """
    index = np.asarray(codes, dtype=np.int64)
    estimated = np.asarray(estimated_v, dtype=float)
    known = np.asarray(known_input_v, dtype=float)
    if index.shape != estimated.shape or index.shape != known.shape:
        raise ConfigError("codes, estimates and known inputs must share shape")
    if np.any((index < 0) | (index >= n_codes)):
        raise ConfigError("codes exceed the declared table length")
    if np.any(~np.isfinite(estimated)) or np.any(~np.isfinite(known)):
        raise ConfigError("estimates and known inputs must be finite")
    error = estimated - known
    counts = np.bincount(index, minlength=n_codes).astype(np.int64)
    total = np.bincount(index, weights=error, minlength=n_codes)
    missing = np.flatnonzero(counts == 0)
    if missing.size:
        raise ConfigError(
            f"{missing.size} codes were never visited during training; "
            "no interpolation or zero-fill is allowed for a calibration table"
        )
    table = total / counts
    residual = error - table[index]
    return CodeCorrection(
        table_v=table,
        counts=counts,
        covered_codes=int(np.count_nonzero(counts)),
        min_count=int(counts.min()),
        residual_rms_v=float(np.sqrt(np.mean(residual**2))),
        n_samples=int(index.size),
    )


@dataclass(frozen=True)
class ElementWeightCalibration:
    """逐单元有效权重（C/Cf），**残差域参数化**，不是 `a*x + sum(b*m)`。

    外部审查给出的正确形式（`review_c817e91/reproduce_identifiability.py`）：

        F[n,p,j] = x[n] - V_bottom[n,p,j] = x[n] - V_low - (V_high-V_low)*m[n,p,j]
        fine_v[n] = sum_{p,j} w[p,j] * F[n,p,j] + offset

    每一列都是一个 (片, 单元) 的**电容电压**，`w = C/Cf` 同时作用在 `x` 与开关项上，
    因此不存在"自由增益 a 与 sum(b) 共线"的退化——这正是早期
    `fit_mask_weights` 解出负权重的原因（那版把同一份物理拆成两族参数）。

    重建：`x_hat = (fine_v - offset - sum w*term) / sum w`，`term = V_low + ΔV*m` 取负号后相加。
    """

    weights: np.ndarray  # (n_slices, n_main+n_sub)，单位 C/Cf
    offset_v: float
    residual_rms_v: float
    n_samples: int
    rank: int
    condition: float
    reference_high_v: float
    reference_low_v: float
    n_main: int
    n_sub: int

    def __post_init__(self):
        """冻结权重并要求全部为正（有效电容比不能为负）。"""
        weights = np.asarray(self.weights, dtype=float)
        if weights.ndim != 2 or weights.shape[1] != self.n_main + self.n_sub:
            raise ConfigError("element weights must be (n_slices, n_main+n_sub)")
        if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
            raise ConfigError("effective element weights must be finite and positive")
        if not np.isfinite(self.offset_v) or not np.isfinite(self.residual_rms_v):
            raise ConfigError("element calibration offset and residual must be finite")
        object.__setattr__(self, "weights", readonly(weights, np.float64))

    @property
    def n_slices(self) -> int:
        """物理片数。"""
        return int(self.weights.shape[0])

    def reconstruct(self, data: DigitalObservation, known_input_v) -> np.ndarray:
        """按 `x_hat = (fine - off - sum w*(V_low+ΔV*m)) / sum w` 反解输入。

        Args:
            data: 与训练同布局的观测。
            known_input_v: 不参与重建，仅为接口对称保留；传 None 亦可。

        Returns:
            (N,) 输入估计 [V]。
        """
        del known_input_v
        masks = _mask_matrix(data).reshape(len(data.adc2_code), self.n_slices, -1)
        delta = self.reference_high_v - self.reference_low_v
        term = self.reference_low_v + delta * masks
        w = self.weights
        numerator = np.asarray(data.fine_v, dtype=float) - self.offset_v - np.sum(
            w[None, :, :] * term, axis=(1, 2)
        )
        denominator = np.sum(w[None, :, :] * np.ones_like(term), axis=(1, 2))
        if np.any(denominator <= 0):
            raise ConfigError("calibrated signal gain is nonpositive")
        return numerator / denominator

    def to_dict(self) -> dict:
        """导出系数与诊断。"""
        return {
            "schema": "element_weights_v1",
            "weights_c_per_cf": self.weights.tolist(),
            "offset_v": self.offset_v,
            "residual_rms_v": self.residual_rms_v,
            "n_samples": self.n_samples,
            "rank": self.rank,
            "condition": self.condition,
            "reference_high_v": self.reference_high_v,
            "reference_low_v": self.reference_low_v,
            "n_main": self.n_main,
            "n_sub": self.n_sub,
        }


def fit_element_weights(
    data: DigitalObservation,
    known_input_v,
    *,
    reference_high_v: float,
    reference_low_v: float,
    rank_rtol: float = 1e-10,
) -> ElementWeightCalibration:
    """残差域逐单元权值拟合（审查给出的可辨识形式）。

    Args:
        data: 静态电荷域观测；不得有溢出。
        known_input_v: (N,) 已知输入 [V]（训练激励必须是已知量）。
        reference_high_v/reference_low_v: 实际轨电压 [V]。
        rank_rtol: 秩判定阈值。

    Returns:
        ElementWeightCalibration。

    Raises:
        ConfigError: 含溢出、维度不符或秩不足。
    """
    data.validate()
    if not data.static_charge_domain:
        raise ConfigError("element-weight training requires a controlled static charge domain")
    if np.any(data.overflow):
        raise ConfigError(
            f"training contains {int(np.count_nonzero(data.overflow))} clipped conversions"
        )
    x = np.asarray(known_input_v, dtype=float)
    masks = _mask_matrix(data)
    n, k = masks.shape
    if x.shape != (n,):
        raise ConfigError("known input must supply one value per sample")
    if np.any(~np.isfinite(x)):
        raise ConfigError("known input must be finite")
    delta = float(reference_high_v) - float(reference_low_v)
    if not np.isfinite(delta) or delta <= 0:
        raise ConfigError("reference rails must be finite with V_high > V_low")
    # 电容两端电压：F = x - (V_low + ΔV*m)
    voltages = x[:, None] - (float(reference_low_v) + delta * masks)
    # 从不切换的单元（本拓扑里是每片子阵列的第 8 位）彼此 F 完全相同，逐片不可分辨；
    # 合并成一列拟合一个公共权重，再摊回每个单元。丢列会让重建的分母少算这部分电容。
    fixed = np.ptp(masks, axis=0) == 0
    if np.any(fixed):
        merged = voltages[:, fixed].sum(axis=1, keepdims=True)
        design = np.column_stack([voltages[:, ~fixed], merged, np.ones(n)])
    else:
        design = np.column_stack([voltages, np.ones(n)])
    n_columns = design.shape[1] - 1
    if np.any(~np.isfinite(design)):
        raise ConfigError("design matrix contains non-finite entries")
    if n <= k + 1:
        raise ConfigError(
            f"{n} samples cannot identify {k + 1} coefficients with residual degrees of freedom"
        )
    theta, _, rank, singular = np.linalg.lstsq(design, np.asarray(data.fine_v, dtype=float), rcond=None)
    if int(rank) < n_columns + 1:
        raise ConfigError(
            f"rank {int(rank)}/{n_columns + 1}: masks and input do not identify every weight"
        )
    residual = np.asarray(data.fine_v, dtype=float) - design @ theta
    n_slices = int(data.slice_ids.max()) + 1
    flat = np.empty(k, dtype=float)
    flat[~fixed] = theta[: n_columns - 1] if np.any(fixed) else theta[:n_columns]
    if np.any(fixed):
        flat[fixed] = theta[n_columns - 1]
    weights = flat.reshape(n_slices, data.main_masks.shape[2] + data.sub_masks.shape[2])
    return ElementWeightCalibration(
        weights=weights,
        offset_v=float(theta[-1]),
        residual_rms_v=float(np.sqrt(np.mean(residual**2))),
        n_samples=n,
        rank=int(rank),
        condition=float(singular[0] / singular[-1]),
        reference_high_v=float(reference_high_v),
        reference_low_v=float(reference_low_v),
        n_main=data.main_masks.shape[2],
        n_sub=data.sub_masks.shape[2],
    )
