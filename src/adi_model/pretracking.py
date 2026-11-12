"""从已可见数字判决生成预跟踪目标，不接受模拟输入或电容真值。

codebook_v由调用方明确给出，单位为判决输入节点V。减去同笔已知注入并除signal_scale后，
目标才处于外部输入等效V。控制器不能用plant的物理阈值、真实输入或误差反推码表。
[12]允许使用另一ADC的partial/MSB判决。

本模块只核验ready时刻和来源组：它不生成ADC完成时间、DAC电容开关或目标空闲窗口。
无合法来源时valid=False，不能把零电压冒充一次真实预置。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from numbers import Real
from typing import cast

import numpy as np

from adi_model._arrays import readonly


def _finite_scalar(value) -> bool:
    # cast 补全 NumPy 的标量存根类型，ufunc 仍接收原对象。
    return (
        isinstance(value, Real)
        and not isinstance(value, bool | np.bool_)
        and bool(np.isfinite(cast(float, value)))
    )


def _physical_ids(values, n_slices: int) -> tuple[int, ...]:
    ids = np.asarray(values)
    if (
        ids.ndim != 1
        or ids.size == 0
        or not np.issubdtype(ids.dtype, np.integer)
        or np.any(ids < 0)
        or np.any(ids >= n_slices)
        or len(np.unique(ids)) != len(ids)
    ):
        raise ValueError("pretracking.slice_ids needs unique in-range physical integer IDs")
    return tuple(map(int, ids))


@dataclass(frozen=True)
class DecisionRecord:
    """真实数字判决及来源；ready_s为可用时刻[s]，estimate_v为名义目标[V]。

    sample_id为调用方单调记录ID，不隐式处理硬件计数器回绕；slices为物理ID。
    """

    sample_id: int
    ready_s: float
    slices: tuple[int, ...]
    coarse_code: int
    estimate_v: float


@dataclass(frozen=True)
class PretrackPrediction:
    """按目标物理ID排序的只读候选：电压/valid为(A,)，来源为(A,H)。

    无合法来源时电压/时间为NaN、ID=-1、valid=False；调用方先检查valid再推进实际状态。
    H是所选数字历史深度，不是ADC物理数量。
    """

    voltage_v: np.ndarray
    source_ids: np.ndarray
    ready_times_s: np.ndarray
    valid: np.ndarray


@dataclass(frozen=True)
class InputAssistTrace:
    """来源、目标及分源电荷观测；电压V、时间s、电荷C。

    target/valid为(N,A)，来源为(N,A,H)。预充电源、AUX源和clock reset电荷分列，
    不能全部并入主输入信号源电荷；观测不作为数字估计器的真值输入。
    """

    pretrack_start_s: np.ndarray
    pretrack_target_v: np.ndarray
    pretrack_valid: np.ndarray
    pretrack_source_ids: np.ndarray
    pretrack_available_s: np.ndarray
    pretrack_source_charge_c: np.ndarray
    auxiliary_voltage_v: np.ndarray
    auxiliary_branch_charge_c: np.ndarray
    auxiliary_source_charge_c: np.ndarray
    auxiliary_reset_charge_c: np.ndarray

    @classmethod
    def allocate(cls, n: int, active: int, *, history: int) -> InputAssistTrace:
        """分配观测，不生成预测；无充电事件的电荷为零。

        Args:
            n: 正整数样本数N。
            active: 正整数物理组大小A。
            history: 明确选择的正整数数字历史深度H。

        Returns:
            未发生的电压/来源保持NaN/-1的独立数组。
        """
        if any(type(v) is not int or v < 1 for v in (n, active, history)):
            raise ValueError("input-assist trace dimensions must be positive integers")
        return cls(
            np.full(n, np.nan),
            np.full((n, active), np.nan),
            np.zeros((n, active), dtype=bool),
            np.full((n, active, history), -1, dtype=np.int64),
            np.full((n, active, history), np.nan),
            np.zeros(n),
            np.full(n, np.nan),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
        )


class QuantizedPretracker:
    """发布已完成判决，拒绝同组历史冒充另一ADC。

    latest只检查最近一笔已完成记录；同组则无更新，不悄悄回退自身历史。
    weighted要求明确完整历史窗口均来自不重叠组；暖机不足不更新。目标组是否处于合法预置空闲相，
    仍由上层物理调度核验。
    """

    def __init__(self, n_slices: int, *, codebook_v, signal_scale: float, weights=None):
        """绑定数字可见码表，不生成物理网格或默认加权系数。

        Args:
            n_slices: 正整数物理池大小。
            codebook_v: (K,)有限名义数字码表[判决输入节点V]。
            signal_scale: 显式名义信号传递比例，无量纲且正。
            weights: 可选(H,)非负数字权重，H>=2；给出后按总和归一化。None不选择加权，历史深度为一。

        Raises:
            ValueError: 非法维度、非有限码表/尺度或无效权重。
        """
        if np.iscomplexobj(codebook_v):
            raise ValueError("pretracking.codebook_v must be real [V]")
        table = np.asarray(codebook_v, dtype=float)
        if (
            type(n_slices) is not int
            or n_slices < 1
            or table.ndim != 1
            or len(table) < 2
            or np.any(~np.isfinite(table))
            or not _finite_scalar(signal_scale)
            or signal_scale <= 0
        ):
            raise ValueError(
                "pretracking needs physical IDs and an explicit finite digital codebook [V]"
            )
        self.n_slices = n_slices
        self.codebook_v = readonly(table)
        self.signal_scale = float(signal_scale)
        self._weights = None
        if weights is not None:
            w = np.asarray(weights, dtype=float)
            if (
                w.ndim != 1
                or len(w) < 2
                or np.any(~np.isfinite(w))
                or np.any(w < 0)
                or w[0] <= 0
                or not np.isfinite(w.sum())
                or w.sum() <= 0
            ):
                raise ValueError(
                    "pretracking.weights needs at least two finite nonnegative weights"
                )
            self._weights = readonly(w / w.sum())
        self.history_depth = 1 if self._weights is None else len(self._weights)
        self._pending: deque[DecisionRecord] = deque()
        self._recent: deque[DecisionRecord] = deque(maxlen=self.history_depth)
        self._last_id = -1
        self._last_ready = -np.inf
        self._last_query = -np.inf

    def record(self, sample_id, ready_s, slice_ids, coarse_code, *, known_injection_v):
        """登记同笔整数判决、可用时刻与数字已知注入。

        Args:
            sample_id: 非负且严格递增的来源记录ID。
            ready_s: 有限且按登记顺序非递减的可用时刻[s]。
            slice_ids: 来源样本的实际物理ID。
            coarse_code: 整数判决，范围[0,len(codebook_v))。
            known_injection_v: 同笔已知注入[判决输入节点V]；关闭时显式给零，不能从真实输入误差反推。

        Side effects:
            仅加入pending；predict达到ready_s后才发布。

        Raises:
            ValueError: ID/时间失序、非法整数码/片集合或非有限注入。
        """
        ids = _physical_ids(slice_ids, self.n_slices)
        if (
            not isinstance(sample_id, int | np.integer)
            or isinstance(sample_id, bool | np.bool_)
            or sample_id <= self._last_id
            or not _finite_scalar(ready_s)
            or ready_s < self._last_ready
            or not isinstance(coarse_code, int | np.integer)
            or isinstance(coarse_code, bool | np.bool_)
            or not 0 <= coarse_code < len(self.codebook_v)
            or not _finite_scalar(known_injection_v)
        ):
            raise ValueError(
                "pretracking requires ordered decision IDs/times and in-range integer codes"
            )
        estimate = (self.codebook_v[int(coarse_code)] - known_injection_v) / self.signal_scale
        self._pending.append(
            DecisionRecord(int(sample_id), float(ready_s), ids, int(coarse_code), float(estimate))
        )
        self._last_id, self._last_ready = int(sample_id), float(ready_s)

    def predict(self, slice_ids, at_s: float, mode: str) -> PretrackPrediction:
        """只在完整且不同组的已完成来源齐备时返回有效目标。

        Args:
            slice_ids: 目标物理组；与来源任一片重叠即不满足另一ADC条件。
            at_s: 有限、单调非递减查询时刻[s]；同刻ready可见。
            mode: off、latest或weighted；weighted必须先提供权重。

        Returns:
            只读候选；无合法更新时valid全False。

        Side effects:
            输入检查完成后，发布ready_s<=at_s的pending记录。

        Raises:
            ValueError: 非法模式、片组、时间或缺失加权合同。
        """
        ids = _physical_ids(slice_ids, self.n_slices)
        if mode not in ("off", "latest", "weighted"):
            raise ValueError("pretracking.mode must be off, latest or weighted")
        if not _finite_scalar(at_s) or at_s < self._last_query:
            raise ValueError("pretracking query times must be finite and monotonic [s]")
        if mode == "weighted" and self._weights is None:
            raise ValueError("pretracking.weights [unitless] is required for weighted updates")
        self._last_query = float(at_s)
        while self._pending and self._pending[0].ready_s <= at_s:
            self._recent.appendleft(self._pending.popleft())
        count = self.history_depth if mode == "weighted" else 1
        selected = list(self._recent)[:count] if mode != "off" else []
        eligible = len(selected) == count and all(set(ids).isdisjoint(r.slices) for r in selected)
        voltage = np.full(len(ids), np.nan)
        sources = np.full((len(ids), self.history_depth), -1, dtype=np.int64)
        ready = np.full(sources.shape, np.nan)
        valid = np.zeros(len(ids), dtype=bool)
        if selected and eligible:
            w = np.ones(1) if mode == "latest" else self._weights
            assert w is not None
            voltage[:] = np.dot(w, [r.estimate_v for r in selected])
            sources[:, :count] = [r.sample_id for r in selected]
            ready[:, :count] = [r.ready_s for r in selected]
            valid[:] = True
        return PretrackPrediction(
            readonly(voltage), readonly(sources, np.int64), readonly(ready), readonly(valid, bool)
        )
