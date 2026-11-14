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


from adi_model.dem_dither import post_mask, bridge_swap, pre, dither_split  # noqa: E402


def charge(m):                     # 单位 1/8 Cu
    return sum(m[s][j] * 32 for s in range(8) for j in range(16)) + \
           sum(m[s][16 + t] * SUB[t] for s in range(8) for t in range(4))


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


def fwd_dac(w_dev, ids, m, bits):
    """前向：从物理电容网络直接算 RDAC 输出与采样扰动量。不调用重构公式。"""
    q_main = 0.0
    q_sw = 0.0
    q_pre = 0.0
    for s_ in range(8):
        sid = ids[s_]
        for j in range(16):
            q_main += w_dev[sid][j]              # 全部主单元都采样 Vin
        for j in range(20):                      # 开关电荷含子阵列
            if m[s_][j]:
                q_sw += w_dev[sid][j]
        for t in range(4):
            if bits[s_][t]:
                q_pre += w_dev[sid][16 + t]
    return q_main, q_sw, q_pre


def dig_recon(y, G, Wm, Wp):
    """数字：只用加载系数与观测量反解。与前向不共享任何中间量。"""
    return (y - 3 * G + 6 * Wm - 6 * Wp) / G


def _G(w, ids):
    return sum(w[sid][j] for sid in ids for j in range(16)) / CF


def _Wm(w, ids, m):
    return sum(w[ids[s]][j] * m[s][j] for s in range(8) for j in range(20)) / CF


def _Wp(w, ids, bits):
    return sum(w[ids[s]][16 + t] * bits[s][t] for s in range(8) for t in range(4)) / CF


def convert_mm(x, ctx, ids, w_dev, w_L):
    """前向用器件真值，重构用加载系数。两侧只共享 ids / mask / ADC2 码。

    RA 增益 = 采样总电容 / 反馈电容 = 实际 G（不是固定 32）。
    """
    bits, T, U, Ld = pre(ctx['dither'], ctx['v'])
    c, ev = sadc(x + Ld * DELTA)
    guard = (c >= 512)
    k = ev[-1] + U
    if not (0 <= k <= 520):
        return None
    m = bridge_swap(post_mask(k, ctx['h'], ctx['v']),
                    ctx['h'], ctx['v'], ctx.get('acq_seq', 0), ctx.get('coin', 0))
    # ---- 前向：物理网络
    q_main, q_sw, q_pre = fwd_dac(w_dev, ids, m, bits)
    G_dev = q_main / CF
    D_dev = 3.0 * (2 * q_sw / q_main - 1)
    xeff = x + 6.0 * q_pre / q_main
    y = G_dev * (xeff - D_dev)                 # 增益就是实际 G
    code = int(math.floor((y - YMIN) * N2 / SPAN))
    if not (0 <= code < N2):
        return dict(over=True)
    yh = YMIN + (code + 0.5) * SPAN / N2
    # ---- 数字：加载系数
    GL, WmL, WpL = _G(w_L, ids), _Wm(w_L, ids, m), _Wp(w_L, ids, bits)
    xh = dig_recon(yh, GL, WmL, WpL)
    c20 = max(0, min((1 << 20) - 1, int(round((xh + VFS) / 6.0 * (1 << 20) - 0.5))))
    return dict(over=False, k=k, xh=xh, c20=c20, G=G_dev,
                ev_ok=(len(ev) == (11 if guard else 7)))


