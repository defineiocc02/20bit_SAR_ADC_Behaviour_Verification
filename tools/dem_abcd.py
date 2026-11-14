"""DEM 四组对照：把「片组调度」与「片内 DEM 相位」解耦。

A 固定片组 + 固定相位   —— 两者都冻结（基线）
B 固定片组 + 轮转相位   —— 只让片内置换变
C 轮转片组 + 固定相位   —— 只让片组变
D 轮转片组 + 轮转相位   —— 两者都变（正常 DEM）

同实例 / 同输入 / 同 ADC2 配置。指标严格区分：
  - 输出频谱 SFDR（对 x̂），dBc
  - 误差谱（对 x̂−x）：先拟合增益 a、偏置 b，再取残差谱
  - 报告残差 RMS、误差谱最大峰 dBc 及 bin
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from forward_model import quantize_adc2
from dither_physical import real_w, dac_v
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, N_COLUMNS)

Q = 1 << 30
N = 1024
FIN = 37
AMP = 1.5


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


def run_abcd(w, coef, rot_group, rot_phase, g0=3):
    """均匀采样：每点必出结果；出不来就整轮判无效，不删点。"""
    xs, ys = [], []
    for n in range(N):
        x = AMP * np.sin(2 * np.pi * FIN * n / N)
        g = n if rot_group else g0
        ph = dem_phases(n) if rot_phase else dem_phases(g0 * 9)
        p = run_transaction(Transaction(n, 256, 0, 0, w), slice_group=g, phases=ph)
        if p.get('slice_ids') is None:
            return None
        ids = p['slice_ids']
        k = 0
        for b in range(8, -1, -1):
            t = k | (1 << b)
            if x >= dac_v(w, mask_of(t, *ph), ids):
                k = t
        if not (0 <= k <= 511):
            return None
        resid = x - dac_v(w, mask_of(k, *ph), ids)
        a2 = quantize_adc2(resid)
        if a2 is None:
            return None
        r = run_transaction(Transaction(n, k, 0, a2, coef), slice_group=g, phases=ph)
        if r.get('x_weight_v') is None:
            return None
        xs.append(x); ys.append(r['x_weight_v'])
    return np.array(xs), np.array(ys)


def analyze(x, y):
    win = np.hanning(len(y))
    spec = np.abs(np.fft.rfft(y * win)) ** 2
    sig_bin = FIN
    fund = spec[sig_bin - 2: sig_bin + 3].max()
    s = spec.copy(); s[sig_bin - 2: sig_bin + 3] = 0; s[0:3] = 0
    sfdr = 10 * np.log10(fund / s.max())
    # 误差：先拟合 gain/offset
    A = np.vstack([x, np.ones_like(x)]).T
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    e = y - (a * x + b)
    es = np.abs(np.fft.rfft(e * win)) ** 2
    es[0:3] = 0
    peak_bin = int(np.argmax(es))
    err_peak_db = 10 * np.log10(es[peak_bin] / (es.sum() - es[peak_bin]))
    return dict(sfdr=sfdr, gain=a, off=b * 1e6, rms=np.sqrt(np.mean(e ** 2)),
                peak_bin=peak_bin, peak_db=err_peak_db)


if __name__ == '__main__':
    w = real_w(); nom = nominal_w()
    print(f"N={N}  fin=bin{FIN}  amp={AMP}V  同一实例 seed=2026")
    print(f"{'组':4s} {'SFDR':>8s} {'gain':>9s} {'off/uV':>9s} {'残差RMS/uV':>12s} {'误差峰bin':>9s} {'误差峰dBc':>10s}")
    for g0 in (1, 3, 6):
        print(f"--- 固定片组 g0={g0} ---")
        for name, rg, rp in (("A", False, False), ("B", False, True),
                             ("C", True, False), ("D", True, True)):
            out = run_abcd(w, nom, rg, rp, g0=g0)
            if out is None:
                print(f"  {name:4s}  存在无效样本（整轮判无效，不删点）")
                continue
            r = analyze(*out)
            print(f"  {name:4s} SFDR {r['sfdr']:6.2f}  gain {r['gain']:.6f}  "
                  f"残差rms {r['rms']*1e6:8.1f} uV  峰 bin {r['peak_bin']:4d} {r['peak_db']:6.2f} dBc")
