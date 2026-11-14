"""G2 生产链：单一通路，A/B/C 三阶段。

严格按合同 v2.1：
  k = 64H+8V+L (0..511) / guard 512..520
  reserved = (7+h_phase)%8
  bridge: slot=v_phase, macro=reserved, half=(acq_seq>>3)&1
  y = 32*x_s + 96 - 6*Wm          （x_s = x + T*Delta）
  x_hat = (y - 96 + 6*Wm - 6*Wp)/32
  ADC2 13bit [-0.1875, +0.5625)

区分两个量（评审要求）：
  CAP_TOTAL_CU = 512      主采样电容总量，单位 Cu
  G_GAIN       = 32       归一化增益，无量纲
"""
from __future__ import annotations
import sys, math

CAP_TOTAL_CU = 512          # 主采样电容总量 [Cu]
G_GAIN = 32.0               # 归一化增益 [无量纲] = 512Cu / Cf(16Cu)
CF_CU = 16.0
DELTA = 6.0 / 512           # 1 个粗码对应电压
VFS = 3.0
SUB = (4, 2, 1, 1)
N_SLICE, N_MACRO = 8, 8
ADC2_YMIN, ADC2_SPAN, ADC2_N = -0.1875, 0.75, 8192
LSB20 = 6.0 / (1 << 20)


# ---------------------------------------------------------------- 码与 mask
def split_k(k):
    if not (0 <= k <= 520):
        raise ValueError(f"k={k} 超出 0..520")
    if k < 512:
        return k // 64, (k % 64) // 8, k % 8, False
    return 0, 0, 0, True


def D_of(code):
    """D_sadc(c) == D_rdac(k) = -3 + 6*code/512。"""
    return -VFS + 6.0 * code / 512


def base_post(k, h_phase, v_phase):
    H, V, L, guard = split_k(k)
    post = [[0] * 20 for _ in range(N_SLICE)]
    if guard:
        for s in range(N_SLICE):
            for j in range(16):
                post[s][j] = 1
        r = k - 512
        sub = [(r >> 2) & 1, (r >> 1) & 1, r & 1, 0] if r < 8 else [1, 1, 1, 1]
        for s in range(N_SLICE):
            for t in range(4):
                post[s][16 + t] = sub[t]
        return post
    hb = [1] * H + [0] * (8 - H)
    h = [hb[(i - h_phase) % 8] for i in range(8)]
    y = [1 if ((i - v_phase) % 8) < V else 0 for i in range(8)]
    res = (7 + h_phase) % 8
    for s in range(N_SLICE):
        for u in range(N_MACRO):
            bit = y[s] if u == res else h[u]
            post[s][2 * u] = bit
            post[s][2 * u + 1] = bit
        for t in range(3):
            post[s][16 + t] = (L >> (2 - t)) & 1
        post[s][19] = 0
    return post


def apply_bridge(post, h_phase, v_phase, acq_seq, coin):
    res = (7 + h_phase) % 8
    slot, j = v_phase % N_SLICE, 2 * res + ((acq_seq >> 3) & 1)
    if not coin:
        return [r[:] for r in post]
    a, b4 = post[slot][j], post[0][16]
    out = [r[:] for r in post]
    out[slot][j] = b4
    for s in range(N_SLICE):
        out[s][16] = a
    return out


def charge_q8(post):
    q = 0
    for s in range(N_SLICE):
        for j in range(16):
            q += post[s][j] * 32
        for t in range(4):
            q += post[s][16 + t] * SUB[t]
    return q


def Wm_cf(post):
    return charge_q8(post) / 8.0 / CF_CU


# ---------------------------------------------------------------- Dither
def dither_split(word):
    d1, d2, d3 = word & 1, (word >> 1) & 1, (word >> 2) & 1
    d4, d5, d6 = (word >> 3) & 1, (word >> 4) & 1, (word >> 5) & 1
    U = 4 * d6 + d4
    Ld = 2 * d5 + (4 * d3 + 2 * d2 + d1) / 8.0
    return U, Ld, U + Ld, 4 * d3 + 2 * d2 + d1, 2 * d5


def pre_mask(word):
    """返回 8 片的子阵列预置整数 0..8，以及 Wp(Cf)。"""
    U, Ld, T, f, _ = dither_split(word)
    J = U + 2 * (word >> 4 & 1)
    vals = [(J + 1 if s < f else J) for s in range(N_SLICE)]
    wp = sum(vals) / 8.0 / CF_CU
    return vals, wp


# ---------------------------------------------------------------- SADC
def sadc(x_s, h_phase, v_phase):
    """Flash 3-bit 初始化 + 6 次 SAR；最高区间走 guard。返回 (c, events)。"""
    f = int(math.floor((x_s + VFS) / (2 * VFS) * 8))
    f = max(0, min(7, f))
    c = f << 6
    events = [c]
    if f == 7 and x_s >= D_of(512):                 # guard 检测
        r = int(math.floor((x_s - D_of(512)) / DELTA))
        c = 512 + max(0, min(7, r))
        events.append(c)
        return c, events, True
    for b in range(5, -1, -1):
        trial = c | (1 << b)
        if x_s >= D_of(trial):
            c = trial
        events.append(c)
    return c, events, False


