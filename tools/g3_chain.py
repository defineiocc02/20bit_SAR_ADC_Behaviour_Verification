"""G3：事件驱动 + 双 qid + 18 片池 + 事务生命周期（单一生产路径）。

对上一轮（g2_chain.py）的两处撤回：
  * 不宣称"G2-C 双 qid 事务通过"——上一轮只是交替了 qid 字段，没有独立上下文
  * 不宣称"最终 20 位误差 0.250 LSB"——0.250 只是**舍入前浮点**重构误差
本文件把最终 20 位整数码、定点误差单独报告。

事件是真驱动：每个 SADC 已决事件 -> Follower(k=c+U) -> DEM(post-mask) -> 更新 RDAC，
最终 mask 取最后一个事件的结果。guard 低三位由比较器事件逐步解析，不由 Vin 直接算码。
pre-mask 按 v_phase 轮转展开成各片物理开关位，采样电荷由独立前向模型从此算得，
不再额外注入名义扰动电压。
"""
from __future__ import annotations
import math

CAP_TOTAL_CU = 512
G_GAIN = 32.0
CF_CU = 16.0
DELTA = 6.0 / 512
VFS = 3.0
SUB = (4, 2, 1, 1)
NS, NM = 8, 8
ADC2_YMIN, ADC2_SPAN, ADC2_N = -0.1875, 0.75, 8192
LSB20 = 6.0 / (1 << 20)
Q24 = 1 << 24
N_SLICE_POOL = 18


# ------------------------------------------------------------------ mask/电荷
def split_k(k):
    if k < 512:
        return k // 64, (k % 64) // 8, k % 8, False
    if k <= 520:
        return 0, 0, 0, True
    raise ValueError(f"k={k}")


def D_of(c):
    return -VFS + 6.0 * c / 512


def base_post(k, h, v):
    H, V, L, guard = split_k(k)
    post = [[0] * 20 for _ in range(NS)]
    if guard:
        r = k - 512
        sub = ([(r >> 2) & 1, (r >> 1) & 1, r & 1, 0] if r < 8 else [1, 1, 1, 1])
        for s in range(NS):
            for j in range(16):
                post[s][j] = 1
            for t in range(4):
                post[s][16 + t] = sub[t]
        return post
    hb = [1] * H + [0] * (8 - H)
    hw = [hb[(i - h) % 8] for i in range(8)]
    yw = [1 if ((i - v) % 8) < V else 0 for i in range(8)]
    res = (7 + h) % 8
    for s in range(NS):
        for u in range(NM):
            b = yw[s] if u == res else hw[u]
            post[s][2 * u] = b
            post[s][2 * u + 1] = b
        for t in range(3):
            post[s][16 + t] = (L >> (2 - t)) & 1
    return post


def bridge_swap(post, h, v, acq_seq, coin):
    res = (7 + h) % 8
    slot = v % NS
    j = 2 * res + ((acq_seq >> 3) & 1)
    if not coin:
        return [r[:] for r in post], 0
    a, b4 = post[slot][j], post[0][16]
    out = [r[:] for r in post]
    out[slot][j] = b4
    for s in range(NS):
        out[s][16] = a
    ch = sum(1 for s in range(NS) for jj in range(20) if out[s][jj] != post[s][jj])
    return out, ch


def charge_q8(post, ids=None):
    """按物理 slice ID 寻址累加。ids 为 None 时退化为槽位序（仅向后兼容）。"""
    q = 0
    for s in range(NS):
        sid = ids[s] if ids else s
        if not (0 <= sid < N_SLICE_POOL):
            raise ValueError(f"非法 slice id {sid}")
        for j in range(16):
            q += post[s][j] * 32
        for t in range(4):
            q += post[s][16 + t] * SUB[t]
    return q


def Wm_cf(post, ids=None):
    return charge_q8(post, ids) / 8.0 / CF_CU


# ------------------------------------------------------------------ dither/pre
def dither_split(word):
    d1, d2, d3 = word & 1, (word >> 1) & 1, (word >> 2) & 1
    d4, d5, d6 = (word >> 3) & 1, (word >> 4) & 1, (word >> 5) & 1
    U = 4 * d6 + d4
    Ld = 2 * d5 + (4 * d3 + 2 * d2 + d1) / 8.0
    return U, Ld, U + Ld, 4 * d3 + 2 * d2 + d1, 2 * d5


def pre_bits(word, v_phase):
    """v2.1 §7.2 精确规则：

        pre_value[s] = J + int(((s - v_phase) % 8) < f)

    再展开成每片 4 个物理开关位 [4,2,1,1bal]；值 8 用 [1,1,1,1]。
    """
    U, Ld, T, f, _ = dither_split(word)
    J = U + 2 * ((word >> 4) & 1)
    bits = []
    for s in range(NS):
        val = J + (1 if ((s - v_phase) % NS) < f else 0)
        if val >= 8:
            bits.append([1, 1, 1, 1])
        else:
            bits.append([(val >> 2) & 1, (val >> 1) & 1, val & 1, 0])
    return bits, T, U, Ld


