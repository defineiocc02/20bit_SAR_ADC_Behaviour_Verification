"""线性测量的通用可观测性与样本预算计算。

对 y = U @ theta + noise，秩与奇异值只描述给定测量矩阵；它们不证明电容拓扑，不恢复绝对电容，
也不确立公开芯片的校准算法。生产静态电荷估计器与冻结推理见 weight_calibration。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _matrix(value) -> np.ndarray:
    """检查测量矩阵为有限实数二维数组。"""
    if np.iscomplexobj(value):
        raise ValueError("observability requires a real measurement matrix")
    matrix = np.asarray(value, dtype=float)
    if matrix.ndim != 2 or np.any(~np.isfinite(matrix)):
        raise ValueError("measurement matrix must be finite and two-dimensional")
    return matrix


@dataclass(frozen=True)
class Observability:
    """给定矩阵的秩、奇异值参与度与条件数。"""

    n_parameters: int
    rank: int
    eff_rank: float
    cond: float
    svals: np.ndarray = field(repr=False)

    @property
    def identifiable_fraction(self) -> float:
        """返回独立可辨识参数方向占全部方向的比例。"""
        return self.rank / self.n_parameters if self.n_parameters else 0.0

    def as_dict(self) -> dict:
        """导出无量纲矩阵诊断，不据此作物理结论。"""
        return {
            "n_parameters": self.n_parameters,
            "rank": self.rank,
            "eff_rank": self.eff_rank,
            "cond": self.cond,
            "identifiable_fraction": self.identifiable_fraction,
            "n_null_directions": self.n_parameters - self.rank,
        }


def observability(U: np.ndarray, tol: float | None = None) -> Observability:
    """按显式绝对奇异值阈值分析给定矩阵。

    tol=None 时使用 max(U.shape) * eps * largest_singular_value。秩亏矩阵的全参数条件数为无穷。
    结果针对所给激励与参数化，不代表所有可能工作序列。
    """
    matrix = _matrix(U)
    if tol is not None and (not np.isfinite(tol) or tol < 0):
        raise ValueError("singular-value tolerance must be finite and nonnegative")
    singular = np.linalg.svd(matrix, compute_uv=False)
    if np.any(~np.isfinite(singular)):
        raise ValueError("singular value decomposition returned non-finite values")
    largest = float(singular[0]) if singular.size else 0.0
    threshold = max(matrix.shape) * np.finfo(float).eps * largest if tol is None else tol
    rank = int(np.count_nonzero(singular > threshold))
    # 先缩放再平方，避免有限矩阵计算参与度时溢出。
    normalized = singular / largest if largest > 0 else singular
    energy: np.ndarray = normalized**2
    effective = float(energy.sum() ** 2 / np.dot(energy, energy)) if largest > 0 else 0.0
    n_parameters = matrix.shape[1]
    condition = (
        float(largest / singular[-1]) if n_parameters > 0 and rank == n_parameters else float("inf")
    )
    return Observability(n_parameters, rank, effective, condition, singular)


def group_aggregate(U: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """按显式非负组 ID 合并测量矩阵的列。

    分组背后的等价假设由调用方负责。合并列会改变参数化，不证明原始物理参数已经能够分别辨识。
    """
    matrix = _matrix(U)
    labels = np.asarray(groups)
    if (
        labels.shape != (matrix.shape[1],)
        or not np.issubdtype(labels.dtype, np.integer)
        or np.any(labels < 0)
    ):
        raise ValueError("group IDs must be nonnegative integers, one per matrix column")
    unique = np.unique(labels)
    return (
        np.stack([matrix[:, labels == label].sum(axis=1) for label in unique], axis=1)
        if unique.size
        else np.empty((matrix.shape[0], 0))
    )


def required_samples(
    U: np.ndarray,
    noise_std: float,
    target_parameter_std: float,
    *,
    oversample: float,
    sample_rate_hz: float,
) -> dict:
    """估计重复独立线性测量所需的样本预算。

    单位须满足 y = U @ theta + noise：noise_std 使用 y 的单位，
    target_parameter_std 使用 theta 的单位；全部参数列须共享声明的目标尺度。
    噪声估计假设误差独立、等方差，并重复同一设计矩阵。oversample 是显式选择的冗余裕量，
    不是物理下限；时长假设每个样本均以 sample_rate_hz [Hz] 观测。
    """
    matrix = _matrix(U)
    if not np.isfinite(noise_std) or noise_std < 0:
        raise ValueError("measurement noise standard deviation must be finite and nonnegative")
    if not np.isfinite(target_parameter_std) or target_parameter_std <= 0:
        raise ValueError("target parameter standard deviation must be finite and positive")
    if not np.isfinite(oversample) or oversample < 1:
        raise ValueError("explicit observation redundancy must be finite and at least one")
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("observation sample rate must be finite and positive [Hz]")
    info = observability(matrix)
    n_rows, n_parameters = matrix.shape
    common = {"rank": info.rank, "n_parameters": n_parameters, "n_rows_given": n_rows}
    if n_parameters == 0 or n_rows == 0 or info.rank != n_parameters:
        return {
            **common,
            "feasible": False,
            "recommended_samples": float("inf"),
            "duration_s": float("inf"),
            "reason": "the supplied matrix does not identify all requested parameter directions",
        }
    # 同一矩阵重复 k 次时，最小二乘协方差缩小为原来的 1/k。
    relative_std = noise_std / info.svals / target_parameter_std
    noise_samples = float(n_rows * np.mean(relative_std**2))
    redundancy_samples = float(oversample * n_parameters)
    recommended = max(noise_samples, redundancy_samples)
    return {
        **common,
        "feasible": True,
        "noise_bound_samples": noise_samples,
        "redundancy_samples": redundancy_samples,
        "oversample_assumed": oversample,
        "recommended_samples": recommended,
        "duration_s": recommended / sample_rate_hz,
        "reason": "conditional linear-noise estimate and explicitly selected redundancy margin",
    }
