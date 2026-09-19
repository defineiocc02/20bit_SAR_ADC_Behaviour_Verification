"""RTL 位宽镜像的门禁：``sim/ref/recon_rtl_mirror.py`` 必须与黄金模型一致。

为什么需要它
    ``rtl/core/recon_core.sv`` 的每一步都有**显式截断**（SUM_BITS / W_RAIL / W_OP3 /
    W_WIDE / W_S1 / W_SHIFT / W_A）。这些宽度选错时，VCS 仿真往往表现为"大部分样本对、
    少数样本差几十个 LSB"，定位极慢。本文件让宽度选择在**几秒内**就被 Python 侧推翻。

    ``sim/ref/recon_rtl_mirror.py`` 里的宽度必须与 RTL 的 localparam 逐条一致 ——
    改宽度要动两处，这是刻意的（防止单边静默改）。

为什么还要"位级"镜像（2026-09-18 补）
    本文件原来只对拍**位宽**，``div_floor`` 在镜像里直接写成了 ``a // d`` —— 镜像的是
    "应该做什么"，不是"RTL 实际怎么做的"。于是 ``rtl/core/div_floor.sv`` 里
    "一个时钟内 7 级级联、第 s 级的商位该落在本组第几位"这类**位序** bug 天然逃逸：
    真出过一次（``qbits[s]`` 应为 ``qbits[P_STAGES-1-s]``，表现是每个 7 位块内部位序反转，
    而 4 组配置全 PASS）。现在 ``div_floor_bitwise`` 逐位复刻 RTL，并有
    ``test_bitwise_mirror_would_catch_the_group_order_bug`` 证明它**确实**抓得到那个 bug ——
    没有那条，"镜像"就只是又一份自说自话。

判据
    1. 四组 dither 配置下，"掩码和展开式"与模型 ``_terms`` 的定义逐项相等；
    2. ``word`` 与 ``CodeStream.code`` 逐样本相等；
    3. **能变红**：故意把镜像的一个宽度调窄，检查必须失败；
    4. **能变红（位级）**：把组内位序切到历史 bug 的写法，镜像必须与 ``a // d`` 不符；
    5. **反漂移**：镜像里的每个宽度常量都能在 RTL 源码里找到同名 localparam，
       且**表达式**求值后逐条相等（改 RTL 忘了改镜像 = FAIL，不靠人记得）。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MIRROR = REPO / "sim" / "ref" / "recon_rtl_mirror.py"
RTL_CORE = REPO / "rtl" / "core"
DIV_SV = RTL_CORE / "div_floor.sv"
RECON_SV = RTL_CORE / "recon_core.sv"
ADC2_SV = RTL_CORE / "adc2_dec.sv"
PARAMS_VH = REPO / "rtl" / "params" / "rtl_params.vh"


def _load():
    """把镜像脚本作为模块加载（它不在 adi_model 包里，只能走文件路径）。"""
    spec = importlib.util.spec_from_file_location("recon_rtl_mirror", MIRROR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recon_rtl_mirror"] = module
    spec.loader.exec_module(module)
    return module


m = _load()
base = m.Config.paper_literal()


@pytest.mark.parametrize(
    ("cfg", "n", "label"),
    [
        (base, 48, "off"),
        (
            m.replace(
                base,
                dither_mode="quantizer",
                dither_discrete=True,
                dither_amplitude_lsb1=64,
                dem_enable=True,
            ),
            48,
            "quantizer+DEM",
        ),
        (m.replace(base, dither_mode="sampling", dither_discrete=True), 48, "sampling"),
    ],
)
def test_mirror_matches_golden_model(cfg, n, label):
    """四组配置里挑三组（失配那组慢，留给脚本全量跑）逐样本比特相等。"""
    assert m.check(cfg, n, label) == 0


def test_mirror_widths_cover_the_checked_domain():
    """把位宽论证写成可执行的算式，防止有人改宽度时只改注释。"""
    assert m.W_WIDE >= m.ACC + 32, "受检操作数需要 >= ACC+32 位才装得下 op3"
    assert m.W_S1 >= m.ACC + 1, "s1 = num + gain*vscale，|num| < 2^95 时需要 ACC+1 位"
    assert m.W_SHIFT >= m.ACC + m.OUT + 1, "shifted = s1 * count"
    assert m.W_A == m.ACC - (m.V_FRAC + 1), "A = shifted >>> 33 的 63 位取法"
    assert m.W_TOP == m.W_SHIFT - m.ACC + 1, "shifted[116:95] 的位宽"


def test_narrowing_a_width_is_detected():
    """**能变红**：把宽度调窄到实测数据量级以下，检查必须失败。

    顺带记一个**实测事实**（不是猜测）：把 ``W_S1`` 从 97 窄到 95 **不会**让检查变红 ——
    因为契约声明的界是 ``|num| < 2^95``，而真实 ``num`` 只在 ``2^67`` 量级，
    窄 2 位根本咬不到数据。这正是「位宽必须先实测再收缩」（计划 §5.3 / 契约 §8 D3）
    的理由：**声明界与可达范围差着 28 个二进制数量级**，
    所以"够用"必须由实测证明，不能由声明界反推。

    下面两段一起断言，既证明门禁有牙，也把这个差距钉住。
    """
    original_s1, original_shift = m.W_S1, m.W_SHIFT
    cfg = m.replace(base, dither_mode="quantizer", dither_discrete=True)
    try:
        # (a) 窄 2 位：**预期仍然 PASS** —— 声明界远宽于实测范围
        m.W_S1 = original_s1 - 2
        assert m.check(cfg, 48, "narrowed-by-2") == 0, (
            "窄 2 位居然变红了 —— 说明实测峰值已经顶到声明界附近，"
            "D3 的收缩空间比预期小得多，必须重新评估位宽策略"
        )

        # (b) 窄到实测量级以下：**必须 FAIL** —— 否则这个门禁是装饰品
        m.W_SHIFT = 70
        assert (
            m.check(cfg, 48, "narrowed-hard") != 0
        ), "把 W_SHIFT 从 117 压到 70 仍未失败 —— 检查器太松，不能用来守位宽"
    finally:
        m.W_S1 = original_s1
        m.W_SHIFT = original_shift


def test_measured_peaks_are_far_below_the_declared_bounds():
    """把「声明界 vs 实测峰值」的差距量出来（契约 §8 / 计划 §5.3 的 D3 义务）。

    实测值用于判断"96 位累加器能收到多窄"；这里只做**量测与登记**，不做收缩。
    收缩必须另写 ADR 并附新旧差异表。
    """
    import numpy as np

    cfg = m.replace(
        base,
        dither_mode="quantizer",
        dither_discrete=True,
        dither_amplitude_lsb1=64,
        dem_enable=True,
    )
    r = m.run_pipeline(cfg, m.sine_input(0.8 * cfg.v_fs, cfg.fs * 73 / 2048), 64)
    data = m.DigitalObservation.from_result(r)
    model = m.FixedPointReconstructor.from_result(r)
    ids, signal, bmd = m._terms(data, 0, 64)
    inj = np.asarray(data.common_injection_v, dtype=float)
    plus, a_bit, rail_add = m._masks(cfg, data, signal, bmd, inj, 64)

    peak_op, peak_shift = 0, 0
    for i in range(64):
        w = model.weights_q[ids[i]]
        gain = int((w * a_bit[i]).sum())
        total = int(w.sum())
        rails = int(w.sum()) - 2 * int((w * plus[i]).sum()) + int((w * rail_add[i]).sum())
        fine, _ = m.adc2_dec(int(data.adc2_code[i]), model.adc2_min_q, model.adc2_max_q, 12)
        inj_q = int(np.rint(float(inj[i]) / cfg.v_fs * (1 << m.V_FRAC)))
        op1 = (fine - model.offset_q) << m.W_FRAC
        num = op1 - (rails << m.V_FRAC) - total * inj_q
        shifted = (num + (gain << m.V_FRAC)) << m.OUT
        peak_op = max(peak_op, abs(num).bit_length())
        peak_shift = max(peak_shift, abs(shifted).bit_length())

    declared = m.ACC - 1  # 2^95
    assert peak_shift < declared, "实测 shifted 竟超过声明界 —— 契约 §4.2 的界要重算"
    assert peak_shift < m.W_SHIFT, "实测 shifted 装不进 W_SHIFT —— 位宽不够"
    # 记录测量结果（不是断言数值，而是断言"差距存在且被量到"）
    assert peak_op > 0 and peak_shift > peak_op
    print(f"  实测峰值: |num|={peak_op} bit, |shifted|={peak_shift} bit; 声明界={declared} bit")


def test_dividing_by_zero_is_flagged():
    """D == 0 必须报 err 且 q 为 0（结构性错误，不是溢出）。"""
    q, err = m.div_floor(1234, 0)
    assert err is True
    assert q == 0


def test_floor_not_truncation():
    """Floor 与"向 0 截断"必须在负商上可分 —— 这是契约 §3.5 的核心。"""
    assert m.div_floor(-7, 2)[0] == -4  # floor(-3.5) = -4
    assert m.div_floor(7, 2)[0] == 3
    assert m.div_floor(-1, 2)[0] == -1
    # "向 0 截断"会给出 -3、3、0；只要有一个对上就说明镜像退化成了截断
    assert m.div_floor(-1, 2)[0] != 0


def test_ties_to_even_samples_are_reproduced():
    """复现 tests/unit/test_fixed_point.py::test_signed_ties_to_even 的 8 个样例。

    用 ``nbits=12`` 让除数变成 ``2^13``：取 ``delta = 4096*x``、``code = 0``，
    ``fine = min_q + round_even(4096x, 8192) = min_q + round_even(x, 2)``。
    """
    x = (-7, -5, -3, -1, 1, 3, 5, 7)
    expect = (-4, -2, -2, 0, 0, 2, 2, 4)
    got = []
    for xi in x:
        fine, ovf = m.adc2_dec(0, 1000, 1000 + 4096 * xi, 12)
        assert ovf is False
        got.append(fine - 1000)
    assert tuple(got) == expect


# ==========================================================================
# 位级镜像：div_floor 的**算法**（不只是位宽）
# ==========================================================================
def _clamp(value: int, lo: int, hi: int) -> int:
    """把 ``value`` 夹到 ``[lo, hi]``（构造定向用例时要保证可表示）。"""
    return lo if value < lo else hi if value > hi else value


def _case_set(seed: int = 20260918, random_cases: int = 6000) -> list[tuple[int, int]]:
    """定向 + 随机混合的 ``(a, d)``，全部落在 ``P_W_A=63`` 有符号 / ``P_W_D=64`` 无符号的可表示范围内。

    刻意**不**放 ``0x4000_0000_0000_0001``：P_W_A=63 时 bit62 是符号位，这个字面量按有符号
    解读是负数，属于"位模式"用例，单独在指纹测试里处理 —— 混进来会让"``a // d`` 就是期望值"
    这个判据失去意义。
    """
    import random

    lo, hi = -(1 << 62), (1 << 62) - 1
    cases: list[tuple[int, int]] = [
        (0, 1),
        (0, (1 << 64) - 1),
        (1, 1),
        (-1, 1),
        (1, 2),
        (-1, 2),
        (-1, (1 << 60) - 1),
        (lo, 1),  # 最负：|a| 顶到 2^62
        (hi, 1),
        (lo, (1 << 62) - 1),
        (hi, (1 << 60) - 1),
        (-(1 << 60), (1 << 60) - 1),
        (-(1 << 60) - 1, (1 << 60) - 1),
        (lo, (1 << 64) - 1),
    ]
    for k in range(64):
        d = 1 << (k % 64)
        cases += [
            (_clamp(1 << k, lo, hi), min(d, (1 << 64) - 1)),
            (_clamp(-(1 << k), lo, hi), min(1 << (k % 64), (1 << 64) - 1)),
            (_clamp((1 << k) - 1, lo, hi), 1 << ((61 - k) % 62)),
            (_clamp(-(1 << k) - 1, lo, hi), 1 << ((60 - k) % 61)),
        ]
    for k in range(1, 61):
        d = (1 << k) - 1
        cases += [(hi, d), (lo, d), (1, d), (-1, d), (-d, d), (d, d)]
    rng = random.Random(seed)
    for _ in range(random_cases):
        a = rng.randint(lo, hi)
        d = rng.choice(
            [
                rng.randint(1, (1 << 64) - 1),
                rng.randint(1, 1 << 60),
                1 << rng.randint(0, 63),
                rng.randint(1, 1 << 20),
            ]
        )
        cases.append((a, max(1, d)))
    for a, d in cases:
        assert lo <= a <= hi and 1 <= d <= (1 << 64) - 1, (a, d)
    return cases


def test_bitwise_div_floor_matches_floor_for_real():
    """逐位镜像必须与 ``a // d`` **逐值**相等（d > 0），且 d == 0 时 err 且 q == 0。

    判据不是"镜像自己说的"，而是 Python 的 ``//`` —— 对负商它向 -inf 取整，正是契约 §3.5
    要求的 floor 语义。注意这不等于"照抄 RTL 的算子"：RTL 没有 ``//``，只有逐位恢复余数法，
    所以这条等价性本身就是一个**有内容的**断言。
    """
    cases = _case_set()
    for a, d in cases:
        got, err = m.div_floor_bitwise(a, d)
        assert err is False, (a, d)
        assert got == a // d, (a, d, got, a // d)
    assert len(cases) > 6000, "随机 + 定向的总量不能缩水"

    q, err = m.div_floor_bitwise(1234, 0)
    assert err is True and q == 0
    # 负数的 floor 与"向 0 截断"必须可分 —— 否则上面的等价断言可能只是碰巧成立
    assert m.div_floor_bitwise(-7, 2)[0] == -4
    assert m.div_floor_bitwise(-1, 2)[0] == -1
    assert m.div_floor_bitwise(-1, 2)[0] != 0


def _reverse_bit_groups(value: int, width: int, stages: int) -> int:
    """把 ``value`` 的 ``width`` 位按每 ``stages`` 位一组**组内**倒序（低位组在前）。"""
    out = 0
    for lo in range(0, width, stages):
        block = (value >> lo) & ((1 << stages) - 1)
        out |= int(f"{block:0{stages}b}"[::-1], 2) << lo
    return out


def test_bitwise_mirror_would_catch_the_group_order_bug():
    """**能变红（位级）**：切到历史 bug 的写法，镜像必须与真实语义不符。

    这是本组测试里最重要的一条：没有它，``div_floor_bitwise`` 只是又一份"自说自话"。
    历史 bug（``rtl/core/div_floor.sv`` 模块头有记录）：一个时钟内第 ``s`` 级产生的商位
    写成 ``qbits[s]``，应为 ``qbits[P_STAGES-1-s]``，于是**每个 7 位块内部位序反转**。

    关于 ``0x4000_0000_0000_0001`` 这个指纹：``P_W_A=63`` 时 bit62 是**符号位**，所以这个
    位模式按有符号解读是负数（~ ``-(2**62-1)``），除数 ``d = 1``。倒序后商的幅值变成
    ``2**63 - 2**56 - 1`` —— 它**装不进** 63 位有符号端口，所以 RTL 会把符号位截掉。
    这一点本身就是 bug 的指纹之一（模块头"不可能溢出"的论证只在位序正确时成立）。
    """
    a, d = 0x4000000000000001, 1  # TB 就是按 63 位位模式驱动的
    good, err = m.div_floor_bitwise(a, d)
    bad, err_bad = m.div_floor_bitwise(a, d, grouped_bit_order=False)
    assert err is False and err_bad is False
    assert good == m._sgn(a, m.DIV_P_W_A) // d == -(2**62 - 1)
    assert bad != good, "位序写反的镜像给出了同一个商 —— 这个镜像抓不到那个 bug"

    # 差异必须**恰好**是"每组 7 位内部倒序"（这里 d = 1，余数为 0，所以幅值就是商）。
    q_abs_good = -good
    q_abs_bad = _reverse_bit_groups(q_abs_good, m.DIV_W_PAD, m.DIV_P_STAGES)
    assert bad == m._sgn(-q_abs_bad, m.DIV_P_W_A), (
        "位序写反应当得到「每组 7 位内部倒序」的商；"
        f"实测 {bad}，倒序后应为 {m._sgn(-q_abs_bad, m.DIV_P_W_A)}"
    )
    assert q_abs_bad > (
        1 << (m.DIV_P_W_A - 1)
    ), "倒序后的商居然装得进 P_W_A 位有符号 —— 那这条指纹就太弱了，需要换用例"
    # 实测的差异位：恰好是本组（bit 56..62）的最高位与最低位互换 —— 与模块头
    # "Q[62] 与 Q[56] 之一为 1"的记录一致（注意极性：正确解在 bit56，出 bug 时 bit62）。
    flipped = [i for i in range(64) if ((q_abs_good ^ q_abs_bad) >> i) & 1]
    assert flipped == [56, 62], f"预期只有 56/62 两位互换，实测差异位 {flipped}"

    # P_STAGES == 1 时一位一组、没有组内顺序，两种写法**必须**给出同一结果 ——
    # 这解释了为什么 P1 的模块（P_STAGES=1）查不出来，也证明上面的差异确实来自组结构。
    one_good, _ = m.div_floor_bitwise(a, d, P_STAGES=1)
    one_bad, _ = m.div_floor_bitwise(a, d, P_STAGES=1, grouped_bit_order=False)
    assert one_good == one_bad == good

    # 不是孤例：在一批用例上统计两种模式的失配数。正确模式必须零失配。
    cases = _case_set(seed=7)[:1500]
    bad_hits = sum(
        1 for x, y in cases if m.div_floor_bitwise(x, y, grouped_bit_order=False)[0] == x // y
    )
    good_hits = sum(1 for x, y in cases if m.div_floor_bitwise(x, y)[0] == x // y)
    assert good_hits == len(cases), "正确模式出现失配 —— 位级镜像本身写错了"
    exposed = len(cases) - bad_hits
    assert exposed > len(cases) // 4, (
        f"位序 bug 只在 {exposed}/{len(cases)} 个用例上暴露 —— "
        "覆盖面太窄，这个门禁对真实代码的守护力不足"
    )

    # **最强的一条**：把镜像的除法器整体切到写反的位序，整条 recon 镜像的 check()
    # 必须从 0 变成非 0。这正是原始事故的形态 —— 那次 4 组配置全 PASS，
    # 因为镜像里根本没有位级实现。现在它抓得到。
    original_div_floor = m.div_floor

    def buggy_div_floor(x: int, y: int) -> tuple[int, bool]:
        return m.div_floor_bitwise(x, y, grouped_bit_order=False)

    cfg = m.replace(base, dither_mode="quantizer", dither_discrete=True)
    try:
        assert m.check(cfg, 48, "bit order correct") == 0
        m.div_floor = buggy_div_floor
        assert (
            m.check(cfg, 48, "bit order reversed") != 0
        ), "整条镜像在组内位序写反时仍然 PASS —— 说明 check() 没有走到位级除法器上"
    finally:
        m.div_floor = original_div_floor
    assert m.check(cfg, 48, "restored") == 0, "复原后必须重新变绿"
    print(f"  位序 bug 暴露率: {exposed}/{len(cases)}")


# ==========================================================================
# 反漂移门禁：从 RTL 源码抽 localparam 表达式，与镜像常量逐条比对
# ==========================================================================
_VH_SCALAR = re.compile(r"localparam\s*\[[^\]]*\]\s*(\w+)\s*=\s*\d+'d(\d+)\s*;")
_INT_LOCALPARAM = re.compile(r"localparam\s+int\s+(\w+)\s*=\s*([^;]+);")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DIV_PARAM_NAMES = ("P_W_A", "P_W_D", "P_STAGES")


def _char_depths(text: str) -> list[int]:
    """每个字符处的括号深度；``(`` 自身记在**外层**深度上。"""
    depths: list[int] = []
    depth = 0
    for ch in text:
        if ch == ")":
            depth -= 1
        depths.append(depth)
        if ch == "(":
            depth += 1
    return depths


def _replace_innermost_ternary(text: str, original: str) -> str:
    """把 ``text`` 里**最内层**的 ``C ? A : B`` 换成 ``(A if C else B)``。

    刻意不做正则：``((P_W_A > P_W_D) ? P_W_A : P_W_D) + 1`` 这种"条件自己带括号"
    的形状，正则要么漏要么错，而漏掉就会静默地把门禁削掉一层。
    """
    depths = _char_depths(text)
    questions = [i for i, ch in enumerate(text) if ch == "?"]
    depth = max(depths[i] for i in questions)
    q = next(i for i in questions if depths[i] == depth)
    if depth == 0:
        lo, hi = 0, len(text)
    else:
        group_start = next(
            (i for i in range(q, -1, -1) if text[i] == "(" and depths[i] == depth - 1), -1
        )
        if group_start < 0:
            raise AssertionError(f"找不到 {original!r} 里 '?' 的外层括号")
        nesting = 0
        group_end = -1
        for i in range(group_start, len(text)):
            if text[i] == "(":
                nesting += 1
            elif text[i] == ")":
                nesting -= 1
                if nesting == 0:
                    group_end = i
                    break
        if group_end < 0:
            raise AssertionError(f"{original!r} 里的括号不配对")
        lo, hi = group_start + 1, group_end
    inner = text[lo:hi]
    inner_depth = _char_depths(inner)
    rel_q = next(i for i, ch in enumerate(inner) if ch == "?" and inner_depth[i] == 0)
    rel_c = next(
        (i for i, ch in enumerate(inner) if ch == ":" and inner_depth[i] == 0 and i > rel_q), -1
    )
    if rel_c < 0:
        raise AssertionError(f"{original!r} 里的三元表达式缺少同层的 ':'")
    cond = inner[:rel_q].strip()
    yes = inner[rel_q + 1 : rel_c].strip()
    no = inner[rel_c + 1 :].strip()
    if any("?" in part for part in (cond, yes, no)):
        raise AssertionError(f"{original!r} 的三元表达式嵌套过深，门禁需要扩展")
    return f"{text[:lo]}({yes} if {cond} else {no}){text[hi:]}"


def _param_block(text: str) -> str:
    """返回模块 ``#( ... )`` 参数表的原文（括号平衡处收口，注释已排除在外）。

    必须限定在参数表里抽：文件头的说明注释里也会出现 ``P_STAGES=7`` 这类字样，
    直接全文搜索会抽到注释而不是代码。
    """
    start = text.index("#(")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("模块参数表没有闭合的 ')'")


def _param_default(block: str, name: str) -> str:
    """在参数表里抽 ``NAME = EXPR`` 的 EXPR（扫到**顶层** ``,`` 或 ``)`` 为止，括号平衡）。"""
    mo = re.search(rf"\b{name}\s*=\s*", block)
    assert mo is not None, f"参数表里找不到 {name}"
    depth = 0
    i = mo.end()
    while i < len(block):
        ch = block[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch in ",;" and depth == 0:
            break
        i += 1
    return block[mo.end() : i].strip()


def _vh_scalars() -> dict[str, int]:
    """``rtl_params.vh`` 里的 ``localparam [x:y] NAME = N'dV;`` 常量。"""
    text = PARAMS_VH.read_text(encoding="utf-8")
    return {mo.group(1): int(mo.group(2)) for mo in _VH_SCALAR.finditer(text)}


