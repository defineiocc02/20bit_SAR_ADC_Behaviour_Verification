"""数字算法链：Vin -> 20 位码。一个文件，一条路径。

替代原来的 g2_chain.py + g3_chain.py（两份重叠实现，已删）。
"""
import math, sys

VFS, G, CF = 3.0, 32.0, 16.0
DELTA = 6.0 / 512
SUB = (4, 2, 1, 1)                 # 子阵列 Cu
YMIN, SPAN, N2 = -0.1875, 0.75, 8192
LSB20 = 6.0 / (1 << 20)


def D(c):                          # SADC 与 RDAC 同码相等
    return -VFS + 6.0 * c / 512


def post_mask(k, h, v):
    """k<512: 64H+8V+L；512..520: 主阵列全选 + 子阵列。"""
    m = [[0] * 20 for _ in range(8)]
    if k >= 512:
        r = k - 512
        sub = [1, 1, 1, 1] if r >= 8 else [(r >> 2) & 1, (r >> 1) & 1, r & 1, 0]
        for s in range(8):
            m[s][:16] = [1] * 16
            m[s][16:] = sub
        return m
    H, V, L = k // 64, (k % 64) // 8, k % 8
    hw = [(1 if ((i - h) % 8) < H else 0) for i in range(8)]
    yw = [(1 if ((i - v) % 8) < V else 0) for i in range(8)]
    res = (7 + h) % 8
    for s in range(8):
        for u in range(8):
            b = yw[s] if u == res else hw[u]
            m[s][2 * u] = m[s][2 * u + 1] = b
        for t in range(3):
            m[s][16 + t] = (L >> (2 - t)) & 1
    return m


def charge(m):                     # 单位 1/8 Cu
    return sum(m[s][j] * 32 for s in range(8) for j in range(16)) + \
           sum(m[s][16 + t] * SUB[t] for s in range(8) for t in range(4))


def split(word):
    d = [(word >> i) & 1 for i in range(6)]          # d0..d5
    U = 4 * d[5] + d[3]
    Ld = 2 * d[4] + (4 * d[2] + 2 * d[1] + d[0]) / 8.0
    return U, Ld, U + Ld, 4 * d[2] + 2 * d[1] + d[0]


def pre(word, v):
    """pre_value[s] = J + ((s-v)%8 < f)，展开成 4 个开关位。"""
    U, Ld, T, f = split(word)
    J = U + 2 * ((word >> 4) & 1)
    bits = []
    for s in range(8):
        val = J + (1 if ((s - v) % 8) < f else 0)
        bits.append([1, 1, 1, 1] if val >= 8 else
                    [(val >> 2) & 1, (val >> 1) & 1, val & 1, 0])
    return bits, T, U, Ld


def sadc(x):
    """返回逐事件已决码。普通 7 个，guard 8 个。"""
    f = max(0, min(7, int(math.floor((x + VFS) / (2 * VFS) * 8))))
    c, ev = f << 6, []
    ev.append(c)
    for b in range(5, -1, -1):
        t = c | (1 << b)
        if x >= D(t):
            c = t
        ev.append(c)
    if c >= 508 and x >= D(512):
        c = 512; ev.append(c)
        for b in (2, 1, 0):
            t = c + (1 << b)
            if x >= D(t):
                c = t
            ev.append(c)
    return c, ev


