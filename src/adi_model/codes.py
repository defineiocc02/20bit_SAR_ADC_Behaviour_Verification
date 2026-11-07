# =============================================================================
# 文件名     : codes.py
# 模块名     : adi_model.codes
# 版本       : v1.0
# 功能       : 码域位宽的唯一真源（一级粗码 9 bit、输出 20 bit）
#
# 设计说明   :
#   * 512 这个数此前在 task2/eight/model.py、task1/experiments/residue_control_study.py
#     和 task2/model/fig19_seven.py 各自写了一遍字面量。三处一致靠的是人记得住，
#     不是机器保证——改架构时漏改一处不会有任何测试报错。
#   * 放在库层是因为三处分别属于 task1 的实验层与 task2 的两个子模型，
#     任何一处都不能被另外两处当作真源，否则就是反向依赖。
#   * 本模块只放常量。20 bit 的有符号/无符号换算与中点电压在 M6 落地时
#     一并加到这里，那时才是「口径统一」真正做的事；现在先把位宽钉住。
#   * 测试文件里的 512 字面量故意保留：独立 oracle 的价值就在于不复用被测方的
#     常量，否则常量写错时 oracle 会跟着一起错。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""码域位宽与码/电压换算的唯一真源。"""

from __future__ import annotations

import numpy as np

# 一级（SADC）量化 9 bit，与 ISSCC2024 论文「resulting in 9b quantization in the
# first stage」一致；也是 residual 计算里 coarse 的取值上界。
COARSE_BITS = 9
COARSE_CODES = 1 << COARSE_BITS

# 最终输出 20 bit。
OUTPUT_BITS = 20
OUTPUT_CODES = 1 << OUTPUT_BITS


def to_unsigned(output_v, peak_v: float, *, bits: int = OUTPUT_BITS, strict: bool = False):
    """把浮点输出电压换成无符号 20 位码，向下取整。

    取整方向是**向下**，不是四舍五入：这是 transfer curve 的定义（落在哪个码仓
    就属于哪个码），换方向会让 DNL 在所有边界上偏半格。``experiment._output``
    与 ``inl_dnl`` 早先各自实现过一遍，这里合成一处。

    Args:
        output_v: 输出电压，标量或数组，单位 V。
        peak_v: 单端满幅，单位 V，必须为正。
        bits: 输出位宽。默认取本模块的 20 位；显式传参是为了让调用方的
            模块级 ``OUTPUT_BITS`` 仍可被 monkeypatch 替换（早先的实现读的是
            调用方模块的全局量，小位宽测试正是靠这个把 20 位压到 4 位）。
        strict: True 时越界抛 ``ValueError``；默认 False 与旧实现一致，
            允许返回值落在 ``[0, OUTPUT_CODES)`` 之外，由调用方自行 clip 或检查。
            之所以不默认抛：``inl_dnl`` 与 ``uncalibrated_linearity`` 有多处
            先算码再 clip 的用法，默认抛会改变它们的行为。

    Returns:
        无符号码，``int64`` 标量或数组。

    Raises:
        ValueError: ``peak_v`` 非正；或 ``strict=True`` 且存在越界码。
    """
    if peak_v <= 0:
        raise ValueError("peak must be positive")
    if bits <= 0:
        raise ValueError("bit width must be positive")
    codes = 1 << bits
    # 不强制转 float64：旧实现按输入 dtype 计算，强转会让 float32 输入在
    # 码边界上偏一个码，而 inl_dnl 的码宽统计对单码差异极其敏感。
    values = np.asarray(output_v)
    code = np.floor((values + peak_v) * (codes / (2.0 * peak_v)))
    if strict and np.any((code < 0) | (code >= codes)):
        raise ValueError("output code is outside the unsigned range")
    if np.ndim(output_v) == 0:
        return np.int64(code)
    return code.astype(np.int64)


def to_signed(unsigned, *, bits: int = OUTPUT_BITS):
    """把无符号码换成以中点为原点的有符号码。

    中点约定为 ``OUTPUT_CODES//2``，与 ``fixed.py`` 的定点式
    ``(n*FULL_SCALE_CODE)//g - FULL_SCALE_CODE//2`` 是同一个偏置。

    Args:
        unsigned: 无符号码，标量或数组。
        bits: 输出位宽，默认 20。

    Returns:
        有符号码，与输入同形状。
    """
    return unsigned - ((1 << bits) >> 1)


def midpoint_v(code, peak_v: float, *, bits: int = OUTPUT_BITS):
    """给出码仓中点对应的输出电压，单位 V。

    用 ``code + 0.5`` 而不是 ``code``：FFT 的输入必须是码仓中点，取边界会让
    每个码都带 −0.5 LSB 的固定偏移，看起来像失调、实际是取点错。

    Args:
        code: 无符号码，标量或数组。
        peak_v: 单端满幅，单位 V。
        bits: 输出位宽，默认 20。

    Returns:
        中点电压，单位 V，与 ``code`` 同形状。
    """
    if peak_v <= 0:
        raise ValueError("peak must be positive")
    step = 2.0 * peak_v / (1 << bits)
    return (code + 0.5) * step - peak_v
