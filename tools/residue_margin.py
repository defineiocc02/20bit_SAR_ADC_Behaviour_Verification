"""ADC2 余差裕量审计：真实 RDAC 相对理想直线的偏差与台阶范围。

扫描 512 粗码 × 9 物理片组 × 多组 DEM 相位，统计：
  1. D_real(k) − D_ideal(k) 的正负分布（决定需要多少正/负余量）
  2. 相邻台阶 D(k+1) − D(k) 的范围与 ≤0 计数（局部单调性）
  3. 全局单调性（整个码本是否存在 D(k+1) ≤ D(k)）
  4. 单边 ADC2 窗口 0~14.06 mV 的失效率

不修改任何 D5/DEM 公式，仅测量。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, N_COLUMNS)

Q = 1 << 30
VFS = 3.0
STEP_IDEAL = 2 * VFS / 512
ADC2_MAX = 0.45 / 32          # 14.0625 mV


def real_w(seed=2026):
    r = np.random.default_rng(seed)
    w = {}
    for sid in range(18):
        g = 1.0 + r.normal(0, 0.01)
        for j in range(N_COLUMNS):
            w[(sid, j)] = 0.25 * g * (1 + r.normal(0, 0.015))
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(sid, 16 + i)] = (wt / 8) * g * (1 + r.normal(0, 0.025))
    return {k: int(round(v * Q)) for k, v in w.items()}


def mask_of(k, res, hp, vp):
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    return [[yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
            + [0, 0, (k >> 1) & 1, k & 1] for s in range(8)]


def d_real(w, m, ids):
    num = 0.0; den = 0.0
    for slot, sid in enumerate(ids):
        for j in range(N_COLUMNS):
            wj = w[(sid, j)]; den += wj
            num += (-VFS if m[slot][j] else VFS) * wj
        for j in (18, 19):
            if m[slot][j]:
                num += -2.0 * VFS * w[(sid, j)]
    return -num / den


def d_ideal(k):
    return -VFS + 2.0 * VFS * k / 512.0


def main():
    w = real_w()
    # 相位集合：覆盖 h_phase 低值 + 几个不同 vp
    phases = [dem_phases(9 * q) for q in (0, 128, 256, 384, 512)]
    groups = list(range(9))
    offs, steps = [], []
    glob_break = 0; total_pairs = 0
    per_code_off = np.zeros(512)
    for g in groups:
        p = run_transaction(Transaction(0, 256, 0, 0, w), slice_group=g)
        ids = p['slice_ids']
        for ph in phases:
            prev = None
            for k in range(512):
                d = d_real(w, mask_of(k, *ph), ids)
                per_code_off[k] = d - d_ideal(k)
                if prev is not None:
                    s = d - prev
                    steps.append(s)
                    total_pairs += 1
                    if s <= 0:
                        glob_break += 1
                prev = d
            offs.append(per_code_off.copy())
    offs = np.array(offs); steps = np.array(steps)
    print(f"扫描 {len(groups)*len(phases)} 个 (片组,相位) 组合 × 512 码")
    print(f"\n[1] D_real(k) − D_ideal(k)  偏移分布")
    print(f"    min {offs.min()*1e3:+.3f} mV   max {offs.max()*1e3:+.3f} mV   "
          f"mean {offs.mean()*1e3:+.3f} mV   标准差 {offs.std()*1e3:.3f} mV")
    print(f"    负偏移占比 {(offs<0).mean()*100:.1f}%   正偏移占比 {(offs>0).mean()*100:.1f}%")
    print(f"\n[2] 相邻台阶 D(k+1)−D(k)（理想 {STEP_IDEAL*1e3:.3f} mV）")
    print(f"    min {steps.min()*1e3:+.3f} mV   max {steps.max()*1e3:+.3f} mV   "
          f"mean {steps.mean()*1e3:+.3f} mV")
    print(f"    ≤0 的台阶数 {glob_break}/{total_pairs} = {glob_break/total_pairs*100:.3f}%")
    print(f"\n[3] 余差需求（相对理想码边界）")
    need_pos = -offs.min()      # 输入刚过边界时余差可能为负多少
    need_neg = offs.max()       # 余差可能多出多少
    print(f"    单边窗口 0~{ADC2_MAX*1e3:.2f} mV 时：")
    print(f"      需负向余量至少 {need_pos*1e3:.3f} mV")
    print(f"      需正向余量至少 {need_neg*1e3:.3f} mV")
    print(f"      合计动态范围 { (need_pos+need_neg)*1e3:.3f} mV，"
          f"当前单边仅 {ADC2_MAX*1e3:.2f} mV -> 缺口 {(need_pos+need_neg-ADC2_MAX)*1e3:+.3f} mV")
    fail = ((offs > ADC2_MAX) | (offs < 0)).mean() * 100
    print(f"\n[4] 若余差均匀落在 [0,台阶) 而偏移如上行分布，单边窗口失效率约 {fail:.1f}%")


if __name__ == '__main__':
    main()
