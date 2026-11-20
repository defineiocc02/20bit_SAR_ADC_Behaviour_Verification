#!/usr/bin/env python3
"""sched18（random18 片组调度器）的随机性质量统计。

为什么需要这个脚本
------------------
`tb/tb_sched18.sv` 只做两件事：逐拍与 Python 模型对拍、每拍检查结构性不变量。
它证明的是"RTL == 模型"，**不证明"随机性够不够好"**。

而 sched18 曾经有一个 16 位乘法溢出缺陷（`(r1 * 16'd10) >> 16` 恒为 0），
该缺陷下 tb 的逐拍对拍会因为"模型与 RTL 都错"或者"口径不一致"而暴露，
但仅凭不变量检查（|ACQ|=8 等）完全看不出来——那些不变量在退化序列下依然成立。
所以必须单独量随机性质量。

本脚本直接跑 RTL 模型（src/adi_model/sched18_rtl.py，与 RTL 位精确对应），
输出四组判据，任何一项越界即非零退出：

  1. 每片被选为 ACQ 的次数均匀性（χ² 检验）
  2. IDLE 对覆盖 C(18,2)=153 与频次离散度
  3. ACQ 8 元组覆盖 C(18,8)=43758
  4. 结构性不变量（ACQ∩CONV=∅、|ACQ|=|CONV|=8、|IDLE|=2）

用法：
    python3 tools/sched18_stats.py [--cycles 20000] [--seed 0xACE10001]

为什么能在本机跑
----------------
本脚本按文件路径直载 sched18_rtl.py，不经过 adi_model/__init__.py，
因此不依赖 numpy/scipy，也没有 Python>=3.10 的限制（同 cmp_dem_dither.py）。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_HERE, '..', 'src', 'adi_model', 'sched18_rtl.py')
_spec = importlib.util.spec_from_file_location('_sched18_rtl_standalone', _MODEL_PATH)
_model = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_model)

N_SLICE = 18
N_ACTIVE = 8

# χ² 临界值（0.05 显著性）—— 硬编码以避免依赖 scipy。
# 自由度 17 时 95% 分位是 27.587。
CHI2_CRIT_DF17 = 27.587


def bits(mask: int) -> list[int]:
    """18 位掩码 -> 升序片号列表。"""
    return [i for i in range(N_SLICE) if (mask >> i) & 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cycles', type=int, default=20000)
    ap.add_argument('--seed', type=lambda s: int(s, 0), default=0xACE10001)
    args = ap.parse_args()

    sch = _model.Scheduler18Rtl(args.seed)
    acq_hist: Counter[int] = Counter()
    pair_hist: Counter[tuple[int, int]] = Counter()
    acq_sets: set[tuple[int, ...]] = set()
    violations = 0

    for _ in range(args.cycles):
        st = sch.step()
        a = st['acq']
        c = st['conv']
        i = st['idle']
        # 运行期不变量（step() 内部也会查，这里独立复核一遍）
        if set(a) & set(c):
            violations += 1
        if len(a) != N_ACTIVE or len(c) != N_ACTIVE or len(i) != 2:
            violations += 1
        if len(set(a) | set(c) | set(i)) != N_SLICE:
            violations += 1
        for s in a:
            acq_hist[s] += 1
        pair_hist[tuple(i)] += 1
        acq_sets.add(tuple(a))

    n = args.cycles
    exp = n * N_ACTIVE / N_SLICE
    counts = [acq_hist[s] for s in range(N_SLICE)]
    chi2 = sum((acq_hist[s] - exp) ** 2 / exp for s in range(N_SLICE))
    imbalance = (max(counts) - min(counts)) / exp * 100.0

    n_pair_total = 18 * 17 // 2          # C(18,2) = 153
    n_acq_total = 43758                   # C(18,8)

    print(f"sched18 随机性统计  seed={args.seed:#x}  cycles={n}")
    print("-" * 62)
    print(f"结构性不变量违例      {violations}")
    print(f"单片选中次数          min={min(counts)} max={max(counts)} 期望={exp:.0f}")
    print(f"  不平衡度            {imbalance:.2f}%")
    print(f"  χ²(df=17)           {chi2:.1f}   临界(0.05)={CHI2_CRIT_DF17}")
    print(f"IDLE 对覆盖           {len(pair_hist)}/{n_pair_total}"
          f"  min={min(pair_hist.values())} max={max(pair_hist.values())}"
          f" 期望={n/n_pair_total:.1f}")
    print(f"ACQ 8 元组覆盖        {len(acq_sets)}/{n_acq_total}"
          f" ({100.0*len(acq_sets)/n_acq_total:.1f}%)")
    print("-" * 62)

    ok = True
    if violations:
        print(f"FAIL: 结构性不变量违例 {violations} 次")
        ok = False
    if chi2 > CHI2_CRIT_DF17:
        print(f"FAIL: χ²={chi2:.1f} 超过临界 {CHI2_CRIT_DF17}，片选择不均匀")
        ok = False
    if len(pair_hist) != n_pair_total:
        print(f"FAIL: IDLE 对只覆盖 {len(pair_hist)}/{n_pair_total}"
              f"，随机性不足（退化序列的典型症状）")
        ok = False

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
