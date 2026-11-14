"""【WIP / 未通过】Python 系统级闭环：独立前向（电容电荷）→ ADC2 → D5 重构。

当前状态：**未收敛，不可作为证据**。
- 符号约定已定（前向电荷极性取反 = 数字 D_eff）
- 但残差基线仍有 ~56 mV 偏差（疑似 ADC2 中值偏置与 TΔ 的口径未对齐）
- A/B 两侧误差相同（32 mV rms），说明校正效果尚未体现
下一步：把 ADC2 中值 r̂(2048)=7.03 mV 的偏置显式扣掉，再核对 TΔ 单位。

原始说明：独立前向（电容电荷）→ ADC2 → D5 数字重构 → 20 位码。

数字侧只拿 k / adc2 / dither / 冻结系数；真实权重只进前向模型。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from forward_model import dac_from_connection, quantize_adc2, ADC2_LSB, RA_GAIN
from adi_model.digital_chain import run_transaction, Transaction, dem_phases, \
    _horizontal_columns, vertical_bits, _dither_terms, N_COLUMNS

Q = 1 << 30
rng = np.random.default_rng(2026)


def make_instance(seed=2026):
    r = np.random.default_rng(seed)
    w = {}
    for sid in range(18):
        g = 1.0 + r.normal(0, 0.01)
        for j in range(N_COLUMNS):
            w[(sid, j)] = 0.25 * g * (1 + r.normal(0, 0.015))
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(sid, 16 + i)] = (wt / 8) * g * (1 + r.normal(0, 0.025))
    return {k: int(round(v * Q)) for k, v in w.items()}


def masks_for(sid, k):
    res, hp, vp = dem_phases(sid)
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    m = []
    for s in range(8):
        row = [yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
        row += [0, 0, (k >> 1) & 1, k & 1]   # 子阵列 j=18/19 用 k[1]/k[0]
        m.append(row)
    return m, res


def main():
    wt = make_instance()
    nom = {}
    for s in range(18):
        for j in range(N_COLUMNS):
            nom[(s, j)] = int(0.25 * Q)
        for i, w2 in enumerate((0.5, 0.25, 0.125, 0.0625)):
            nom[(s, 16 + i)] = int(w2 * Q / 8)
    SUB = [18, 19]
    errA, errB = [], []
    n = 0
    while n < 60:
        sid = int(rng.integers(0, 300)); k = int(rng.integers(0, 512)); word = int(rng.integers(0, 64))
        u, l, t = _dither_terms(word)
        if k + u > 511:
            continue
        # 生产链先给出该码对应的片组与真实 dac（用于确定输入落在哪个粗码）
        p = run_transaction(Transaction(sid, k + u, word, 0, wt))
        if p.get('dac_hw_q24') is None:
            continue
        # 独立前向：从连接逐单元算，目标输入 = dac + TΔ + 余差
        m, res = masks_for(sid, k)   # mask 用内部码 k（final_code - U）
        ids = p['slice_ids']          # 必须用生产链实际片组，不得按公式硬编码
        # 前向按电荷极性算出 RDAC 输出；数字侧 D_eff 取其相反数（D=-K/G）
        x_norm = -dac_from_connection(wt, m, ids, N_COLUMNS, SUB)
        x = x_norm + t * 3.0 / 256.0    # x_norm 已是伏特
        # 残差（相对 RDAC 输出）→ RA → ADC2
        # 取目标余差 0..14.06 mV 内的随机点（单边窗口）
        resid = float(rng.uniform(0.001, 0.013))
        x = x_norm + t * 3.0 / 256.0 + resid
        a2 = quantize_adc2(resid)
        if a2 is None:
            continue
        for w, acc in ((nom, errA), (wt, errB)):
            r = run_transaction(Transaction(sid, k + u, word, a2, w))
            if r.get('x_weight_v') is None:
                continue
            acc.append(abs(r['x_weight_v'] - x))
        n += 1
    for tag, e in (("A 名义", errA), ("B 真系数", errB)):
        if e:
            print(f"{tag}: n={len(e)} max={max(e)*1e6:8.1f} uV rms={np.sqrt(np.mean(np.square(e)))*1e6:7.1f} uV")


if __name__ == '__main__':
    main()
