"""采样电容的因果归属和结构RTL时序代理，共供行为runner使用。

build_slice_plan负责generic固定A/B或从空闲池中随机选组；
build_rtl_slice_plan负责结构18/8 allocator的LFSR游标、扫描顺序与启动预充。
两者都要求当周期conv/acq不交叉、acq[n]交给conv[n+1]，但随机分布不相同。这里输出物理slice ID与状态，
不估计时偏/带宽，不等同完整模拟采样电路。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config


@dataclass
class SlicePlan:
    """逐周期采集/转换归属；数组第二维是逻辑slot，元素是物理slice ID。

    conv/acq: (N, n_active)，当周期转换/采集组；集合逐行不相交。conv_source_cycle: (N,)，
    对应采集周期索引，可为启动预充周期-1。generic计划的首周期valid=False，runner须预充后消费；
    RTL计划已丢弃无效启动advance，返回的valid全True。组内顺序是接线/slot顺序，不是另一笔采样。
    """

    conv: np.ndarray
    acq: np.ndarray
    conv_source_cycle: np.ndarray
    valid: np.ndarray
    strategy: str
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        """返回转换周期数N。"""
        return int(self.conv.shape[0])


def _lfsr8(state: int) -> int:
    """将已验证的非零uint32 Galois状态推进8步；不生成模拟噪声电压。"""
    for _ in range(8):
        state = (state >> 1) ^ (0x80200003 if state & 1 else 0)
    return state


def rtl_dither_codes(n_samples: int, seed: int) -> np.ndarray:
    """返回结构D=2采样合同的离散dither序列，单位为RDAC单位码。

    Args:
        n_samples: 非负整数raw样本数N。
        seed: 非零uint32状态。

    Returns:
        (N,) int64，值域-2..2；高字节经8档映射，端点与内部档并非五档等概率。
        该接口不读取dither_amplitude_lsb1，不代表任意幅度的扫描已生效。

    Raises:
        ValueError: 样本数或种子不满足上述范围。

    Side effects:
        仅使用函数局部LFSR状态；不推进外部RNG。
    """
    if type(n_samples) is not int or n_samples < 0:
        raise ValueError("RTL dither needs a nonnegative integer sample count")
    if type(seed) is not int or not 0 < seed <= 0xFFFF_FFFF:
        raise ValueError("RTL dither seed must be a nonzero uint32")
    out = np.empty(n_samples, dtype=np.int64)
    state = seed
    for i in range(n_samples):
        reduced = (state >> 24) % 8
        out[i] = ((reduced + 1) >> 1) - 2
        state = _lfsr8(state)
    return out


def build_rtl_slice_plan(n_samples: int, *, shuffle: bool, seed: int) -> SlicePlan:
    """重现结构18/8 allocator的预充、接线顺序与LFSR游标。

    每周期从cursor开始环扫18个物理ID，跳过当前8个conv，取前8个空闲ID作acq。shuffle=True只改变游标；
    它不是在全部8-of-18组合中均匀抽样。高字节>=252时保留游标，LFSR仍推进。
    shuffle=False在启动后使用固定游标0。

    Args:
        n_samples: 非负整数，有效raw周期数N。
        shuffle: 是否启用LFSR游标；由结构profile的dem_enable控制。
        seed: 非零uint32；仅决定数字选择序列。

    Returns:
        SlicePlan；已跳过无效启动advance。首个输出组来自预充，实际外部sample ID
        由runner在此基础上从2开始分配。18/8及PRNG均为工程实现合同。

    Raises:
        ValueError: N、布尔开关或种子非法。

    Side effects:
        无外部状态；不能据此宣称单元等使用或时序失配已校正。
    """
    if type(n_samples) is not int or n_samples < 0:
        raise ValueError("RTL schedule needs a nonnegative integer sample count")
    if type(shuffle) is not bool or type(seed) is not int or not 0 < seed <= 0xFFFF_FFFF:
        raise ValueError("RTL schedule needs a boolean shuffle and a nonzero uint32 seed")
    conv = np.empty((n_samples, 8), dtype=np.int64)
    acq = np.empty_like(conv)
    current = np.arange(8, dtype=np.int64)
    cursor, state = 8, seed
    for cycle in range(n_samples + 1):
        used = set(map(int, current))
        following: list[int] = []
        for k in range(18):
            candidate = (cursor + k) % 18
            if candidate not in used and len(following) < 8:
                following.append(candidate)
        next_ids = np.asarray(following, dtype=np.int64)
        if cycle:
            conv[cycle - 1] = current
            acq[cycle - 1] = next_ids
        current = next_ids
        if shuffle:
            raw = state >> 24
            state = _lfsr8(state)
            if raw < 252:
                cursor = raw % 18
        else:
            cursor = 0
    return SlicePlan(
        conv,
        acq,
        np.arange(n_samples, dtype=np.int64) - 1,
        np.ones(n_samples, dtype=bool),
        "rtl16",
        {"shuffle": shuffle, "seed": seed, "startup_advances": 2},
    )


def build_slice_plan(
    cfg: Config,
    n_samples: int,
    rng: np.random.Generator,
    strategy: str = "pingpong",
    *,
    spare_period: int | None = None,
) -> SlicePlan:
    """从当前空闲slice中选择下一采集组，并保持跨周期样本归属。

    Args:
        cfg: 物理池维度；不得读取制造失配以挑选电容。
        n_samples: 非负周期数N。
        rng: 专用调度随机源，与采样/噪声随机源分开。
        strategy: pingpong使用固定A/B；shuffle_causal对当前空闲ID做置换并取前n_active。
        spare_period: pingpong可选的备用slice顶替周期；None表示不顶替。

    Returns:
        SlicePlan。conv[n+1]消费acq[n]；组内排序只固定存储顺序，不能把该slot统计
        当成固定物理slice统计。与RTL的游标扫描策略不同。

    Raises:
        ValueError: 池不足两组、N/策略/顶替周期非法。

    Side effects:
        随机策略推进传入rng；固定无顶替路径不消耗它。时偏/失调由物理pool另行施加。
    """
    n_active = cfg.n_active
    if n_samples < 0 or cfg.n_slices < 2 * n_active or n_active < 1:
        raise ValueError("invalid cycle count or slice pool dimensions")
    if strategy not in ("pingpong", "shuffle_causal"):
        raise ValueError(f"unknown strategy {strategy!r}")
    if spare_period is not None and spare_period < 1:
        raise ValueError("spare_period must be positive")
    conv = np.empty((n_samples, n_active), dtype=np.int64)
    acq = np.empty_like(conv)
    all_ids = np.arange(cfg.n_slices)
    current = np.arange(n_active)
    if strategy == "pingpong" and spare_period is None:
        groups = np.arange(2 * n_active).reshape(2, n_active)
        parity = np.arange(n_samples) % 2
        source = np.arange(n_samples, dtype=np.int64) - 1
        return SlicePlan(
            groups[parity],
            groups[1 - parity],
            source,
            source >= 0,
            strategy,
            {"n_slices": cfg.n_slices, "n_active": n_active, "spare_period": None},
        )
    for n in range(n_samples):
        conv[n] = current
        free = all_ids[~np.isin(all_ids, current)]
        if strategy == "shuffle_causal":
            nxt = np.sort(rng.permutation(free)[:n_active])
        else:
            nxt = free[:n_active].copy()
            if spare_period is not None and n % spare_period == 0 and free.size > n_active:
                nxt[n // spare_period % n_active] = free[n_active:][
                    n // spare_period % (free.size - n_active)
                ]
                nxt.sort()
        acq[n] = nxt
        current = nxt
    source = np.arange(n_samples, dtype=np.int64) - 1
    return SlicePlan(
        conv,
        acq,
        source,
        source >= 0,
        strategy,
        {"n_slices": cfg.n_slices, "n_active": n_active, "spare_period": spare_period},
    )