def eval_mm(w_dev, w_L, n=256, dem=True, dith=True):
    """真实 18 片池推进；DEM/Dither 可关。"""
    st = dict(n=0, ok=0, over=0, bad_ev=0, e_flt=0.0, sq=0.0, e_c20=0, gdev=[])
    rng = 0xACE1
    A, C, I = list(range(8)), list(range(8, 16)), [16, 17]
    for i in range(n):
        x = -VFS + 6.0 * i / n
        ids = list(A)
        h = (i % 8) if dem else 0
        v = ((i // 8) % 8) if dem else 0
        word = ((i * 7) % 64) if dith else 0
        r = convert_mm(x, dict(h=h, v=v, dither=word,
                               acq_seq=i & 15, coin=i & 1),
                       ids, w_dev, w_L)
        # 池推进：C'=A，A'⊂C∪I
        avail = C + I
        rng ^= (rng << 13) & 0xFFFFFFFF; rng ^= rng >> 17; rng ^= (rng << 5) & 0xFFFFFFFF
        r1 = rng & 0xFFFFFFFF
        rng ^= (rng << 13) & 0xFFFFFFFF; rng ^= rng >> 17; rng ^= (rng << 5) & 0xFFFFFFFF
        r2 = rng & 0xFFFFFFFF
        i1 = ((r1 & 0xFFFF) * 10) >> 16
        j0 = ((r2 >> 16) * 9) >> 16
        i2 = min(j0 + (1 if j0 >= i1 else 0), 9)
        drop = {avail[i1], avail[i2]}
        A, C, I = [z for z in avail if z not in drop], list(A), \
                 [z for z in avail if z in drop]
        st['n'] += 1
        if r is None or r['over']:
            st['over'] += 1
            continue
        st['gdev'].append(r['G'])
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




# ================= G6：DEM / Dither 启停 × 校准前后 =================
def eval_sine(w_dev, w_L, n=1024, fin=127, amp=1.5, dem=True, dith=True):
    """正弦输入。注意：这里算的是**误差谱**，sig 是信号 bin 上的误差分量，
    不是输出谱的 SFDR。列名按实际含义标注，不冒充 SFDR。"""
    import cmath
    xs, es = [], []
    rng = 0xACE1
    A, C, I = list(range(8)), list(range(8, 16)), [16, 17]
    for i in range(n):
        x = amp * math.sin(2 * math.pi * fin * i / n)
        h = (i % 8) if dem else 0
        v = ((i // 8) % 8) if dem else 0
        word = ((i * 7) % 64) if dith else 0
        r = convert_mm(x, dict(h=h, v=v, dither=word, acq_seq=i & 15, coin=i & 1),
                       list(A), w_dev, w_L)
        if r is None or r['over']:
            continue
        xs.append(x); es.append(r['xh'] - x)
        avail = C + I
        rng ^= (rng << 13) & 0xFFFFFFFF; rng ^= rng >> 17; rng ^= (rng << 5) & 0xFFFFFFFF
        r1 = rng & 0xFFFFFFFF
        rng ^= (rng << 13) & 0xFFFFFFFF; rng ^= rng >> 17; rng ^= (rng << 5) & 0xFFFFFFFF
        r2 = rng & 0xFFFFFFFF
        i1 = ((r1 & 0xFFFF) * 10) >> 16
        j0 = ((r2 >> 16) * 9) >> 16
        i2 = min(j0 + (1 if j0 >= i1 else 0), 9)
        drop = {avail[i1], avail[i2]}
        # 合同 §4：C_{n+1}=A_n；A_{n+1}=new_acq ⊂ C_n∪I_n
        A, C, I = [z for z in avail if z not in drop], list(A), [z for z in avail if z in drop]
    if len(es) < n // 2:
        return None
    e = [0.0] * n
    for i, val in enumerate(es[:n]):
        e[i] = val
    F = [abs(sum(e[t] * cmath.exp(-2j * math.pi * k * t / n) for t in range(n))) / n
         for k in range(n // 2)]
    sig = F[fin]
    spur = max(F[k] for k in range(1, n // 2) if abs(k - fin) > 2)
    sfdr = 20 * math.log10(sig / spur) if spur > 0 else 99
    rms = (sum(v * v for v in es) / len(es)) ** 0.5
    return dict(sfdr=sfdr, spur=spur, rms=rms, n=len(es))


def g6_main():
    w_nom = make_w(0.0)
    w_dev = make_w(0.01)
    print(f"{'DEM':>3s} {'Dith':>4s} {'校准':>4s} {'误差谱峰比(dB)':>13s} {'最大杂散L':>9s} {'RMS L':>7s}")
    for dem in (False, True):
        for dith in (False, True):
            for cal, wl in (("关", w_nom), ("开", w_dev)):
                r = eval_sine(w_dev, wl, dem=dem, dith=dith)
                if r is None:
                    print(f"{int(dem):3d} {int(dith):4d} {cal:>4s}      样本不足")
                    continue
                print(f"{int(dem):3d} {int(dith):4d} {cal:>4s} {r['sfdr']:9.2f} "
                      f"{r['spur']/LSB20:10.1f} {r['rms']/LSB20:9.1f}")


if __name__ == '__main__':
    main()
    g4_main()
    g6_main()
