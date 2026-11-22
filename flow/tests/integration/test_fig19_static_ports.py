# =============================================================================
# 文件名     : test_fig19_static_ports.py
# 模块名     : Fig.19 两端采样与整数重构对照
# 版本       : v1.0
# 功能       : 核查 US10505561B2 图 19 静态研究的双采样端口、掩码与出码。
# 设计说明   :
#   * 用 Fig11Parameters 的 17 主端口、5 子端口和 13 位 ADC2，
#     单元及桥接失配置零，并显式关闭采样噪声和 RA 噪声。
#   * 采样双端口使用负等效注入。前缀超出 RDAC 码域时不得更新物理片组；
#     终码饱和与模拟越界标志独立核对。
# 变更记录   : 本组固定 Fig11Parameters；生产顶层与默认 Python 的接口对拍见 docs/audit/AUDIT_ALGO_VS_SOURCE.md。
# =============================================================================
"""用独立计算核对 Fig.11 电荷夹具中的图 19 双端口扰动。

正例扫 ±2 V 范围内的输入及全部 64 个扰动字。期望粗码取该夹具的码本阈值，
掩码按实际片号独立推导，ADC2 用夹具声明的窗口和位数复算，最后比较 20 位输出。
负例检查输入格式、非法早期前缀，以及扰动端口与数字符号接错时的检出能力。
"""

from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from adi_model.behavioral.sampler import dc_input
from adi_model.control.dither import (
    FIG19_FRACTIONAL_WEIGHTS_Q,
    FIG19_STEP_Q,
    Fig19DitherProgram,
    Fig19DitherRangeError,
    Fig19StaticSpec,
    complete_flash3_sar6_prefix_table,
)
from experiments.fig11 import Fig11Parameters, fig11_model
from adi_model.simulation.config import ConfigError
from adi_model.simulation.pipeline import run_pipeline
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.mapper import Mapper

STEP_Q = FIG19_STEP_Q


def _word_values(words):
    upper, lower, total = [], [], []
    fractional = [round(Fraction(weight, 7) * STEP_Q) for weight in (4, 2, 1)]
    for word in words:
        word = int(word)
        u = 4 * ((word >> 5) & 1) + ((word >> 3) & 1)
        lo = 2 * ((word >> 4) & 1) * STEP_Q
        lo += sum(
            ((word >> bit) & 1) * weight for bit, weight in zip((2, 1, 0), fractional, strict=True)
        )
        upper.append(u)
        lower.append(lo)
        total.append(lo + u * STEP_Q)
    return np.array(upper), np.array(lower), np.array(total)


def _spec():
    return Fig19StaticSpec(
        3.0,
        STEP_Q,
        32,
        FIG19_FRACTIONAL_WEIGHTS_Q,
        "subtract",
        "Declared static equivalent-port engineering fixture",
    )


@pytest.fixture(scope="module")
def ideal_model():
    return fig11_model(
        parameters=replace(Fig11Parameters(), unit_mismatch_std=0.0, bridge_mismatch_std=0.0),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )


def _codebook(model):
    """返回 Fig.11 研究夹具的码本电压，单位 V。

    粗码从实际阈值表求出。均匀步长外推会把部分输入判到相邻码，
    并随之错判 RDAC 的实际开关和余差。
    """
    return np.asarray(model.codebook.voltage_v, dtype=float)


