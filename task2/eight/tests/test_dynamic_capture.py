# =============================================================================
# 文件名     : test_dynamic_capture.py
# 模块名     : test_dynamic_capture
# 版本       : v1.0
# 功能       : 验证整周期采集、共享孔径、事件因果和粗／精参考交接。
# 设计说明   :
#   [独立性] 参考网络与电荷守恒由独立ODE检验，不只比较同一个公式。
#   [来源] 单位R/C例只做数学验证，不作为芯片C/R或性能预测。
#   [修复] 两个原文时序约束现为普通断言，不再xfail或默认阻断代替实现。
# 变更记录   : 替换被否决草案用例，保留原静态回归和分块连续性检查。
# =============================================================================

"""与原文关系对齐的模型结构验证，不冒充模拟参数已标定。"""

from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from adi_model.capacitors import ExperimentConfig, cap_pool
from adi_model.reference_mux import CouplingBinding, ReferenceBinding, SwitchedReference
from adi_model.transients import DynamicConfig, RampDrive, SineDrive
from task1.experiments.adctoolbox_calibration import CalibrationConfig
from task1.experiments.residue_control_study import MapperTable
from task1.model.codebook import codebook
from task2.eight.dynamic import WIRE_SHAPES, DynamicCapture
from task2.eight.experiment import _capture
from task2.eight.model import native_step_v, words


@pytest.fixture
def fixture():
    """固定一颗2%失配虚拟芯片；不在各臂或各笔重新抽电容。"""
    exp = replace(ExperimentConfig(), mismatch_sigma_unit=0.02)
    nom, caps = cap_pool(exp)
    return exp, caps, np.r_[codebook(nom, exp), 5.0], MapperTable.build(bridge=True)


def coupling():
    """显式单位电荷投影；source明确为数学例，不是工艺寄生值。"""
    rng = np.random.default_rng(91)
    return CouplingBinding(
        {k: rng.uniform(-1, 1, shape) * 1e-6 for k, shape in WIRE_SHAPES.items()},
        1.0,
        10e-9,
        1.0,
        "normalized math test; not physical extraction",
    )


def reference(tau=10e-9):
    """线性网络的显式测试绑定；法拉／欧姆尺度不用于任何ADC性能声明。"""
    return ReferenceBinding(
        1.0,
        tau,
        10.0,
        1e-9,
        2e-9,
        1e-9,
        10.0,
        (0.0, 0.0),
        "normalized math test; not a chip estimate",
        "independent",
        "zero_reset",
    )


def capture(model, fixture, count=64, start=0, strict=True):
    """用全局时间和独立PRBS提供对齐控制，支持在非周期边界切块。"""
    exp, _, threshold, table = fixture
    t = np.arange(start, start + count) * model.config.period_s
    x = model.drive.value(t)
    ids = np.tile(np.arange(8), (count, 1))
    phase = (np.arange(start, start + count) % 8, np.arange(start, start + count) % 16)
    word = words(np.ones(start + count, dtype=bool), 0x5A1709C3)[start:]
    return model.capture(
        x,
        word,
        ids,
        phase,
        threshold,
        native_step_v(exp),
        table,
        replace(CalibrationConfig(), adc2_half_range_v=0.1),
        strict=strict,
    )


def test_full_period_acquisition_and_shared_aperture(fixture):
    """直接断言论文的两个关键关系；不是把未修缺陷写成通过。"""
    exp, caps, _, _ = fixture
    cfg = DynamicConfig(acquisition=True)
    # [来源] 默认没有保护间隔，必须完整跟踪T；不能靠延长周期掩盖半周期采集。
    # 相同时间戳检查属于时序合同，不是两个模拟采样开关的抖动签核。
    assert cfg.acquisition_s == cfg.period_s
    assert cfg.ra_start_s + cfg.ra_s == pytest.approx(cfg.period_s, abs=1e-18)
    model = DynamicCapture(cfg, caps, exp, SineDrive(2, 1e6))
    capture(model, fixture)
    t = model.last_trace
    np.testing.assert_array_equal(t["adc2_sample_time_s"], t["next_input_sample_time_s"])
    np.testing.assert_allclose(
        t["adc2_sample_time_s"] - t["input_sample_time_s"], cfg.period_s, atol=1e-20
    )


def test_nominal_bandwidth_and_no_invented_tau_spread(fixture):
    """15×Nyquist是缩放基准，片间C比例来自已有制造池，没有再加±2%τ。"""
    exp, caps, _, _ = fixture
    cfg = DynamicConfig(acquisition=True)
    # 标称带宽与逐片倍率分开验，防止在倍率里重新藏入随机τ。
    # 制造池相同只能保证同一C，不能证明真实电路具有相同R。
    assert 1 / (2 * np.pi * cfg.input_tau_s) == pytest.approx(15 * exp.fs_hz / 2)
    model = DynamicCapture(cfg, caps, exp, RampDrive(0, 0))
    np.testing.assert_array_equal(
        model.tau_multiplier, caps.main.sum(axis=1) / (32 * exp.unit_cap_f)
    )


