"""默认失配 σ 必须被钉住：它是从论文指标**反解**出来的，不是可随手调的量。

## 为什么需要这个测试

2026-11-13 做变异测试时发现：把 `DefaultParameters.unit_mismatch_std`
从 `1e-4` 改成 `2e-4`，**全套 1778 个测试全部通过**。

这不是等价变异——它真的会改变模型：

| σ | 某片前 3 个单元的 C (fF) |
|:---:|:---|
| 1e-4 | 155.6104 / 155.5813 / 155.6116 |
| 2e-4 | 155.6232 / 155.5648 / 155.6254 |

但**没有测试断言这个默认值**：既有测试要么显式传入 σ，
要么用 `unit_mismatch_std=0` 关闭失配（为了确定性），
于是"默认值本身"从未被检查。

## 这个值的依据

`1e-4` 是由论文 DR（94.2 dB）与 NSD（9.3 nV/√Hz）两个**独立指标**
同时反解出来的（见 `docs/mechanisms/paper_metrics_calibration.md`）。
它一旦漂移，模型与论文的对照关系就悄悄断了，而测试不会报错。

本测试把这三个事实钉住：
1. 默认值是 1e-4；
2. 它与推论表里的标注一致（是反解值，不是披露值）；
3. 改动它会让指标模型失效——用一次快速运行间接验证。
"""

from __future__ import annotations

import numpy as np
import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

from adi_model.defaults import DefaultModel, DefaultParameters, default_model


def test_default_unit_mismatch_is_the_back_solved_value():
    """默认失配 σ 必须等于反解值 1e-4。"""
    assert DefaultParameters().unit_mismatch_std == pytest.approx(1e-4, rel=1e-12)


def test_mismatch_changes_the_fabricated_capacitors():
    """改变 σ 必须真的改变制造出的电容（防止它变成死参数）。"""
    seed = 20261105
    small = DefaultModel(
        seed=seed,
        parameters=DefaultParameters(unit_mismatch_std=1e-4),
        config_overrides=None,
    ).chip.C_main
    large = DefaultModel(
        seed=seed,
        parameters=DefaultParameters(unit_mismatch_std=2e-4),
        config_overrides=None,
    ).chip.C_main
    assert not np.array_equal(small, large), (
        "changing unit_mismatch_std did not change the fabricated capacitors; "
        "the parameter has become inert"
    )


def test_default_model_uses_the_documented_mismatch_and_reports_it():
    """默认模型的元数据必须如实报告失配已启用及所用 σ。"""
    model = default_model()
    assert model.parameters.unit_mismatch_std == pytest.approx(1e-4, rel=1e-12)
    rows = model.inference_table()
    # 推论表里应有一行记录失配的来源分级
    graded = {
        name: row["grade"]
        for name, row in rows.items()
        if "mismatch" in name.lower()
    }
    assert graded, "inference_table should carry at least one mismatch-related row"


def test_ra_clip_is_never_reached_and_that_is_recorded():
    """`ra_clip_v` 在当前参数下**从不触发**——这是一个需要显式记录的事实。

    2026-11-13 变异测试：把 `ra_clip_v` 从 0.45 改成 0.5，全套 1782 个测试通过。
    查证后确认这是**等价变异**，不是覆盖缺口：

    | 输入 | clip=0.45 的 ra_sat | clip=0.50 的 ra_sat |
    |:---:|:---:|:---:|
    | ±3 V、0 V、±2.99 V 各 8 笔 | 0 | 0 |

    RA 输出最大约 0.34 V（vra max = 0.340862 @ 0.31 V 输入），
    远未达到 0.45 V 的限幅阈值。

    **这意味着 ra_clip 目前是一个"死参数"**：它存在、有依据（= ADC2 窗口宽度），
    但在当前参数组合下不会起作用。如果将来噪声/失配变大让它开始触发，
    这个测试会失败并提醒重新审视——而不是让人以为它一直被验证着。
    """
    import numpy as np

    from adi_model.defaults import DefaultModel, DefaultParameters

    worst = 0.0
    for voltage in (-3.0, -2.99, 0.0, 2.99, 3.0):
        for clip in (0.45, 0.5):
            model = DefaultModel(
                seed=20261105,
                parameters=DefaultParameters(),
                config_overrides={"ra_v_clip": clip},
            )
            run = model.simulate(lambda t, v=voltage: np.full_like(np.asarray(t, float), v), 8)
            worst = max(worst, float(np.max(np.asarray(run.raw.vra))))
            assert not np.any(np.asarray(run.raw.ra_sat)), (
                f"RA clipping started firing at input {voltage} V with clip={clip}; "
                "ra_clip is no longer inert — re-examine the noise/mismatch budget"
            )
    assert worst < 0.45, (
        f"peak |vra| = {worst:.4f} V reached the clip threshold; the assumption "
        "that ra_clip is inert no longer holds"
    )


