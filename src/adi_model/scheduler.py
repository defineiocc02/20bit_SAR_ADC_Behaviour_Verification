"""Causal acquisition scheduling for the 18-slice ADC pool.

Conversion consumes the group acquired in the preceding cycle. Both reserve()
and reserve_dual() use timing.build_slice_plan; the former returns the conversion
view. Fixed, spare-rotation and shuffled choices all preserve sample ownership.
Randomization is confined to slices free during the acquisition interval.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config, ConfigError
from .timing import build_slice_plan


@dataclass
class Allocation:
    """第 n 个样本占用的物理资源。采样时确定，之后不可更改。

    Attributes:
        bank:      (N,) int64，0 = bank A，1 = bank B（ping-pong 相位标签）。
        slice_ids: (N, n_active) int64，参与本样本转换的 slice 编号
                   （采集组由 reserve_dual / acq_ids 推导，不在此存储）。
    """

    bank: np.ndarray  # (N,) int，0 = bank A，1 = bank B
    slice_ids: np.ndarray  # (N, n_active) int，参与本样本的 slice 编号


class Scheduler:
    """固定 A/B 两组的确定性调度（+ 可选 spare 周期顶替）。

    Attributes:
        bank_a/bank_b: 两组各 n_active 个 slice 的编号数组。
        spares:        spare slice 编号（n_slices - 2·n_active 个）。
    """

    def __init__(self, cfg: Config, spare_rotation: bool = False, spare_period: int = 97):
        """划分固定的 A/B 两组，并配置可选的 spare 周期顶替。

        Args:
            cfg: 模型配置（决定 slice 池大小与每组片数）。
            spare_rotation: 是否启用 spare 周期顶替（默认关——开启后
                            误差归因引入额外时变性，仅用于专门实验）。
            spare_period:   顶替周期 [样本数]。
        Raises:
            ValueError: 池太小，无法构成不相交的 A/B 两组。
        """
        self.cfg = cfg
        self.spare_rotation = spare_rotation
        self.spare_period = spare_period
        n = cfg.n_slices
        n_act = cfg.n_active
        if n < 2 * n_act:
            raise ValueError("slice 池不足以构成 A/B 两组")
        self.bank_a = np.arange(0, n_act)
        self.bank_b = np.arange(n_act, 2 * n_act)
        self.spares = np.arange(2 * n_act, n)

    def reserve(self, n_samples: int) -> Allocation:
        """生成分配表：偶样本 bank A 转换 / 奇样本 bank B 转换。

        Args:
            n_samples: 样本数 N [无量纲]。
        Returns:
            Allocation（转换组视角）：
                bank:      (N,) int64，0=A / 1=B（ping-pong 相位标签）。
                slice_ids: (N, n_active) int64，转换组 slice 编号
                           （采集组由 reserve_dual / acq_ids 推导）。
        Side effects: 无。
        """
        conv, _ = self.reserve_dual(n_samples)
        return Allocation(bank=np.arange(n_samples, dtype=np.int64) % 2, slice_ids=conv)

    def reserve_dual(self, n_samples: int, rng: np.random.Generator | None = None):
        """返回 (conv, acq)：每样本的转换组与采集组，各 n_active 个。

        基类实现 = A/B ping-pong（acq[n] = 另一 bank 的固定 8 片——
        这正是"采集占满对方转换相"的 2-phase 交织时序）。
        Args:
            rng: 忽略（基类确定性；签名与 ShuffledScheduler 对齐）。
        Returns:
            (conv, acq) 各 (N, n_active) int64：
                conv: 转换组 slice 编号（bank A/B 固定 8 片）。
                acq:  采集组 slice 编号（另一 bank 固定 8 片）；
                      两者逐样本不相交，pipeline 断言此契约。
        Side effects: 无（确定性）。
        """
        plan = build_slice_plan(
            self.cfg,
            n_samples,
            np.random.default_rng(self.cfg.seed + 4201),
            "pingpong",
            spare_period=self.spare_period if self.spare_rotation else None,
        )
        return plan.conv, plan.acq


class ShuffledScheduler(Scheduler):
    """Causal 8-of-18 acquisition selection with a dedicated random stream."""

    def __init__(self, cfg: Config, rng: np.random.Generator):
        """用独立随机源构造随机洗牌调度器。

        Args:
            cfg: 模型配置（透传给基类）。
            rng: 洗牌序列生成器（独立于主噪声 rng，由调用方播种并持有，
             保证换调度器不改变噪声实现的可复现性）。
        """
        super().__init__(cfg)
        self.rng = rng

    def reserve_dual(self, n_samples: int, rng: np.random.Generator | None = None):
        """因果洗牌：上次采集组转换，空闲组中选择下次采集电容。

        Args:
            n_samples: 需要调度的样本数 N。
            rng: 覆盖构造时的生成器（None = 用 self.rng）。
        Returns:
            (conv, acq) 各 (N, n_active) int64（组内排序仅为了确定的
            存储顺序，物理上组内无序）。Side effects: 推进洗牌 rng。
        性能注：纯 Python 逐样本循环，O(N·18)；2^13 样本量级足够快，
        若上 2^17 需向量化（组合数学无现成闭式，保持可读性优先）。
        """
        plan = build_slice_plan(self.cfg, n_samples, rng or self.rng, "shuffle_causal")
        return plan.conv, plan.acq


def make_scheduler(
    cfg: Config,
    rng: np.random.Generator,
    scheduler: Scheduler | None = None,
) -> Scheduler:
    """按 ``cfg.dem_mode`` 选调度器，并拒绝与显式传入的调度器冲突。

    为什么需要这个函数
    ------------------
    ``dem_mode`` 此前是一个**没有任何读取点**的字段：选哪种调度完全取决于
    调用方实例化哪个 ``Scheduler`` 子类。写 ``dem_mode="permute"`` 却传
    ``Scheduler(...)``，用户以为自己换了机制，实际只换了一个字符串 ——
    外部复核（2026-09-11 第三轮）把它与 v7.0.0 的 ``ra_gain_model`` 失效
    归为同一类"配置与行为脱节"的缺陷。三个仿真入口现在都经由本函数取调度
    器，于是这个开关真的会改变机制。

    契约：显式传入的调度器与 ``cfg.dem_mode`` 不一致时**拒绝运行**，而不是
    让其中一个悄悄胜出 —— 两者同时存在必然意味着调用方对"跑的是哪种调度"
    有两种说法。

    调度策略与全部 runner 共用同一因果生成器；改变 dem_mode 同时改变实际
    转换电容集合，不能只改变一个标签。

    Args:
        cfg: 仿真配置；读取 ``cfg.dem_mode``（"rotate" 或 "permute"）。
        rng: 随机源；``dem_mode="permute"`` 时交给 ``ShuffledScheduler``。
        scheduler: 调用方显式提供的调度器；None = 由配置决定。

    Returns:
        Scheduler: 与 ``cfg.dem_mode`` 一致的那个调度器实例。

    Raises:
        ConfigError: 显式传入的调度器与 ``cfg.dem_mode`` 不一致。

    Examples:
        >>> from adi_model import Config
        >>> import numpy as np
        >>> s = make_scheduler(Config(dem_mode="rotate"), np.random.default_rng(0))
        >>> type(s).__name__
        'Scheduler'
        >>> p = make_scheduler(Config(dem_mode="permute"), np.random.default_rng(0))
        >>> type(p).__name__
        'ShuffledScheduler'
    """
    if scheduler is None:
        return ShuffledScheduler(cfg, rng) if cfg.dem_mode == "permute" else Scheduler(cfg)

    actual = "permute" if isinstance(scheduler, ShuffledScheduler) else "rotate"
    if cfg.dem_mode != actual:
        raise ConfigError(
            f"cfg.dem_mode={cfg.dem_mode!r} 与传入的调度器 "
            f"{type(scheduler).__name__}（对应 dem_mode={actual!r}）不一致："
            f"配置说一种调度、调用方给了另一种，拒绝运行。"
            f"改 cfg.dem_mode，或去掉 scheduler= 让配置决定。"
        )
    return scheduler
