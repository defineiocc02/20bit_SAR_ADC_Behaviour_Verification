"""普通逐笔 RA Auto-Zero（ADI ISSCC 2024 的 ordinary AZ）。

与 CAAZ 的区别（必须保留）：
- 本模块**每笔刷新、全速率**，N 笔进 N 笔出；不做偶奇配对，不降 fs/2。
- 只建模 RA 的输入等效失调、AZ 采样噪声与保持 droop；不读全局 AZ 字，
  不共享相邻样本状态。
- CAAZ（输入斩波 + 两笔共享 AZ + 数字配对）在 ``extensions/caaz/``，主包不依赖。

证据层级：L1 行为模型；参数默认关闭，开启后必须用独立对照验证。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OrdinaryAZConfig:
    """普通 AZ 的显式参数；全部默认关闭。"""

    enabled: bool = False
    offset_v: float = 0.0
    noise_v: float = 0.0
    droop_v_per_s: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        if self.noise_v < 0:
            raise ValueError("noise_v must be non-negative [V]")
        if self.droop_v_per_s < 0:
            raise ValueError("droop_v_per_s must be non-negative [V/s]")


@dataclass(frozen=True)
class OrdinaryAZResult:
    """一笔一样本的 AZ 结果；长度与输入一致。"""

    input_v: np.ndarray
    residual_v: np.ndarray
    sample_id: np.ndarray
    valid: np.ndarray


def apply_ordinary_az(
    input_v,
    sample_id,
    cfg: OrdinaryAZConfig,
    *,
    hold_time_s=None,
) -> OrdinaryAZResult:
    """对每笔独立施加普通 AZ。

    Args:
        input_v: 形状 ``(N,)`` 的 RA 输入等效电压 [V]。
        sample_id: 形状 ``(N,)`` 的 uint32 样本号；只用于对齐，不参与配对。
        cfg: 普通 AZ 参数。``enabled=False`` 时逐位返回输入。
        hold_time_s: 可选 ``(N,)`` **自上次 AZ 刷新以来的保持时间** [s]。
            droop 需要它；不是记录的绝对时间。普通 AZ 逐笔刷新，因此保持时间
            是"本笔读取时刻 − 本笔 AZ 时刻"，不是 `n/fs`（外部审查 A04）。

    Returns:
        ``OrdinaryAZResult``：``input_v`` 为 AZ 后电压，``residual_v`` 为
        **仍然残留、需要加到 RA 输入上**的误差（失调 + 噪声 + droop），
        ``valid`` 为逐笔有限性标志。

    Notes:
        符号约定只有一处：`residual_v` 是要**加**的残差，`input_v + residual_v`
        就是被污染后的输入。调用方不要再取负号。
        本函数不做两点平均，也不改变样本数。若调用方需要 CAAZ 的 fs/2
        配对，必须显式使用扩展，不得把本函数的输出再配对一次。
    """
    x = np.asarray(input_v, dtype=float)
    ids = np.asarray(sample_id)
    if x.ndim != 1:
        raise ValueError("input_v must be one-dimensional [V]")
    if ids.shape != x.shape:
        raise ValueError("sample_id must have the same shape as input_v")
    if not np.all(np.isfinite(x)):
        raise ValueError("input_v must be finite [V]")

    n = x.size
    if not cfg.enabled:
        return OrdinaryAZResult(x.copy(), np.zeros(n), ids, np.ones(n, dtype=bool))

    residual = np.full(n, cfg.offset_v, dtype=float)
    if cfg.noise_v:
        # 逐样本以 (seed, sample_id) 为键：分块运行与一次运行得到同一条噪声流。
        noise = np.array(
            [
                np.random.default_rng([int(cfg.seed), int(sample)]).normal(0.0, cfg.noise_v)
                for sample in ids
            ],
            dtype=float,
        )
        residual += noise
    if cfg.droop_v_per_s:
        if hold_time_s is None:
            raise ValueError("hold_time_s is required when droop is enabled [s]")
        t = np.asarray(hold_time_s, dtype=float)
        if t.shape != x.shape:
            raise ValueError("hold_time_s must have the same shape as input_v")
        if np.any(t < 0):
            raise ValueError("hold_time_s must be nonnegative [s]")
        residual += cfg.droop_v_per_s * t

    out = x + residual
    valid = np.isfinite(out)
    return OrdinaryAZResult(out, residual, ids, valid)