def _int_localparams(path: Path) -> dict[str, str]:
    """``localparam int NAME = EXPR;`` → ``{NAME: EXPR 原文}``。"""
    return {
        mo.group(1): mo.group(2).strip()
        for mo in _INT_LOCALPARAM.finditer(path.read_text(encoding="utf-8"))
    }


def _sv_int(value: int) -> int:
    """SystemVerilog int cast: truncate to 32 bits and interpret as signed."""
    bits = value & 0xFFFFFFFF
    return bits - (1 << 32) if bits & (1 << 31) else bits


def test_explicit_sv_int_cast_in_width_expressions():
    for expression, expected in [
        ("int'(V_BITS) + 1", 65),
        ("int'(2147483648)", -(1 << 31)),
        ("int'(4294967297)", 1),
        ("int'(-1)", -1),
    ]:
        assert (
            eval(_to_python(expression), {"__builtins__": {}, "sv_int": _sv_int}, {"V_BITS": 64})
            == expected
        )


def _to_python(expr: str) -> str:
    """把 Verilog 表达式翻成 Python：``C ? A : B`` → ``A if C else B``、``/`` → ``//``。

    遇到翻不动的形状就 FAIL 并指名表达式 —— 静默跳过等于门禁失效。
    """
    out = re.sub(r"\bint\s*'\s*\(", "sv_int(", expr)
    for _ in range(30):
        if "?" not in out:
            break
        out = _replace_innermost_ternary(out, expr)
    assert "?" not in out, f"残留的三元表达式: {out!r}"
    return re.sub(r"(?<!/)/(?!/)", "//", out)