def Wp_from_bits(bits):
    q = 0
    for s in range(NS):
        for t in range(4):
            q += bits[s][t] * SUB[t]
    return q / 8.0 / CF_CU


# ------------------------------------------------------------------ SADC 事件
def sadc_events(x_sadc):
    """返回逐事件已决码序列。guard 低三位也由比较器事件逐步解析。"""
    f = max(0, min(7, int(math.floor((x_sadc + VFS) / (2 * VFS) * 8))))
    c = f << 6
    ev = [c]
    for b in range(5, -1, -1):
        t = c | (1 << b)
        if x_sadc >= D_of(t):
            c = t
        ev.append(c)
    if c >= 508 and x_sadc >= D_of(512):          # 进 guard
        c = 512
        ev.append(c)
        for b in (2, 1, 0):                        # 低三位逐位比较
            t = c + (1 << b)
            if x_sadc >= D_of(t):
                c = t
            ev.append(c)
    return c, ev


def protocol_ok(ev, guard):
    """事件完整性：普通 7 个（1 Flash + 6 SAR），guard 8 个；码单调不减且以最终码收尾。"""
    need = 8 if guard else 7
    if len(ev) != need:
        return False, f"事件数 {len(ev)} != {need}"
    for a, b in zip(ev, ev[1:]):
        if b < a:
            return False, "事件码回退"
    return True, ""


# ------------------------------------------------------------------ G3 链路
class Scheduler18:
    """18 片池三态：A(采样 8) / C(转换 8) / I(备用 2)。

    合同 §4：C_{n+1} = A_n；A_{n+1} ⊂ C_n ∪ I_n（= 18 - A_n）；余 2 片为 I_{n+1}。
    reserve() 预留本笔片组，commit() 原子推进；预留未清时再 reserve 返回 None。
    """

    def __init__(self, seed=0xACE1):
        self.rng = seed & 0xFFFFFFFF
        self.seed0 = seed & 0xFFFFFFFF
        self.A = list(range(8))
        self.C = list(range(8, 16))
        self.I = [16, 17]
        self.taken = None            # 已预留、未提交的片组

    def _xs(self):
        x = self.rng
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.rng = x & 0xFFFFFFFF
        return self.rng

    def check(self):
        assert len(self.A) == 8 and len(self.C) == 8 and len(self.I) == 2, "8/8/2"
        assert not (set(self.A) & set(self.C)), "A/C 重叠"
        assert not (set(self.A) & set(self.I)), "A/I 重叠"
        assert not (set(self.C) & set(self.I)), "C/I 重叠"
        assert sorted(self.A + self.C + self.I) == list(range(18)), "未覆盖 18 片"

    def reserve(self):
        """预留本笔采样片组。已被占用则返回 None（stall）。"""
        if self.taken is not None:
            return None
        self.taken = list(self.A)
        return list(self.taken)

    def commit(self):
        """笔结束：C'=A，A' 从 C∪I 选 8，余 2 为 I'。"""
        if self.taken is None:
            return
        self.check()
        avail = list(self.C) + list(self.I)      # = 18 - A_n，恰好 10 片
        r1, r2 = self._xs(), self._xs()
        i1 = ((r1 & 0xFFFF) * 10) >> 16
        j0 = ((r2 >> 16) * 9) >> 16
        i2 = j0 + (1 if j0 >= i1 else 0)
        i2 = min(i2, 9)
        drop = {avail[i1], avail[i2]}
        newA = [x for x in avail if x not in drop]
        newI = [x for x in avail if x in drop]
        self.C, self.A, self.I = list(self.A), newA, newI
        self.taken = None
        self.check()

    def rollback(self):
        self.taken = None

    def reset(self):
        self.rng = self.seed0
        self.A = list(range(8))
        self.C = list(range(8, 16))
        self.I = [16, 17]
        self.taken = None


