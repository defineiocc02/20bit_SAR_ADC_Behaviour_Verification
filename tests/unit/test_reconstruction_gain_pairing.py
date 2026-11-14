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


def test_gain_ratio_is_known_and_compensated():
    """记录**参数层面**的比值 1.0314，并确认解码器已按它换算。

    历史：定点分母 Σw 与 RA 物理增益 G_phys 相差 3.14%（S4 的 1C）。
    2026-11-13 起 `FixedPointReconstructor.reconstruct(physical_gain=...)`
    会把 fine 换算到 Σw 口径，因此**输出层面已修复**；
    本条测试记录的是参数比值本身（它仍存在，只是被显式补偿）。
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
    # 解码器必须接受 physical_gain 并把 fine 换算到 Σw 口径；
    # 换算后定点输出应与浮点接近（下面另有一条更强的断言）。
    import inspect

    signature = inspect.signature(model.nominal_decoder.reconstruct)
    assert "physical_gain" in signature.parameters
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


def test_fixed_point_now_matches_the_float_path():
    """定点与浮点必须同源 —— 这是 2026-11-13 修复后的**新**不变量。

    修复前：零失配零噪声下定点 RMS 约 37 LSB20，而浮点精确。
    修复后（解码器按 physical_gain 换算 fine）：两者应几乎一致。
    """
    model = _model_zero_noise()
    fixed, floating = [], []
    for x in (-2.9, -1.0, 0.0, 1.0, 2.9):
        result = model.simulate(lambda t, x=x: x, 2)
        fixed.append(float(np.mean(result.codes.voltage)) - x)
        floating.append(float(np.mean(result.raw.out)) - x)
    fixed_rms = float(np.sqrt(np.mean(np.square(fixed)))) / LSB20_V
    float_rms = float(np.sqrt(np.mean(np.square(floating)))) / LSB20_V
    assert abs(fixed_rms - float_rms) < 5.0, (
        f"fixed-path RMS {fixed_rms:.1f} should track float-path RMS {float_rms:.1f} LSB20"
    )


def test_all_evaluation_tools_pass_the_physical_gain():
    """所有"评价模拟精度"的工具都必须传 physical_gain。

    历史教训：给解码器加了 physical_gain 之后，`DefaultModel.simulate` 传了，
    但 `tools/run_p4_ablation.py` 自己调 `reconstruct(data)` **绕过了修复**，
    导致矩阵里 A0 的 INL 一直显示 32.9 LSB20 而不是真实的 0.56。

    这里用源码静态检查守住：凡是在 tools/ 下对数字观测做 `reconstruct(...)`
    并用于**精度评价**的文件，调用处必须出现 `physical_gain`。

    `export_rtl_vectors.py` 与 `run_fig19_rtl_calibration.py` 例外：
    它们导出/对拍 RTL 向量，验证的是**定点解码器本身**，
    两侧必须用同一种（不带 G_phys 换算的）口径，刻意不传。
    """
    from pathlib import Path

    tools = Path(__file__).resolve().parents[2] / "tools"
    # 评价类工具：必须传 physical_gain
    must_pass = ["run_p4_ablation.py", "run_review_figures.py"]
    # RTL 对拍类：刻意不传（验证解码器本身，两侧须同口径）
    intentionally_not = ["export_rtl_vectors.py", "run_fig19_rtl_calibration.py"]

    for name in must_pass:
        text = (tools / name).read_text(encoding="utf-8")
        assert "physical_gain" in text, (
            f"{name} reconstructs digital observations for accuracy evaluation "
            "but does not pass physical_gain; its numbers will be off by ~3.1%"
        )
    for name in intentionally_not:
        text = (tools / name).read_text(encoding="utf-8")
        assert "physical_gain" not in text, (
            f"{name} is an RTL-vector/calibration tool; passing physical_gain here "
            "would desynchronise the Python and RTL sides"
        )