def _eval_rtl(table: dict[str, str], wanted: list[str], known: dict[str, int]) -> dict[str, int]:
    """按依赖顺序求值 ``table`` 中 ``wanted`` 及其依赖，返回**实测**的常量值。"""
    order: list[str] = []
    pending = list(wanted)
    while pending:
        for name in list(pending):
            deps = set(_IDENT.findall(table[name])) if name in table else set()
            if all(d not in table or d in order for d in deps):
                order.append(name)
                pending.remove(name)
                break
        else:
            raise AssertionError(f"localparam 依赖成环或缺失: {pending}")
    values = dict(known)
    for name in order:
        if name not in table:
            continue
        try:
            values[name] = int(
                eval(_to_python(table[name]), {"__builtins__": {}, "sv_int": _sv_int}, values)
            )
        except NameError as exc:  # pragma: no cover - 只有 RTL 引用了未知名字才会到这里
            raise AssertionError(f"{name} 的表达式引用了未知名字: {table[name]!r} ({exc})") from exc
    return {name: values[name] for name in wanted}


@pytest.fixture(scope="module")
def rtl() -> dict[str, object]:
    """一次抽好三份 RTL 的 localparam 表与参数默认值。"""
    vh = _vh_scalars()
    div_block = _param_block(DIV_SV.read_text(encoding="utf-8"))
    recon_block = _param_block(RECON_SV.read_text(encoding="utf-8"))
    div_params = {}
    for name in _DIV_PARAM_NAMES:
        expr = _param_default(div_block, name)
        div_params[name] = int(eval(_to_python(expr), {"__builtins__": {}, "sv_int": _sv_int}, vh))
    return {
        "vh": vh,
        "recon": _int_localparams(RECON_SV),
        "div": _int_localparams(DIV_SV),
        "adc2": _int_localparams(ADC2_SV),
        "div_params": div_params,
        "recon_p_stages": int(
            eval(
                _to_python(_param_default(recon_block, "P_STAGES")),
                {"__builtins__": {}, "sv_int": _sv_int},
                vh,
            )
        ),
    }


