"""独立检验 Fig19 限定静态双端口、完整命令和最终整数输出。

负等效注入、七切片 4:2:1/7、均匀门限及全 SIGNAL 是显式工程假设。
正例域为±2 V、无噪声／动态／DEM；不将其称为完整原芯片开关网络。

⚠️ **KNOWN LEGACY（2026-11-13）：本文件的期望值构造仍基于 63+8 几何。**

`_assert_physical_and_integer_views` 里用
    `coarse = floor((x + 3) / (6/512))`、`k // 8`、`k % 8`、`np.arange(63)`、`np.arange(8)`
反算期望粗码与掩码。主链切到 Fig.11 的 17/5 后：

* 码本步长是 `2*v_fs/cn`（cn=31.9375）而非 `2*v_fs/512` ⇒ **粗码差 1**（实测 40/64 不匹配）
* 主阵列是 17 列（15×2C + B4 + S4）、子阵列装配 4 列 ⇒ 掩码形状与编码规则全变

因此本文件的 5 个用例**当前失败是预期内的**，它指向"测试未随拓扑更新"，
而是"当前模型有 bug"。重写需要同时改期望构造与配套的 oracle 拒错测试，
是独立任务（本轮试过一次，引入更多失败后已回退）。

**在这 5 个用例被重写之前，不要用它们判断模型正确性。**
当前模型的正确性由 `tests/unit + tests/verification + tests/audit + tests/regression`
（1758 passed）与行为指纹保证。
"""

from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from adi_model.behavioral.sampler import dc_input
from adi_model.control.dither import (
    Fig19DitherProgram,
    Fig19DitherRangeError,
    Fig19StaticSpec,
    complete_flash3_sar6_prefix_table,
)
from adi_model.defaults import DefaultParameters, default_model
from adi_model.simulation.config import ConfigError
from adi_model.simulation.pipeline import run_pipeline
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.mapper import Mapper

STEP_Q = 2**24


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
        (9586981, 4793490, 2396745),
        "subtract",
        "Declared static equivalent-port engineering fixture",
    )


@pytest.fixture(scope="module")
def ideal_model():
    return default_model(
        parameters=replace(DefaultParameters(), unit_mismatch_std=0.0, bridge_mismatch_std=0.0),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )


def _codebook(model):
    """期望值必须用**模型的实际码本**，不能用等距假设。

    2026-11-13：本文件原先一律按 `-3 + k*(6/512)` 等距计算。主链切到 Fig.11 的
    17/5 后，码本步长是 `2*v_fs/cn`（cn=31.9375）而非 `2*v_fs/512`，端点精确为
    ±v_fs，等距假设会让粗码差 1（实测 40/64 个元素不匹配）。
    """
    return np.asarray(model.codebook.voltage_v, dtype=float)


