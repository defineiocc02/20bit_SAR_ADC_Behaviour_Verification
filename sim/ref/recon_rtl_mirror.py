"""recon_core.sv 的位宽镜像：把 RTL 的每一处截断复刻到 Python，与黄金模型对拍。

职责一句话
    用 Python 精确复刻 ``rtl/core/recon_core.sv``、``rtl/core/adc2_dec.sv`` 与
    ``rtl/core/div_floor.sv`` 的**位宽、截断与位级算法**，然后对同一条
    ``run_pipeline`` 记录与 :class:`adi_model.fixed_point.FixedPointReconstructor`
    逐样本比对。本文件**不**验证 RTL 的语法或综合，只验证"声明的位宽够不够、
    口径对不对、位级实现是否与语义一致"。

来源
    ``rtl/core/recon_core.sv`` 的 `localparam` 宽度（SUM_BITS / W_RAIL / W_OP3 /
    W_WIDE / W_S1 / W_SHIFT / W_A / W_TOP）；``rtl/core/adc2_dec.sv`` 的
    K / W_MUL / W_SUM；``rtl/core/div_floor.sv`` 的 W_R / N_CYC / W_PAD 与其
    恢复余数法的逐位流程；``docs/rtl/RTL_ARITHMETIC_CONTRACT.md`` §3–§4；
    ``src/adi_model/fixed_point.py`` 作为黄金值。

单位契约
    权重 Q30（48 bit 有符号）；电压 Q32（64 bit 有符号）；输出 20 bit offset-binary。
    全部为整数，**不做浮点伏特运算**。

参数来源分级
    本文件的所有宽度都是 [推导] 值，来自 RTL 的 localparam —— **改宽度必须同时改这里**，
    否则本镜像会与 RTL 脱节。这是刻意的：让"改宽度"这件事必须动两处，不能单边静默改。
    现在这条纪律是**机械可查**的：``tests/unit/test_recon_mirror.py`` 的
    ``test_localparams_are_not_drifting_from_the_rtl`` 会直接从 RTL 源码正则抽取
    这些 localparam 的**表达式**（不只是值），在 Python 里求值并与本文件的同名常量比对。

契约与不变量 / 适用域
    * 覆盖四种 dither 配置：off（生产）、quantizer+DEM、sampling、失配+quantizer+DEM。
    * 断言三条**掩码和展开式**与模型 ``_terms`` 的定义逐项相等：
      ``rails = sum_W - 2*sum_Won + sum_Wr``、``gain = Sigma W*a``、``total = Sigma W``。
      任一条不等就退出码非零 —— 这是最容易出错、也最难在 VCS 里定位的地方。
    * 断言 ``word``（floor 除法结果）与 ``stream.code`` 逐样本相等。
    * ``div_floor`` 走 ``div_floor_bitwise``（RTL 的位级算法），不是 ``a // d``：
      位序类的实现 bug 只有镜像到"RTL 实际怎么做"才可能被抓到。
    * 适用域：仅这四种配置 + 本文件的波形；**不是**"RTL 在所有配置下正确"的证明。
      真正的证明在 ``sim/tb/p2_tb.sv``（含 2^20 全码 oracle 与定义式自检）。

用法::

    PYTHONPATH=src python sim/ref/recon_rtl_mirror.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adi_model import Config, sine_input  # noqa: E402
from adi_model.fixed_point import FixedPointReconstructor  # noqa: E402
from adi_model.pipeline import run_pipeline  # noqa: E402
from adi_model.weight_calibration import DigitalObservation, _terms  # noqa: E402

# --- 与 recon_core.sv / adc2_dec.sv / div_floor.sv 的 localparam 逐条对应 ---
# 改一处必须改两处；`tests/unit/test_recon_mirror.py` 的
# `test_localparams_are_not_drifting_from_the_rtl` 会从 RTL 源码里正则抽取这些
# 表达式并逐条比对，人忘了改也会被机械地抓住。
V_BITS, ACC, OUT = 64, 96, 20
W_BITS = 48
W_FRAC, V_FRAC = 30, 32
SUM_BITS = 64
W_RAIL = SUM_BITS + 2
W_OP3 = SUM_BITS + V_BITS + 2
W_WIDE = W_OP3 + 2
W_S1 = ACC + 1
W_SHIFT = ACC + OUT + 1
W_A = ACC - (V_FRAC + 1)
W_TOP = W_SHIFT - ACC + 1
LIM = 1 << (ACC - 1)

# --- div_floor.sv：模块自带的参数默认值，以及 recon_core.sv 的例化值 ---
# 默认值是模块头声明的契约（独立于例化）；例化值才是 recon_core 真正跑的配置。
# 两者都要镜像：只对例化值，会把"默认值被改坏"漏过去。
DIV_DEFAULT_P_W_A = ACC - (V_FRAC + 1)  # div_floor.sv 的 parameter int P_W_A 默认
DIV_DEFAULT_P_W_D = 60  # div_floor.sv 的 parameter int P_W_D 默认
DIV_DEFAULT_P_STAGES = 7  # div_floor.sv 的 parameter int P_STAGES 默认
DIV_P_W_A = W_A  # recon_core.sv 的 .P_W_A(W_A)
DIV_P_W_D = SUM_BITS  # recon_core.sv 的 .P_W_D(SUM_BITS)
DIV_P_STAGES = 7  # recon_core.sv 的 parameter int P_STAGES = 7

# --- adc2_dec.sv 的生产位宽（P_ADC2_BITS 取自 rtl_params.vh） ---
ADC2_BITS = 12


def div_widths(
    P_W_A: int = DIV_P_W_A,
    P_W_D: int = DIV_P_W_D,
    P_STAGES: int = DIV_P_STAGES,
) -> tuple[int, int, int]:
    """复刻 ``div_floor.sv`` 的三个 localparam ``(W_R, N_CYC, W_PAD)``。

    天真的实现（把参数当常量）会漏掉"参数变了但表达式没跟着变"这类漂移，
    所以这里写成函数：反漂移门禁会用**多组**参数求值并与 RTL 表达式比。
    """
    w_r = max(P_W_A, P_W_D) + 1
    n_cyc = (P_W_A + P_STAGES - 1) // P_STAGES
    return w_r, n_cyc, n_cyc * P_STAGES


def adc2_widths(P_ADC2_BITS: int = ADC2_BITS) -> tuple[int, int, int]:
    """复刻 ``adc2_dec.sv`` 的三个 localparam ``(K, W_MUL, W_SUM)``。"""
    k = P_ADC2_BITS + 1
    w_mul = P_ADC2_BITS + 1 + V_BITS + 1
    return k, w_mul, w_mul + 1


DIV_W_R, DIV_N_CYC, DIV_W_PAD = div_widths()
ADC2_K, ADC2_W_MUL, ADC2_W_SUM = adc2_widths()


def _sgn(x: int, w: int) -> int:
    """把 x 截成 w 位二补数并解释为有符号整数（复刻 Verilog 的赋值截断）。"""
    x &= (1 << w) - 1
    return x - (1 << w) if x >> (w - 1) else x


def _u(x: int, w: int) -> int:
    """把 x 截成 w 位无符号整数。"""
    return x & ((1 << w) - 1)


def round_half_even_shift(x: int, k: int) -> int:
    """复刻 adc2_dec 的 ``$signed(x) >>> k`` + 余数比较（ties to even）。"""
    q = x >> k
    r = _u(x, k)
    half = 1 << (k - 1)
    return q + (1 if (r > half or (r == half and (q & 1))) else 0)


def adc2_dec(code: int, min_q: int, max_q: int, nbits: int) -> tuple[int, bool]:
    """复刻 ``rtl/core/adc2_dec.sv``。返回 ``(fine_q, ovf)``。"""
    k, w_mul, w_sum = adc2_widths(nbits)
    delta = _sgn(max_q - min_q, V_BITS + 1)
    n = _sgn((2 * code + 1) * delta, w_mul)
    sh = _sgn(round_half_even_shift(n, k), w_mul)
    total = _sgn(min_q + sh, w_sum)
    ovf = not (-(1 << 63) <= total < (1 << 63))
    if ovf:
        return ((1 << 63) - 1 if total > 0 else -(1 << 63)), True
    return _sgn(total & ((1 << V_BITS) - 1), V_BITS), False


def div_floor_bitwise(
    a: int,
    d: int,
    P_W_A: int = DIV_P_W_A,
    P_W_D: int = DIV_P_W_D,
    P_STAGES: int = DIV_P_STAGES,
    *,
    grouped_bit_order: bool = True,
) -> tuple[int, bool]:
    """逐位复刻 ``rtl/core/div_floor.sv``：恢复余数法，floor(a/d)，d == 0 报 err。

    与 ``a // d`` 的关系：本函数**不是**"把应该做的写一遍"，而是把 RTL 的
    **位级实现**照抄一遍 —— 取绝对值、补零到 ``W_PAD``、``bitpos`` 的位序、
    一个时钟内 ``P_STAGES`` 级级联、``quo << P_STAGES | qbits`` 的累加、
    以及负商修正 ``-(q_abs + (R != 0))``。任何一处写错都会与 ``a // d`` 不符。

    Args:
        a: 有符号被除数；按 ``P_W_A`` 位二补数截断（复刻输入端口宽度）。
        d: 无符号除数；按 ``P_W_D`` 位截断。
        P_W_A: ``a`` / ``q`` 的位宽。
        P_W_D: ``d`` 的位宽。
        P_STAGES: 一个时钟内级联的级数。
        grouped_bit_order: ``True`` = RTL 现在的正确写法（第 ``s`` 级产生的商位
            落在本组第 ``P_STAGES-1-s`` 位，MSB 优先）；``False`` = 故意复现
            历史 bug 的写法（``qbits[s]``），只用于证明"这个镜像抓得到那个 bug"。

    Returns:
        ``(q, err)``；``d == 0`` 时 ``err`` 为 True 且 ``q == 0``。
    """
    w_r, n_cyc, w_pad = div_widths(P_W_A, P_W_D, P_STAGES)
    a_in = _sgn(a, P_W_A)
    d_in = _u(d, P_W_D)
    if d_in == 0:
        return 0, True

    # mag = |a|（复刻 `a[P_W_A-1] ? (~a + 1'b1) : a`），高位补零到 W_PAD。
    mag = -a_in if a_in < 0 else a_in
    dv_ext = d_in  # 复刻 dv_ext = {{(W_R - P_W_D){1'b0}}, dv}
    rem, quo = 0, 0
    for cnt in range(n_cyc):
        r_chain = [rem]
        qbits = 0
        for s in range(P_STAGES):
            bitpos = w_pad - 1 - (cnt * P_STAGES + s)
            bit = (mag >> bitpos) & 1
            # 复刻 `shifted = {r_chain[s][W_R-2:0], mag[bitpos]}`
            shifted = ((r_chain[s] & ((1 << (w_r - 1)) - 1)) << 1) | bit
            if shifted >= dv_ext:
                r_chain.append(shifted - dv_ext)
                take = 1
            else:
                r_chain.append(shifted)
                take = 0
            if take:
                # 商 MSB 优先：第 s 级产生的位属于这一组的高位。
                qbits |= 1 << ((P_STAGES - 1 - s) if grouped_bit_order else s)
        rem = r_chain[P_STAGES]
        quo = ((quo << P_STAGES) | qbits) & ((1 << w_pad) - 1)

    q_abs = quo & ((1 << P_W_A) - 1)  # 复刻 q_next[P_W_A-1:0]
    q = -(q_abs + (1 if rem else 0)) if a_in < 0 else q_abs
    return _sgn(q, P_W_A), False


def div_floor(a: int, d: int) -> tuple[int, bool]:
    """``div_floor_bitwise`` 的生产参数封装，签名与既有调用点保持一致。

    刻意**委托**到位级镜像：原来的实现直接写 ``a // d``，镜像的是"应该做什么"
    而不是"RTL 实际怎么做的"，于是位级 bug（组内位序写反）天然逃逸。
    """
    return div_floor_bitwise(a, d)


def reconstruct(
    nbits: int,
    plus_row: np.ndarray,
    a_bit_row: np.ndarray,
    rail_add_row: np.ndarray,
    inj_v: float,
    v_fs: float,
    weights_row: np.ndarray,
    code: int,
    offset_q: int,
    min_q: int,
    max_q: int,
) -> tuple[int, bool, bool]:
    """复刻 ``rtl/core/recon_core.sv`` 的算术路径。返回 ``(word, acc_ovf, gain_err)``。"""
    w = weights_row.astype(object)
    sum_w = int(w.sum())
    sum_wa = int((w * a_bit_row).sum())
    sum_won = int((w * plus_row).sum())
    sum_wr = int((w * rail_add_row).sum())
    rails = _sgn(sum_w - 2 * sum_won + sum_wr, W_RAIL)
    gain, total = sum_wa, sum_w

    fine, _ = adc2_dec(code, min_q, max_q, nbits)
    inj_q = int(np.rint(float(inj_v) / v_fs * (1 << V_FRAC)))
    op1 = _sgn((fine - offset_q) << W_FRAC, W_WIDE)
    op2 = _sgn(rails << V_FRAC, W_WIDE)
    op3 = _sgn(total * inj_q, W_WIDE)
    num = _sgn(op1 - op2 - op3, W_WIDE)

    bad = any(not -LIM <= x < LIM for x in (op1, op2, op3, num))
    bad_den = gain >= (1 << (ACC - 1 - (V_FRAC + 1)))
    gv = _sgn(gain << V_FRAC, W_S1)
    s1 = _sgn(_sgn(num, W_S1) + gv, W_S1)
    shifted = _sgn(s1 << OUT, W_SHIFT)
    top = _u(shifted >> (ACC - 1), W_TOP)
    shifted_ok = (top == 0) or (top == (1 << W_TOP) - 1)
    ovf = bad or bad_den or (not shifted_ok)

    word, derr = div_floor(_sgn(shifted >> (V_FRAC + 1), W_A), gain)
    return word, ovf, (gain == 0) or derr


def _masks(cfg, data, signal, bmd, inj, n):
    """按 ``_terms`` 的语义重建 ``(plus, a_bit, rail_add)``，与 RTL 的输入口径一致。

    注意：``railbit`` **只在 sampling 模式**下才是 ``bmd`` 的一部分。
    无条件填 railbit 会污染非 sampling 模式的 ``plus``。
    """
    nm, ns = int(cfg.dac_n_main), int(cfg.dac_n_sub)
    n_units = nm + ns
    mc = int(2 * cfg.dither_units_range)
    end = n_units if cfg.dither_split_bank == "sub" else nm
    beg = end - mc
    sampling = cfg.dither_mode == "sampling"

    railbit = np.zeros((n, n_units), dtype=np.int64)
    if sampling:
        d = np.asarray(data.bank_dither, dtype=np.int64)
        railbit[:, beg:end] = 2 * (np.arange(mc)[None, :] < mc // 2 + d[:, None]) - 1

    x = (bmd - inj[:, None, None]) / cfg.v_fs
    plus = np.rint((1.0 - x + railbit[:, None, :]) / 2.0).astype(np.int64)
    a_bit = np.ones_like(signal, dtype=np.int64)
    rail_add = np.zeros_like(signal, dtype=np.int64)
    if sampling:
        a_bit[:, :, beg:end] = 0
        rail_add[:, :, beg:end] = railbit[:, None, beg:end]
    return plus, a_bit, rail_add


def check(cfg, n: int, label: str) -> int:
    """跑一条配置，返回失败计数（0 = 通过）。"""
    r = run_pipeline(cfg, sine_input(0.8 * cfg.v_fs, cfg.fs * 73 / 2048), n)
    data = DigitalObservation.from_result(r)
    model = FixedPointReconstructor.from_result(r)
    ids, signal, bmd = _terms(data, 0, n)
    inj = np.asarray(data.common_injection_v, dtype=float)
    plus, a_bit, rail_add = _masks(cfg, data, signal, bmd, inj, n)

    w = model.weights_q[ids]
    rails_model = np.sum(
        w * np.rint((bmd - inj[:, None, None]) / cfg.v_fs).astype(np.int64), axis=(1, 2)
    )
    gain_model = np.sum(w * signal.astype(np.int64), axis=(1, 2))
    total_model = np.sum(w, axis=(1, 2))
    sum_w = w.sum(axis=(1, 2))
    sum_wa = (w * a_bit).sum(axis=(1, 2))
    sum_won = (w * plus).sum(axis=(1, 2))
    sum_wr = (w * rail_add).sum(axis=(1, 2))

    ok_rails = np.array_equal(sum_w - 2 * sum_won + sum_wr, rails_model)
    ok_gain = np.array_equal(sum_wa, gain_model)
    ok_total = np.array_equal(sum_w, total_model)

    stream = r.to_codes()
    bad = flag = 0
    for i in range(n):
        word, ovf, gerr = reconstruct(
            int(cfg.adc2_n_bits),
            plus[i],
            a_bit[i],
            rail_add[i],
            inj[i],
            cfg.v_fs,
            model.weights_q[ids[i]],
            int(data.adc2_code[i]),
            model.offset_q,
            model.adc2_min_q,
            model.adc2_max_q,
        )
        if ovf or gerr:
            flag += 1
            continue
        if word != int(stream.code[i]):
            if bad < 4:
                print(
                    f"      首个失配 i={i} got={word} exp={int(stream.code[i])} gap={word - int(stream.code[i])}"
                )
            bad += 1

    ok = ok_rails and ok_gain and ok_total and bad == 0 and flag == 0
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {label}: rails={ok_rails} gain={ok_gain} total={ok_total} | "
        f"n={n} code_mismatch={bad} ovf_or_gainerr={flag}"
    )
    return 0 if ok else 1


def main() -> int:
    """跑全部配置；任一失败即非零退出。"""
    print("recon_rtl_mirror: 位宽镜像 vs FixedPointReconstructor")
    print(f"  宽度: W_WIDE={W_WIDE} W_S1={W_S1} W_SHIFT={W_SHIFT} W_A={W_A} W_RAIL={W_RAIL}")
    print(
        f"  div_floor: W_R={DIV_W_R} N_CYC={DIV_N_CYC} W_PAD={DIV_W_PAD}"
        f" (P_W_A={DIV_P_W_A} P_W_D={DIV_P_W_D} P_STAGES={DIV_P_STAGES})"
    )
    base = Config.paper_literal()
    cases = (
        (base, 512, "paper_literal (dither off)"),
        (
            replace(
                base,
                dither_mode="quantizer",
                dither_discrete=True,
                dither_amplitude_lsb1=64,
                dem_enable=True,
            ),
            512,
            "quantizer + DEM on",
        ),
        (replace(base, dither_mode="sampling", dither_discrete=True), 256, "sampling dither"),
        (
            replace(
                base,
                mismatch_enable=True,
                dem_enable=True,
                dither_mode="quantizer",
                dither_discrete=True,
                dither_amplitude_lsb1=32,
            ),
            256,
            "mismatch + quantizer + DEM",
        ),
    )
    fails = sum(check(cfg, n, label) for cfg, n, label in cases)
    print(f"RESULT: {'PASS' if fails == 0 else 'FAIL'}（{len(cases)} 组配置，失败 {fails} 组）")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