def test_scalar_localparams_in_the_header_match_the_mirror(rtl):
    """``rtl_params.vh`` 的定点/位宽常量与镜像同名常量逐条相等。"""
    vh = rtl["vh"]
    pairs = {
        "W_FRAC": m.W_FRAC,
        "W_BITS": m.W_BITS,
        "V_FRAC": m.V_FRAC,
        "V_BITS": m.V_BITS,
        "ACC_BITS": m.ACC,
        "OUT_BITS": m.OUT,
        "ADC2_BITS": m.ADC2_BITS,
    }
    for name, mirrored in pairs.items():
        assert name in vh, f"rtl_params.vh 里找不到 {name} —— 名字改了，镜像要跟着改"
        assert vh[name] == mirrored, f"{name}: RTL={vh[name]} 镜像={mirrored}"


def test_recon_core_localparams_do_not_drift(rtl):
    """``recon_core.sv`` 的 ``localparam int`` 表达式求值后必须等于镜像常量。"""
    table = rtl["recon"]
    ns = {**rtl["vh"], "P_STAGES": rtl["recon_p_stages"]}
    mirrored = {
        "SUM_BITS": m.SUM_BITS,
        "W_RAIL": m.W_RAIL,
        "W_OP3": m.W_OP3,
        "W_WIDE": m.W_WIDE,
        "W_S1": m.W_S1,
        "W_SHIFT": m.W_SHIFT,
        "W_A": m.W_A,
        "W_TOP": m.W_TOP,
    }
    for name in mirrored:
        assert name in table, f"recon_core.sv 里找不到 {name} —— 镜像常量已失去对应物"
    got = _eval_rtl(table, list(mirrored), ns)
    for name, value in mirrored.items():
        assert got[name] == value, f"{name}: RTL 表达式算出 {got[name]}，镜像写的是 {value}"


