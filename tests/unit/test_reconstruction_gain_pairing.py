"""定点重建与 RA 增益必须同源 —— 一条此前反复踩坑的不变式。

## 为什么需要这个测试

定点重建把 `fine_v`（ADC2 读回的 RA 输出）除以**权重和** `G_fixed`；
而 `fine_v` 本身是 RA 用**物理增益** `g_vec` 产生的。两者必须相等，否则：

    residue(定点) = fine_v / G_fixed  ≠  fine_v / g_vec = residue(实际)

实测（零失配、零 RA 噪声、x=0）：`G_fixed = 31.028463`、`g_vec = 32.002092`，
比值 **1.0314** —— 正好是"定点权重和 31.9375C vs 采样口径 32.94C"那个 3.14% 差，
折到输出约 **32 LSB20**。

## 本测试守住什么

1. 记录当前两个增益的实际值，让差异**可见**（而不是藏在 37 LSB20 的噪声里）；
2. 若将来有人把两者对齐了，`test_gain_pairing_is_currently_mismatched` 会失败，
   强迫更新这条记录（避免"已经修好却还写着有 bug"）；
3. 给出可复算的换算：差异 → LSB20。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.defaults import DefaultModel, DefaultParameters
from dataclasses import replace

LSB20_V = 6.0 / 2**20


def _model_zero_noise():
    """零 RA 输出噪声：把增益配对问题从随机噪声里分离出来。"""
    return DefaultModel(
        seed=20261105,
        parameters=replace(DefaultParameters(), ra_noise_rms_v=0.0),
        config_overrides=None,
    )


def _fixed_point_gain(model, slice_ids):
    """定点重建用的分母：**本次参与转换的 8 片**权重和（无量纲）。

    注意不能对全部 18 片求和 —— 那样会得到 18/8 倍的错值（实测 ratio 0.458）。
    """
    decoder = model.nominal_decoder
    weights = decoder.weights_q / 2 ** decoder.format.weight_fraction_bits
    ids = np.asarray(slice_ids)
    return float(weights[ids].sum())


def test_float_path_is_exact_with_zero_mismatch():
    """浮点路径（vd0 + residue）在零失配、零噪声下必须精确 —— 作为基准。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import run_p4_ablation as runner

    model = runner._build_model(0.0, False)
    result = model.simulate(lambda t: 0.0, 1).raw
    k = int(np.asarray(result.k)[0])
    total = model.codebook.voltage_v[k] + float(result.residue[0])
    assert abs(total) < 1e-6, f"float path should be exact at x=0, got {total:+.3e} V"


def test_gain_pairing_is_currently_mismatched():
    """记录当前不一致：定点分母与 RA 物理增益相差约 3.14%。

    这条断言**故意**写成"当前不一致"。一旦两者对齐，它会失败，提示更新文档与
    `docs/mechanisms/p1_gaps_20261113.md` §16 的结论。
    """
    model = _model_zero_noise()
    result = model.simulate(lambda t: 0.0, 1).raw
    g_fixed = _fixed_point_gain(model, result.digital_observation.slice_ids)
    g_ra = float(result.g_vec[0])
    ratio = g_ra / g_fixed
    assert ratio == pytest.approx(1.0314, abs=5e-4), (
        "如果这个比值变了，说明增益配对被改动 —— 请先确认是有意修复，"
        f"再更新本测试与文档（当前 ratio={ratio:.6f}）"
    )
    # 折算到输出端的误差量级
    error_lsb20 = abs(ratio - 1.0) * abs(float(result.residue[0])) / LSB20_V
    assert error_lsb20 < 100, "residue 很小时折算误差也应很小（这条只是防呆）"


def test_zero_mismatch_removes_sample_to_sample_spread():
    """零失配下逐笔结果完全相同 ⇒ 逐笔散布来自片池轮换 + 片间失配，不是缺陷。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import run_p4_ablation as runner

    model = runner._build_model(0.0, False)
    codes = np.asarray(model.simulate(lambda t: -2.9, 4).codes.voltage, dtype=float)
    assert np.ptp(codes) == 0.0, f"zero mismatch should give identical codes, got ptp={np.ptp(codes):.3e}"


def test_fixed_point_error_persists_without_mismatch_and_without_noise():
    """定点偏差在"零失配 + 零噪声"下仍然存在 ⇒ 它是结构性问题，不是统计误差。

    这条把"37 LSB20 地板"定性：它不随失配变化，因此不能归因于器件失配。
    """
    model = _model_zero_noise()
    errors = []
    for x in (-2.9, -1.0, 0.0, 1.0, 2.9):
        value = float(np.mean(model.simulate(lambda t, x=x: x, 2).codes.voltage))
        errors.append(value - x)
    rms_lsb20 = float(np.sqrt(np.mean(np.square(errors)))) / LSB20_V
    assert rms_lsb20 > 10.0, (
        "若这条失败，说明定点偏差已被消除（好事）——请更新文档并调整本断言"
    )