def test_nominal_full_tracking_erases_initial_condition(fixture):
    """只检查参与跟踪的八片；未采集的物理片必须保留各自初值。"""
    exp, caps, _, _ = fixture
    models = [
        DynamicCapture(
            DynamicConfig(acquisition=True, input_initial_v=v), caps, exp, SineDrive(2, 1e6)
        )
        for v in (-5.0, 5.0)
    ]
    for m in models:
        capture(m, fixture)
    # 允许解析核浮点尾差；只比较采过的八片，不错误要求空闲片也失去初值。
    np.testing.assert_allclose(models[0].held[:8], models[1].held[:8], rtol=0, atol=2e-14)


@pytest.mark.parametrize("flags", [{"crosstalk": True}, {"reference": True}])
def test_unknown_physical_parameters_are_not_guessed(fixture, flags):
    """只开开关而没有参数来源必须拒绝，不再默认为0.02 fF或1 nF。"""
    exp, caps, _, _ = fixture
    with pytest.raises(ValueError, match="UNBOUND"):
        DynamicCapture(DynamicConfig(**flags), caps, exp, RampDrive(0, 0))


def test_closed_entry_is_bit_exact(fixture):
    """全关保留旧主路径，逐元素比较粗码、残差与ADC2原码。"""
    exp, caps, th, table = fixture
    drive = SineDrive(2, 1e6)
    model = DynamicCapture(DynamicConfig(), caps, exp, drive)
    x = drive.value(np.arange(64) * model.config.period_s)
    ids = np.tile(np.arange(8), (64, 1))
    phase = (np.zeros(64, dtype=int),) * 2
    word = words(np.ones(64, dtype=bool), 123)
    cfg = replace(CalibrationConfig(), adc2_half_range_v=0.1)
    args = (x, word, ids, phase, th, native_step_v(exp), table, model.physical, cfg)
    old = _capture(*args)
    new = _capture(*args, transients=model)
    # 数值必须逐元素完全一致，而非只允许近似相同SNDR。
    # 这是旧静态路径不受本次动态接入影响的直接检查。
    for i in (1, 2, 3):
        np.testing.assert_array_equal(old[i], new[i])


def test_chunking_preserves_reference_and_event_history(fixture):
    """23笔边界不整除八槽／字周期，分块仍需逐字节保持相同历史。"""
    exp, caps, _, _ = fixture
    cfg = DynamicConfig(acquisition=True, crosstalk=True, reference=True)

    def make():
        return DynamicCapture(
            cfg,
            caps,
            exp,
            SineDrive(2, 1e6),
            coupling=coupling(),
            reference=reference(),
            reference_charge=lambda i, c: [(cfg.command_s, np.array([1e-3, 0.7e-3]))],
        )

    full, split = make(), make()
    expected = capture(full, fixture)
    a = capture(split, fixture, 23)
    b = capture(split, fixture, 41, 23)
    # 原码一致之外再比内部状态，防止下一块才暴露的延迟错误漏检。
    # 不同块长不得重置参考节点或输入保持值。
    for k in (1, 2, 3):
        np.testing.assert_array_equal(expected[k], np.r_[a[k], b[k]])
    np.testing.assert_array_equal(full.reference.internal, split.reference.internal)
    np.testing.assert_array_equal(full.held, split.held)


def test_late_conversion_edges_only_reach_later_input(fixture):
    """当前转换相位改变时，本笔输入串扰不变，后续孔径才可能变化。"""
    exp, caps, th, table = fixture
    records = []
    for p in (0, 1):
        m = DynamicCapture(
            DynamicConfig(crosstalk=True), caps, exp, RampDrive(1, 0), coupling=coupling()
        )
        m.capture(
            np.ones(2),
            np.array([15, 15]),
            np.tile(np.arange(8), (2, 1)),
            (np.array([p, 0]), np.array([p, 0])),
            th,
            native_step_v(exp),
            table,
            replace(CalibrationConfig(), adc2_half_range_v=0.1),
        )
        records.append(m.last_trace["crosstalk_sample_v"])
    # 当前相位在采样之后才影响转换控制；若本笔串扰变了就是反因果。
    # 第二笔必须可观测到差异，避免因整条耦合通路失效而得到假通过。
    assert records[0][0] == records[1][0]
    assert abs(records[0][1] - records[1][1]) > 1e-18


