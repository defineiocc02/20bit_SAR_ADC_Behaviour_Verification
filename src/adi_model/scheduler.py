"""scheduler.py -- slice 池调度：A/B 时序、转换组/采集组绑定、8/18 随机洗牌。

============================================================================
物理模型与文献出处
============================================================================
论文 [00]："the RDAC is composed of 8 slice DACs (sDAC) converting the
signal, and 8 sDACs acquiring the signal, dynamically selected from a
pool of 18 sDACs. Two spare sDACs are introduced to allow shuffling of
the sampling DACs and spread the residual interleaving tones."
PPT p.10-12：18 slice = 8（采集）+ 8（保持/残差）+ 2（spare/randomisation）。

三种调度模式（物理含义递进）：
    * Scheduler（默认）          ：A/B ping-pong 固定两组。slice 间带宽/时刻
                                  失配 -> f_S/2±f_IN 固定交织杂散（stage19
                                  验收③④的"固定组"基准）。
    * Scheduler(spare_rotation)  ：spare 周期性顶替组内成员（最简轮换，
                                  尚未建模轮换期间的状态传递——开放项）。
    * ShuffledScheduler          ：每样本 8/18 随机洗牌。杂散打散进噪声底
                                  （stage19 实测：带宽失配降 31 dB、
                                  timing skew 降 42 dB）。

单位与数据契约：
    * slice 编号 = [0, n_slices) 的 int64；前 n_active 个属 bank A，
      次n_active 个属 bank B，其余为 spare（物理意义只在固定组模式成立；
      洗牌模式下编号与 bank 的绑定无物理含义，只有"池里 18 片"这一层）。
    * Allocation 在**采样时确定并保存，之后不可更改**（硬约束——
      数字重构时重新选择会悄悄改变误差归属，破坏"误差去哪里"的可归因性）。
    * reserve_dual 返回的 (conv, acq) 逐样本两两不相交，各 n_active 个
      —— pipeline.SlicePool.invariants 逐样本断言此契约。

参数来源分级：
    n_slices=18 / n_active=8   [披露]（论文原文 + PPT p.10）
    spare_period=97            [假设]（互质于常见样本数，避免与 DEM 周期同步）

已知边界（勿当已实现）：
    * 固定组模式下 slice_ids 的组内顺序固定 —— DEM 的组内轮转由 mapper
      负责，本模块不做任何码相关置换（职责单一：时序与占用，不含算法）。
    * spare 顶替期间被换下 slice 的 v_top 冻结，换上 slice 的 v_top 取
      池中保存值 —— 洗牌模式下等价于"任意历史"，由 pipeline 的逐相位
      提交语义自动覆盖；固定组+spare_rotation 组合下这是近似。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config, ConfigError


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
        n = np.arange(n_samples)
        bank = (n % 2).astype(np.int64)  # A/B ping-pong
        base = np.stack([self.bank_a, self.bank_b])  # (2, n_active)
        slice_ids = base[bank].copy()  # (N, n_active)

        if self.spare_rotation and self.spares.size > 0:
            # 每 spare_period 个样本，把一个 slice 换成 spare（先做最简版本：
            # 用 spare 替换 bank 内编号最小的那个）
            hit = (n % self.spare_period) == 0
            if hit.any():
                k = n[hit] // self.spare_period
                victim = k % self.cfg.n_active
                donor = self.spares[k % self.spares.size]
                slice_ids[hit, victim] = donor
        return Allocation(bank=bank, slice_ids=slice_ids)

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
        alloc = self.reserve(n_samples)
        conv = alloc.slice_ids
        acq = np.empty_like(conv)
        acq[0::2] = self.bank_b
        acq[1::2] = self.bank_a
        if self.spare_rotation and self.spares.size > 0:
            # 与 reserve 相同的替换逻辑镜像到 acq（简化：同样按命中样本替换）
            n = np.arange(n_samples)
            hit = (n % self.spare_period) == 0
            if hit.any():
                k = n[hit] // self.spare_period
                victim = k % self.cfg.n_active
                donor = self.spares[(k + 1) % self.spares.size]
                acq[hit, victim] = donor
        return conv, acq


class ShuffledScheduler(Scheduler):
    """8/18 随机洗牌调度（论文 [00_1]："shuffling of the sampling DACs to spread the residual interleaving tones"）。

    每样本从 18 片池中随机抽 8 片转换、随后 8 片采集（两两不相交，
    剩余 2 片为 spare）。确定性 bank 交替下，slice 间带宽/时刻失配在
    f_S/2±f_IN 产生固定交织杂散；随机化后同一 slice 的使用间隔随机化，
    杂散能量被打散进噪声底（定量：stage19 验收③④，带宽失配降 31 dB、
    timing skew 降 42 dB；err RMS 略升是物理正确的——杂散换噪声底）。

    继承 reserve()（固定组视角）仅供 reserve_dual 的签名兼容；
    本类的**有效调度只在 reserve_dual**。
    """

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
        """逐样本独立洗牌：8 转换 + 8 采集 + 2 spare。

        Args:
            n_samples: 需要调度的样本数 N。
            rng: 覆盖构造时的生成器（None = 用 self.rng）。
        Returns:
            (conv, acq) 各 (N, n_active) int64（组内排序仅为了确定的
            存储顺序，物理上组内无序）。Side effects: 推进洗牌 rng。
        性能注：纯 Python 逐样本循环，O(N·18)；2^13 样本量级足够快，
        若上 2^17 需向量化（组合数学无现成闭式，保持可读性优先）。
        """
        g = rng or self.rng
        n_act = self.cfg.n_active
        conv = np.empty((n_samples, n_act), dtype=np.int64)
        acq = np.empty((n_samples, n_act), dtype=np.int64)
        for i in range(n_samples):
            perm = g.permutation(self.cfg.n_slices)
            conv[i] = np.sort(perm[:n_act])
            acq[i] = np.sort(perm[n_act : 2 * n_act])
        return conv, acq


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

    接线 != 调度正确
    ----------------
    ``dem_mode="permute"`` 现在确实会得到 ``ShuffledScheduler``，但**洗牌
    调度器自身的样本归属缺陷仍未闭合**：它的 conv/acq 不保证来自同一组
    slice（见 ``tests/audit/test_review_contracts.py`` 中
    ``xfail(strict=True)`` 的 ``TestR1SampleOwnership``）。把开关接上不等于
    把物理问题修好，两者是独立验收项。

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