def test_div_floor_widths_do_not_drift(rtl):
    """``div_floor.sv`` 的 ``W_R / N_CYC / W_PAD`` 在**多组参数**下都要与镜像一致。

    只比一组（例化值）是不够的：表达式改错但恰好在这一组上算对，是真实存在的情形
    （例如把 ceil 写成 floor，P_W_A 恰为 P_STAGES 的整数倍时看不出来）。
    """
    table = rtl["div"]
    for name in ("W_R", "N_CYC", "W_PAD"):
        assert name in table, f"div_floor.sv 里找不到 {name}"
    # 先证明镜像内部自洽：模块级常量必须等于表达式函数在例化参数下的取值。
    # 没有这一条，改坏 DIV_W_R 之类的常量不会有任何测试变红（没人用它）。
    declared = (m.DIV_W_R, m.DIV_N_CYC, m.DIV_W_PAD)
    derived = m.div_widths()
    assert (
        declared == derived
    ), f"DIV_W_R/DIV_N_CYC/DIV_W_PAD={declared} 与 div_widths()={derived} 脱节"
    for p_w_a, p_w_d, p_stages in (
        (m.DIV_P_W_A, m.DIV_P_W_D, m.DIV_P_STAGES),  # recon_core 的例化值
        (m.DIV_DEFAULT_P_W_A, m.DIV_DEFAULT_P_W_D, m.DIV_DEFAULT_P_STAGES),  # 模块默认值
        (63, 60, 7),
        (64, 60, 7),
        (64, 64, 2),
        (63, 64, 1),
        (33, 96, 5),
        (127, 128, 11),
        (60, 60, 60),
    ):
        ns = {**rtl["vh"], "P_W_A": p_w_a, "P_W_D": p_w_d, "P_STAGES": p_stages}
        got = _eval_rtl(table, ["W_R", "N_CYC", "W_PAD"], ns)
        want = m.div_widths(p_w_a, p_w_d, p_stages)
        assert (got["W_R"], got["N_CYC"], got["W_PAD"]) == want, (
            f"P_W_A={p_w_a} P_W_D={p_w_d} P_STAGES={p_stages}: "
            f"RTL 算出 {tuple(got[n] for n in ('W_R', 'N_CYC', 'W_PAD'))}，镜像给出 {want}"
        )