def test_external_edges_use_the_same_quiet_time_order(fixture):
    """外部VCD投影可在窗内插入多事件；孔径边界事件须归到下一窗口。"""
    exp, caps, _, _ = fixture
    m = DynamicCapture(
        DynamicConfig(crosstalk=True),
        caps,
        exp,
        RampDrive(1, 0),
        coupling=coupling(),
        external_edges=lambda i: [(50e-9, 1e-6)],
    )
    capture(m, fixture, 2)
    # 先证明合法外部事件确实生效，再检查半开区间端点不会被重复归属。
    assert m.last_trace["crosstalk_sample_v"][0] != 0
    bad = DynamicCapture(
        DynamicConfig(crosstalk=True),
        caps,
        exp,
        RampDrive(1, 0),
        coupling=coupling(),
        external_edges=lambda i: [(100e-9, 1e-6)],
    )
    with pytest.raises(ValueError, match="inside"):
        capture(bad, fixture, 1)


def test_ideal_coarse_precharge_keeps_sar_charge_off_fine_source():
    """即便SAR抽取电荷，理想粗预充已补足时REF_IN不能再被重复算整笔负载。"""
    b = reference(tau=0)
    ref = SwitchedReference(b)
    _, o = ref.cycle(
        [(DynamicConfig().command_s, np.array([0.2, 0.1]))],
        DynamicConfig(),
        0.03,
        np.array([0.5, -0.5]),
    )
    np.testing.assert_allclose(o["coarse_supplied_charge_c"], [0.2, 0.1], atol=1e-15)
    # 源电荷检查不能仅看终点电压；重复算整笔负载也可能最终恢复到零缺额。
    np.testing.assert_allclose(o["fine_source_charge_c"], 0, atol=1e-15)


def test_reference_handoff_matches_independent_ode_and_charge_balance():
    """独立积分两参考节点和RA，并用总电荷守恒检查粗源／精确源划分。"""
    b = ReferenceBinding(
        1.0,
        1.0,
        3.0,
        0.2,
        0.1,
        0.3,
        1.0,
        (0.0, 0.0),
        "dimensionless unit example",
        "independent",
        "zero_reset",
    )
    cfg = DynamicConfig(period_s=10.0)
    q = np.array([0.1, 0.06])
    s = np.array([0.4, -0.2])
    target = 0.03
    model = SwitchedReference(b)
    output, o = model.cycle([(cfg.command_s, q)], cfg, target, s)
    d0 = q / b.internal_cap_f * np.exp(-(cfg.ra_start_s - cfg.command_s) / b.coarse_tau_s)
    initial = np.array([d0[0], 0, d0[1], 0, 0.0])

    def ode(t, y):
        result = np.zeros(5)
        for rail in range(2):
            d, p = y[2 * rail : 2 * rail + 2]
            current = (d - p) / b.mux_r_ohm
            result[2 * rail] = -current / b.internal_cap_f
            result[2 * rail + 1] = (current - p / b.fine_source_r_ohm) / b.fine_pin_cap_f
        result[4] = (target + s @ y[[0, 2]] - y[4]) / b.ra_tau_s
        return result

    independent = solve_ivp(ode, (0, cfg.ra_s), initial, rtol=2e-12, atol=1e-14)
    # 生产核用矩阵指数；这里用独立自适应积分，不复用生产矩阵生成函数。
    # 同时检查RA输出与两节点总电荷，避免只在一个终点偶然相等。
    np.testing.assert_allclose(output, independent.y[4, -1], rtol=0, atol=2e-12)
    deficit_end = b.internal_cap_f * o["internal_end_v"] + b.fine_pin_cap_f * o["pin_end_v"]
    supplied = (
        o["coarse_supplied_charge_c"] + o["fine_source_charge_c"] + o["fine_recovery_charge_c"]
    )
    np.testing.assert_allclose(supplied + deficit_end, q, rtol=0, atol=2e-15)
    assert np.all(o["fine_source_charge_c"] < q)


def test_unconfirmed_auxiliary_reference_domain_is_rejected(fixture):
    """辅助注入参考不能擅自归给精确REF_IN；外部模式必须有采样误差绑定。"""
    exp, caps, _, _ = fixture
    b = replace(reference(), auxiliary_domain="external_trace")
    # 缺少辅助采样误差时，不能默认与精确REF_IN共源或默认为无误差。
    with pytest.raises(ValueError, match="UNBOUND_AUXILIARY"):
        DynamicCapture(
            DynamicConfig(reference=True),
            caps,
            exp,
            RampDrive(0, 0),
            reference=b,
            reference_charge=lambda i, c: [],
        )


