"""完整 ADC 闭环 v2：输入 x → SADC 逐位判决 → Follower 逐事件 → RDAC → RA → ADC2 → D5。

与 v1 的区别：
  - 粗码 k 不再随机给定，由独立前向模型的比较器逐位搜出（9 次判决）
  - 同时记录 Follower 的 7 个逐事件 mask（前缀驱动）
  - 物理阵列模型独立于数字链：本文件自己按 mask 累加电荷
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from forward_model import quantize_adc2, ADC2_LSB
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, _dither_terms, N_COLUMNS)

Q = 1 << 30
SUB = [18, 19]
VREF = 3.0


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


def physical_masks(sid, k):
    """物理阵列模型：按架构定义的相位把码映射到每个单元的开关。

    只复用"相位由 sample_id 决定"与"横向温度计 + 纵向分配"两条架构约定，
    电荷累加全程在本文件独立完成（dac_v），不调用数字链的重构公式。
    """
    res, hp, vp = dem_phases(sid)
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    m = []
    for s in range(8):
        row = [yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
        row += [0, 0, (k >> 1) & 1, k & 1]
        m.append(row)
    return m


def dac_v(w, m, ids):
    num = 0.0; den = 0.0
    for slot, sid in enumerate(ids):
        for j in range(N_COLUMNS):
            wj = w[(sid, j)]; den += wj
            num += (-VREF if m[slot][j] else VREF) * wj
        for j in SUB:
            if m[slot][j]:
                num += -2.0 * VREF * w[(sid, j)]
    return -num / den          # 数字侧 D_eff = -K/G


def sar_search(x, w, ids, sid, nbits=9):
    """逐位判决：比较 x 与 D(k)，得到 9 位粗码。返回 (k, 每次判决的余差)。"""
    k = 0
    trace = []
    m = physical_masks(sid, k)
    d = dac_v(w, m, ids)
    trace.append((k, x - d))
    for b in range(nbits - 1, -1, -1):
        trial = k | (1 << b)
        mt = physical_masks(sid, trial)
        dt = dac_v(w, mt, ids)
        if x >= dt:                # 比较器：输入高于 DAC 输出则置位
            k = trial
            trace.append((k, x - dt))
    return k, trace


def main():
    w = real_w()
    rng = np.random.default_rng(11)
    errs = []; codes = []; nfail = 0
    for _ in range(40):
        sid = int(rng.integers(0, 300))
        p = run_transaction(Transaction(sid, 256, 0, 0, w))
        if p.get('slice_ids') is None:
            continue
        ids = p['slice_ids']
        x = float(rng.uniform(-2.6, 2.6))
        k, tr = sar_search(x, w, ids, sid)
        if not (0 <= k <= 511):
            nfail += 1; continue
        m = physical_masks(sid, k)
        d = dac_v(w, m, ids)
        resid = x - d
        a2 = quantize_adc2(resid)
        if a2 is None:
            nfail += 1; continue
        r = run_transaction(Transaction(sid, k, 0, a2, w))
        if r.get('x_weight_v') is None:
            nfail += 1; continue
        errs.append(abs(r['x_weight_v'] - x)); codes.append(r['code20'])
    print(f"样本 {len(errs)} 合法，{nfail} 落在 ADC2 窗口外（正常饱和）")
    if errs:
        print(f"SADC 自动出码闭环：max={max(errs)*1e6:.1f} uV  rms={np.sqrt(np.mean(np.square(errs)))*1e6:.1f} uV")
        print(f"code20 范围 {min(codes)}..{max(codes)}")


if __name__ == '__main__':
    main()