def convert(x, ctx, cut=0, zero_pre=False, first_last=False):
    """一笔转换。cut>0 表示故意截断事件（故障注入用）。"""
    bits, T, U, Ld = pre(ctx['dither'], ctx['v'])
    if zero_pre:
        bits = [[0] * 4 for _ in range(8)]
    Wp_fwd = sum(bits[s][t] * SUB[t] for s in range(8) for t in range(4)) / 8.0 / CF
    # 故障注入：物理位清零但重构仍用名义 Wp —— 两者必须分开，否则互相抵消
    Wp_rec = (T / CF) if zero_pre else Wp_fwd
    c, ev = sadc(x + Ld * DELTA)
    guard = (c >= 512)                 # 是否走了 guard 解析（与 k=c+U 无关）
    if cut:
        ev = ev[:cut]
    if first_last:
        ev = [ev[0], ev[-1]]
    if not ev:
        return None
    k = ev[-1] + U
    if not (0 <= k <= 520):
        return None
    m = post_mask(k, ctx['h'], ctx['v'])
    y = G * x + 3 * G - 6 * (charge(m) / 8.0 / CF) + 6 * Wp_fwd
    code = int(math.floor((y - YMIN) * N2 / SPAN))
    if not (0 <= code < N2):
        return None
    yh = YMIN + (code + 0.5) * SPAN / N2
    xh = (yh - 3 * G + 6 * (charge(m) / 8.0 / CF) - 6 * Wp_rec) / G
    c20 = max(0, min((1 << 20) - 1, int(round((xh + VFS) / 6.0 * (1 << 20) - 0.5))))
    return dict(k=k, ev=ev, xh=xh, c20=c20, guard=guard,
                ev_ok=(len(ev) == (11 if guard else 7)))


