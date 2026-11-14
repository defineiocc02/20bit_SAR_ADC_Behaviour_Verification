"""唯一系统级入口：V_IN → SADC → Follower/DEM → RDAC → RA → ADC2 → D5 → D_OUT

每一级的输出都由前一级真实产生：
  - 粗码由比较器逐位判决得到，**不调用 ResolvedPrefixes.from_final()**
  - 余差由真实电容电荷算出，**不由 D5 的 dac_hw 反推**
  - ADC2 输入是 RA 的真实输出，不是人工构造
  - D5 只拿到 (k, adc2, dither, 冻结系数)

三个档位只改三件事：
  L1 理想：宽 ADC2（不量化）+ 理想 RA（增益 32，无限带宽）
  L2 真实窗口：ADC2 恢复 0~0.45 V 单边窗口，记录溢出
  L3 非理想：RA 动态/参考建立/噪声 —— 见 UNIFIED.md 的 NOT_RUN 说明
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, _dither_terms, N_COLUMNS)

Q = 1 << 30
VFS = 3.0
DELTA = 3.0 / 256.0
RA_GAIN = 32.0
ADC2_LSB = 0.45 / 4096
ADC2_MAX = 0.45
FOLLOWER_EVENTS = 9          # 8 位温度计 + 1 位子阵列，逐位判决


# ---------------------------------------------------------------- 制造实例
def make_device(seed=2026, gain=0.01, main=0.015, sub=0.025):
    r = np.random.default_rng(seed)
    w = {}
    for sid in range(18):
        g = 1.0 + r.normal(0, gain)
        for j in range(N_COLUMNS):
            w[(sid, j)] = 0.25 * g * (1 + r.normal(0, main))
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(sid, 16 + i)] = (wt / 8) * g * (1 + r.normal(0, sub))
    return {k: int(round(v * Q)) for k, v in w.items()}


def nominal_device():
    w = {}
    for s in range(18):
        for j in range(N_COLUMNS):
            w[(s, j)] = int(0.25 * Q)
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(s, 16 + i)] = int(wt * Q / 8)
    return w


# ---------------------------------------------------------------- RDAC 电荷
class RDElement:
    """物理电容阵列：按真实权重与开关算输出电压。"""

    def __init__(self, w):
        self.w = w
        self._cache = {}

    def ids(self, group):
        p = run_transaction(Transaction(0, 256, 0, 0, self.w), slice_group=group)
        return p['slice_ids']

    def vout(self, group, phases, k):
        key = (group, phases, k)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        res, hp, vp = phases
        h = _horizontal_columns(k, res, hp)
        yb = vertical_bits(k, 8, vp)
        ids = self.ids(group)
        num = den = 0.0
        for s, sid in enumerate(ids):
            for j in range(N_COLUMNS):
                wj = self.w[(sid, j)]
                den += wj
                m = yb[s] if j == res else h[j]
                num += (-VFS if m else VFS) * wj
            if (k >> 1) & 1:
                num += -2.0 * VFS * self.w[(sid, 18)]
            if k & 1:
                num += -2.0 * VFS * self.w[(sid, 19)]
        self._cache[key] = -num / den
        return self._cache[key]


# ---------------------------------------------------------------- SADC + Follower
def sadc_and_follower(vin, rdac, group, phases):
    """比较器逐位判决；每个事件立刻用当前已决前缀驱动 RDAC。

    返回 (k, 事件轨迹)。**不使用最终码反推前缀。**
    """
    k = 0
    timeline = [(0, rdac.vout(group, phases, 0))]   # 事件 0：全零码
    for b in range(FOLLOWER_EVENTS - 1, -1, -1):
        trial = k | (1 << b)
        d = rdac.vout(group, phases, trial)          # 比较器看到的是"候选码下的真实 RDAC 输出"
        if vin >= d:
            k = trial
        timeline.append((k, rdac.vout(group, phases, k)))  # 该事件后 RDAC 实际落脚点
    return k, timeline


# ---------------------------------------------------------------- 主转换
def convert(vin, rdac, coef, group, sample_id, word=0,
            tier=2, ra=None, rng=None, wide=False):
    """一次完整转换。tier 只影响 ADC2 与 RA 的理想程度。"""
    if rng is None:
        rng = np.random.default_rng(sample_id)
    phases = dem_phases(sample_id)
    u, l, t = _dither_terms(word)

    # 1) SADC + Follower：粗码由比较器逐位得出
    k, timeline = sadc_and_follower(vin, rdac, group, phases)

    # 2) RDAC 实际输出 → 余差（由真实电荷产生，不由 D5 反推）
    d_real = rdac.vout(group, phases, k)
    resid = vin - d_real

    # 3) RA
    if tier == 1 or ra is None:
        ra_out = resid * RA_GAIN
    else:
        arr, _sat = ra.evaluate(np.array([resid]), rng, RA_GAIN)
        ra_out = float(arr[0])

    # 4) ADC2
    if wide:
        a2 = None                      # 宽窗口诊断：不量化，直接给余差
        r_obs = ra_out / RA_GAIN
    else:
        if ra_out < 0.0 or ra_out > ADC2_MAX:
            return dict(status="overflow", r=resid, ra=ra_out, k=k,
                        timeline=timeline, code=None)
        a2 = int(np.floor(ra_out / ADC2_LSB))
        if a2 >= 4096:
            a2 = 4095
        r_obs = (a2 + 0.5) * ADC2_LSB / RA_GAIN

    # 5) D5：只拿 k / adc2 / dither / 冻结系数
    if wide:
        # 诊断档直接用理想余差代替 ADC2 码（明确标注非硬件路径）
        r = run_transaction(Transaction(sample_id, k + u, word, 0, coef),
                            slice_group=group, phases=phases)
        d_nom = r['dac_hw_q24'] / float(1 << 24) if r.get('dac_hw_q24') is not None else None
        if d_nom is None:
            return dict(status="illegal", r=resid, ra=ra_out, k=k, timeline=timeline, code=None)
        x_hat = d_nom + r_obs + t * DELTA
        code = int(round((x_hat / VFS + 1.0) * (1 << 19)))
    else:
        r = run_transaction(Transaction(sample_id, k + u, word, a2, coef),
                            slice_group=group, phases=phases)
        if r.get('code20') is None:
            return dict(status="illegal", r=resid, ra=ra_out, k=k, timeline=timeline, code=None)
        x_hat = r['x_weight_v']
        code = r['code20']
    return dict(status="ok", r=resid, ra=ra_out, k=k, timeline=timeline,
                code=code, x_hat=x_hat, d_real=d_real)


def run_block(vin_list, device, coef, tier=2, wide=False, ra=None, words=None):
    rdac = RDElement(device)
    out = []
    for n, vin in enumerate(vin_list):
        word = 0 if words is None else words[n % len(words)]
        out.append(convert(vin, rdac, coef, n % 9, n, word=word,
                           tier=tier, ra=ra, wide=wide))
    return out
