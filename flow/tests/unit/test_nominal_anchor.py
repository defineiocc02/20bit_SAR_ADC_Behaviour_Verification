"""名义网络必须同时满足三个可独立核对的事实（20.5 pF / β=1/16 / G=32 / 端点闭合）。

## 这个测试守的缺陷

模型声称以论文披露的 **20.5 pF** 有源电容为锚点，但 `nominal_active_cap_f` 曾经是
**19.8776 pF**、`nominal_gain` 是 **31.028**：

    19.8776 pF = 8 × (31 + 15/16) × Cu        ← 只数了参与码的 16 个主单元
    20.5000 pF = 8 × (32 + 15/16) × Cu        ← 采样期 17 个主单元全接信号（含 S4）

差的那 1C 就是 FIG.19 顶层的 S4。它在**采样**时接输入（所以输入看到的电容、RA 的残差
归一化都含它），但它在**转换**时不被任何码选中（九个码位只覆盖 15×2C + B4 的 31C）。
于是同一片阵列有两个**不同**的归一化电容，混用就差 3.13%：

| 口径 | 定义 | 值/片 | ×8 片 | 用途 |
|:---|:---|:---|:---|:---|
| `C_samp` | 32C + β·15C | 32.9375C | **20.5 pF**（锚点）| 输入电容、RA 物理增益 G = C_samp/Cf = 32.0 |
| `C_conv` | 31C + β·15C | 31.9375C | 19.8776 pF | 码本分母（必须 == drive(511)）、定点重建 Σw |

`C_conv` 不能改成 `C_samp`：drive(511) 只能到 31 + 15/16 C（S4 无码位），若分母取
32.9375C，511 码只到 +2.818 V，顶端 3% 输入全部溢出；若硬把 S4 塞进码（末级 2C 跳变），
495/496 码之间会留 17 LSB 的空档，20 bit 目标直接废掉。所以两个口径必须**分开命名**：
`nominal_active_cap_f`（C_samp）与 `nominal_conversion_cap_f`（C_conv）。

## 断言的三个事实

1. `8 × (32 + 15/16) × Cu == 20.5 pF`（相对 1e-4）——锚点自洽；
2. `nominal_gain == 32.0`（相对 1e-4）——与 Cf = 20.5 pF/32 一致；
3. `drive(511) / C_conv == 1.0`（相对 1e-6）——端点闭合，用**现有的码本/掩码接口**算，
   不另写公式（掩码 × 权重表 = 解码器权重和 = 码本分母，三者同源）。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from adi_model.control.dem3d import fig11_masks
from adi_model.defaults import DefaultModel, DefaultParameters, default_model
from adi_model.topology.capacitors import GROUP_SLICES, MAIN_WEIGHTS, SUB_WEIGHTS

ANCHOR_F = 20.5e-12  # 论文披露的有源电容锚点
MAIN_SAMPLED_C = 32.0  # 15×2C + B4 1C + S4 1C
MAIN_SWITCHED_C = 31.0  # 只有 15×2C + B4 有码位
SUB_SWITCHED_C = 15.0  # 8C + 4C + 2C + 1C
BETA = 1.0 / 16.0  # Cb/(Cb + ΣB) = C/(C + 15C)
UNIT_TOL = 1e-4  # 锚点/增益判据（要求 <1e-4 相对）
ENDPOINT_TOL = 1e-6  # 端点闭合判据

# ⚠️ 凡是比较**带单位的物理量**（电容 F、电压 V），`approx` 必须显式写 `abs=0`。
# `pytest.approx` 的容差是 `max(rel*|expected|, abs)`，而 `abs` 的默认值是 **1e-12**，
# 写 `rel` 不会把它归零。在 20.5 pF（2.05e-11 F）上，`abs=1e-12` 相当于 **±4.88%**：
#     19.877609e-12 == approx(20.5e-12, rel=1e-4)          → True   ← 缺陷值照样通过
#     19.877609e-12 == approx(20.5e-12, rel=1e-4, abs=0)   → False  ← 才能抓住
# 本轮真踩过：把 `defaults.py` 回退到 1C 缺口的缺陷态，锚点断言**仍然通过**。
# 无量纲量（gain、beta、比值、以 C 为单位的单元数）的 1e-12 可忽略，但也一律显式写，
# 免得下次有人照着"能过"的写法复制到电容上。


def _model() -> DefaultModel:
    """默认名义模型（制造值存在但不进入名义系数）。"""
    return DefaultModel(seed=1, parameters=DefaultParameters(), config_overrides={})


def _drive_units(model: DefaultModel, code: int) -> float:
    """用**现有掩码接口**（`fig11_masks`）× 权重表算该码的驱动权重，单位 C/片。

    刻意不在这里重写 `main_code → therm/ones` 的分解律：那等于给同一件事开第二个
    定义点。掩码绑定是主链真正使用的那一个（`DefaultModel.simulate` 把它交给
    `PhysicalSlicePool`），因此它的输出就是模型的驱动量。
    """
    ids = np.broadcast_to(
        np.arange(model.cfg.n_active, dtype=np.int64), (1, model.cfg.n_active)
    )
    command = fig11_masks(
        model.geometry,
        np.array([code], dtype=np.int64),
        np.zeros(1, dtype=np.int64),
        ids,
    )
    main_on = np.asarray(command.main_on)[0, 0]
    sub_on = np.asarray(command.sub_on)[0, 0]
    # 子阵列物理第 5 列是恒接低轨的哑单元（权重 0），端口宽度 5、装配 4 列。
    sub_weights = np.concatenate([np.asarray(SUB_WEIGHTS, float), [0.0] * (sub_on.size - len(SUB_WEIGHTS))])
    main_units = float(main_on.astype(float) @ np.asarray(MAIN_WEIGHTS, float))
    sub_units = float(sub_on.astype(float) @ sub_weights)
    return main_units + model.nominal_beta * sub_units


def test_unit_geometry_is_the_figure_19_array():
    """先钉住几何本身：15×2C + 2×1C 主、8/4/2/1 子。否则下面三条断言没有意义。"""
    assert tuple(MAIN_WEIGHTS) == (2,) * 15 + (1, 1)
    assert tuple(SUB_WEIGHTS) == (8, 4, 2, 1)
    assert sum(MAIN_WEIGHTS) == MAIN_SAMPLED_C
    assert sum(MAIN_WEIGHTS[:15]) + MAIN_WEIGHTS[15] == MAIN_SWITCHED_C
    assert sum(SUB_WEIGHTS) == SUB_SWITCHED_C
    assert GROUP_SLICES == 8


def test_active_capacitance_matches_the_paper_anchor():
    """`8 × (32 + 15/16) × Cu == 20.5 pF` —— 锚点、单位电容与几何三者自洽。"""
    model = _model()
    parameters = model.parameters
    per_slice_units = MAIN_SAMPLED_C + BETA * SUB_SWITCHED_C
    assert per_slice_units == pytest.approx(32.9375, rel=1e-12, abs=0)
    total_f = GROUP_SLICES * per_slice_units * parameters.unit_cap_f
    assert total_f == pytest.approx(ANCHOR_F, rel=UNIT_TOL, abs=0), (
        "8 slices of the FIG.19 main array must equal the disclosed 20.5 pF anchor; "
        f"got {total_f * 1e12:.6f} pF"
    )
    # 模型对外报的就是这个量（而不是只含 16 列的 19.8776 pF）
    assert model.nominal_active_cap_f == pytest.approx(total_f, rel=1e-12, abs=0)
    assert model.nominal_beta == pytest.approx(BETA, rel=1e-12, abs=0)
    # 单位电容必须由锚点反推，不能沿用 63+8 时代的 40.04 fF
    assert parameters.unit_cap_f == pytest.approx(
        ANCHOR_F / (GROUP_SLICES * per_slice_units), rel=1e-6, abs=0
    )


def test_nominal_gain_is_the_design_target():
    """`nominal_gain == 32.0` —— 这是 RA 的物理增益 C_samp/Cf，不是 Σw 口径的 31.028。"""
    model = default_model()
    assert model.nominal_gain == pytest.approx(32.0, rel=UNIT_TOL, abs=0), (
        "nominal_gain must be C_samp/Cf = 20.5 pF / 640.625 fF = 32; "
        f"got {model.nominal_gain:.6f}"
    )
    # 与 Cf = 20.5 pF/32 自洽（G 保持 32 是论文给的设计目标）
    assert model.parameters.feedback_cap_f == pytest.approx(ANCHOR_F / 32.0, rel=1e-12, abs=0)
    assert model.nominal_gain == pytest.approx(
        model.nominal_active_cap_f / model.parameters.feedback_cap_f, rel=1e-12, abs=0
    )
    # g0 是同一个数的另一来源：两者现在必须一致（曾经一个是 32、一个是 31.028）
    assert model.nominal_gain == pytest.approx(model.cfg.g0, rel=UNIT_TOL, abs=0)


def test_nominal_gain_is_bit_identical_to_the_plant_gain_vector():
    """`nominal_gain` 必须**严格等于** plant 自己算出的 `raw.g_vec`（零失配下逐位相同）。

    这是"名义值与器件同源"的最强判据，也是比分母口径推理更干净的一条：

    * 缺陷态（`nominal_active_cap_f` 用码本口径 31.9375C）下比值为 **0.9696**
      （31.028 / 32.0），也就是 3.13% 的口径错位；
    * 修复后比值 **1.0**，且浮点逐位相等（两边是同一个 C_samp/Cf 表达式）。

    为什么把失配归零：`unit_mismatch_std=1e-4` 是**设计内的物理随机**，逐个 seed 会让 8 片
    的实际电容偏离名义值，`g_vec` 因此稳定偏离约 3e-6（实测 3.18e-6）。那不是口径错误，
    用 1e-6 阈值去卡它只会得到一条"因为物理随机而随 seed 抖"的测试。关掉失配后两者是
    同一个表达式，可以要求**严格相等**（不用 approx），缺陷态则必失败。
    """
    parameters = replace(
        DefaultParameters(), unit_mismatch_std=0.0, bridge_mismatch_std=0.0
    )
    model = DefaultModel(seed=1, parameters=parameters, config_overrides={})
    raw = model.simulate(lambda t: np.zeros_like(np.asarray(t, float)), 8).raw
    g_vec = np.asarray(raw.g_vec, dtype=float)
    assert np.all(g_vec == model.nominal_gain), (
        "nominal_gain must be the same expression the plant uses, not merely close: "
        f"nominal={model.nominal_gain!r} g_vec={g_vec!r}"
    )
    # 顺便钉住采样口径本身（这是 g_vec 的分子）
    c_norm = np.asarray(raw.normalization_capacitance_f, dtype=float)
    assert np.all(c_norm == model.nominal_active_cap_f), (
        f"plant Cnorm should be the sampling capacitance {model.nominal_active_cap_f!r}, "
        f"got {c_norm!r}"
    )


def test_code_span_closes_the_endpoints():
    """`drive(511) / C_conv == 1` —— 码本分母必须等于满码驱动量，否则端点不闭合。"""
    model = _model()
    unit = model.parameters.unit_cap_f
    cspan_units = model.nominal_conversion_cap_f / (model.cfg.n_active * unit)
    assert cspan_units == pytest.approx(
        MAIN_SWITCHED_C + BETA * SUB_SWITCHED_C, rel=1e-12, abs=0
    )

    drive_511 = _drive_units(model, 511)
    assert drive_511 == pytest.approx(
        MAIN_SWITCHED_C + BETA * SUB_SWITCHED_C, rel=1e-12, abs=0
    ), f"drive(511) should be 31 + 15/16 = 31.9375 C/slice, got {drive_511:.9f}"
    ratio = drive_511 / cspan_units
    assert ratio == pytest.approx(1.0, rel=ENDPOINT_TOL, abs=0), (
        "drive(511) must equal the codebook denominator so that code 511 lands on +v_fs; "
        f"got ratio {ratio:.9f}"
    )
    # 另一端：码 0 的驱动必须是 0（否则 -v_fs 落不到码 0 上）。
    # 期望值是 0，`rel` 无效，只能给绝对值：1e-12 个**电容单元**（≈7.8e-26 F），严格到荒谬。
    assert _drive_units(model, 0) == pytest.approx(0.0, abs=1e-12)
    # 码本电压表的两个端点由这条闭合判据直接给出
    assert model.codebook.voltage_v[0] == pytest.approx(-model.cfg.v_fs, rel=1e-12, abs=0)
    assert model.codebook.voltage_v[511] == pytest.approx(model.cfg.v_fs, rel=1e-12, abs=0)
    # 单调：drive 随码严格递增（512 码全覆盖）
    drives = np.array([_drive_units(model, code) for code in range(512)])
    assert np.all(np.diff(drives) > 0), "drive(code) must be strictly increasing"


def test_decoder_weight_sum_shares_the_codebook_denominator():
    """解码器 Σw、掩码 drive(511)、码本 C_conv 必须是同一个量（1C 的口径差不能再回来）。"""
    model = _model()
    decoder = model.nominal_decoder
    weights = np.asarray(decoder.weights_q, dtype=float) / 2 ** decoder.format.weight_fraction_bits
    unit = model.parameters.unit_cap_f
    feedback = model.parameters.feedback_cap_f
    ids = np.arange(model.cfg.n_active, dtype=np.int64)
    # 权重是无量纲的 C/Cf，Σw 覆盖本次转换的 8 片；折回"每片多少 C"再与码本分母比。
    sigma_w_c = (
        float(weights[ids].sum()) * feedback / unit / model.cfg.n_active
    )
    cspan_units = model.nominal_conversion_cap_f / (model.cfg.n_active * unit)
    # 哑单元权重是 2/2^Q 的极小正值（电学上可忽略），容差取 1e-6 而非 1e-12
    assert sigma_w_c == pytest.approx(cspan_units, rel=1e-6, abs=0)
    assert sigma_w_c == pytest.approx(_drive_units(model, 511), rel=1e-6, abs=0)


def test_sampling_and_conversion_capacitances_differ_by_exactly_one_unit():
    """两个口径的差必须是**恰好 1C/片**（S4），且 C_samp 是对外报的那个。"""
    model = _model()
    unit = model.parameters.unit_cap_f
    delta_units = (
        model.nominal_active_cap_f - model.nominal_conversion_cap_f
    ) / (model.cfg.n_active * unit)
    assert delta_units == pytest.approx(1.0, rel=1e-9, abs=0), (
        "the sampling network carries exactly one extra 1C (S4) that the code cannot drive; "
        f"got {delta_units:.9f} C/slice"
    )
    # 大头是采样口径：锚点 20.5 pF 必须落在 nominal_active_cap_f 上
    assert model.nominal_active_cap_f > model.nominal_conversion_cap_f
    assert model.nominal_active_cap_f == pytest.approx(ANCHOR_F, rel=UNIT_TOL, abs=0)
