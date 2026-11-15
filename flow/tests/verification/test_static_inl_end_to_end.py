"""端到端静态回归：模型必须仍然给出**论文量级**的 INL。

## 为什么需要这个测试

2026-11-13（承接上一轮补 DR/NSD 端到端测试）检查发现：
INL 只在 `tests/audit/test_audit_findings.py` 里出现过，而那两处是
**指标函数的纯数学正确性**（喂合成数据），**没有任何测试跑模型再看 INL**。

于是和 DR/NSD 之前一样存在空档：边角行为有单测，但"模型整体线性度是否还在"
没人管。

## 口径说明（必须看清）

本测试量的是 **256 电平平均传输误差的端点拟合 INL**，
**不是**逐码 code-density INL，也**不是**论文 Fig.9.8.4 的口径
（论文该图只标「20b level」、无具体数值，口径未复现）。

因此断言只用来抓**量级退化**（例如失配参数漂移、掩码错接），
**不用来声称"复现了论文 INL"**——见 `docs/ACCEPTANCE_20261113.md` §4.0。

## 运行成本

64 电平 × 4 次重复，约 3–5 s。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.defaults import DefaultModel, DefaultParameters
from adi_model.metrics import metrics

V_FS = 3.0
LSB20 = 2 * V_FS / 2**20


def _measure_inl(points: int = 64, repeats: int = 4, seed: int = 20261105) -> dict:
    """慢斜坡扫电平，返回端点拟合 INL 的统计量。"""
    model = DefaultModel(
        seed=seed,
        parameters=DefaultParameters(),   # 默认 σ=1e-4、噪声启用
        config_overrides=None,
    )
    span = 0.985 * V_FS
    levels = np.linspace(-span, span, points)
    mean_error = np.zeros(points)
    for index, level in enumerate(levels):
        run = model.simulate(
            lambda t, value=float(level): np.full_like(np.asarray(t, dtype=float), value),
            repeats,
        )
        voltage = np.asarray(run.codes.voltage, dtype=float)
        mean_error[index] = float(voltage.mean()) - level
    inl = metrics.inl_from_mean_error(levels, mean_error, LSB20)
    return {
        "inl_max_lsb20": float(inl["inl_max_lsb"]),
        "offset_v": float(inl["offset_v"]),
        "gain_err": float(inl["gain_err"]),
    }


@pytest.fixture(scope="module")
def static_result():
    return _measure_inl()


def test_static_inl_is_still_at_the_20_bit_level(static_result):
    """INL 必须仍在 20 位量级（远小于 1 LSB20 的 32768 倍满量程）。

    本模型的典型值是 2–4 LSB20。容差放宽到 20 LSB20，
    只抓"线性度崩了"这类退化（例如失配参数错、掩码错接）。
    """
    inl = static_result["inl_max_lsb20"]
    assert inl < 20.0, (
        f"INL degraded to {inl:.1f} LSB20; the 20-bit linearity is lost. "
        "Check mismatch parameters and the mask construction."
    )
    # 也不能是"好得不可能"——那通常意味着测量路径断了（比如在测输入而不是输出）
    assert inl > 0.1, (
        f"INL is only {inl:.3f} LSB20, suspiciously small; "
        "is the measurement reading the reconstructed output?"
    )


def test_static_offset_and_gain_errors_are_small(static_result):
    """端点拟合出的失调与增益误差应在小量级（用于发现符号/比例错误）。"""
    assert abs(static_result["offset_v"]) < 1e-2, (
        f"offset error {static_result['offset_v']:.4e} V is too large"
    )
    assert abs(static_result["gain_err"]) < 1e-2, (
        f"gain error {static_result['gain_err']:.4e} is too large"
    )
