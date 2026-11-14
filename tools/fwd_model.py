"""独立前向模型（L1：理想电容、无失配、无 Dither）。

与生产链的隔离原则：
  * **不 import run_transaction**，电荷/判决全部在本文件内算
  * 只复用接口常量（N_COLUMNS、VFS、相位表）与物理参数 w
  * 输出的是"观测"（SADC 判决、RDAC 电压、ADC2 码），不是重构结果

后续 L2/L3/L4/L5 逐步接入失配、校准、DEM、Dither。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

VFS = 3.0
N_COL = 16
N_ACT = 8


def mask_from_k(k, reserved, h_phase, v_phase):
    """与 dem_shuffle 同构的 mask（本文件独立实现，不 import 生产函数）。"""
    H = (k >> 5) & 0xF
    M = (k >> 2) & 7
    data = [c for c in range(N_COL) if c != reserved]
    st = h_phase % len(data)
    h = [0] * N_COL
    for i in range(H):
        h[data[(st + i) % len(data)]] = 1
    y = [0] * N_ACT
    for i in range(M):
        y[(v_phase + i) % N_ACT] = 1
    return [[y[s] if j == reserved else h[j] for j in range(N_COL)] for s in range(N_ACT)]


def rdac_voltage(w, ids, mask):
    """真实 RDAC 差分输出（由电容电荷直接算，不用任何重构式）。"""
    num = den = 0.0
    for sl, sid in enumerate(ids):
        for j in range(N_COL):
            wj = w[(sid, j)]
            den += wj
            num += (-VFS if mask[sl][j] else VFS) * wj
    return -num / den, den


def sadc_decide(vin, w_sadc, ids_sadc, phases, nbits=9):
    """SADC 逐位比较：比较器只看**自己的** sDAC 电荷输出。

    返回 (k, 事件序列)。事件 i 的码只依赖此前已决位 —— 因果性由此保证。
    """
    k = 0
    trace = []
    for b in range(nbits - 1, -1, -1):
        trial = k | (1 << b)
        m = mask_from_k(trial, *phases)
        v, _ = rdac_voltage(w_sadc, ids_sadc, m)
        if vin >= v:
            k = trial
        trace.append(k)
    return k, trace


def convert(vin, w_real, w_sadc, ids, phases, dither=0.0):
    """一次完整转换。返回观测字典（不返回重构码）。"""
    # 1) SADC 判决（用 sDAC 自己的权重，与 RDAC 分离）
    k, ev = sadc_decide(vin + dither, w_sadc, ids, phases)
    # 2) DEM mask（由 SADC 已决码驱动）
    m = mask_from_k(k, *phases)
    # 3) RDAC 真实输出（用 RDAC 的真实权重）
    v_rdac, g = rdac_voltage(w_real, ids, m)
    # 4) 余差
    r = vin - v_rdac
    return dict(k=k, events=ev, mask=m, v_rdac=v_rdac, g=g, resid=r)


def ideal_w(scale=1.0):
    w = {}
    for s in range(18):
        for j in range(N_COL):
            w[(s, j)] = 0.25 * scale
    return w


if __name__ == '__main__':
    w = ideal_w()
    ids = list(range(8))
    vin = 0.1
    out = convert(vin, w, w, ids, (0, 0, 0))
    LSB = 6.0 / (1 << 20)
    print(f"Vin={vin}  k={out['k']}  V_rdac={out['v_rdac']:.6f}  余差={out['resid']*1e3:.3f} mV")
    print(f"20 位 LSB={LSB*1e6:.2f} uV  余差/LSB={out['resid']/LSB:.1f}")
    print(f"事件数={len(out['events'])}  因果性: 每个事件只依赖已决位")


def full_check(vin, w_real, w_sadc, ids, phases, coef, adc2_max=0.45, adc2_lsb=0.45/4096, gain=32.0):
    """L1 全链路：前向观测 → D5 生产重构 → 与 Vin 比。

    注意：run_transaction 只在**解码**阶段使用，不参与观测生成。
    """
    from adi_model.digital_chain import Transaction, run_transaction
    obs = convert(vin, w_real, w_sadc, ids, phases)
    ra = obs['resid'] * gain
    if ra < 0 or ra > adc2_max:
        return dict(status='overflow', obs=obs)
    a2 = min(int(ra / adc2_lsb), 4095)
    r = run_transaction(Transaction(0, obs['k'], 0, a2, coef),
                        slice_ids=ids, phases=phases)
    if r.get('code20') is None:
        return dict(status='illegal', obs=obs)
    return dict(status='ok', obs=obs, code=r['code20'],
                err_v=(r['code20'] / (1 << 19) - 1) * 3.0 - vin)
