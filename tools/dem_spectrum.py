"""DEM 开/关的频域对照：同一失配实例、同一正弦输入，只改 DEM 相位推进。

DEM 关：所有样本用同一相位（sample_id 固定）→ 失配误差随码固定 → 出现谐波/杂散
DEM 开：相位随样本轮转 → 失配误差被随机化 → 杂散下降、底噪抬升

指标：误差序列（x̂ − x）的 FFT，报最大杂散与谐波能量。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from forward_model import quantize_adc2
from dither_physical import real_w, mask_of, dac_v, VREF, SUB
from adi_model.digital_chain import run_transaction, Transaction, dem_phases, N_COLUMNS

Q = 1 << 30
DELTA = 3.0 / 256.0
N = 1024
FIN_BIN = 37          # 相干采样


def sar(x, w, ids, sid):
    k = 0
    for b in range(8, -1, -1):
        t = k | (1 << b)
        if x >= dac_v(w, mask_of(sid, t), ids):
            k = t
    return k


def nominal_w():
    w = {}
    for s_ in range(18):
        for j in range(N_COLUMNS):
            w[(s_, j)] = int(0.25 * Q)
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(s_, 16 + i)] = int(wt * Q / 8)
    return w


def capture(w, dem_on, amp=2.0, coef=None):
    errs = []
    for n in range(N):
        x = amp * np.sin(2 * np.pi * FIN_BIN * n / N)
        sid = n if dem_on else 0            # DEM 关 = 相位冻结
        p = run_transaction(Transaction(sid, 256, 0, 0, w))
        if p.get('slice_ids') is None:
            errs.append(np.nan); continue
        ids = p['slice_ids']                # 片组随 sid 变，必须逐笔取
        k = sar(x, w, ids, sid)
        if not (0 <= k <= 511):
            errs.append(np.nan); continue
        resid = x - dac_v(w, mask_of(sid, k), ids)
        a2 = quantize_adc2(resid)
        if a2 is None:
            errs.append(np.nan); continue
        r = run_transaction(Transaction(sid, k, 0, a2, coef if coef is not None else w))
        if r.get('x_weight_v') is None:
            errs.append(np.nan); continue
        errs.append(r['x_weight_v'] - x)
    return np.array(errs)


def metrics(e):
    m = ~np.isnan(e)
    if m.sum() < N // 2:
        return None
    ee = e[m] - np.mean(e[m])
    sp = np.abs(np.fft.rfft(ee * np.hanning(len(ee)))) ** 2
    sp[0:3] = 0
    k = np.argmax(sp[3:]) + 3
    tot = sp[3:].sum()
    return 10 * np.log10(sp[k] / (tot - sp[k])), k, np.sqrt(np.mean(ee ** 2))


if __name__ == '__main__':
    w = real_w()
    nom = nominal_w()
    print("--- 校正开（真系数）：DEM 无残余失配可打散 ---")
    for on in (False, True):
        e = capture(w, dem_on=on)
        r = metrics(e)
        tag = "DEM 开" if on else "DEM 关"
        if r:
            print(f"{tag}: rms误差 {r[2]*1e6:8.2f} uV  最大杂散/总能量 {r[0]:6.1f} dB  (bin {r[1]})")
    print("--- 校正关（名义系数）：失配保留，DEM 才有作用对象 ---")
    for on in (False, True):
        e = capture(w, dem_on=on, coef=nom)
        r = metrics(e)
        tag = "DEM 开" if on else "DEM 关"
        if r:
            print(f"{tag}: rms误差 {r[2]*1e6:8.2f} uV  最大杂散/总能量 {r[0]:6.1f} dB  (bin {r[1]})")
        else:
            print(f"{tag}: 有效样本不足")