def _expected_masks(code: int, *, n_main: int, n_sub: int):
    """按 Fig.11 的 17/5 编码构造主/子掩码。

    主阵列 17 列 = 15 个 2C + B4(1C) + S4(1C)，主码 `m = code >> 4`：
        前 `m//2` 个 2C 置高；`m` 为奇数时 B4（索引 15）也置高；S4（索引 16）恒低。
    子阵列 5 个端口中，4 个可切换单元由子码 `s = code & 15` 的位控制
    （bit3..bit0 对应 8/4/2/1）；剩余端口不开关。
    """
    main = np.zeros(n_main, dtype=bool)
    m = code >> 4
    main[: m // 2] = True
    if m % 2 == 1 and n_main > 15:
        main[15] = True
    sub = np.zeros(n_sub, dtype=bool)
    s = code & 15
    for bit, weight in enumerate((8, 4, 2, 1)):
        if bit < n_sub and (s & weight):
            sub[bit] = True
    return main, sub


def _unit_switch_pattern(code: int, *, n_main: int, n_sub: int):
    """把 Fig.11 编码给出各端口的 0/1 开关向量（1 = 接高轨）。"""
    main, sub = _expected_masks(code, n_main=n_main, n_sub=n_sub)
    return np.concatenate((main, sub)).astype(np.int64)


def _round_even_pair(numerator: int, denominator: int) -> int:
    """四舍六入五取偶（定点格式声明的居中规则），纯整数实现。"""
    quotient, remainder = divmod(numerator, denominator)
    twice = 2 * remainder
    if twice > denominator or (twice == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient


# =============================================================================
# 1. 从码位、器件掩码与 ADC2 码箱独立重构
# =============================================================================
def _integer_oracle(parameters, backend_codes, k_codes, total_q, slice_ids, model, physical_gain):
    """用纯 Python 整数复算 20 bit 定点输出，与声明方程同构。

    名义权重取自 Fig.11 夹具，ADC2 窗口和位数取自同一份规格。
    覆盖对象是全部 64 个扰动字产生的样本，每笔使用该次采样的 ADC2 码。

    计算顺序为：

        fine_q = min_q + round_even((2a+1)(max_q − min_q), 2**(adc2_bits+1))
        fine_q = round_even(fine_q · gain, G_phys · 2^wf)      ← 换算到 Σw 口径
        gain   = Σ_{活跃片} Σ_{端口} w                      （signal≡1，见下）
        term   = v_fs·(1 − 2·sw) − T                        （实际 k 掩码的采样公共项）
        known  = Σ_{活跃片} Σ_{端口} w·term
        word   = floor((fine_q·2^wf − known + gain·2^vf)·2^ob / (2·gain·2^vf))

    三个影响对照结果的约定：

    1. **`signal_mask` 全为 1**，它是"哪些端口参与"的有效性掩码，
       不是开关掩码；开关在 `main_masks`/`sub_masks` 里（它们只用于 term 的 ±v_fs）。
       因此 `gain` 等于**全权重和 × 活跃片数**。
    2. **开关用 RDAC 码 k = c − U**（224 减法器之后），不是粗码 c。
       U 非零时，直接使用 c 会比较与实际转换不同的开关状态。
    3. 实际掩码记录 k。由 r = x − T − VD(k)，恢复输入需要 VD(k) + r + T。
       对等步长码本，也可以用原始粗码算 VD(c) + r + L。

    Args:
        parameters: 提供 adc2 窗口与位数。
        backend_codes: 二级 ADC 码 (N,)。
        k_codes: RDAC 码 k = c − U (N,)。
        total_q: 每笔的 T 定点值（3 V 归一的 Q32）(N,)。
        slice_ids: 每笔的活跃物理片 ID (N, n_active)。
        model: 提供定点格式、权重表与窗口常数。
        physical_gain: 本笔 RA 物理增益（`raw.state.estimated_gain`，实测 32.0）。

    Returns:
        (N,) int64，20 bit 输出码。
    """
    fmt = model.nominal_decoder.format
    spec = model.nominal_decoder.spec
    weights_all = np.asarray(model.nominal_decoder.weights_q, dtype=np.int64)
    decoder = model.nominal_decoder
    min_q, max_q = int(decoder.adc2_min_q), int(decoder.adc2_max_q)
    offset_q = int(decoder.offset_q)
    vscale = 1 << fmt.voltage_fraction_bits
    wscale = 1 << fmt.weight_fraction_bits
    count = 1 << fmt.output_bits
    scale_q = int(round(float(physical_gain) * wscale))
    slice_ids = np.asarray(slice_ids)
    n_ports = weights_all.shape[1]
    ring = 15
    result = []
    for index, (backend, k, injection) in enumerate(
        zip(backend_codes, k_codes, total_q, strict=True)
    ):
        phased = np.empty((slice_ids.shape[1], n_ports), dtype=np.int64)
        for slot in range(slice_ids.shape[1]):
            row = _unit_switch_pattern(int(k), n_main=17, n_sub=n_ports - 17)
            row[:ring] = np.roll(row[:ring], int(slice_ids[index, slot]) % ring)
            phased[slot] = row
        active = slice_ids.shape[1]
        gain = int(weights_all[0].sum()) * active
        term_v = spec.v_fs * (1 - 2 * phased) - int(injection) / 2**32 * 3.0
        terms_q = np.rint(term_v / spec.v_fs * vscale).astype(np.int64)
        # ⚠️ 必须用 **Python 任意精度整数求和**，不能用 numpy 求和：
        # 单个乘积约 2.6e8 × 4.3e9 ≈ 1.1e18，8 片 × 22 端口的和可达 ~1e20，
        # **超过 int64 上限（9.2e18）会发生静默溢出**。
        # 实测：V=−2.0 时 numpy 求和得 3.26e18（溢出后），正确值应为 9.55e19。
        # 实现内部用的是 `sum(term_products)`（Python 整数），因此不会溢出。
        weight_row = np.broadcast_to(weights_all[0], terms_q.shape)
        known = sum(
            int(weight) * int(term)
            for weight, term in zip(weight_row.flat, terms_q.flat, strict=True)
        )
        fine_q = min_q + _round_even_pair(
            (2 * int(backend) + 1) * (max_q - min_q), 2 ** (spec.adc2_n_bits + 1)
        )
        fine_q = _round_even_pair(fine_q * gain, scale_q)
        numerator = (fine_q - offset_q) * wscale - known
        word = ((numerator + gain * vscale) * count) // (2 * gain * vscale)
        result.append(min(max(word, 0), count - 1))
    return np.array(result, dtype=np.int64)


def _assert_physical_and_integer_views(run, external, words, model):
    """独立核对 Fig19 双端口的整链自洽性（17/5 口径）。"""
    parameters = model.parameters
    book = _codebook(model)
    upper, lower_q, total_q = _word_values(words)
    lower_v, total_v = lower_q * (3 / 2**32), total_q * (3 / 2**32)
    # 判码规则：阈值即码本电压，x 落在 [v(k), v(k+1)) 中
    coarse = np.clip(
        np.searchsorted(book, external - lower_v, side="right") - 1, 0, book.size - 1
    ).astype(np.int64)
    k = coarse - upper
    vd_c = book[coarse]
    vd_k = book[k]
    # RDAC 以总扰动 T 采样，并切换到减去 U 后的实际码 k。
    # 从该码的实际码本电压计算余差，不把粗码与转换码视作同一开关状态。
    residue = external - total_v - vd_k
    np.testing.assert_array_equal(run.raw.dither_program.words, words)
    np.testing.assert_array_equal(run.raw.dither_program.lower_q, lower_q)
    np.testing.assert_array_equal(run.raw.dither_program.total_q, total_q)
    np.testing.assert_array_equal(run.raw.coarse, coarse)
    np.testing.assert_array_equal(run.raw.k, k)
    np.testing.assert_allclose(run.raw.sample.x_sadc, external - lower_v, atol=0, rtol=0)
    np.testing.assert_allclose(run.raw.sample.rdac_input_v, external - total_v, atol=0, rtol=0)
    np.testing.assert_allclose(
        run.raw.sampled_effective_charge_c,
        -run.raw.normalization_capacitance_f * (external - total_v),
        atol=1e-28,
        rtol=32 * np.finfo(float).eps,
    )
    np.testing.assert_allclose(run.raw.residue, residue, atol=2e-14, rtol=0)
    # 用名义 RA 增益复算的电压采用相对容差，容许电容比的数值舍入。
    # 后续定点重构另取本次记录的物理增益，以匹配权重表的尺度。
    np.testing.assert_allclose(run.raw.vra, 32 * residue, rtol=1e-7, atol=0)
    # 回代原输入应使用实际转换码 k 的电压和对应的总采样注入 T。
    # 这个恒等式不依赖粗码与转换码之间的步进近似。
    np.testing.assert_allclose(vd_k + residue + total_v, external, atol=5e-16, rtol=0)
    expected_events = (
        np.array([[(int(c) >> shift) << shift for shift in range(6, -1, -1)] for c in coarse])
        - upper[:, None]
    )
    np.testing.assert_array_equal(run.raw.dither_trace.rdac_event_codes, expected_events)
    assert not run.raw.dither_trace.event_range_overflow.any()
    # 掩码按实际端口维度构造。固定片内映射用物理片号决定 15 个 2C 主单元的起点。
    # 不同笔的活跃片号可以不同，所以相同 k 仍需逐笔对照实际掩码。
    # B4 和 S4 不随主单元环旋转，子阵列也按固定权重位解释。
    slice_ids = np.asarray(run.raw.acq_slice_ids)
    n_main, n_sub = run.raw.main_masks.shape[-1], run.raw.sub_masks.shape[-1]
    n_active = run.raw.main_masks.shape[1]
    ring = 15
    expected_main = np.zeros_like(run.raw.main_masks)
    expected_sub = np.zeros_like(run.raw.sub_masks)
    for index, code in enumerate(k):
        template_main, template_sub = _expected_masks(int(code), n_main=n_main, n_sub=n_sub)
        for slot in range(n_active):
            phase = int(slice_ids[index, slot]) % ring
            phased = np.zeros(n_main, dtype=bool)
            phased[:ring] = np.roll(template_main[:ring], phase)
            if n_main > ring:
                phased[ring:] = template_main[ring:]
            expected_main[index, slot] = phased
            expected_sub[index, slot] = template_sub
    np.testing.assert_array_equal(run.raw.main_masks, expected_main)
    np.testing.assert_array_equal(run.raw.sub_masks, expected_sub)
    masks = np.concatenate((expected_main, expected_sub), axis=2)
    # 从字位独立解出 T；只对实际 k 掩码补 T。若误用 L，单开 D6 将少约四个粗码。
    expected_term = -total_v[:, None, None] + 3 * (1 - 2 * masks.astype(np.int64))
    np.testing.assert_array_equal(run.raw.digital_observation.known_voltage_term_v, expected_term)
    np.testing.assert_array_equal(run.raw.dither_program.known_common_q, -total_q)
    exported = run.raw.binding_metadata["dither"]
    assert "input_v - T" in exported["stored_rdac_input"]
    assert "applied common voltage term = -total_v" in exported["digital_known_common_q"]
    # ADC2 量化步长从同一份参数的窗口和后端位数计算。
    # 先查 13 位原始码，再比较它进入定点重构后的 20 位输出。
    raw_voltage = 32 * residue
    backend_step = (parameters.adc2_v_max - parameters.adc2_v_min) / 2**parameters.adc2_n_bits
    backend = np.floor((raw_voltage - parameters.adc2_v_min) / backend_step).astype(np.int64)
    np.testing.assert_array_equal(run.raw.adc2_code, backend)
    np.testing.assert_array_equal(run.codes.code, _integer_oracle(
            parameters, backend, k, total_q,
            run.raw.digital_observation.slice_ids,
            model,
            run.raw.state.estimated_gain,
        ))
    assert not run.codes.analog_overflow.any()
    assert not run.codes.clipped_low.any() and not run.codes.clipped_high.any()



@pytest.mark.parametrize("voltage", [-2.0, 0.0, 0.31, 2.0])
def test_all_sixty_four_words_reach_both_real_analog_nodes_and_final_integer_code(
    ideal_model, voltage
):
    words = np.arange(64)
    run = ideal_model.simulate(dc_input(voltage), 64, dither_words=words)
    _assert_physical_and_integer_views(run, np.full(64, voltage), words, ideal_model)
    np.testing.assert_allclose(run.raw.out, voltage, rtol=0, atol=6 / 2**20)
    assert np.ptp(np.asarray(run.codes.code, dtype=np.int64)) <= 1
    np.testing.assert_array_equal(run.raw.sample_id, np.arange(64))
    np.testing.assert_array_equal(
        run.raw.held_sample_ids, np.broadcast_to(np.arange(64)[:, None], (64, 8))
    )
    words[:] = 0
    np.testing.assert_array_equal(run.raw.dither_program.words, np.arange(64))


def test_same_lower_different_upper_keeps_residue_but_changes_real_charge_and_masks(ideal_model):
    run = ideal_model.simulate(dc_input(0.31), 64, dither_words=np.arange(64))
    np.testing.assert_array_equal(run.raw.coarse[[0, 8, 32, 40]], run.raw.coarse[0])
    # U 段同时进入总采样注入和 RDAC 转换命令，两路使用同一码本尺度。
    # 只改变 U 的样本应呈现相同余差。允许 1 nV 的差用于有限精度，
    # 同时独立判断物理电荷和实际开关确实随 U 改变。
    np.testing.assert_allclose(
        run.raw.residue[[0, 8, 32, 40]], run.raw.residue[0], atol=1e-9, rtol=0
    )
    assert np.unique(run.raw.k[[0, 8, 32, 40]]).size == 4
    assert np.unique(run.raw.sampled_effective_charge_c[[0, 8, 32, 40]]).size == 4
    assert np.any(run.raw.main_masks[0] != run.raw.main_masks[32]) or np.any(
        run.raw.sub_masks[0] != run.raw.sub_masks[32]
    )
    assert run.raw.coarse[0] != run.raw.coarse[16]
    assert run.raw.residue[0] != run.raw.residue[4]


def test_illegal_early_prefix_never_updates_the_rdac_even_when_final_k_is_valid(ideal_model):
    template = ideal_model.simulate(dc_input(0.2), 1)
    old = template.raw.pool
    cfg = replace(ideal_model.cfg, dither_enable=True)
    pool = PhysicalSlicePool(
        cfg,
        chip=ideal_model.chip,
        nominal_codebook=ideal_model.codebook,
        mask_binding=old.mask_binding,
        acquisition_binding=old.acquisition_binding,
        schedule_binding=old.schedule_binding,
    )
    mapper = Mapper(
        np.arange(512),
        nominal_codebook=ideal_model.codebook,
        event_codes_by_coarse=complete_flash3_sar6_prefix_table(),
    )
    with pytest.raises(Fig19DitherRangeError) as rejected:
        run_pipeline(
            cfg,
            dc_input(-2.9),
            1,
            pool=pool,
            mapper=mapper,
            sample_ids=np.array([0]),
            dem_states=np.array([0]),
            sampled_top_v=0.0,
            sampled_subnode_v=0.0,
            reference_high_v=3.0,
            reference_low_v=-3.0,
            nominal_reference_high_v=3.0,
            nominal_reference_low_v=-3.0,
            static_charge_domain=True,
            dither_program=Fig19DitherProgram.from_words([32], spec=_spec()),
        )
    np.testing.assert_array_equal(rejected.value.trace.rdac_code, [4])
    np.testing.assert_array_equal(
        rejected.value.trace.rdac_event_codes[0], [-4, -4, -4, 4, 4, 4, 4]
    )
    assert not pool.n_conversions.any()


@pytest.mark.parametrize("voltage,word", [(3.2, 0), (-3.2, 16)])
def test_each_analog_node_flag_survives_even_if_final_code_does_not_clip(
    ideal_model, voltage, word
):
    run = ideal_model.simulate(dc_input(voltage), 4, dither_words=np.full(4, word))
    assert len(run.codes.code) == 4
    assert run.raw.sadc_over.all() and run.raw.ra_sat.all() and run.raw.adc2_over.all()
    assert not run.raw.rdac_over.any()
    assert run.codes.analog_overflow.all()
    if voltage < 0:
        assert not run.codes.clipped_low.any() and not run.codes.clipped_high.any()
    else:
        assert run.codes.clipped_high.all()


@pytest.mark.parametrize("words", [[], [-1], [64], [1.0], [True], [[0]]])
def test_words_are_explicit_bounded_integers_not_generated_or_coerced(words):
    with pytest.raises(ConfigError):
        Fig19DitherProgram.from_words(words, spec=_spec())


def test_wrong_bit_assignment_or_common_voltage_sign_cannot_be_a_valid_program():
    actual = Fig19DitherProgram.from_words([8, 16], spec=_spec())
    assert actual.upper_code.tolist() == [1, 0]
    assert actual.lower_q.tolist() == [0, 2 * STEP_Q]
    with pytest.raises(ConfigError):
        replace(actual, upper_code=np.array([0, 1]))
    with pytest.raises(ConfigError):
        replace(actual, total_q=-actual.total_q)
    with pytest.raises(ConfigError, match="4:2:1"):
        replace(
            _spec(),
            fractional_lower_weights_q=(
                FIG19_FRACTIONAL_WEIGHTS_Q[2],
                FIG19_FRACTIONAL_WEIGHTS_Q[2],
                FIG19_STEP_Q - 2 * FIG19_FRACTIONAL_WEIGHTS_Q[2],
            ),
        )


def test_source_dither_request_needs_words_and_cannot_silently_keep_default_noise():
    requested = fig11_model(config_overrides={"dither_enable": True})
    with pytest.raises(ConfigError, match="dither|words|program"):
        requested.simulate(dc_input(0.31), 8)
    with pytest.raises(ConfigError, match="zero-noise"):
        fig11_model().simulate(dc_input(0.31), 64, dither_words=np.arange(64))


@pytest.mark.parametrize(
    "fault",
    [
        "swap_d5_d4",
        "missing_sadc_port",
        "missing_rdac_port",
        "reversed_digital_sign",
        "one_q32_common_error",
    ],
)
def test_independent_oracle_rejects_faulted_real_model_wiring(ideal_model, monkeypatch, fault):
    """在内存中故意改变真实端口/数字项，再要求独立oracle检出；非修正错数据。"""
    # 在实际流水线使用的模块中注入故障，再让独立 oracle 核查该次运行。
    # monkeypatch 在用例退出时复原端口和数字项，避免污染后续正例。
    from adi_model.simulation import pipeline_engine

    words = np.arange(64)
    if fault == "swap_d5_d4":

        def bad_lower(program):
            values = program.words
            swapped = (values & ~24) | (((values >> 4) & 1) << 3) | (((values >> 3) & 1) << 4)
            return _word_values(swapped)[1] * (3 / 2**32)

        monkeypatch.setattr(Fig19DitherProgram, "lower_v", property(bad_lower))
    elif fault == "missing_sadc_port":
        monkeypatch.setattr(
            Fig19DitherProgram, "lower_v", property(lambda p: np.zeros(len(p.words)))
        )
    elif fault == "missing_rdac_port":
        monkeypatch.setattr(
            Fig19DitherProgram, "total_v", property(lambda p: np.zeros(len(p.words)))
        )
    else:
        original = pipeline_engine.digital_connection_terms
        total = _word_values(words)[2] * (3 / 2**32)

        def bad_term(*args, **kwargs):
            signal, term = original(*args, **kwargs)
            addition = 2 * total[:, None, None] if fault == "reversed_digital_sign" else 3 / 2**32
            return signal, term + addition

        monkeypatch.setattr(pipeline_engine, "digital_connection_terms", bad_term)
    corrupted = ideal_model.simulate(dc_input(0.31), 64, dither_words=words)
    with pytest.raises(AssertionError):
        _assert_physical_and_integer_views(
            corrupted, np.full(64, 0.31), words, ideal_model
        )


def test_only_postprocessing_an_ordinary_run_cannot_satisfy_either_analog_port_contract(
    ideal_model,
):
    words = np.arange(64)
    ordinary = ideal_model.simulate(dc_input(0.31), 64)
    _, lower_q, total_q = _word_values(words)
    # 不论如何改最终码，真实采样记录仍为x，不能冒充已施加lower/total的端口。
    with pytest.raises(AssertionError):
        np.testing.assert_array_equal(ordinary.raw.sample.x_sadc, 0.31 - lower_q * (3 / 2**32))
    with pytest.raises(AssertionError):
        np.testing.assert_array_equal(
            ordinary.raw.sample.rdac_input_v, 0.31 - total_q * (3 / 2**32)
        )