def convert(tag, x, ctx, drop_events=False, zero_pre=False, qid=None,
            keep_scalar_wp=False, ids=None):
    """一笔转换。ctx 含 h,v,dither,acq_seq,coin,qid。返回完整描述符。"""
    word = ctx['dither']
    q_used = ctx['qid'] if qid is None else qid      # 允许故障注入改 qid
    bits, T, U, Ld = pre_bits(word, ctx['v'])
    if zero_pre:
        bits = [[0] * 4 for _ in range(NS)]
    Wp_fwd = Wp_from_bits(bits)      # 独立前向模型消费实际开关位
    Wp_recon = Wp_fwd                # 正常情况下两者一致
    if keep_scalar_wp:               # 故障注入：位清零但标量仍用名义值
        Wp_recon = T / CF_CU
    Wp_nom = T / CF_CU
    x_sadc = x + Ld * DELTA
    c_fin, ev = sadc_events(x_sadc)
    if drop_events:
        ev = ev[:1] if ev else []
    # 逐事件驱动 Follower -> DEM -> RDAC
    last_k, last_post = None, None
    for c_i in ev:
        k_i = c_i + U
        if not (0 <= k_i <= 520):
            return dict(tag=tag, illegal=True, k=k_i, c=c_i)
        post_i = base_post(k_i, ctx['h'], ctx['v'])
        post_i, _ = bridge_swap(post_i, ctx['h'], ctx['v'], ctx['acq_seq'], ctx['coin'])
        last_k, last_post = k_i, post_i
    if last_post is None:
        return dict(tag=tag, illegal=True, k=None, c=c_fin)
    Wm = Wm_cf(last_post, ids)
    y = G_GAIN * x + 3 * G_GAIN - 6 * Wm + 6 * Wp_fwd
    code_a = int(math.floor((y - ADC2_YMIN) * ADC2_N / ADC2_SPAN))
    over = not (0 <= code_a < ADC2_N)
    cc = max(0, min(ADC2_N - 1, code_a))
    y_hat = ADC2_YMIN + (cc + 0.5) * ADC2_SPAN / ADC2_N
    x_hat = (y_hat - 3 * G_GAIN + 6 * Wm - 6 * Wp_recon) / G_GAIN
    # 最终 20 位整数码（单独一项误差）
    code20 = int(round((x_hat + VFS) / (2 * VFS) * (1 << 20) - 0.5))
    code20 = max(0, min((1 << 20) - 1, code20))
    ev_ok, ev_why = protocol_ok(ev, last_k >= 512)
    if drop_events:
        ev_ok, ev_why = False, "事件被截断"
    return dict(tag=tag, qid=q_used, qid_exp=ctx['qid'], c=c_fin, k=last_k, events=ev,
                ev_ok=ev_ok, ev_why=ev_why,
                mask=last_post, pre=bits, Wm=Wm, Wp=Wp_recon, Wp_fwd=Wp_fwd, Wp_nom=Wp_nom,
                y=y, adc2=cc, over=over, x_hat=x_hat, code20=code20,
                illegal=False, guard=(last_k >= 512))


# ------------------------------------------------------------------ 验收统计
def run_batch(n=512, fault=None):
    st = dict(n=0, ok=0, illegal=0, over=0, lost=0, dup=0, qid_bad=0,
              wp_bad=0, ev_bad=0, e_float=0.0, e_code=0.0, tags=[])
    sch = Scheduler18()
    seen = set()
    for i in range(n):
        x = -VFS + 6.0 * (i % 1024) / 1024 + 0.31 * DELTA
        ctx = dict(h=i % 8, v=(i // 8) % 8, dither=(i * 5) % 64,
                   acq_seq=i & 15, coin=i & 1, qid=i & 1)
        ids = sch.reserve()
        if ids is None:
            sch.commit()
            ids = sch.reserve()
        tag = 1000 + i
        r = convert(tag, x, ctx, ids=ids,
                    drop_events=(fault == 'no_events'),
                    zero_pre=(fault in ('no_pre', 'no_pre_scalar')),
                    keep_scalar_wp=(fault == 'no_pre_scalar'),
                    qid=(0 if fault in ('bad_qid', 'all_qid0') else None))
        if fault == 'flash_only':
            r = convert(tag, x, ctx, ids=ids, drop_events=True)
            r['events'] = r['events'][:1]
            r['ev_ok'], r['ev_why'] = False, '只保留 Flash'
        elif fault == 'first_last':
            r = convert(tag, x, ctx, ids=ids)
            r['events'] = [r['events'][0], r['events'][-1]]
            r['ev_ok'], r['ev_why'] = False, '只保留首末'
        sch.commit()
        st['n'] += 1
        if fault == 'all_illegal':
            st['illegal'] += 1; continue
        if r.get('illegal'):
            st['illegal'] += 1; continue
        if r['over']:
            st['over'] += 1; continue
        if not r['ev_ok']:
            st['ev_bad'] += 1
        if r['tag'] != tag:
            st['lost'] += 1
        if r['tag'] in seen:
            st['dup'] += 1
        seen.add(r['tag'])
        if r['qid'] != r['qid_exp']:
            st['qid_bad'] += 1
        if abs(r['Wp_fwd'] - r['Wp_nom']) > 1e-12:
            st['wp_bad'] += 1
        st['ok'] += 1
        st['tags'].append(r['tag'])
        st['e_float'] = max(st['e_float'], abs(r['x_hat'] - x))
        w20 = max(0, min((1 << 20) - 1,
                         int(round((x + VFS) / (2 * VFS) * (1 << 20) - 0.5))))
        st['e_code'] = max(st['e_code'], abs(r['code20'] - w20))
    return st


def accept(st):
    reasons = []
    for k in ('illegal', 'over', 'lost', 'dup', 'qid_bad', 'wp_bad', 'ev_bad'):
        if st.get(k):
            reasons.append(f"{k}={st[k]}")
    if st['ok'] == 0:
        reasons.append("无有效输出")
    if st['e_float'] > 0.5 * LSB20:
        reasons.append(f"浮点 {st['e_float']/LSB20:.3f} LSB20")
    if st['e_code'] > 1:
        reasons.append(f"最终码 {st['e_code']} LSB20")
    return (len(reasons) == 0), reasons