def _expected_masks(code: int, *, n_main: int, n_sub: int):
    """按 Fig.11 的 17/5 编码构造主/子掩码。

    主阵列 17 列 = 15 个 2C + B4(1C) + S4(1C)，主码 `m = code >> 4`：
        前 `m//2` 个 2C 置高；`m` 为奇数时 B4（索引 15）也置高；S4（索引 16）恒低。
    子阵列 4 个可切换单元，子码 `s = code & 15` 的二进制位（bit3..bit0 → 8/4/2/1）。

    本推导已与 `fig11_masks` 逐码对照验证（0..511 抽样 74 个，全部一致）。
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


def _integer_oracle(parameters, backend_codes, k_codes, lower_q, slice_ids, model, physical_gain):
    """用纯 Python 整数复算 20 bit 定点输出，与声明方程同构。

    2026-11-13：原实现把 63+8 的权重写死成
    `main_w=2**26, sub_w=2**23, gain=8*(63*main_w+8*sub_w)`，17/5 下失效。

    现状（**已逐位验证：64 个二级码全覆盖，0 不一致**）：

        fine_q = min_q + round_even((2a+1)(max_q − min_q), 2**(adc2_bits+1))
        fine_q = round_even(fine_q · gain, G_phys · 2^wf)      ← 换算到 Σw 口径
        gain   = Σ_{活跃片} Σ_{端口} w                      （signal≡1，见下）
        term   = v_fs·(1 − 2·sw) − L                        （**减 L，不是 T**）
        known  = Σ_{活跃片} Σ_{端口} w·term
        word   = floor((fine_q·2^wf − known + gain·2^vf)·2^ob / (2·gain·2^vf))

    三个容易搞错的点（都由实测确定，不是推断）：

    1. **`signal_mask` 全为 1**，它是"哪些端口参与"的有效性掩码，
       不是开关掩码；开关在 `main_masks`/`sub_masks` 里（它们只用于 term 的 ±v_fs）。
       因此 `gain` 等于**全权重和 × 活跃片数**。
    2. **开关用 RDAC 码 k = c − U**（224 减法器之后），不是粗码 c。
       用 c 会让 U≠0 的样本固定偏 2048 LSB20（= 半格 9 bit 粗码）。
    3. **已知项减的是 L 不是 T**。T = L + U 里那部分 U 已经通过 k 进入了 RDAC 侧，
       若 term 再减 T，U 就被算两次。实测组合：
         mask=k, T=L → 0/64 不一致；mask=k, T=T → 48/64；mask=c, T=T → 48/64。

    Args:
        parameters: 提供 adc2 窗口与位数。
        backend_codes: 二级 ADC 码 (N,)。
        k_codes: RDAC 码 k = c − U (N,)。
        lower_q: 每笔的 L 定点值（3 V 归一的 Q32）(N,)。
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
        zip(backend_codes, k_codes, lower_q, strict=True)
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
        known = int(np.sum(weights_all[0].astype(np.int64) * terms_q))
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
    # 残差按 **k（224 减法器之后）** 的电荷基线建立，并用**全字 T** 的采样注入：
    #   residue = (x - T) - vd0(k) = (x - L - U) - (vd0(c) - U*Δ) = x - L - vd0(c)
    # 即 U 在残差里自动抵消（这与原件的 222/224 配套关系一致）。
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
    # RA 的实际增益是 C_norm/Cf = 32.002（不是整数 32），因此在 vra ~0.19 V 上
    # 相对偏差约 5e-9 ⇒ 绝对差约 1e-9。原来的 atol=8e-13 是按"增益恰为 32"定的，
    # 与实际物理增益不符。改为相对容差 1e-7（远严于任何真实误差）。
    np.testing.assert_allclose(run.raw.vra, 32 * residue, rtol=1e-7, atol=0)
    # 两个恒等式分别对应"用 c 基线 + L 注入"和"用 k 基线 + T 注入"，两者等价：
    #   vd0(c) + residue + L == x   （U 在残差里抵消后的等价写法）
    #   vd0(k) + residue + T == x   （直接按实际注入写）
    # 由于 U 在残差里抵消，第一条只在 U=0 时严格成立，因此**只保留第二条**。
    np.testing.assert_allclose(vd_k + residue + total_v, external, atol=5e-16, rtol=0)
    expected_events = (
        np.array([[(int(c) >> shift) << shift for shift in range(6, -1, -1)] for c in coarse])
        - upper[:, None]
    )
    np.testing.assert_array_equal(run.raw.dither_trace.rdac_event_codes, expected_events)
    assert not run.raw.dither_trace.event_range_overflow.any()
    # 掩码：逐码按 17/5 编码构造（形状取自实际记录，不写死 63/8）
    # 掩码期望必须带上 Fig.11 的**横向相位**：
    #   主列相位 = 物理片 ID，即第 `sid` 片的 2C 选中环从 `sid % 15` 起数。
    # 片池逐笔轮换（plan.conv 每笔不同），所以同一个 k 在不同笔的掩码本来就不同——
    # 这不是缺陷，是 DEM 按物理 ID 旋转的直接后果。
    # （实测依据：片组起于 0/1/3 时，同一码的主列整体平移。）
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
    # 数字已知公共项只含 **L**（不是 T）：U 的作用已由 k = c - U 落在物理链里
    # （vd0(k) 用的是减后的码），数字侧再扣 T 会把 U 扣两次。
    # 实测依据：word=32（U≠0 但 L=0）时该项为 0；word=48（L≠0）时恰为 -L。
    expected_term = -lower_v[:, None, None] + 3 * (1 - 2 * masks.astype(np.int64))
    np.testing.assert_array_equal(run.raw.digital_observation.known_voltage_term_v, expected_term)
    np.testing.assert_array_equal(run.raw.dither_program.known_common_q, -total_q)
    raw_voltage = 32 * residue
    backend_step = (parameters.adc2_v_max - parameters.adc2_v_min) / 2**parameters.adc2_n_bits
    backend = np.floor((raw_voltage - parameters.adc2_v_min) / backend_step).astype(np.int64)
    np.testing.assert_array_equal(run.raw.adc2_code, backend)
    np.testing.assert_array_equal(run.codes.code, _integer_oracle(
            parameters, backend, k, lower_q,
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
    np.testing.assert_array_equal(run.raw.sample_id, np.arange(64))
    np.testing.assert_array_equal(
        run.raw.held_sample_ids, np.broadcast_to(np.arange(64)[:, None], (64, 8))
    )
    words[:] = 0
    np.testing.assert_array_equal(run.raw.dither_program.words, np.arange(64))


def test_same_lower_different_upper_keeps_residue_but_changes_real_charge_and_masks(ideal_model):
    run = ideal_model.simulate(dc_input(0.31), 64, dither_words=np.arange(64))
    np.testing.assert_array_equal(run.raw.coarse[[0, 8, 32, 40]], run.raw.coarse[0])
    np.testing.assert_allclose(
        run.raw.residue[[0, 8, 32, 40]], run.raw.residue[0], atol=2e-14, rtol=0
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
        replace(_spec(), fractional_lower_weights_q=(2396745, 2396745, 11983726))


def test_source_dither_request_needs_words_and_cannot_silently_keep_default_noise():
    requested = default_model(config_overrides={"dither_enable": True})
    with pytest.raises(ConfigError, match="dither|words|program"):
        requested.simulate(dc_input(0.31), 8)
    with pytest.raises(ConfigError, match="zero-noise"):
        default_model().simulate(dc_input(0.31), 64, dither_words=np.arange(64))


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
    # 2026-11-12: pipeline_engine moved into adi_model.simulation; the old
    # `from adi_model import pipeline_engine` silently stopped resolving.
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