def test_div_floor_parameter_defaults_match_the_mirror(rtl):
    """``div_floor.sv`` 的**参数默认值**也是契约的一部分，同样要逐条对齐。"""
    params = rtl["div_params"]
    assert params["P_W_A"] == m.DIV_DEFAULT_P_W_A
    assert params["P_W_D"] == m.DIV_DEFAULT_P_W_D
    assert params["P_STAGES"] == m.DIV_DEFAULT_P_STAGES
    assert rtl["recon_p_stages"] == m.DIV_P_STAGES, "recon_core.sv 的 P_STAGES 默认值变了"
    assert (
        m.DIV_P_W_A == m.W_A and m.DIV_P_W_D == m.SUM_BITS
    ), "recon_core.sv 的 div_floor 例化值必须仍是 (.P_W_A(W_A), .P_W_D(SUM_BITS))"


def test_adc2_dec_widths_do_not_drift(rtl):
    """``adc2_dec.sv`` 的 ``K / W_MUL / W_SUM`` 在每个受支持的位宽下都要与镜像一致。"""
    table = rtl["adc2"]
    for name in ("K", "W_MUL", "W_SUM"):
        assert name in table, f"adc2_dec.sv 里找不到 {name}"
    for nbits in (1, 2, 3, 4, 8, 12, 20):
        ns = {**rtl["vh"], "P_ADC2_BITS": nbits}
        got = _eval_rtl(table, ["K", "W_MUL", "W_SUM"], ns)
        want = m.adc2_widths(nbits)
        assert (
            got["K"],
            got["W_MUL"],
            got["W_SUM"],
        ) == want, f"P_ADC2_BITS={nbits}: RTL={(got['K'], got['W_MUL'], got['W_SUM'])}，镜像={want}"


