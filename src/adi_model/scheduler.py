"""Generic物理slice调度器：决定哪组采样电容承载哪笔输入。

reserve返回转换组视角，reserve_dual同时返回conv/acq；二者使用
timing.build_slice_plan并满足acq[n]交给conv[n+1]。固定A/B、备用顶替和
空闲池随机选择均不改变该归属合同，也不估计或校正时偏/增益/带宽。
结构rtl16 profile由pipeline_engine直接调用build_rtl_slice_plan，不经本工厂；
其LFSR游标扫描分布不能与generic ShuffledScheduler混写。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config, ConfigError
from .timing import build_slice_plan


@dataclass
class Allocation:
    """N笔样本的转换组视图；采集时确定，后续转换不能换成别的电容。

    Attributes:
        bank: (N,) int64，0/1是ping-pong相位标签；随机/备用策略下不代表固定物理组。
        slice_ids: (N, n_active) int64，逻辑slot对应的物理slice ID；
            配套采集组由reserve_dual返回，不保存在本对象。
    """

    bank: np.ndarray  # (N,) int，0/1相位标签，不是物理slice ID
    slice_ids: np.ndarray  # (N, n_active) int，参与本样本的 slice 编号


class Scheduler:
    """Generic固定A/B调度，支持声明周期的备用slice顶替。

    bank_a/bank_b/spares保存名义分组。启用备用顶替或使用随机子类时，实际转换ID
    必须读取reserve_dual的结果，不得由bank标签反推。
    """

    def __init__(self, cfg: Config, spare_rotation: bool = False, spare_period: int = 97):
        """保存名义分组和备用顶替控制，不抽取物理失配。

        Args:
            cfg: 池大小及活动slice数。
            spare_rotation: 是否周期顶替备用slice；默认关闭。
            spare_period: 顶替周期，单位为raw样本数。

        Raises:
            ValueError: 池不足以构造两组不相交活动电容。
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
        """返回N笔样本的转换组ID及0/1相位标签；实际分配委托reserve_dual。

        Args:
            n_samples: 非负raw样本数N。

        Returns:
            Allocation；slice_ids形状(N, n_active)，bank形状(N,)。

        Side effects:
            基类固定路径无随机状态；随机子类按其reserve_dual合同推进RNG。
        """
        conv, _ = self.reserve_dual(n_samples)
        return Allocation(bank=np.arange(n_samples, dtype=np.int64) % 2, slice_ids=conv)

    def reserve_dual(self, n_samples: int, rng: np.random.Generator | None = None):
        """返回同周期不相交、跨周期因果衔接的(conv, acq)。

        Args:
            n_samples: raw周期数N。
            rng: 基类忽略；备用顶替是确定性选择。

        Returns:
            两个(N, n_active) int64物理ID数组。无备用顶替时A/B固定；启用顶替时
            不得再用bank_a/bank_b描述所有实际ID。启动有效性由runner预充处理。

        Side effects:
            无外部随机状态。
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
    """Generic因果随机采集：只从当前conv之外的空闲物理slice中选下一组。"""

    def __init__(self, cfg: Config, rng: np.random.Generator):
        """保存独立调度RNG；切换策略不应改变采样噪声的随机流。

        Args:
            cfg: 名义池维度。
            rng: 调用方播种的专用随机生成器，由后续reserve_dual推进。
        """
        super().__init__(cfg)
        self.rng = rng

    def reserve_dual(self, n_samples: int, rng: np.random.Generator | None = None):
        """消费上一采集组，从空闲池置换后取下一组。

        Args:
            n_samples: raw周期数N。
            rng: 显式覆盖构造时生成器；None使用self.rng。

        Returns:
            (conv, acq)，各(N, n_active) int64物理ID；组内排序只确定逻辑slot顺序。
            该分布不同于结构RTL游标扫描，排序还会影响slot与物理ID的相关统计。

        Side effects:
            推进所用RNG；循环复杂度随N和物理池大小增长。
        """
        plan = build_slice_plan(self.cfg, n_samples, rng or self.rng, "shuffle_causal")
        return plan.conv, plan.acq


def make_scheduler(
    cfg: Config,
    rng: np.random.Generator,
    scheduler: Scheduler | None = None,
) -> Scheduler:
    """按generic配置选择调度器，拒绝显式对象与配置冲突。

    Args:
        cfg: dem_mode为rotate或permute；它选择实际转换/采集电容集合。
        rng: permute交给ShuffledScheduler的专用调度流。
        scheduler: 可选显式调度器；None时由配置创建。

    Returns:
        与cfg.dem_mode一致的Scheduler或ShuffledScheduler。
        rtl16结构profile由主路径直接调用build_rtl_slice_plan，不使用本工厂。

    Raises:
        ConfigError: 显式对象类型与dem_mode不一致；不静默让任一设置覆盖另一设置。

    Examples:
        >>> from adi_model import Config
        >>> import numpy as np
        >>> type(make_scheduler(Config(dem_mode="rotate"), np.random.default_rng(0))).__name__
        'Scheduler'
        >>> type(make_scheduler(Config(dem_mode="permute"), np.random.default_rng(0))).__name__
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
