"""【负结果，证明 PARTIAL 的成因】Dither 物理作用路径：

结论：把 Lower 做成"前级输入偏移 ΔL = L·3/256"后，冻结的 D5 解码（TΔ=(U+L)·3/256）
**不能抵消它**，同一输入跨 64 字的 x̂ 极差 126 mV。因为 D5 的 TΔ 已含 L，
若 L 又作用于输入，就变成加了两次。要让 L 走物理注入必须改 TΔ，而那是禁止的。

因此 L4 记为：数字 Dither 补偿闭合 PASS，实际物理注入 PARTIAL。

原始说明：
  Lower（D5,D3,D2,D1） -> 前级采样/比较：等效输入偏移 ΔL = L·3/256
  Upper（D6,D4）       -> 粗码偏移 k = c − U，进而改变 RDAC 控制码与物理开关

数字去扰：D5 重构的 TΔ = (U+L)·3/256 把这两项加回。
判据：对同一物理输入 x，遍历 64 个字后恢复出的 x̂ 应一致（差异 ≤ 半 LSB）。
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


def mask_of(sid, k):
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
    return -num / den


def sar(x_eff, w, ids, sid, nbits=9):
    k = 0
    for b in range(nbits - 1, -1, -1):
        t = k | (1 << b)
        if x_eff >= dac_v(w, mask_of(sid, t), ids):
            k = t
    return k


def main():
    w = real_w()
    rng = np.random.default_rng(5)
    worst = 0.0; n = 0; fails = 0
    for _ in range(12):                      # 12 个物理输入
        sid = int(rng.integers(0, 300))
        p = run_transaction(Transaction(sid, 256, 0, 0, w))
        if p.get('slice_ids') is None:
            continue
        ids = p['slice_ids']
        x = float(rng.uniform(-2.5, 2.5))
        outs = []
        for word in range(64):               # 每个输入遍历 64 个字
            u, l, t = _dither_terms(word)
            x_eff = x + l * 3.0 / 256.0      # Lower 物理作用于前级
            k = sar(x_eff, w, ids, sid)
            if not (0 <= k <= 511):
                fails += 1; continue
            resid = x_eff - dac_v(w, mask_of(sid, k), ids)
            a2 = quantize_adc2(resid)
            if a2 is None:
                fails += 1; continue
            r = run_transaction(Transaction(sid, k + u, word, a2, w))
            if r.get('x_weight_v') is None:
                fails += 1; continue
            outs.append(r['x_weight_v'])
            n += 1
        if len(outs) > 1:
            worst = max(worst, max(outs) - min(outs))
    print(f"样本 {n} 笔（12 输入 x 64 字），窗口外/非法 {fails}")
    print(f"同一输入跨 64 字的 x̂ 极差：max {worst*1e6:.2f} uV（判据 <= 半 LSB 1.717 uV x2）")


if __name__ == '__main__':
    main()