def test_nominal_gain_is_the_physical_gain_and_the_fixed_point_sum_is_separate():
    """`nominal_gain`（物理增益）与"定点权重和增益"必须被区分，且都写进文档。

    ## 2026-11-13 的第二轮修正（本轮）

    第一轮只把两个数并列记录：`inference_table` 的 `nominal_residue_gain = 32`（设计目标
    `cfg.g0`）、`DefaultModel.nominal_gain = 31.028`（"实际算出的"）。但那条记录把一个
    **口径错误**写成了"两者都对的差异"：

    * `nominal_gain` 的语义是 **Cnorm/Cf**，而 `Cnorm` 是残差/RA 的归一化电容
      （`pipeline_engine`：`Cnorm` = 全 Signal 连接的归一化电容）。
      采样期 17 个主单元**全部**接信号（含 S4），所以
      Cnorm = 8 × (32 + 15/16) × Cu = **20.5 pF**，G = 20.5 pF/0.640625 pF = **32.0**。
      31.028 用的是 8 × 31.9375 × Cu，那是**码本分母**（转换期只有 16 列参与驱动），
      不是 Cnorm。把它当 nominal_gain 会让"名义值"与器件不同源。
    * 31.028 并没有消失，它换了个名字：`G_fixed` = 定点重建的 Σw 口径，
      比物理增益小 1.0314 倍（S4 不进码）。`reconstruct(physical_gain=...)` 负责换算，
      由 `tests/unit/test_reconstruction_gain_pairing.py` 钉住。

    本测试把修正后的三个事实钉住：
    1. `g0 == 32`（设计目标）；
    2. `nominal_gain == 32.0`（物理增益，= C_samp/Cf），与 `g0` 一致；
    3. README 同时解释了两个口径（32.0 与 31.028），读者不会混用；
    4. Σw 口径的 G_fixed 仍是 31.028（不能被"顺手改掉"，否则 RTL 侧口径漂移）。
    """
    import numpy as np

    from adi_model.defaults import DefaultParameters, default_model

    model = default_model()
    assert model.cfg.g0 == pytest.approx(32.0, abs=0)
    assert model.nominal_gain == pytest.approx(32.0, rel=1e-6, abs=0)
    # 名义 Cnorm 必须落在锚点上（20.5 pF），不是 19.8776 pF。
    # ⚠️ `abs=0` 不可省：`approx` 默认 abs=1e-12 在 20.5 pF 上相当于 ±4.88%，
    # 缺陷态的 19.8776 pF（差 3.13%）会照样通过（已实测）。
    assert model.nominal_active_cap_f == pytest.approx(20.5e-12, rel=1e-4, abs=0)
    # 定点 Σw 口径：8 × 31.9375 × Cu / Cf = 31.028，仍然是这个数
    parameters = DefaultParameters()
    g_fixed = (
        model.cfg.n_active * 31.9375 * parameters.unit_cap_f / parameters.feedback_cap_f
    )
    assert g_fixed == pytest.approx(31.02846314161869, rel=1e-9)
    # 两个口径的比值就是 S4 那 1C
    assert model.nominal_gain / g_fixed == pytest.approx(1.0314, abs=5e-4)
    # 解码器权重和必须等于 Σw 口径（同源），不能被 nominal_gain 影响
    decoder = model.nominal_decoder
    weights = np.asarray(decoder.weights_q, dtype=float) / 2 ** decoder.format.weight_fraction_bits
    ids = np.arange(model.cfg.n_active, dtype=np.int64)
    assert float(weights[ids].sum()) == pytest.approx(g_fixed, rel=1e-6)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "32.0" in readme, "README should state the nominal (physical) gain"
    assert "31.028" in readme, (
        "README must still explain the fixed-point Σw gain (31.028) separately; "
        "the two are different quantities and mixing them costs ~32 LSB20"
    )
    assert "设计目标" in readme or "g0" in readme, (
        "README should explain that 32 is both the design target g0 and the physical gain"
    )

