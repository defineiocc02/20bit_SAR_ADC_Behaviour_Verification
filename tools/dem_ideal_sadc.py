"""分离 SADC 与 RDAC：理想单调 SADC 出码，真实失配 RDAC 生成余差。

- SADC：解析式 D_ideal(k) = -VFS + 2*VFS*k/512，严格单调，与任何权重无关
- RDAC：按真实权重与 DEM 开关算 D_real(k)，D_real 可以非单调
- 宽 ADC2：先不量化（余差直接取），只看 DEM 自身的线性化效果
- 实际 ADC2：恢复 0..0.45V 窗口，统计溢出比例

回答两个问题：(1) D 组为何失效；(2) 理想 SADC + 真实 RDAC 下 DEM 是否有效。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, N_COLUMNS)

Q = 1 << 30
VFS = 3.0
N = 1024
FIN = 37


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


def nominal_w():
    w = {}
    for s_ in range(18):
        for j in range(N_COLUMNS):
            w[(s_, j)] = int(0.25 * Q)
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(s_, 16 + i)] = int(wt * Q / 8)
    return w


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


def sadc_ideal(x):
    """理想单调 SADC：直接解析求码，不查任何权重。"""
    k = int(np.floor((x + VFS) / (2 * VFS) * 512))
    return min(511, max(0, k))


def diagnose(w, seed=2026):
    """逐笔记录失败事务，区分非单调与窗口溢出。"""
    nmono = nwin = 0
    samples = []
    for n in range(64):
        x = 1.5 * np.sin(2 * np.pi * FIN * n / N)
        sid = n
        ph = dem_phases(sid)
        p = run_transaction(Transaction(sid, 256, 0, 0, w), slice_group=n, phases=ph)
        ids = p['slice_ids']
        k = sadc_ideal(x)
        dk = d_real(w, mask_of(k, *ph), ids)
        dk1 = d_real(w, mask_of(min(511, k + 1), *ph), ids)
        resid = x - dk
        over = (resid < 0.0) or (resid > 0.45 / 32)
        if over:
            samples.append((n, ids[0], ph, x, k, dk, dk1, dk1 - dk, resid))
            if dk1 <= dk:
                nmono += 1
            else:
                nwin += 1
    return samples, nmono, nwin


def abcd(w, coef, rot_g, rot_p, g0=3, wide=True):
    errs = []; over = 0
    for n in range(N):
        x = 1.5 * np.sin(2 * np.pi * FIN * n / N)
        g = n if rot_g else g0
        ph = dem_phases(n) if rot_p else dem_phases(g0 * 9)
        p = run_transaction(Transaction(n, 256, 0, 0, w), slice_group=g, phases=ph)
        if p.get('slice_ids') is None:
            over += 1; continue
        ids = p['slice_ids']
        k = sadc_ideal(x)
        d = d_real(w, mask_of(k, *ph), ids)
        resid = x - d
        if wide:
            rhat = resid                       # 宽窗口：理想观测
        else:
            if resid < 0 or resid > 0.45 / 32:
                over += 1; continue
            rhat = resid
        r = run_transaction(Transaction(n, k, 0, 0, coef), slice_group=g, phases=ph)
        # 直接用真实余差重建：x̂ = D_true(k) + rhat（校正关时用名义 D）
        dn = d_real(coef, mask_of(k, *ph), ids)   # 解码用系数（名义 = 未校正）
        errs.append((dn + rhat) - x)
    if not errs:
        return None
    e = np.array(errs)
    return e, over, N - len(errs)


if __name__ == '__main__':
    w = real_w()          # 前向用真实失配实例
    nom = nominal_w()     # 解码用名义系数（校正关，失配保留）
    print("=== Q1: D 组失效诊断（理想 SADC 出码，真实 RDAC）===")
    s, nmono, nwin = diagnose(w)
    print(f"溢出 {len(s)} 笔；其中 D(k+1)<=D(k)（非单调）{nmono} 笔，单调但窗口溢出 {nwin} 笔")
    for row in s[:3]:
        print(f"  n={row[0]} 片组首片={row[1]} ph={row[2]} x={row[3]:+.4f} k={row[4]} "
              f"D(k)={row[5]:+.5f} D(k+1)={row[6]:+.5f} 码间={row[7]*1e3:+.3f}mV 余差={row[8]*1e3:+.3f}mV")
    print("\n=== Q2: 理想 SADC + 真实 RDAC，宽窗口下 A/B/C/D ===")
    for name, rg, rp in (("A", False, False), ("B", False, True),
                         ("C", True, False), ("D", True, True)):
        out = abcd(w, nom, rg, rp)
        if out is None:
            print(f"  {name}: 无有效样本")
            continue
        e, over, miss = out
        print(f"  {name}: 有效 {len(e)} 溢出 {over} 缺 {miss}  残差rms {np.sqrt(np.mean(e**2))*1e6:8.1f} uV  max {np.abs(e).max()*1e6:8.1f} uV")