def run(n=256, cut=0, zero_pre=False, first_last=False):
    st = dict(n=0, ok=0, bad_ev=0, e_flt=0.0, e_c20=0)
    for i in range(n):
        x = -VFS + 6.0 * i / n
        r = convert(x, dict(h=i % 8, v=(i // 8) % 8, dither=(i * 7) % 64),
                    cut=cut, zero_pre=zero_pre, first_last=first_last)
        st['n'] += 1
        if r is None:
            continue
        st['ok'] += 1
        if not r['ev_ok']:
            st['bad_ev'] += 1
        st['e_flt'] = max(st['e_flt'], abs(r['xh'] - x))
        w20 = max(0, min((1 << 20) - 1, int(round((x + VFS) / 6.0 * (1 << 20) - 0.5))))
        st['e_c20'] = max(st['e_c20'], abs(r['c20'] - w20))
    return st


def main():
    base = run(256)
    print(f"基线: {base['ok']}/{base['n']}  "
          f"浮点 {base['e_flt']/LSB20:.3f} LSB20  最终码 {base['e_c20']} LSB  "
          f"事件异常 {base['bad_ev']}")
    assert base['ok'] == 256 and base['bad_ev'] == 0, "基线失败"
    assert base['e_flt'] <= 0.5 * LSB20 and base['e_c20'] <= 1, "基线超界"

    for name, kw in (("只留 Flash", dict(cut=1)),
                     ("只留首末", dict(first_last=True)),
                     ("清 pre", dict(zero_pre=True))):
        s = run(256, **kw)
        bad = s['bad_ev'] or s['e_flt'] > 0.5 * LSB20 or s['e_c20'] > 1 or s['ok'] < 256
        print(f"  {name:10s}: {'捕获' if bad else '漏检(BAD)'}  "
              f"事件异常 {s['bad_ev']}  浮点 {s['e_flt']/LSB20:.1f} LSB")
        assert bad, f"{name} 未被捕获"
    print("PASS")




# ================= G4：电容失配与桥误差（同一路径，不改结构/窗口/极性）=========
def make_w(sigma=0.0, seed=2026):
    """18×20 器件权重（Cu）。sigma=0 即名义。

    桥误差按合同 §3.2 由实际 Cb 与子电容共同求出 beta_s，
    不独立扰动每个子权重。
    """
    import random
    rng = random.Random(seed)
    w = [[0.0] * 20 for _ in range(18)]
    for s in range(18):
        gs = 1.0 + rng.gauss(0, sigma)                  # 片级增益失配
        for j in range(16):
            w[s][j] = 4.0 * gs * (1 + rng.gauss(0, sigma))
        cb = (8.0 / 7.0) * (1 + rng.gauss(0, sigma))    # 桥电容
        csub = [c * (1 + rng.gauss(0, sigma)) for c in SUB]
        beta = cb / (cb + sum(csub))
        for t in range(4):
            w[s][16 + t] = csub[t] * beta
    return w


def _G(w, ids):
    return sum(w[sid][j] for sid in ids for j in range(16))


def _Wm(w, ids, m):
    return sum(w[ids[s]][j] * m[s][j] for s in range(8) for j in range(20))


def _Wp(w, ids, bits):
    return sum(w[ids[s]][16 + t] * bits[s][t] for s in range(8) for t in range(4))


def convert_mm(x, ctx, ids, w_dev, w_L):
    """前向用器件真值 w_dev，重构用加载系数 w_L。两侧只共享 ids/mask/ADC2。"""
    bits, T, U, Ld = pre(ctx['dither'], ctx['v'])
    c, ev = sadc(x + Ld * DELTA)
    guard = (c >= 512)
    k = ev[-1] + U
    if not (0 <= k <= 520):
        return None
    m = post_mask(k, ctx['h'], ctx['v'])
    Gd, Wmd, Wpd = _G(w_dev, ids), _Wm(w_dev, ids, m), _Wp(w_dev, ids, bits)
    D_dev = 3.0 * (2 * Wmd / Gd - 1)
    y = 32.0 * (x + 6 * Wpd / Gd - D_dev)
    code = int(math.floor((y - YMIN) * N2 / SPAN))
    if not (0 <= code < N2):
        return dict(over=True)
    yh = YMIN + (code + 0.5) * SPAN / N2
    GL, WmL, WpL = _G(w_L, ids), _Wm(w_L, ids, m), _Wp(w_L, ids, bits)
    D_hat = 3.0 * (2 * WmL / GL - 1)
    xh = yh / 32.0 + D_hat - 6 * WpL / GL
    c20 = max(0, min((1 << 20) - 1, int(round((xh + VFS) / 6.0 * (1 << 20) - 0.5))))
    return dict(over=False, k=k, xh=xh, c20=c20, ev_ok=(len(ev) == (11 if guard else 7)))


def eval_mm(w_dev, w_L, n=256, sigma_tag=''):
    st = dict(n=0, ok=0, over=0, bad_ev=0, e_flt=0.0, sq=0.0, e_c20=0)
    for i in range(n):
        x = -VFS + 6.0 * i / n
        ids = list(range(8))
        r = convert_mm(x, dict(h=i % 8, v=(i // 8) % 8, dither=(i * 7) % 64),
                       ids, w_dev, w_L)
        st['n'] += 1
        if r is None or r['over']:
            st['over'] += 1
            continue
        st['ok'] += 1
        if not r['ev_ok']:
            st['bad_ev'] += 1
        e = abs(r['xh'] - x)
        st['e_flt'] = max(st['e_flt'], e)
        st['sq'] += e * e
        w20 = max(0, min((1 << 20) - 1, int(round((x + VFS) / 6.0 * (1 << 20) - 0.5))))
        st['e_c20'] = max(st['e_c20'], abs(r['c20'] - w20))
    st['rms'] = (st['sq'] / max(st['ok'], 1)) ** 0.5
    return st


def g4_main():
    w_nom = make_w(0.0)
    w_dev = make_w(0.01)                     # 1% 失配
    cases = [("名义器件 + 名义系数", w_nom, w_nom),
             ("失配器件 + 名义系数", w_dev, w_nom),
             ("失配器件 + 正确系数", w_dev, w_dev)]
    print(f"{'场景':22s} {'ok':>4s} {'越窗':>4s} {'max':>9s} {'rms':>8s} {'码':>5s}")
    for name, wd, wl in cases:
        s = eval_mm(wd, wl)
        print(f"{name:22s} {s['ok']:4d} {s['over']:4d} "
              f"{s['e_flt']/LSB20:8.2f}L {s['rms']/LSB20:7.2f}L {s['e_c20']:5d}")
    a = eval_mm(w_nom, w_nom); b = eval_mm(w_dev, w_nom); c = eval_mm(w_dev, w_dev)
    assert a['e_flt'] <= 0.5 * LSB20, "名义退化"
    assert b['e_flt'] > 10 * a['e_flt'], "失配未体现"
    assert c['e_flt'] <= 0.5 * LSB20, "校正无效"
    print("G4 PASS  未校正/校正后 = "
          f"{b['e_flt']/c['e_flt']:.1f}x")


if __name__ == '__main__':
    main()
    g4_main()