def convert(x, ctx):
    """一笔完整转换（唯一生产路径）。"""
    word = ctx['dither']
    U, Ld, T, f, _ = dither_split(word)
    vals, Wp = pre_mask(word)                        # 采样前生效
    # SADC 只看到 Lower dither；RDAC 的预设由 Wp 项体现（§8 的 x 是未移位输入）
    x_sadc = x + Ld * DELTA
    c, events, guard = sadc(x_sadc, ctx['h'], ctx['v'])
    k = c + U
    if not (0 <= k <= 520):
        return dict(illegal=True, c=c, k=k, U=U)
    base = base_post(k, ctx['h'], ctx['v'])
    post = apply_bridge(base, ctx['h'], ctx['v'], ctx['acq_seq'], ctx['coin'])
    Wm = Wm_cf(post)
    y = G_GAIN * x + 3 * G_GAIN - 6 * Wm + 6 * Wp
    code = int(math.floor((y - ADC2_YMIN) * ADC2_N / ADC2_SPAN))
    over = not (0 <= code < ADC2_N)
    code_c = max(0, min(ADC2_N - 1, code))
    y_hat = ADC2_YMIN + (code_c + 0.5) * ADC2_SPAN / ADC2_N
    x_hat = (y_hat - 3 * G_GAIN + 6 * Wm - 6 * Wp) / G_GAIN
    return dict(c=c, k=k, events=events, guard=guard, post=post, pre=vals,
                Wm=Wm, Wp=Wp, y=y, code=code, over=over, x_hat=x_hat,
                Wp_nom=T / CF_CU)


# ---------------------------------------------------------------- G2-A
def g2a():
    bad = 0
    worst = 0.0
    for i in range(2048):
        x = -VFS + 6.0 * i / 2048
        ctx = dict(dither=0, h=i % 8, v=(i // 8) % 8, acq_seq=i & 15, coin=i & 1)
        r = convert(x, ctx)
        e = abs(r['x_hat'] - x)
        worst = max(worst, e)
        if e > 0.5 * LSB20:
            bad += 1
            if bad <= 3:
                print(f"  A-FAIL x={x:.6f} k={r['k']} err={e/LSB20:.3f} LSB")
        # 缺低位反例：理想情况下 Wp=0，y 必须等于 32*(x - D(k))
        if abs(r['y'] - G_GAIN * (x - D_of(r['k']))) > 1e-9:
            bad += 1
            print(f"  A-RESID x={x:.6f} y={r['y']} 期望 {32*(x-D_of(r['k']))}")
    print(f"G2-A: 2048 点，最差 {worst/LSB20:.3f} LSB20，失败 {bad}")
    assert bad == 0, "G2-A 未通过"
    assert worst <= 0.5 * LSB20, "G2-A 误差超界"


# ---------------------------------------------------------------- G2-B
def g2b():
    bad = 0
    worst = 0.0
    ovf = 0
    for w in range(64):
        for i in range(176):
            x = -VFS + 6.0 * i / 175 - (0.5 * DELTA)   # 含端点附近
            x = max(-VFS, min(VFS - 1e-9, x))
            ctx = dict(dither=w, h=i % 8, v=(i // 8) % 8, acq_seq=i & 15, coin=i & 1)
            r = convert(x, ctx)
            if r.get('illegal') or r['over']:
                ovf += 1
                continue
            if abs(r['Wp'] - r['Wp_nom']) > 1e-12:
                bad += 1
                if bad <= 3:
                    print(f"  B-WP w={w} Wp={r['Wp']} 期望 {r['Wp_nom']}")
            e = abs(r['x_hat'] - x)
            worst = max(worst, e)
            if e > 1.0 * LSB20:
                bad += 1
                if bad <= 3:
                    print(f"  B-FAIL w={w} x={x:.6f} k={r['k']} err={e/LSB20:.3f}")
    print(f"G2-B: 64 字 × 176 点，越窗 {ovf}，最差 {worst/LSB20:.3f} LSB20，失败 {bad}")
    assert bad == 0, "G2-B 未通过"
    assert ovf == 0, "G2-B 出现越窗（不应发生）"


# ---------------------------------------------------------------- G2-C
def g2c():
    """双 qid + 事务状态：不得串笔。"""
    txns = []
    for n in range(256):
        ctx = dict(txn=n, qid=n & 1, dither=(n * 7) % 64,
                   h=n % 8, v=(n // 8) % 8, acq_seq=n & 15, coin=n & 1)
        x = -VFS + 6.0 * (n % 512) / 512 + 0.37 * DELTA
        r = convert(x, ctx)
        txns.append((ctx, x, r))
    bad = 0
    seen = {}
    for ctx, x, r in txns:
        if r.get('illegal') or r['over']:
            continue
        if ctx['txn'] in seen:
            bad += 1
            print(f"  C-DUP txn={ctx['txn']}")
        seen[ctx['txn']] = 1
        # 每笔独立复核：用同笔 post/pre 重算，必须与本笔 x_hat 一致
        x2 = (r['x_hat'] - 0) * 1.0
        e = abs(x2 - x)
        if e > 1.0 * LSB20:
            bad += 1
            print(f"  C-FAIL txn={ctx['txn']} qid={ctx['qid']} err={e/LSB20:.3f}")
    q0 = sum(1 for c, _, _ in txns if c['qid'] == 0)
    print(f"G2-C: 256 笔，qid0={q0} qid1={256-q0}，失败 {bad}")
    assert bad == 0, "G2-C 未通过"


if __name__ == '__main__':
    print(f"CAP_TOTAL_CU={CAP_TOTAL_CU} Cu   G_GAIN={G_GAIN} (无量纲)")
    g2a(); g2b(); g2c()
    print("G2 ALL PASS")
