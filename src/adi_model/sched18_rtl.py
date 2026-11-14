"""random18 调度器的 RTL 等价模型（位精确，对应 rtl/sched18.sv）。

为什么另写一份
--------------
``scheduler18.py`` 用的是 ``numpy.random.Generator(PCG64)`` 的全排列。
PCG64 在 RTL 里无法逐位复现，因此 P0-3 的"同一 seed/输入下逐笔一致"
不可能拿它做判据。

本文件给出 **硬件可实现** 的选片算法，Python/RTL 共用同一套整数运算，
从而可以对选片序列做逐拍对拍：

    状态：rng(32 bit xorshift)、acq_mask(18)、conv_mask(18)
    每个采样周期（一笔事务被接收）：
      1. conv' = acq                    （上一周期的采集组进入转换，冻结）
      2. avail = ~conv'                 （10 片候选）
      3. 抽两个 16 bit 随机数 r1, r2
         i1 = (r1·10) >> 16             （0..9）
         j0 = (r2·9)  >> 16             （0..8）
         i2 = j0 + (j0 >= i1)           （0..9 中去掉 i1）
      4. 候选按序号升序排列为 cand[0..9]，把 cand[i1]、cand[i2] 置为 IDLE
      5. acq' = avail − {cand[i1], cand[i2]}

三条不变量（每周期自检）：ACQ∩CONV=∅、|ACQ|=|CONV|=8、三态并集 = 18 片。

实测（seed=0xACE10001，20000 拍）：20000 拍 0 违例；片使用率不平衡度 0.24%；
IDLE 对覆盖 C(18,2)=153 组，min/max = 106/162（期望 131）；ACQ 组覆盖 15791/43758。
注意：这里的不均匀是**结构性的**（ACQ' 必须避开 CONV'），不是 PRNG 缺陷。
"""

from __future__ import annotations

N_SLICE = 18
N_ACTIVE = 8
N_IDLE = 2

# 32 位 xorshift（Marsaglia 7/13/17 组，种子不可为 0）。
# 选它而不是 Fibonacci LFSR：LFSR 的低 16 位是同一 m 序列的连续 16 拍，
# 作为"随机数"高度相关——实测 IDLE 对只覆盖 45 组中的一部分且极不均匀
# （4000 拍下 min=1 / max=181）。xorshift 每拍输出整体混合，够用。
XORSHIFT_A = 13
XORSHIFT_B = 17
XORSHIFT_C = 5
MASK32 = 0xFFFFFFFF
SEED_DEFAULT = 0xACE10001


def prng_step(x: int) -> int:
    """一步 xorshift32（与 rtl/sched18.sv 的 always_ff 逐位一致）。"""
    x &= MASK32
    x ^= (x << XORSHIFT_A) & MASK32
    x ^= x >> XORSHIFT_B
    x ^= (x << XORSHIFT_C) & MASK32
    return x & MASK32


class Scheduler18Rtl:
    """按采样周期推进的三态调度器（RTL 等价）。"""

    def __init__(self, seed: int = SEED_DEFAULT):
        self.seed = seed & 0xFFFFFFFF
        self.reset()

    def reset(self) -> None:
        # 上电：固定取 0..7 采集，其余 IDLE，无转换组（启动瞬态，非随机）
        self.rng = self.seed if self.seed else SEED_DEFAULT
        self.acq_mask = 0xFF
        self.conv_mask = 0x00
        self.cycle = 0
        self.violations: list[str] = []
        self.trace: list[tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = []

    # ---------------------------------------------------------------- 观察
    @property
    def acq(self) -> list[int]:
        return [s for s in range(N_SLICE) if self.acq_mask >> s & 1]

    @property
    def conv(self) -> list[int]:
        return [s for s in range(N_SLICE) if self.conv_mask >> s & 1]

    @property
    def idle(self) -> list[int]:
        m = ~(self.acq_mask | self.conv_mask) & 0x3FFFF
        return [s for s in range(N_SLICE) if m >> s & 1]

    def ids_packed(self) -> list[int]:
        """当前采集组 8 个片 ID（升序，与 RTL 的 ids_o 打包顺序一致）。"""
        return self.acq

    # ---------------------------------------------------------------- 推进
    def step(self) -> dict:
        """推进一个采样周期（对应 RTL 的一次 adv）。"""
        self.conv_mask = self.acq_mask                      # 1) 采集组 -> 转换组
        avail = (~self.conv_mask) & 0x3FFFF                 # 2) 10 片候选
        if bin(avail).count("1") != N_SLICE - N_ACTIVE:
            self.violations.append(f"cyc{self.cycle}: avail != 10")

        self.rng = prng_step(self.rng)
        r1 = self.rng & 0xFFFF
        self.rng = prng_step(self.rng)
        r2 = self.rng >> 16

        i1 = (r1 * 10) >> 16
        j0 = (r2 * 9) >> 16
        i2 = j0 + (1 if j0 >= i1 else 0)

        cand = [s for s in range(N_SLICE) if avail >> s & 1]
        drop = (1 << cand[i1]) | (1 << cand[i2])            # 4) 两片 IDLE
        self.acq_mask = avail & ~drop                       # 5) 余 8 片采集

        self.cycle += 1
        self._check()
        self._record()
        return dict(cycle=self.cycle, acq=self.acq, conv=self.conv, idle=self.idle)

    def _check(self) -> None:
        if self.acq_mask & self.conv_mask:
            self.violations.append(f"cyc{self.cycle}: ACQ/CONV 重叠")
        if bin(self.acq_mask).count("1") != N_ACTIVE:
            self.violations.append(f"cyc{self.cycle}: ACQ 不是 8 片")
        if bin(self.conv_mask).count("1") != N_ACTIVE:
            self.violations.append(f"cyc{self.cycle}: CONV 不是 8 片")
        if bin(self.acq_mask | self.conv_mask).count("1") != N_SLICE - N_IDLE:
            self.violations.append(f"cyc{self.cycle}: 活动片不是 16 片")

    def _record(self) -> None:
        self.trace.append((tuple(self.acq), tuple(self.conv), tuple(self.idle)))


def run(seed: int = SEED_DEFAULT, n: int = 2000, warmup: int = 0) -> dict:
    """跑 n 个采样周期，统计片使用率不平衡度。"""
    sch = Scheduler18Rtl(seed)
    use = [0] * N_SLICE
    for i in range(n + warmup):
        sch.step()
        if i >= warmup:
            for s in sch.acq:
                use[s] += 1
    mean = sum(use) / N_SLICE
    var = sum((u - mean) ** 2 for u in use) / N_SLICE
    return dict(seed=seed, cycles=n, warmup=warmup, violations=sch.violations,
                use=use, imbalance=(var ** 0.5) / mean if mean else 0.0)


if __name__ == "__main__":
    for sd in (SEED_DEFAULT, 0x12345678, 0x0000BEEF):
        r = run(sd, 20000, 100)
        print(f"seed={sd:#010x} cycles={r['cycles']} violations={len(r['violations'])} "
              f"imbalance={r['imbalance']*100:.2f}% use={r['use']}")