def test_no_xfail_remains_for_fixed_source_constraints():
    """修好后原文约束必须由普通断言通过，不能保留过期豁免。"""
    cfg = DynamicConfig()
    assert cfg.acquisition_s == cfg.period_s
    assert cfg.ra_start_s + cfg.ra_s == pytest.approx(cfg.period_s)


def test_early_follower_events_need_less_fine_reference_charge():
    """同总电荷，较早的位结果给粗驱动更多恢复时间；不能强行全部挪到最后。"""
    b = ReferenceBinding(
        1.0,
        1.0,
        3.0,
        0.2,
        0.1,
        0.3,
        1.0,
        (0.0, 0.0),
        "unit event-order example",
        "independent",
        "zero_reset",
    )
    cfg = DynamicConfig(period_s=10.0)
    q = np.array([0.1, 0.06])
    s = np.array([0.4, -0.2])
    early = SwitchedReference(b).cycle([(1.0, q / 2), (2.0, q / 2)], cfg, 0.03, s)[1]
    late = SwitchedReference(b).cycle([(cfg.command_s, q)], cfg, 0.03, s)[1]
    np.testing.assert_allclose(early["coarse_demand_c"], late["coarse_demand_c"])
    # 总电荷相同但时刻不同，粗源供电比例应随恢复时间改变。
    # 若只用最终码统计负载，这个差异会消失。
    assert np.all(early["fine_source_charge_c"] < late["fine_source_charge_c"])


def test_reference_preserves_previous_ra_output_when_bound():
    """连续RA不被默认清零；需要复位时必须在绑定中明确选择zero_reset。"""
    b = replace(reference(tau=0), ra_tau_s=100e-9, ra_initialization="previous_output")
    model = SwitchedReference(b)
    cfg = DynamicConfig()
    first, _ = model.cycle([], cfg, 0.03, np.zeros(2))
    second, _ = model.cycle([], cfg, 0.03, np.zeros(2))
    # 慢RA保留上笔记忆；两个输出相同意味着被错误逐笔清零。
    assert 0 < first < second < 0.03


def test_reference_rejects_charge_after_precise_handoff():
    """SAR负载不能作为精确REF_IN阶段的一笔完整负载偷偷注入。"""
    model = SwitchedReference(reference())
    # 精确交接之后的负载需要另一种电路绑定，本粗预充事件接口明确拒绝。
    with pytest.raises(ValueError, match="coarse phase"):
        model.cycle([(75e-9, np.ones(2))], DynamicConfig(), 0.0, np.ones(2))


def test_bound_acquisition_response_keeps_slot_and_time_ownership(fixture):
    """外供采集电压可替代固定R近似，但不能改变片号或孔径归属。

    返回目标电压以得到静态残差；不通过增加误差去迎合预期性能。
    两笔分别检查回调序号、八槽形状与实际采集时窗。
    注入端电压仍由既有电荷模型处理，回调不直接伪造ADC2码。
    """
    exp, caps, _, _ = fixture
    seen = []

    def response(index, context):
        # 记录副作用供外部断言，不复用内部生成的trace充当独立参考。
        seen.append((index, context))
        return np.full(8, context["target_v"]), context["target_v"]

    model = DynamicCapture(
        DynamicConfig(acquisition=True), caps, exp, RampDrive(1, 0), acquisition_response=response
    )
    capture(model, fixture, 2)
    assert [index for index, _ in seen] == [0, 1]
    for index, context in seen:
        np.testing.assert_array_equal(context["slice_ids"], np.arange(8))
        assert context["time_s"] == pytest.approx(index * 100e-9)
        assert context["time_s"] - context["start_s"] == pytest.approx(100e-9)
    np.testing.assert_allclose(model.last_trace["input_error_v"], 0, atol=1e-15)
    # 零输入误差仅验证外供采集接口，不据此声称真实开关有理想建立性能。


def test_two_interleaved_sadc_initial_states_are_independent(fixture):
    """外供响应能观察到两个交织SADC的独立历史，不能共用上一笔初态。

    前两笔分别首次使用量化器0和1，初值都应为0；第三笔才重用第0笔的1 V。
    本例通过回调检查历史，不依赖标称极点把初值抹除后偶然得到同码。
    """
    exp, caps, _, _ = fixture
    initial = []

    def response(index, context):
        initial.append(context["sadc_initial_v"])
        return np.ones(8), 1.0

    model = DynamicCapture(
        DynamicConfig(acquisition=True), caps, exp, RampDrive(1, 0), acquisition_response=response
    )
    capture(model, fixture, 3)
    np.testing.assert_array_equal(initial, [0.0, 0.0, 1.0])
