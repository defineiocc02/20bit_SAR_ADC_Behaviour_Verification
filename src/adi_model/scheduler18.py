"""18 片物理池动态调度器（random18，Python 侧原型）。

与 legacy9 的区别：
  legacy9  : sid_base = (8+8g) % 18，9 组固定轮转，每 9 笔回到同一组
  random18 : 每周期从 10 片空闲中经 PRNG 选 8 片进入采集，剩余 2 片 IDLE

三条不变量（每周期检查）：
  1. ACQ 与 CONV 不相交（禁止同时采集与转换同一片）
  2. |ACQ| = |CONV| = 8，|IDLE| = 2，并集 = 18 片
  3. 采集组在转换期间冻结，不被重新分配
"""

from __future__ import annotations

import numpy as np

N_SLICE = 18
N_ACTIVE = 8
N_IDLE = 2


class Scheduler18:
    """按采样周期推进的三态调度器。"""

    def __init__(self, seed: int = 2026):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.acq: list[int] = []      # 正在采集的 8 片
        self.conv: list[int] = []     # 正在转换的 8 片
        self.idle: list[int] = []
        self.cycle = 0
        self.pending_conv: list[int] | None = None
        self.frozen = False
        self.violations: list[str] = []

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        """上电：任意选 8 片采集，其余为 IDLE。"""
        pool = self.rng.permutation(N_SLICE)
        self.acq = sorted(int(x) for x in pool[:N_ACTIVE])
        self.idle = sorted(int(x) for x in pool[N_ACTIVE:])
        self.conv = []
        self.cycle = 0
        self.frozen = False
        self._check()

    def reset(self) -> None:
        self.acq, self.conv, self.idle = [], [], []
        self.pending_conv = None
        self.frozen = False
        self.cycle = 0

    # ---------------------------------------------------------------- 每周期推进
    def step(self) -> dict:
        """推进一个采样周期。

        1) 上一周期的 ACQ 全部转为 CONV（冻结）
        2) 从其余 10 片里选 8 片作为新的 ACQ
        3) 剩下 2 片为 IDLE
        """
        # 1) 采集组 -> 转换组（冻结）
        self.conv = list(self.acq)
        self.frozen = True

        # 2) 候选池 = 全部 18 片 - CONV
        pool = [s for s in range(N_SLICE) if s not in self.conv]
        assert len(pool) == N_SLICE - N_ACTIVE, "候选池必须是 10 片"

        # 3) 从 10 片里选 8 片采集，余 2 片 IDLE
        pick = self.rng.permutation(len(pool))
        self.acq = sorted(pool[i] for i in pick[:N_ACTIVE])
        self.idle = sorted(pool[i] for i in pick[N_ACTIVE:])

        self.frozen = False
        self.cycle += 1
        self._check()
        return dict(cycle=self.cycle, acq=self.acq, conv=self.conv, idle=self.idle)

    # ---------------------------------------------------------------- 自检
    def _check(self) -> None:
        if not self.acq or not self.conv:
            return
        if set(self.acq) & set(self.conv):
            self.violations.append(f"cyc{self.cycle}: ACQ/CONV 重叠 {set(self.acq)&set(self.conv)}")
        if len(self.acq) != N_ACTIVE or len(self.conv) != N_ACTIVE:
            self.violations.append(f"cyc{self.cycle}: 活动片数不是 8")
        if len(set(self.acq) | set(self.conv) | set(self.idle)) != N_SLICE:
            self.violations.append(f"cyc{self.cycle}: 并集不是 18 片")
        if len(self.idle) != N_IDLE:
            self.violations.append(f"cyc{self.cycle}: IDLE 不是 2 片")


def run(seed: int = 2026, n: int = 2000) -> dict:
    sch = Scheduler18(seed)
    sch.start()
    use = np.zeros(N_SLICE)
    for _ in range(n):
        st = sch.step()
        for s in st["conv"]:
            use[s] += 1
    return dict(seed=seed, cycles=n, violations=sch.violations,
                use=use, imbalance=float(use.std() / use.mean()) if use.mean() else 0.0)
