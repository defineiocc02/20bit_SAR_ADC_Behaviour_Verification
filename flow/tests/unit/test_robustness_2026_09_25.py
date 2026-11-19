# =============================================================================
# 文件名     : test_robustness_2026_09_25.py
# 模块名     : 行为模型、数据导出与数值边界回归
# 功能       : 通过解析对照、拒绝路径和实际打包后端检查公开接口。
# 设计说明   : 制造、片选和噪声种子分别控制；正向对照与错误输入使用同一夹具。
# =============================================================================
"""迁移2026-09-25稳健性审查的实际失败保护，历史编号保留于库外对照表。

生产接口只使用当前显式物理/数字合同；旧experiments、unary、KTC、ref_track、
AUX比例和按码距离推串扰均已退役。这里保留独立随机流、有限性、真实电荷/RC、
可辨识性、文件写入和真实构建后端的反例，不以历史报告PASS证明当前芯片性能。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from adi_model.behavioral.charge_ref import ref_ra_charge, ref_ra_charge_nodal
from adi_model.behavioral.conversion import response_interval
from adi_model.behavioral.dynamics import crosstalk_error, switching_activity
from adi_model.behavioral.input_network import (
    InputNetworkParameters,
    PassiveTrackingNetwork,
    track_interval,
)
from adi_model.behavioral.low_frequency_noise import BandLimitedFlicker
from adi_model.behavioral.pretracking import QuantizedPretracker
from adi_model.behavioral.ra import ResidueAmplifier
from adi_model.behavioral.reference_charge import physical_reference_program
from adi_model.behavioral.sadc import build_first_stage_quantizer
from adi_model.behavioral.sampler import dc_input, sine_input
from adi_model.calib import observability, required_samples
from adi_model.calibration.weight_calibration import (
    CalibrationUnidentifiableError,
    fit_unit_weights,
)
from adi_model.cli import main_simulate
from subsys.experiments.fig11 import Fig11Parameters, fig11_model
from adi_model.inventory_gate import GateResult, _check_sources, build_summary, verify
from adi_model.metrics.metrics import reference_precision_bits
from adi_model.simulation.config import Config, ConfigError
from adi_model.simulation.serialization import result_document, write_results
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.dac_arch import NominalCodebook, SplitDAC
from adi_model.topology.mapper import Mapper

REPO = Path(__file__).resolve().parents[3]


# 1. 数值对照夹具
# 关闭采样及残差放大器噪声，以便把输出中的变化归到正在检查的参数。
# 制造失配也置零；这里只测模型接线与确定性量值。
def _quiet_model(seed=999):
    return fig11_model(
        seed=seed,
        parameters=Fig11Parameters(unit_mismatch_std=0, bridge_mismatch_std=0),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )


# 单电容 RC 网络有封闭形式解。1 pF 与 100 Ω 给出 0.1 ns 时间常数，
# 后面的 1 ns 跟踪在十个时间常数处检查指数尾差。
def _network():
    return PassiveTrackingNetwork([1e-12], [100.0], 0.0, 0.0)


# 主阵列、子阵列的采样和转换电压分别提供，以伏特为单位。
# 有符号分数电压可暴露布尔化或取整开关量时产生的电荷偏差。
# 固定种子使节点方程和闭式方程收到同一张矩阵。
def _charge_inputs():
    rng = np.random.default_rng(7)
    return {
        "main_cap_f": np.array([1.0, 1.3, 0.8]) * 1e-12,
        "sub_cap_f": np.array([0.7, 1.1]) * 1e-12,
        "bridge_cap_f": 1.4e-12,
        "sub_parasitic_f": 0.3e-12,
        "feedback_cap_f": 0.4e-12,
        "sample_main_v": rng.uniform(-1, 1, (32, 3)),
        "sample_sub_v": rng.uniform(-1, 1, (32, 2)),
        "conversion_main_v": rng.uniform(-3, 3, (32, 3)),
        "conversion_sub_v": rng.uniform(-3, 3, (32, 2)),
    }


# 小码本把映射检查与器件制造参数隔开。
# 四档电平覆盖负值、零和正值，输入码的形状与取值域可以分别验证。
def _mapper():
    book = NominalCodebook(0, 3, np.array([-1.0, 0.0, 1.0, 2.0]))
    return Mapper(np.arange(4, dtype=np.int64), nominal_codebook=book)


# 截断校准观测时，物理片号、掩码、电压项和样本号须沿第一维同步。
# 保留原规格和其余元数据，只改变可供拟合的观测数。
def _rows(data, count):
    names = (
        "slice_ids",
        "adc2_code",
        "main_masks",
        "sub_masks",
        "signal_mask",
        "known_voltage_term_v",
        "overflow",
        "sample_ids",
    )
    return replace(data, **{name: getattr(data, name)[:count] for name in names})


# 参考节点从零开始，驱动有一阶恢复项。
# 关闭带宽和压摆限制，后面的解析例只有一个有效时间常数。
def _response_inputs():
    return {
        "y0": 0.0,
        "z0": 0.0,
        "target_v": 0.2,
        "reference_drive_v": 0.1,
        "tau_ref_s": 1e-6,
        "dt_s": 1e-6,
        "ra_bw_hz": None,
        "adc_bw_hz": None,
        "clip_v": 1.0,
        "slew_v_s": None,
    }


# 布尔矩阵的第一维为物理事件，后两维给出片号和实际单元。
# 末两步开关相同，用来检查“事件发生”和“有电荷转移”的区别。
def _reference_inputs():
    return {
        "slice_ids": np.array([0]),
        "main_caps_f": np.array([[1e-12, 2e-12]]),
        "sub_caps_f": np.array([[1e-12]]),
        "bridge_caps_f": np.array([1e-12]),
        "main_parasitic_f": np.array([0.25e-12]),
        "sub_parasitic_f": np.array([0.5e-12]),
        "main_on": np.array([[[False, False]], [[True, False]], [[True, False]]]),
        "sub_on": np.zeros((3, 1, 1), bool),
        "times_s": np.array([0.0, 1e-9, 2e-9]),
        "sampled_main_bottom_v": np.full((1, 2), 0.3),
        "sampled_sub_bottom_v": np.full((1, 1), 0.3),
        "sampled_top_v": np.zeros(1),
        "sampled_subnode_v": np.zeros(1),
        "ref_high_v": 3.0,
        "ref_low_v": -3.0,
    }


# 独立辅助源的存在和辅助支路电阻是两个检查项。
# 以同一组合法参数只替换被测项，避免上游先因其他字段报错。
def _aux_parameters(**changes):
    return replace(
        InputNetworkParameters(
            filter_cap_f=0.0,
            auxiliary_cap_f=1e-14,
            auxiliary_bypass=True,
            auxiliary_source_ohm=1.0,
            auxiliary_resistance_ohm=1.0,
        ),
        **changes,
    )


# 参数导出器是仓库工具；从真实路径加载才能覆盖其入口使用的检查器。
# 下面的 fixture 复制显式配置和绑定，使每个负例只写自己的临时副本。
def _load_exporter():
    path = REPO / "tools/export_rtl_params.py"
    spec = importlib.util.spec_from_file_location("robustness_current_exporter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def exporter_case(tmp_path):
    """复制明确的数字组件夹具；不创建未知物理绑定或改原文件。"""
    path = tmp_path / "explicit_component"
    shutil.copytree(REPO / "flow/sim/fixtures/source_control_component", path)
    return _load_exporter(), path


# 2. 同一次制造、不同随机过程
# 场景用相同种子构造两次，既核对输出码和越界标志，也核对片上电容。
# 分流键区分制造、调度、噪声三个来源，能发现碰巧给出相同短码序列的串用。
def test_monte_carlo_mismatch_and_noise_streams_are_independent():
    """F1：实际默认层的制造/计划/噪声分流可重放，不恢复旧MC生产器。"""
    a, b = fig11_model(seed=999), fig11_model(seed=999)
    first = a.simulate(dc_input(0.31), 64)
    second = b.simulate(dc_input(0.31), 64)
    assert np.array_equal(a.chip.C_main, b.chip.C_main)
    assert np.array_equal(first.codes.code, second.codes.code)
    assert np.array_equal(first.codes.analog_overflow, second.codes.analog_overflow)
    partition = first.metadata["seed_partition"]
    assert partition["fabrication_spawn_key"] == [0]
    assert partition["schedule_spawn_key"] == [1, 0]
    assert partition["noise_spawn_key"] == [2, 0]
    assert partition["schedule_seed"] != partition["noise_seed"]


def test_spawn_does_not_consume_parent_stream():
    """F2：NumPy SeedSequence分子流不消耗已建立的父Generator。"""
    sequence = np.random.SeedSequence(42)
    parent = np.random.Generator(np.random.PCG64(sequence))
    sequence.spawn(1)
    assert np.array_equal(parent.normal(size=4), np.random.default_rng(42).normal(size=4))


# 开关噪声前后逐笔比较采集和转换片号。
# 同时观察噪声端口确实由非零变成全零，避免把没有生效的开关判为隔离成功。
def test_noise_switch_does_not_change_physical_schedule_or_chip():
    """F2：实际打开噪声不能消耗调度或制造随机流。"""
    noisy = fig11_model(seed=777)
    quiet = fig11_model(
        seed=777, config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False}
    )
    a, b = noisy.simulate(dc_input(0.31), 64), quiet.simulate(dc_input(0.31), 64)
    assert np.array_equal(noisy.chip.C_main, quiet.chip.C_main)
    assert np.array_equal(a.raw.conv_slice_ids, b.raw.conv_slice_ids)
    assert np.array_equal(a.raw.acq_slice_ids, b.raw.acq_slice_ids)
    assert np.any(a.raw.sample.sadc_noise_v != 0)
    assert np.array_equal(b.raw.sample.sadc_noise_v, np.zeros(64))


# 3. 配置与模拟输入的取值域
# 量纲合法和数值有限要分别检查。NaN 的大小比较可能全部为假，
# 所以要用 NaN 和一个确实越界的有限量分别验证错误入口。
@pytest.mark.parametrize(
    "kwargs",
    [
        {"g0": -32.0},
        {"g0": float("nan")},
        {"n_slices": 0},
        {"dac_n_main": 0},
        {"dac_n_sub": 0},
        {"ra_v_clip": 0.0},
    ],
)
def test_config_legality_rejects_illegal_values(configuration_inputs, kwargs):
    assert replace(configuration_inputs, **kwargs).legality_violations()


# 公开默认模型已经装配几何；直接构造的底层 Config 仍需调用者绑定规格。
# 同一处若偷偷填补缺失阵列，底层配置的反例便会被放行。
def test_config_requested_default_is_legal():
    model = fig11_model()
    assert model.cfg.legality_violations(geometry=model.geometry) == []
    assert Config().legality_violations()  # 底层不完整对象没有隐式物理默认值。


# 通过实际跟踪调用启用 Ron 规律，而非只构造一个未参与求解的参数。
# 负系数与非有限系数都须在计算电容电压前被发现。
@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_active_ron_law_rejects_illegal_coefficients(bad):
    with pytest.raises(ValueError, match="Ron law"):
        track_interval(
            [1e-12],
            [100.0],
            0.0,
            [0.0],
            0.0,
            1e-9,
            dc_input(1.0),
            parameters=InputNetworkParameters(filter_cap_f=0.0),
            ron_coefficient=bad,
        )


# 只改一个实际制造单元，其他片和规格维持有效。
# 这样可分辨是逐单元检查生效，还是上层总电容恰好越界。
def test_chip_build_rejects_nonpositive_unit_capacitance(toy_physical_case):
    bad = toy_physical_case.chip.C_main.copy()
    bad[0, 0] = -1e-12
    with pytest.raises(ValueError, match="positive"):
        replace(toy_physical_case.chip, C_main=bad)


@pytest.mark.parametrize("gain", [0.0, -0.2, float("nan"), float("inf")])
def test_pretracker_rejects_invalid_signal_scale(gain):
    with pytest.raises(ValueError):
        QuantizedPretracker(4, codebook_v=[-1.0, 1.0], signal_scale=gain)


@pytest.mark.parametrize("r_aux", [0.0, -1.0, float("nan"), float("inf")])
def test_auxiliary_branch_rejects_nonpositive_resistance(r_aux):
    with pytest.raises(ValueError, match="auxiliary_resistance_ohm"):
        _aux_parameters(auxiliary_resistance_ohm=r_aux).require_auxiliary_source(dc_input(1.0))


# 相同支路先接合法直流源，再移去输入函数。
# 支路系数合格只保证可以求解，无法替代独立源的电压。
def test_auxiliary_branch_requires_an_independent_positive_source():
    parameters = _aux_parameters()
    assert parameters.require_auxiliary_source(dc_input(1.0)) is None
    with pytest.raises(ValueError, match="auxiliary_input_fn"):
        parameters.require_auxiliary_source(None)


# 向一批合法电压中混入坏样本。若只检查数组首项，
# 量化器会把无效样本送进码边界查找。
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_sadc_rejects_non_finite_input(configuration_inputs, bad):
    with pytest.raises(ValueError, match="finite"):
        build_first_stage_quantizer(configuration_inputs).convert_codes(np.array([0.1, bad]))


# 4. 电荷守恒与串扰
# 对同一组底板电压分别解节点和闭式表达式，再以最大信号归一误差。
# 分母取整批正常幅值，单个近零结果不会夸大比较误差。
def test_explicit_charge_nodal_matches_closed_on_fractional_bottom_voltages():
    values = _charge_inputs()
    closed, nodal = ref_ra_charge(**values), ref_ra_charge_nodal(**values)
    assert np.max(np.abs(closed - nodal)) / np.max(np.abs(closed)) < 1e-12


# 采样连接决定哪些电容见到了输入。
# 若在接口缺失时把所有列当作选通，名义增益会改变而错误仍有可能输出有限数。
def test_unknown_sampling_connections_are_not_an_implicit_full_column(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
    with pytest.raises(ValueError, match="unbound"):
        pool.acquire(
            np.arange(8), np.full(8, 0.1), sample_id=0, sampled_top_v=0.0, sampled_subnode_v=0.0
        )
    for function in (ref_ra_charge, ref_ra_charge_nodal):
        values = _charge_inputs()
        values["conversion_main_v"] = None
        with pytest.raises(ValueError, match="explicit"):
            function(**values)


# 两种算法各自有零激励对照。
# 即使两种实现同样带有固定偏移，彼此相减也看不出，零电荷条件能检出。
def test_explicit_zero_voltage_change_has_zero_feedback_charge():
    values = _charge_inputs()
    values["conversion_main_v"] = values["sample_main_v"].copy()
    values["conversion_sub_v"] = values["sample_sub_v"].copy()
    assert np.array_equal(ref_ra_charge(**values), np.zeros(32))
    assert np.array_equal(ref_ra_charge_nodal(**values), np.zeros(32))


# 耦合项应跟随驱动的电压变化，而非粗码跳了几格。
# 保留正负和分数幅值，并与取整后的错误参照比较。
def test_crosstalk_uses_explicit_fractional_signed_driver_changes():
    caps = np.array([0.5, 1.0, 2.0]) * 1e-15
    changes = np.array([[0.25, -0.5, 1.5], [0.0, 1.0, -1.0]])
    got = crosstalk_error(
        coupling_cap_f=caps, digital_voltage_change_v=changes, output_capacitance_f=1e-12
    )
    expected = [
        sum(float(c) * float(v) for c, v in zip(caps, row, strict=True)) / 1e-12 for row in changes
    ]
    assert np.allclose(got, expected, rtol=0, atol=1e-18)
    rounded = (caps * np.rint(changes)).sum(-1) / 1e-12
    assert not np.allclose(got, rounded, rtol=1e-12, atol=0)


# 5. 估计与指标
# 观测行数多于参数数目仍可能秩亏。
# 同方向的列给出非零观测，却不足以分别识别两个系数。
def test_rank_deficient_overdetermined_system_exposes_null_directions():
    matrix = np.array([[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]])
    info = observability(matrix)
    assert info.rank == 1 and info.n_parameters == 2
    assert np.isinf(info.cond)
    assert np.array_equal(matrix @ np.array([-2.0, 1.0]), np.zeros(3))


# 参考误差以相对零的均方根计，恒定偏置也要进入功率。
# 若用去均值的标准差，直流偏置会被算成没有误差。
def test_reference_precision_keeps_bias_and_rejects_undefined_zero_rms():
    with pytest.raises(ValueError, match="positive"):
        reference_precision_bits(0.0, 1.0)
    constant = np.full(16, 1e-6)
    rms = float(np.sqrt(np.mean(constant**2)))
    assert rms > constant.std()
    assert reference_precision_bits(rms, 1.0) == pytest.approx(np.log2(1 / (np.sqrt(12) * rms)))
    error = np.linspace(-1e-6, 1e-6, 16)
    assert reference_precision_bits(float(np.sqrt(np.mean(error**2))), 1.0) == pytest.approx(
        18.841282446503264
    )


# 码值超范围与输入维数不符使用不同激励。
# 另检查事件表的行数，使校验发生在表与码本结合时。
def test_mapper_encode_validates_contract():
    mapper = _mapper()
    with pytest.raises(ValueError, match="outside"):
        mapper.encode(np.array([4]))
    with pytest.raises(ValueError, match="one-dimensional|1-D|dimensions"):
        mapper.encode(3)
    with pytest.raises(ValueError, match="match coarse rows"):
        Mapper(
            np.arange(4),
            nominal_codebook=mapper.nominal_codebook,
            event_codes_by_coarse=np.zeros((2, 1), int),
        )


# 整数码的语义包括数据类型。
# 允许隐式截断将使分数值误落到一个合法档位，掩盖上游单位错误。
def test_mapper_encode_rejects_noninteger_dtype_and_accepts_integer_control():
    mapper = _mapper()
    for value in (np.array([2.5]), np.array([2.0]), np.array([True])):
        with pytest.raises(ValueError, match="integer"):
            mapper.encode(value)
    assert np.array_equal(mapper.encode(np.array([2], np.int64)).codes, [2])
    with pytest.raises(ValueError, match="outside"):
        mapper.encode(np.array([4]))


# 短记录未必覆盖完整输入周期。
# 从显式频率和采样时刻得到的正弦可逐点比较，无需由短记录反估频率。
def test_short_forward_record_keeps_the_explicit_input_frequency():
    model = _quiet_model()
    for n in (16, 32, 64, 128, 256):
        run = model.simulate(sine_input(1.5, 1e6), n)
        times = np.arange(n) / model.cfg.fs
        expected = 1.5 * np.sin(2 * np.pi * 1e6 * times)
        assert np.array_equal(run.raw.sample.input_clean_v, expected)
        assert len(run.codes.code) == n
    with pytest.raises(TypeError, match="calibration"):
        Config.from_dict({"calibration": "gain_beta"})


# 同步截取少量真实观测，把自由参数个数保持不变。
# 这样暴露的是方程不足，而非某项输入大小不匹配。
def test_underdetermined_fit_rejects_instead_of_inventing_weights(synthetic_linear_data):
    data = _rows(synthetic_linear_data.data, 3)
    with pytest.raises(CalibrationUnidentifiableError, match="cannot identify"):
        fit_unit_weights(data, synthetic_linear_data.known[:3])


def test_crosstalk_uniform_explicit_coupling_and_activity():
    changes = np.array([[1.0, 0.0, -1.0], [0.5, 0.5, 0.5]])
    actual = crosstalk_error(
        coupling_cap_f=np.full(3, 1e-15),
        digital_voltage_change_v=changes,
        output_capacitance_f=1e-12,
    )
    assert np.allclose(actual, [0.0, 1.5e-3], rtol=0, atol=1e-18)
    before = np.array([[False, True, False], [True, True, False]])
    after = np.array([[True, True, False], [False, False, True]])
    assert np.array_equal(switching_activity(before, after), [1, 3])


# 6. 结果身份、仓库路径和发布边界
# 缺测、失败和非有限数各有独立含义。
# 序列化须保留状态及字段位置，并让严格的 JSON 解析器可以读回。
def test_result_serialization_tolerates_null_and_preserves_failure():
    results = {"not_run": None, "status": "FAIL", "bad": np.nan}
    document = result_document(results)
    assert document["not_run"] is None and document["status"] == "FAIL"
    assert document["bad"] is None and document["undefined_numeric_values"][0]["path"] == "/bad"
    assert np.isnan(results["bad"])
    json.dumps(document, allow_nan=False)


# 合成两个内容和摘要都相同的来源登记。
# 即使文件校验全部通过，重复标识仍会使后续引用无法区分。
def test_inventory_gate_flags_duplicate_source_id(tmp_path):
    import hashlib

    data = b"synthetic identity bytes"
    external = tmp_path / "source_inputs"
    external.mkdir()
    (external / "fixture.pdf").write_bytes(data)
    source = {
        "id": "x",
        "file": "fixture.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "pages": 1,
    }
    result = GateResult()
    _check_sources(
        tmp_path,
        {"location_rule": {"default_dir": str(external)}, "sources": [source, dict(source)]},
        result,
    )
    assert any(
        r.check == "sources.duplicate_id" and r.level == "FAIL" for r in result.finalize().records
    )


# 引用检查只读取文件和登记。
# 保留未完成状态，防止一次目录搬迁把未跑过的硬件测试改写成通过。
def test_repo_inventory_gate_checks_static_identity_without_device_pass():
    result = verify(REPO)
    assert result.static_refs_pass, [r for r in result.records if r.level == "FAIL"]
    summary = build_summary(result, REPO)
    assert summary["model_tests_run"] is False and summary["rtl_or_eda_run"] is False
    assert summary["source_conformance"] == "SOURCE_CONFORMANCE_NOT_ASSESSED"
    if not result.required_states_ready:
        assert summary["unresolved_required_states"]


@pytest.mark.parametrize("gain", [0.0, -1.5, float("nan"), float("inf"), -float("inf")])
def test_ra_rejects_nonpositive_or_nonfinite_actual_gain(gain):
    ra = ResidueAmplifier(Config(ra_v_clip=1.0, ra_enable_noise=False))
    with pytest.raises(ValueError, match="gain|positive|finite"):
        ra.evaluate(np.array([0.1]), None, np.array([gain]))


# 选择合法的一笔获得有限输出，再由同一个公开调用送入异常误差项。
# 两个路径应遵守同一套放大器参数边界。
def test_ra_error_input_uses_the_same_domain_guards():
    ra = ResidueAmplifier(Config(ra_v_clip=1.0, ra_enable_noise=False))
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            ra.evaluate(np.array([0.1]), None, np.array([2.0]), input_error_v=np.array([bad]))
    out, sat = ra.evaluate(np.array([0.1]), None, np.array([2.0]), input_error_v=np.array([0.005]))
    assert out[0] == pytest.approx(0.21) and not sat.any()


# 冻结片号、信号选择和已知电压项，让参数观测退化。
# 错误应由可辨识性检查明确返回，不能等浮点库发出除零警告。
def test_degenerate_estimator_is_explicit_and_silent(synthetic_linear_data):
    data = synthetic_linear_data.data
    bad = replace(
        data,
        slice_ids=np.zeros_like(data.slice_ids),
        signal_mask=np.ones_like(data.signal_mask),
        known_voltage_term_v=np.zeros_like(data.known_voltage_term_v),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(CalibrationUnidentifiableError, match="rank"):
            fit_unit_weights(bad, np.ones(len(data.sample_ids)))
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]


# 独立按物理片号重建设计矩阵，与被测拟合器分开。
# ADC2 输入用量化码箱中心，有限步进会进入精度上界。
# 比较用半个量化步和伪逆行和，保持参数单位一致。
def test_full_rank_estimator_recovers_the_declared_positive_weights(synthetic_linear_data):
    case = synthetic_linear_data
    frozen = fit_unit_weights(case.data, case.known)
    data = case.data
    units = case.weights.shape[1]
    design = np.zeros((len(case.known), case.weights.size + 1))
    for row, selected in enumerate(data.slice_ids):
        for slot, physical in enumerate(selected):
            design[row, physical * units : (physical + 1) * units] = (
                data.signal_mask[row, slot] * case.known[row] + data.known_voltage_term_v[row, slot]
            )
    design[:, -1] = 1.0
    truth = np.r_[case.weights.ravel(), case.offset]
    half_step = data.spec.adc2_step_v / 2
    assert np.max(np.abs(data.fine_v - design @ truth)) <= half_step + 1e-15
    expected, _, rank, _ = np.linalg.lstsq(design, data.fine_v, rcond=None)
    estimated = np.r_[frozen.weights.ravel(), frozen.offset_v]
    assert np.allclose(estimated, expected, rtol=1e-11, atol=1e-13)
    # 每笔箱中心误差<=半个ADC2 LSB；pinv每行的绝对和给出对应参数的确定上界。
    bound = np.abs(np.linalg.pinv(design)).sum(axis=1) * half_step
    assert np.all(np.abs(estimated - truth) <= bound + 1e-13)
    assert frozen.rank == rank == case.weights.size + 1


# 若输出名已被普通文件占用，应在建立模型前结束。
# 在模型构造处放哨兵，并检查原目标字节仍在。
def test_cli_reports_output_pointing_at_a_file(tmp_path, monkeypatch, capsys):
    import adi_model.cli as cli

    target = tmp_path / "not_a_dir.txt"
    target.write_text("preserve")

    def forbidden(**kwargs):
        raise AssertionError("existing target must be rejected before model construction")

    monkeypatch.setattr(cli, "default_model", forbidden)
    assert main_simulate(["--samples", "4", "--output", str(target)]) == 2
    assert target.read_text() == "preserve"
    assert json.loads(capsys.readouterr().err)["model_run"] is False


# Git 的后续放行规则可能覆盖前面的排除。
# 测试规则顺序，防止文档 PNG 白名单重新开放内部报告。
def test_internal_review_report_dir_is_ignored():
    patterns = (REPO / ".gitignore").read_text().splitlines()
    assert "docs/robustness_review_2026/" in patterns
    assert patterns.index("docs/robustness_review_2026/") > patterns.index("!docs/**/*.png")


# 实际源码包由 Hatchling 选择文件，判定口径与 Git 忽略规则可有差异。
# 从来源清单取需排除的原件，从图清单取需保留的发布图。
# 目录迁移时必须同时验证实际路径存在及构建后端判定。
def test_sdist_excludes_third_party_originals_and_keeps_real_public_assets():
    """真实hatchling路径判定；后端是dev依赖，缺失必须失败而非skip。"""
    from hatchling.builders.sdist import SdistBuilder

    cfg = SdistBuilder(str(REPO)).config
    original_documents = json.loads((REPO / "references/source_manifest.json").read_text())[
        "documents"
    ]
    excluded = [
        "docs/robustness_review_2026/fig/paper_p1.png",
        "docs/robustness_review_2026/fig/patent12_fig.png",
        "docs/robustness_review_2026/robustness_review_2026.pdf",
        "docs/robustness_review_2026/robustness_review_2026.tex",
        *(row["target_relative_path"] for row in original_documents),
    ]
    assert original_documents
    for relative in excluded:
        assert cfg.path_is_excluded(relative), relative
    kept = [
        "README.md",
        "pyproject.toml",
        "references/README.md",
        "references/source_manifest.json",
            "src/adi_model/defaults.py",
            "src/adi_model/control/dither.py",
            "src/adi_model/calibration/weight_calibration.py",
            "src/adi_model/rtl_observation.py",
            "src/adi_model/simulation/serialization.py",
            "src/adi_model/cli.py",
            "src/adi_model/topology/mapper.py",
            "src/adi_model/behavioral/conversion.py",
            "src/adi_model/metrics/metrics_lsb20.py",
        "tools/run_review_figures.py",
        "docs/figures/behavioral/README.md",
        "docs/figures/behavioral/figures.json",
        "docs/caaz/make_figures.py",
        "flow/sim/fixtures/source_control_component/config.json",
        "flow/sim/fixtures/source_control_component/format.json",
        "flow/sim/fixtures/source_control_component/binding.json",
        "flow/sim/fixtures/source_control_component/source_control_binding.sv",
        "flow/sim/fixtures/source_control_component/source_acquisition_binding.sv",
        "tools/export_rtl_params.py",
        "tools/run_open_rtl.py",
    ]
    # 当前 README 的图集使用逐图哈希清单。只检查现行交付路径；历史样本
    # 的数值重算由独立的快照测试负责，不能用旧路径误判新发行包缺文件。
    # 以发布清单枚举每个 PNG 和 SVG，单看目录存在会漏掉被忽略的单幅图。
    figure_dir = "docs/figures/behavioral"
    figures = json.loads((REPO / figure_dir / "figures.json").read_text(encoding="utf-8"))
    assert figures["figure_files_sha256"]
    kept.extend(f"{figure_dir}/{name}" for name in figures["figure_files_sha256"])
    for relative in kept:
        assert (REPO / relative).is_file(), relative
        assert not cfg.path_is_excluded(relative), relative


# 冻结 JSON 记录的是原配置下的各阶段。
# 这里只读取结构与阶段名；重跑当前模型产生的新数字不能写回该记录。
def test_historical_reference_output_is_present_and_typed():
    """核对归档字节与来源清单；数值仍只属于生成它的历史运行。"""
    import hashlib

    manifest = REPO / "flow/verification/manifests/sources_manifest.json"
    # version_pin 的历史 Git 路径与工作树归档是两个身份。必须读显式实物记录，
    # 否则只因文件名都叫 results.json，就可能错用另一轮的摘要。
    records = json.loads(manifest.read_text())["version_pin"]["worktree_artifacts"]
    assert set(records) == {"reference_results", "report"}
    for role, entry in records.items():
        path = REPO / entry["path"]
        # 已发布 sdist 有意排除 PDF；Git 检出则必须同时保存两个已登记的实物。
        if role == "report" and not (REPO / ".git").exists() and not path.exists():
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    # SHA 确认实际保留的字节，阶段键检查内容角色。两项均不对旧结果的物理正确性作判定。
    # 公共模拟入口的出码验证由生产顶层逐笔对照负责，不能拿这份快照代替。
    payload = json.loads((REPO / records["reference_results"]["path"]).read_text())
    assert isinstance(payload, dict)
    assert {"s1", "mc", "mc_pdk_off", "s13"} <= set(payload)


# 7. 模型的单位、定义域与解析对照
# 码本的负端点是一种合法电压，电容和幅度尺度却要求为正。
# 按物理含义分别检验，以免把所有数值套进同一条范围规则。
def test_split_dac_explicit_nominal_endpoints_are_ordered(toy_physical_case):
    case = toy_physical_case
    book = NominalCodebook(0, 2, np.array([-1.0, 0.0, 1.0]))
    dac = SplitDAC(case.cfg, case.chip, nominal_codebook=book)
    assert np.array_equal(dac.evaluate_nominal(np.array([0, 2])), [-1.0, 1.0])


@pytest.mark.parametrize(
    "field,bad", [(f, b) for f in ("fs", "v_fs") for b in (float("nan"), float("inf"))]
)
def test_config_fs_vfs_reject_nonfinite(configuration_inputs, field, bad):
    assert replace(configuration_inputs, **{field: bad}).legality_violations()


# 给出已知采样率和满量程，验证被测防护仍接受正常正值。
# 非法值和合法值使用同一个配置替换入口。
def test_config_fs_vfs_accept_finite(configuration_inputs):
    assert replace(configuration_inputs, fs=40e6, v_fs=3.0).legality_violations() == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_reference_precision_bits_rejects_nonfinite(bad):
    with pytest.raises(ValueError, match="finite"):
        reference_precision_bits(bad, 1.0)
    with pytest.raises(ValueError, match="finite"):
        reference_precision_bits(1e-3, bad)


def test_reference_precision_bits_accepts_finite():
    assert reference_precision_bits(1e-3, 1.0) == pytest.approx(np.log2(1 / (np.sqrt(12) * 1e-3)))


@pytest.mark.parametrize(
    "field,bad",
    [
        (f, b)
        for f in (
            "filter_cap_f",
            "auxiliary_cap_f",
            "auxiliary_source_ohm",
            "auxiliary_resistance_ohm",
            "pretrack_time_s",
            "pretrack_source_ohm",
        )
        for b in (float("nan"), float("inf"))
    ],
)
def test_input_network_declared_parameters_reject_nonfinite(field, bad):
    with pytest.raises(ValueError, match=field):
        replace(InputNetworkParameters(filter_cap_f=0.0), **{field: bad}).require_tracking()


def test_input_network_declared_parameters_accept_finite():
    parameters = InputNetworkParameters(filter_cap_f=0.0)
    assert parameters.violations() == []
    assert parameters.require_tracking() is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_pretracker_weights_reject_nonfinite(bad):
    with pytest.raises(ValueError, match="weights"):
        QuantizedPretracker(
            4, codebook_v=[-1.0, 0.0, 1.0], signal_scale=1.0, weights=(0.6, 0.3, bad)
        )


# 显式权重的项数决定需要保留的历史长度。
# 除检查各值可用，还核对状态深度。
def test_pretracker_weights_accept_finite():
    tracker = QuantizedPretracker(
        4, codebook_v=[-1.0, 0.0, 1.0], signal_scale=1.0, weights=(0.6, 0.3, 0.1)
    )
    assert tracker.history_depth == 3


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_tracking_interval_rejects_nonfinite_time(bad):
    with pytest.raises(ValueError, match="finite"):
        _network().advance([0.0], 0.0, bad, dc_input(1.0))


# 一阶网络在该间隔的充电量为 1−exp(−10)。
# 只检查输出落在零与一之间，无法检出欧姆、法拉和秒的尺度错误。
def test_tracking_interval_accepts_finite_and_matches_single_pole():
    actual = _network().advance([0.0], 0.0, 1e-9, dc_input(1.0))
    assert actual[0] == pytest.approx(1 - np.exp(-10), abs=1e-13)


@pytest.mark.parametrize(
    "field,bad",
    [
        (f, b)
        for f in ("caps_f", "switch_ohm", "source_ohm", "filter_cap_f")
        for b in (float("nan"), float("inf"))
    ],
)
def test_tracking_components_reject_nonfinite(field, bad):
    inputs = {"caps_f": [1e-12], "switch_ohm": [100.0], "source_ohm": 30.0, "filter_cap_f": 1e-13}
    inputs[field] = [bad] if field in ("caps_f", "switch_ohm") else bad
    with pytest.raises(ValueError, match="finite"):
        PassiveTrackingNetwork(**inputs)


# 加入非零源阻和滤波电容后，网络有两个节点状态。
# 检查两节点在该正阶跃下的有限性与幅度界。
def test_tracking_components_accept_finite():
    network = PassiveTrackingNetwork([1e-12], [100.0], 30.0, 1e-13)
    after = network.advance(np.zeros(2), 0.0, 1e-9, dc_input(1.0))
    assert np.all(np.isfinite(after)) and np.all((after >= 0) & (after <= 1))


@pytest.mark.parametrize("which", ["white_density_v2_hz", "high_hz"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_declared_noise_band_rejects_nonfinite(which, bad):
    values = {
        "white_density_v2_hz": 1e-12,
        "corner_hz": 40.0,
        "low_hz": 0.1,
        "rng": np.random.default_rng(5),
        "high_hz": 40.0,
        "modes": 8,
        "reference_node": "test",
        "distribution": "gaussian",
    }
    values[which] = bad
    with pytest.raises(ValueError):
        BandLimitedFlicker.create(**values)


# 期望方差由声明的谱密度、转折频率和频带端点决定。
# 以频带积分为对照，免受某一条短噪声记录样本方差的波动。
def test_declared_noise_band_has_explicit_ensemble_variance():
    state = BandLimitedFlicker.create(
        1e-12,
        40.0,
        0.1,
        np.random.default_rng(5),
        high_hz=40.0,
        modes=8,
        reference_node="test",
        distribution="gaussian",
    )
    assert state.expected_variance_v2 == pytest.approx(1e-12 * 40 * np.log(400))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_required_samples_rejects_nonfinite(bad):
    with pytest.raises(ValueError, match="target parameter"):
        required_samples(np.eye(4), 1e-4, bad, oversample=2.0, sample_rate_hz=40e6)


# 对四维单位矩阵，过采样因子为二时建议八个样本。
# 同时核对以秒计的时长，防止把采样数误当成时间。
def test_required_samples_accepts_finite():
    res = required_samples(np.eye(4), 1e-4, 1e-3, oversample=2.0, sample_rate_hz=40e6)
    assert res["feasible"] is True
    assert res["recommended_samples"] == pytest.approx(8.0)
    assert res["duration_s"] == pytest.approx(8 / 40e6)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_pretracker_codebook_rejects_nonfinite_lower_endpoint(bad):
    with pytest.raises(ValueError, match="finite"):
        QuantizedPretracker(4, codebook_v=[bad, 1.0], signal_scale=1.0)


# 负电压是双极码本合法的下端。
# 有限性守卫既要拒绝无穷，也应保留合法的负端点及原顺序。
def test_pretracker_codebook_accepts_finite_negative_endpoint():
    tracker = QuantizedPretracker(4, codebook_v=[-2.0, -1.0, 0.0, 1.0], signal_scale=1.0)
    assert np.array_equal(tracker.codebook_v, [-2.0, -1.0, 0.0, 1.0])


@pytest.mark.parametrize(
    "field,bad", [(f, b) for f in ("tau_ref_s", "clip_v") for b in (float("nan"), float("inf"))]
)
def test_response_interval_rejects_nonfinite(field, bad):
    kw = _response_inputs()
    kw[field] = bad
    with pytest.raises(ValueError, match="finite"):
        response_interval(**kw)


# 独立的一阶指数含初始值和参考驱动两项。
# 同时核对两个返回状态和饱和标志，避免数值正确但被标成异常。
def test_response_interval_accepts_finite():
    y, z, sat = response_interval(**_response_inputs())
    assert y == pytest.approx(0.2 + 0.1 / np.e)
    assert y == z and sat is False


@pytest.mark.parametrize(
    "field,bad",
    [
        (f, b)
        for f in ("times_s", "main_caps_f", "ref_high_v")
        for b in (float("nan"), float("inf"))
    ],
)
def test_physical_reference_program_rejects_nonfinite_events(field, bad):
    values = _reference_inputs()
    if field == "times_s":
        values[field] = np.array([0.0, bad, 2e-9])
    elif field == "main_caps_f":
        values[field] = np.array([[bad, 2e-12]])
    else:
        values[field] = bad
    with pytest.raises(ValueError):
        physical_reference_program(**values)


# 第二次收到相同开关设置仍有真实事件时刻。
# 保留时间记录，重复写入产生的增量电荷应为零。
def test_physical_reference_program_preserves_repeated_real_events():
    result = physical_reference_program(**_reference_inputs())
    assert len(result.times_s) == 3 and np.all(np.isfinite(result.charge_c))
    assert np.array_equal(result.charge_c[-1], [0.0, 0.0])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_active_reference_recovery_rejects_nonfinite_tau(bad):
    values = _response_inputs()
    values["tau_ref_s"] = bad
    with pytest.raises(ValueError, match="finite"):
        response_interval(**values)


# 用一个时间常数处的恢复量核对动态路径。
# 同时检查饱和标志，区分解析响应与被钳位后碰巧得到的结果。
def test_active_reference_recovery_matches_its_declared_exponential():
    values = _response_inputs()
    values.update(target_v=0.0, reference_drive_v=1.0, clip_v=2.0)
    y, z, saturated = response_interval(**values)
    assert y == pytest.approx(np.exp(-1)) and z == pytest.approx(y)
    assert not saturated


# 在分解结果进入系数计算的位置注入错误因子。
# 原始训练数据合法，因而可定位为求解或验收阶段的错误。
def test_fit_unit_weights_rejects_nonfinite_or_nonpositive(synthetic_linear_data, monkeypatch):
    """因子非finite必须在乘法前拒绝；有限的非正权重由对应守卫拒绝。"""
    import adi_model.calibration.weight_calibration as wc

    data, known = synthetic_linear_data.data, synthetic_linear_data.known
    assert np.all(fit_unit_weights(data, known).weights > 0)
    count = np.prod(data.spec.shape) + 1

    def factors(bad):
        def fake(design, **kwargs):
            u = np.zeros((len(known), count))
            indices = np.arange(count)
            u[indices, indices] = -np.sign(data.fine_v[:count])
            v = np.eye(count)
            v[0, 0] = bad
            return u, np.ones(count), v

        return fake

    with monkeypatch.context() as patch:
        patch.setattr(wc, "svd", factors(1.0))
        with pytest.raises(ValueError, match="nonpositive"):
            fit_unit_weights(data, known)
    for bad in (float("nan"), float("inf")):
        with monkeypatch.context() as patch:
            patch.setattr(wc, "svd", factors(bad))
            with pytest.raises(ValueError, match="non-finite factors"):
                fit_unit_weights(data, known)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("adc2_v_min", float("nan")),
        ("adc2_v_max", float("inf")),
        ("v_fs", float("nan")),
        ("v_fs", float("inf")),
    ],
)
def test_spec_physical_scales_must_be_finite(synthetic_linear_data, field, bad):
    with pytest.raises(ValueError, match="finite"):
        replace(synthetic_linear_data.data.spec, **{field: bad})


# 量化电压窗参与由码还原电压的计算。
# 在完整合法规格下检查拟合秩，以确认有限性校验没有破坏正常数据路径。
def test_spec_physical_scales_accepts_finite(synthetic_linear_data):
    data = synthetic_linear_data.data
    assert data.validate() is None
    assert fit_unit_weights(data, synthetic_linear_data.known).rank == 7


def test_slice_pool_rejects_nonfinite_caps(toy_physical_case):
    bad = toy_physical_case.chip.C_main.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        replace(toy_physical_case.chip, C_main=bad)


# 片池应保存调用者给出的器件实例和实际片号。
# 复位后没有已捕获样本，合法的电容表也不能提前置有效。
def test_slice_pool_accepts_finite(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
    assert pool.chip is case.chip and not pool.held_valid.any()
    assert np.array_equal(pool.chip.C_main, case.chip.C_main)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_tracking_residual_rejects_nonfinite_interval(bad):
    with pytest.raises(ValueError, match="finite"):
        _network().advance([0.0], 0.0, bad, dc_input(1.0))


def test_tracking_residual_accepts_finite():
    residual = 1 - _network().advance([0.0], 0.0, 1e-9, dc_input(1.0))[0]
    assert residual == pytest.approx(np.exp(-10), abs=1e-13)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_driver_charge_rejects_nonfinite_node_voltage(bad):
    with pytest.raises(ValueError, match="finite"):
        _network().charge_change(np.array([0.0]), np.array([bad]))


# 1 pF 电容由 0 V 充到 1 V，驱动器应提供 1 pC。
# 这个绝对量级能分辨电荷与电压步进的单位。
def test_driver_charge_from_actual_capacitor_state_is_finite():
    assert _network().charge_change(np.array([0.0]), np.array([1.0])) == pytest.approx(1e-12)


# 8. 仿真输入包和写盘
# 只在临时绑定文件中改变相位数，真实导出器重新读取它。
# 副本保留其他合法字段，错误能定位到时序规格。
def test_export_rtl_params_rejects_out_of_range(exporter_case):
    exporter, path = exporter_case
    binding = json.loads((path / "binding.json").read_text())
    binding["timing"]["phases"] = 100
    (path / "binding.json").write_text(json.dumps(binding))
    with pytest.raises(ValueError, match="schedule"):
        exporter.load_contract(path / "config.json", path / "format.json", path / "binding.json")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 3.5])
def test_export_rtl_params_rejects_nonfinite(exporter_case, bad):
    exporter, path = exporter_case
    binding = json.loads((path / "binding.json").read_text())
    binding["timing"]["phases"] = bad
    (path / "binding.json").write_text(json.dumps(binding))
    with pytest.raises(ValueError, match="nonfinite|schedule"):
        exporter.load_contract(path / "config.json", path / "format.json", path / "binding.json")


# 组件夹具的 16 相位与两套源绑定随文件一起发布。
# 检查文件集合，可同时发现改名后漏带的头文件和意外多出的旧副本。
def test_export_rtl_params_accepts_explicit_integer_phases(exporter_case):
    exporter, path = exporter_case
    files = exporter.build(path / "config.json", path / "format.json", path / "binding.json")
    manifest = json.loads(files["rtl_input_manifest.json"])
    assert manifest["parameters"]["PHASES"] == 16
    assert manifest["binding_interface"] == "RTL_BINDING_ABI_2_COMMON_ACQUISITION"
    assert "source_control_binding.sv" in files and "source_acquisition_binding.sv" in files
    assert set(files) == {
        "rtl_params.vh",
        "source_control_binding.sv",
        "source_acquisition_binding.sv",
        "packet_schema.json",
        "config.json",
        "format.json",
        "binding.json",
        "rtl_input_manifest.json",
    }


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_direct_auxiliary_request_checks_nonfinite_branch_resistance(bad):
    parameters = _aux_parameters(auxiliary_resistance_ohm=bad)
    with pytest.raises(ValueError, match="auxiliary_resistance_ohm"):
        parameters.require_auxiliary_source(dc_input(1.0))


# 连续两次检查同一辅助源，确认接口验证是只读的。
# 如果前一次请求偷偷推进状态，第二次准备就可能消费错误的电压。
def test_direct_auxiliary_request_accepts_explicit_finite_source():
    parameters = _aux_parameters()
    original = replace(parameters)
    assert parameters.require_auxiliary_source(dc_input(1.0)) is None
    assert parameters.require_auxiliary_source(dc_input(1.0)) is None
    assert parameters == original


# 让最后的原子替换失败，模拟临时文件已写而目标尚未更新。
# 同时检查旧目标和目录内容，可发现残留的半成品。
def test_atomic_result_write_failure_preserves_target_and_removes_temp(tmp_path, monkeypatch):
    import adi_model.simulation.serialization as serialization

    target = tmp_path / "result.json"
    target.write_text("original")

    def denied(*args):
        raise PermissionError("directed atomic publish failure")

    monkeypatch.setattr(serialization.os, "replace", denied)
    with pytest.raises(PermissionError, match="publish failure"):
        write_results(target, {"value": 1.0})
    assert target.read_text() == "original"
    assert set(tmp_path.iterdir()) == {target}


# 读回实际文件并与返回对象比较，避免只验证内存里的格式。
# NumPy 输入中的 NaN 应保持在调用者手里，文件使用合法 JSON 表示。
def test_atomic_result_write_preserves_null_failure_and_numpy_data(tmp_path):
    target = tmp_path / "result.json"
    original = {"未运行": None, "status": "FAIL", "data": np.array([1.0, np.nan])}
    result = write_results(target, original)
    assert json.loads(target.read_text()) == result
    assert result["status"] == "FAIL" and result["未运行"] is None
    assert result["data"] == [1.0, None] and result["undefined_numeric_values"]
    assert np.isnan(original["data"][1])


# 过时的开关范围字段不得改变当前模型的选片。
# 逐个送入未知键，确认调用者能看到究竟哪个接口需要迁移。
@pytest.mark.parametrize(
    "field", ["dither_units_range", "dither_split_bank", "dither_units_total", "dither_discrete"]
)
def test_retired_dither_mask_range_is_not_implicitly_accepted(field):
    with pytest.raises(TypeError, match=field):
        Config.from_dict({field: 4})


# 未绑定采集状态时，用会抛错的波形函数作为哨兵。
# 理想输入一旦被求值就说明拒绝发生得太晚。
def test_active_aux_request_cannot_create_an_ideal_pipeline():
    model = fig11_model(config_overrides={"input_network": _aux_parameters()})

    def forbidden(_times):
        raise AssertionError("unbound active AUX must reject before sampling a waveform")

    with pytest.raises(ConfigError, match="complete active acquisition-state binding"):
        model.simulate(forbidden, 4)
