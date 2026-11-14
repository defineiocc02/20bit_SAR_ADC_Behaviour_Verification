# =============================================================================
# 文件名     : transaction_trace.py
# 模块名     : adi_model.simulation.transaction_trace
# 版本       : v1.0
# 功能       : 从一次仿真结果抽出 V2 事务 trace（epoch + sample_id + event_index）。
#
# 设计说明   :
#   * 为什么需要它：顶层对拍（V3/V4）要求"每笔转换可逐事件对齐"。没有三段主键，
#     对拍只能比总量，无法定位"哪一笔的第几个事件错了"。
#   * 三段主键的含义：
#       epoch        —— 配置纪元。配置/权重/binding 在捕获期间不变的声明；
#                       变了就必须开新 epoch，否则跨纪元的样本不能直接比。
#       sample_id    —— 采样序号（同一 epoch 内唯一、单调）。
#       event_index  —— 该笔内的第几个位试验（从 0 开始）。
#   * 本模块**只重组已有观测**，不新造模拟量、不读真值：
#     所有字段都来自 SimResult 里已有的数组，可逐项回溯。
#   * 不生成 `conversion_trace`（那是 joint 动态路径的电气观测，另一层东西）。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：定义三段主键与连续性判据（N_accepted = N_retired + N_cancelled）。
# =============================================================================

"""V2 事务 trace：三段主键与连续性判据。

约定：
  * 行序即执行序（同一 epoch 内）。
  * `event_index` 每笔从 0 递增，笔内连续。
  * 主键 (epoch, sample_id, event_index) 必须唯一。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError


@dataclass(frozen=True)
class TransactionTrace:
    """逐事件事务记录。

    Attributes:
        epoch: 标量整数，配置纪元。
        sample_id: (R,) 每行的采样序号。
        event_index: (R,) 每行的笔内事件序号。
        coarse_code: (R,) 该事件结束时的粗码（整数）。
        rdac_code: (R,) 该事件结束时的 RDAC 码（整数）。
    """

    epoch: int
    sample_id: np.ndarray
    event_index: np.ndarray
    coarse_code: np.ndarray
    rdac_code: np.ndarray

    def __post_init__(self) -> None:
        """校验形状、类型与主键唯一性；不合规直接拒绝，不静默修补。"""
        if isinstance(self.epoch, bool) or not isinstance(self.epoch, int | np.integer):
            raise ConfigError("epoch must be an integer")
        if int(self.epoch) < 0:
            raise ConfigError("epoch must be nonnegative")
        sizes = set()
        for name in ("sample_id", "event_index", "coarse_code", "rdac_code"):
            value = np.asarray(getattr(self, name))
            if value.ndim != 1:
                raise ConfigError(f"{name} must be one-dimensional")
            if not np.issubdtype(value.dtype, np.integer):
                raise ConfigError(f"{name} must be an integer array")
            sizes.add(int(value.size))
        if len(sizes) != 1:
            raise ConfigError("all trace columns must have the same length")
        if np.any(self.sample_id < 0):
            raise ConfigError("sample_id must be nonnegative")
        if np.any(self.event_index < 0):
            raise ConfigError("event_index must be nonnegative")
        # 主键唯一
        keys = np.stack([self.sample_id, self.event_index], axis=1)
        if np.unique(keys, axis=0).shape[0] != keys.shape[0]:
            raise ConfigError("transaction trace keys (sample_id, event_index) must be unique")

    @property
    def n_rows(self) -> int:
        """总事件行数。"""
        return int(self.sample_id.size)

    @property
    def n_samples(self) -> int:
        """覆盖到的样本数（按唯一 sample_id 计）。"""
        return int(np.unique(self.sample_id).size)


def event_index_restarts_each_sample(*, sample_id, event_index) -> bool:
    """判据：每笔的 `event_index` 从 0 开始且连续。

    Args:
        sample_id: (R,) 采样序号。
        event_index: (R,) 事件序号。

    Returns:
        True 表示每笔都从 0 开始、步长恰为 1，且没有跨笔重复。

    Raises:
        ConfigError: 形状不符或含负值。

    Side effects: 无。
    """
    sid = np.asarray(sample_id)
    evt = np.asarray(event_index)
    if sid.shape != evt.shape or sid.ndim != 1:
        raise ConfigError("sample_id and event_index must be one-dimensional and equal length")
    if np.any(sid < 0) or np.any(evt < 0):
        raise ConfigError("sample_id and event_index must be nonnegative")
    for value in np.unique(sid):
        indexes = evt[sid == value]
        if indexes[0] != 0:
            return False
        if not np.array_equal(indexes, np.arange(indexes.size)):
            return False
    return True


def transaction_continuity(*, n_accepted: int, n_retired: int, n_cancelled: int, n_pending: int) -> bool:
    """判据：样本守恒 `N_accepted = N_retired + N_cancelled + N_pending`。

    这是顶层对拍最基本的一条：进来的样本要么走了、要么被取消、要么还挂着，
    不能凭空多出来或消失。

    Args:
        n_accepted: 已接收的样本数。
        n_retired: 已完成并输出的样本数。
        n_cancelled: 被取消的样本数。
        n_pending: 仍未完成的样本数。

    Returns:
        True 表示守恒成立。

    Raises:
        ConfigError: 任一计数为负或非整数。

    Side effects: 无。
    """
    for name, value in (
        ("n_accepted", n_accepted),
        ("n_retired", n_retired),
        ("n_cancelled", n_cancelled),
        ("n_pending", n_pending),
    ):
        if isinstance(value, bool) or not isinstance(value, int | np.integer):
            raise ConfigError(f"{name} must be an integer")
        if int(value) < 0:
            raise ConfigError(f"{name} must be nonnegative")
    return int(n_accepted) == int(n_retired) + int(n_cancelled) + int(n_pending)


def build_trace_from_result(*, result, epoch: int = 0) -> TransactionTrace:
    """从一次仿真结果抽出 V2 trace。

    Args:
        result: 仿真原始结果（需要 `sample_id` 与 `k`，以及 `conv_slice_ids`）。
        epoch: 配置纪元（由调用方声明；本函数不推断）。

    Returns:
        TransactionTrace：每个样本一行（一笔转换一个终态码）。

    Raises:
        ConfigError: 结果缺少必需字段，或字段长度不一致。

    Side effects: 无；只读结果，不修改它。
    """
    missing = [n for n in ("sample_id", "k") if not hasattr(result, n)]
    if missing:
        raise ConfigError(f"result lacks required fields for V2 trace: {missing}")
    sample_id = np.asarray(result.sample_id)
    rdac = np.asarray(result.k)
    if sample_id.shape != rdac.shape:
        raise ConfigError("sample_id and rdac code must have the same shape")
    coarse = np.asarray(getattr(result, "coarse", rdac))
    if coarse.shape != rdac.shape:
        raise ConfigError("coarse code shape must match rdac code")
    # V2 记录的是**终态**码（每笔一个），因此 event_index 全 0。
    # 逐事件版本要等 conversion_trace（joint 动态路径）提供，届时再扩。
    event_index = np.zeros(sample_id.size, dtype=np.int64)
    return TransactionTrace(
        epoch=int(epoch),
        sample_id=sample_id.astype(np.int64),
        event_index=event_index,
        coarse_code=coarse.astype(np.int64),
        rdac_code=rdac.astype(np.int64),
    )