def test_the_drift_gate_itself_has_teeth(rtl):
    """**能变红**：把镜像改错，上面的比对必须失败。

    没有这一条，"反漂移门禁"可能只是两个都写错却互相吻合的数字。
    三条路径分别对应门禁的三个层级：模块级常量、位宽**表达式**、参数默认值。
    """
    original_s1 = m.W_S1
    try:
        m.W_S1 = original_s1 + 1
        with pytest.raises(AssertionError, match="W_S1"):
            test_recon_core_localparams_do_not_drift(rtl)
    finally:
        m.W_S1 = original_s1

    original_div_w_r = m.DIV_W_R
    try:
        m.DIV_W_R = original_div_w_r + 1
        with pytest.raises(AssertionError, match="DIV_W_R"):
            test_div_floor_widths_do_not_drift(rtl)
    finally:
        m.DIV_W_R = original_div_w_r

    # 最关键的一条：改坏**表达式函数**（而不是模块级常量）也必须被抓住 ——
    # 这才是"RTL 改了、镜像没跟着改"的真实形态。这里的变异是把 ceil 换成 floor，
    # 只在 P_W_A 不是 P_STAGES 整数倍时才看得出来。
    original_div_widths = m.div_widths

    def _floor_instead_of_ceil(
        p_w_a: int = m.DIV_P_W_A,
        p_w_d: int = m.DIV_P_W_D,
        p_stages: int = m.DIV_P_STAGES,
    ) -> tuple[int, int, int]:
        w_r, _, w_pad = original_div_widths(p_w_a, p_w_d, p_stages)
        return w_r, p_w_a // p_stages, w_pad  # ceil -> floor（P_W_A 非整除倍数时才可见）

    try:
        m.div_widths = _floor_instead_of_ceil
        with pytest.raises(AssertionError, match="镜像给出"):
            test_div_floor_widths_do_not_drift(rtl)
    finally:
        m.div_widths = original_div_widths

    # RTL 侧（最关键的方向）：把 N_CYC 从 ceil 改成 floor —— 这正是"改 RTL 忘了改镜像"
    # 的真实形态。门禁必须失败，否则它守不住它声称守的东西。
    # 顺带钉住一件事：**只用例化值那一组比不出来**（63 是 7 的整数倍），
    # 所以要等 (64, 60, 7) 这一组才报红 —— 多组参数的必要性就在这。
    table = rtl["div"]
    saved_n_cyc = table["N_CYC"]
    try:
        table["N_CYC"] = "P_W_A / P_STAGES"  # ceil -> floor
        with pytest.raises(AssertionError, match="镜像给出"):
            test_div_floor_widths_do_not_drift(rtl)
    finally:
        table["N_CYC"] = saved_n_cyc

    # 复原后必须重新变绿，否则上面的 finally 是坏的
    restored = (m.DIV_W_R, m.DIV_N_CYC, m.DIV_W_PAD)
    assert restored == original_div_widths()
    assert original_s1 == m.W_S1
    test_recon_core_localparams_do_not_drift(rtl)
    test_div_floor_widths_do_not_drift(rtl)
