"""主链路的因果预跟踪：仅从已可用的 SADC coarse code 生成预充电目标。

输入来自真实量化器判决及其完成时刻，不接受理想输入、物理电容/阈值或
逐 slice 时偏真值。离线仿真虽能预先生成全记录，pending 队列仍只在
ready_s<=预跟踪起点时发布结果，避免用未来判决改善当前建立。

名义估计为 (lower_v+(coarse_code+0.5)*coarse_step_v-known_injection_v)
/signal_scale：先取 coarse bin 中心，再减已知注入，最后恢复名义输入
尺度。这里是量化历史的预置器，不是电容权重拟合、交织补偿或低频学习器。
后续若加低频学习，须用数字可见记录和独立参考验证可辨识性，不能把
pool 的 v_os/t_skew/tau_rel 等 plant 真值作为学习输入。

输出为各物理 slice 的预跟踪目标 [外部输入等效 V] 及来源样本 ID/时间；
主链路再按有限预充电网络推进电压。本模块不求解连续建立、介电吸收或
真实开关/辅助驱动电路，不能由目标更准确直接推断器件带宽/噪声收益。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DecisionRecord:
    """一笔名义输入估计及其数字来源，字段不可变。

    sample_id 标识来源转换；ready_s 为判决可用时刻 [s]；slices 为曾采集
    该样本的物理 ID 元组，不能当作组内 slot；estimate_v 为名义输入估计 [V]。
    """

    sample_id: int
    ready_s: float
    slices: tuple[int, ...]
    estimate_v: float


@dataclass(frozen=True)
class PretrackPrediction:
    """按请求物理 ID 顺序返回目标与最多三笔来源。

    voltage_v 为 (A,) 输入等效目标 [V]，A 为本次请求片数；source_ids 和
    ready_times_s 为 (A,3)，分别是 sample ID 与可用时刻 [s]。未用来源用
    -1/NaN 标记，不能当作真实来源 0；数组列次序为最近到更早。
    """

    voltage_v: np.ndarray
    source_ids: np.ndarray
    ready_times_s: np.ndarray


@dataclass(frozen=True)
class InputAssistTrace:
    """保存主链路预跟踪和辅助输入网络的观测，不参与数字学习。

    pretrack_start_s 为 (N,) 起点 [s]，pretrack_target_v 为 (N,A) 目标 [V]；
    pretrack_source_ids/pretrack_available_s 为 (N,A,3) 来源 sample ID/时刻。
    pretrack_source_charge_c 及 auxiliary_*charge_c 为 (N,) 电荷 [C]，
    auxiliary_voltage_v 为 (N,) 辅助节点电压 [V]。这些与输入源电荷、时钟
    驱动电荷分列，不能全部算作从外部输入信号源抽取的同一种电荷。
    """

    pretrack_start_s: np.ndarray
    pretrack_target_v: np.ndarray
    pretrack_source_ids: np.ndarray
    pretrack_available_s: np.ndarray
    pretrack_source_charge_c: np.ndarray
    auxiliary_voltage_v: np.ndarray
    auxiliary_branch_charge_c: np.ndarray
    auxiliary_source_charge_c: np.ndarray
    auxiliary_reset_charge_c: np.ndarray

    @classmethod
    def allocate(cls, n: int, active: int) -> InputAssistTrace:
        """分配空观测数组，不产生预测或推进网络状态。

        Args:
            n: raw 样本数 N。
            active: 每组片数 A，即逻辑 slot 数。

        Returns:
            InputAssistTrace；缺失来源 ID=-1、时刻=NaN，其余电压/电荷初值为零。
        """
        return cls(
            np.full(n, np.nan),
            np.zeros((n, active)),
            np.full((n, active, 3), -1, dtype=np.int64),
            np.full((n, active, 3), np.nan),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
        )


class QuantizedPretracker:
    """带可用时间队列的名义码估计器，按物理 slice 保存最近来源。

    pending 保存尚未可见的判决；recent 保存最多三笔已可见判决；own 按
    物理 ID 保存该片最近采样的判决。构造后历史为空，跨调用是否复用由
    调用方决定；主 runner 在每次记录开始构造新实例。
    """

    def __init__(
        self,
        n_slices: int,
        bits: int,
        lower_v: float,
        coarse_step_v: float,
        signal_scale: float = 1.0,
        weights=(0.6, 0.3, 0.1),
    ):
        """绑定数字端已知的 coarse 网格并建立空历史。

        Args:
            n_slices: 物理 slice 总数，正值。
            bits: SADC 判决位数，不是最终输出位宽。
            lower_v: coarse 量化网格下界 [V]，有限。
            coarse_step_v: coarse bin 步长 delta1 [V/code]，有限且正；
                不等同 RDAC 单位步长 deltaDAC 或最终 LSB20。
            signal_scale: 名义输入信号比例，无量纲且正，采样掩码时通常为 alpha。
            weights: 最近到更早三笔历史的非负权重；首项必须为正。

        Raises:
            ValueError: 非法片数/位数、网格、尺度，或权重非有限、形状非 (3,)、
                存在负值/首项为零。

        Side effects:
            复制权重，清空 pending/recent/own；不读取物理真值或消费 RNG。
        """
        if (
            n_slices < 1
            or bits < 1
            or not np.isfinite(lower_v)  # 名义网格必须有限，不能让 NaN 进入预置目标。
            or not np.isfinite(coarse_step_v)
            or coarse_step_v <= 0
            or not np.isfinite(signal_scale)
            or signal_scale <= 0
        ):
            raise ValueError("pretracking requires positive slice/decision counts and nominal step")
        self.bits, self.lower_v, self.step_v = bits, lower_v, coarse_step_v
        self.signal_scale = signal_scale
        self._weights = np.asarray(weights, dtype=float).copy()
        if (
            self._weights.shape != (3,)
            or np.any(~np.isfinite(self._weights))
            or np.any(self._weights < 0)
            or self._weights[0] <= 0
        ):
            raise ValueError(
                "pretracking requires three nonnegative weights with a positive newest weight"
            )
        self._pending: deque[DecisionRecord] = deque()
        self._recent: deque[DecisionRecord] = deque(maxlen=3)
        self._own: list[DecisionRecord | None] = [None] * n_slices
        self._last_id = -1
        self._last_ready = -np.inf
        self._last_query = -np.inf

    def record(self, sample_id, ready_s, slice_ids, coarse_code, known_injection_v=0.0):
        """登记 coarse 判决及真正可用时刻，暂不发布给预测器。

        Args:
            sample_id: 来源转换的单调递增 ID，不是 bank、slot 或物理 slice ID。
            ready_s: 判决完成时刻 [s]，有限且按登记顺序非递减，允许同刻完成。
            slice_ids: 采过该来源样本的物理 slice ID 集合，非空、不重复且在池内。
            coarse_code: SADC 整数码 [0,2**bits)，不是 RDAC 加 dither 后的命令。
            known_injection_v: 量化器已知注入电压 [V]，按名义 bin 中心扣除；
                不是从理想输入差值反推的未知扰动或物理失配真值。

        Side effects:
            将名义 bin 中心估计加入 pending，推进最后登记 ID/完成时间。
            只有后续 predict 查询达到 ready_s，记录才进入 recent/own。

        Raises:
            ValueError: ID/时间失序、非法 coarse 码、片集合或非有限注入。
        """
        ids = tuple(int(i) for i in slice_ids)
        if (
            sample_id <= self._last_id
            or ready_s < self._last_ready
            or not np.isfinite(ready_s)
            or not np.isfinite(coarse_code)
            or coarse_code != int(coarse_code)
            or not 0 <= coarse_code < 2**self.bits
            or not ids
            or len(set(ids)) != len(ids)
            or min(ids) < 0
            or max(ids) >= len(self._own)
            or not np.isfinite(known_injection_v)
        ):
            raise ValueError(
                "pretracking decisions require ordered IDs/times and legal integer codes"
            )
        # coarse bin 中心处于注入后的码域；先减已知注入，再除名义输入比例。
        estimate = (
            self.lower_v + (coarse_code + 0.5) * self.step_v - known_injection_v
        ) / self.signal_scale
        self._pending.append(DecisionRecord(sample_id, ready_s, ids, float(estimate)))
        self._last_id, self._last_ready = sample_id, ready_s

    def predict(self, slice_ids, at_s: float, mode: str) -> PretrackPrediction:
        """在预跟踪开始时选择已可见历史，按请求物理 ID 生成目标。

        Args:
            slice_ids: (A,) 物理 slice ID；输出顺序保持与此数组一致。
            at_s: 查询起点 [s]，有限且相对前次查询非递减；ready_s==at_s
                的判决在本次即为可用。
            mode: reset_mid 输出零；latest 使用最近一笔；own 使用各片最后一次
                采样来源；weighted 使用最多三笔最近记录，截取后重新归一权重。

        Returns:
            PretrackPrediction；缺少历史时目标为零、source ID=-1、时刻=NaN。
            own 保存的是物理片的历史，绝不按逻辑 slot 偷换来源。

        Side effects:
            先推进最后查询时刻，再把可用 pending 移入 recent/own；不读取未来
            记录、真实输入或模拟失配。非法片 ID 的检查发生在队列推进之后，
            因此异常调用不具备事务回滚语义。

        Raises:
            ValueError: 未知模式、查询时间失序/非有限，或片 ID 越界/非一维。
        """
        if mode not in ("reset_mid", "latest", "own", "weighted"):
            raise ValueError("unknown pretracking mode")
        if not np.isfinite(at_s) or at_s < self._last_query:
            raise ValueError("pretracking query times must be finite and monotonic")
        self._last_query = at_s
        # 同一时刻完成的判决可见；离线已生成但尚未 ready 的判决必须留在队列。
        while self._pending and self._pending[0].ready_s <= at_s:
            record = self._pending.popleft()
            self._recent.appendleft(record)
            for physical in record.slices:
                self._own[physical] = record
        ids = np.asarray(slice_ids, dtype=np.int64)
        if ids.ndim != 1 or np.any(ids < 0) or np.any(ids >= len(self._own)):
            raise ValueError("pretracking slice IDs are out of bounds")
        voltage = np.zeros(len(ids))
        sources = np.full((len(ids), 3), -1, dtype=np.int64)
        ready = np.full((len(ids), 3), np.nan)
        for j, physical in enumerate(ids):
            own = self._own[physical]
            if mode == "own":
                selected = [] if own is None else [own]
            elif mode == "latest":
                selected = list(self._recent)[:1]
            elif mode == "weighted":
                selected = list(self._recent)
            else:
                selected = []
            if selected:
                weights = self._weights[: len(selected)].copy()
                weights /= weights.sum()
                voltage[j] = np.dot(weights, [r.estimate_v for r in selected])
                sources[j, : len(selected)] = [r.sample_id for r in selected]
                ready[j, : len(selected)] = [r.ready_s for r in selected]
        return PretrackPrediction(voltage, sources, ready)
