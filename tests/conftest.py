"""提供测试专用的显式合成组件输入，不作为目标芯片的物理默认值。

小维度校准数据用于独立线性代数检查。夹具不补齐缺失的 Config 字段，也不代替
调用方推断实际选通、逐单元信号／电压项或重构系数。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def rng():
    """声明测试专用随机流，与制造失配参数无关。"""
    return np.random.default_rng(20260910)


@pytest.fixture
def adc2_config_factory():
    """只绑定独立 ADC2 内核消费的五项输入。"""
    from adi_model.simulation.config import Config

    def make(*, bits, lo, hi, endpoint):
        return Config(
            adc2_n_bits=bits,
            adc2_v_min=lo,
            adc2_v_max=hi,
            adc2_codebook="uniform_midrise",
            adc2_endpoint_policy=endpoint,
        )

    return make


@pytest.fixture
def synthetic_sadc_thresholds():
    """提供显式声明的阶梯阈值，不作为提取的 SADC 阈值。"""
    return tuple(np.linspace(-3.0, 3.0, 513))


@pytest.fixture
def configuration_inputs(synthetic_sadc_thresholds):
    """仅构造输入校验示例，不运行映射或物理模型。"""
    from adi_model.simulation.config import Config

    return Config(
        dac_n_main=2,
        dac_n_sub=1,
        sadc_thresholds_v=synthetic_sadc_thresholds,
        sadc_endpoint_policy="half_open",
        adc2_n_bits=12,
        adc2_v_min=-2.0,
        adc2_v_max=2.0,
        adc2_codebook="uniform_midrise",
        adc2_endpoint_policy="half_open",
        ra_v_clip=2.5,
    )


@pytest.fixture
def explicit_format():
    """声明测试算术格式，不作为论文披露或硬件默认格式。"""
    from adi_model.contracts.fixed_point import FixedPointFormat

    return FixedPointFormat(
        weight_fraction_bits=30,
        voltage_fraction_bits=32,
        voltage_bits=64,
        coefficient_bits=48,
        accumulator_bits=96,
    )


@pytest.fixture
def observation_factory():
    """要求调用方提供实际控制记录、信号系数、电压项、原始码和有效性。"""
    from adi_model.calibration.weight_calibration import DigitalObservation

    return DigitalObservation


@pytest.fixture
def identity_record_factory():
    """构造显式三项算术恒等式，不推断模拟绑定。"""
    from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation

    def make(*, bits):
        count = 2**bits
        spec = CalibrationSpec(1, 1, 2, 1, 3.0, bits, -3.0, 3.0, "uniform_midrise", "half_open")
        return DigitalObservation(
            spec=spec,
            slice_ids=np.zeros((count, 1), np.int64),
            adc2_code=np.arange(count, dtype=np.int64),
            main_masks=np.zeros((count, 1, 2), bool),
            sub_masks=np.zeros((count, 1, 1), bool),
            signal_mask=np.ones((count, 1, 3), bool),
            known_voltage_term_v=np.zeros((count, 1, 3)),
            overflow=np.zeros(count, bool),
            sample_ids=np.arange(count),
            static_charge_domain=True,
        )

    return make


@pytest.fixture
def synthetic_linear_data():
    """声明 128×7 线性回归数据，不作为缩小的物理 ADC。"""
    from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation

    generator = np.random.default_rng(17)
    spec = CalibrationSpec(2, 1, 2, 1, 3.0, 24, -2.0, 2.0, "uniform_midrise", "half_open")
    n = 128
    ids = generator.integers(0, 2, (n, 1))
    signal = generator.integers(0, 2, (n, 1, 3))
    signal[:, :, 0] = 1
    term = generator.uniform(-0.25, 0.25, (n, 1, 3))
    known = generator.uniform(-0.4, 0.4, n)
    weights = np.array([[0.11, 0.13, 0.07], [0.12, 0.10, 0.08]])
    offset = 0.002
    voltage = (weights[ids] * (signal * known[:, None, None] + term)).sum((1, 2)) + offset
    code = np.floor((voltage - spec.adc2_v_min) / spec.adc2_step_v).astype(np.int64)
    data = DigitalObservation(
        spec=spec,
        slice_ids=ids,
        adc2_code=code,
        main_masks=generator.integers(0, 2, (n, 1, 2)),
        sub_masks=generator.integers(0, 2, (n, 1, 1)),
        signal_mask=signal,
        known_voltage_term_v=term,
        overflow=np.zeros(n, bool),
        sample_ids=np.arange(n),
        static_charge_domain=True,
    )
    return SimpleNamespace(data=data, known=known, weights=weights, offset=offset)


@pytest.fixture
def toy_physical_case():
    """声明 2 主单元、2 子单元的电容夹具，用于 KCL 与事务检查，不作为芯片数据。"""
    from adi_model.simulation.config import Config
    from adi_model.topology.dac_arch import NominalCodebook, SplitChip
    from adi_model.control.dem import SamplingConnections, SplitSwitchCommand

    capacitance = 1e-12
    chip = SplitChip(
        C_main=np.full((18, 2), capacitance),
        C_sub=np.full((18, 2), capacitance),
        C_bridge=np.full(18, 2 * capacitance),
        c_p_sub=np.full(18, 0.5 * capacitance),
        c_p_main=np.full(18, 0.25 * capacitance),
        C_feedback_true=0.4 * capacitance,
    )
    ids = np.arange(8)[None, :]
    main = np.broadcast_to([True, False], (1, 8, 2)).copy()
    sub = main.copy()
    return SimpleNamespace(
        cfg=Config(dac_n_main=2, dac_n_sub=2),
        chip=chip,
        capacitance=capacitance,
        ids=ids,
        command=SplitSwitchCommand(main, sub),
        connections=SamplingConnections(np.zeros((1, 8, 2), int), np.zeros((1, 8, 2), int)),
        book=NominalCodebook(0, 0, np.array([1.0])),
    )


@pytest.fixture(scope="session")
def explicit_pipeline_factory():
    """声明双极 18/8 小型流水线夹具，其二进制绑定不是原芯片 DEM。

    9 主单元／1 子单元数组、参考轨、反馈电容及 seed817 调度器均为显式测试输入。
    不从未完整声明的 Config 补值，也不从物理真值生成数字推理系数。
    """
    from dataclasses import replace

    from adi_model.simulation.config import Config
    from adi_model.topology.dac_arch import NominalCodebook, SplitChip
    from adi_model.control.dem import SamplingConnections, SplitSwitchCommand
    from adi_model.topology.mapper import Mapper
    from adi_model.simulation.slice_pool import PhysicalSlicePool
    from adi_model.control.timing import SlicePlan

    def binary_masks(geometry, codes, states, slice_ids):
        if np.any(states != 0):
            raise ValueError("this declared binary fixture has no unit-DEM mapping")
        bits = ((codes[:, None] >> np.arange(9)) & 1).astype(bool)
        return SplitSwitchCommand(
            np.broadcast_to(bits[:, None, :], (len(codes), 8, 9)).copy(),
            np.zeros((len(codes), 8, 1), bool),
        )

    def all_signal(geometry, sample_ids, slice_ids, control):
        if control is not None:
            raise ValueError("this fixture declares fixed all-signal acquisition")
        return SamplingConnections(
            np.zeros((len(sample_ids), 8, 9), int), np.zeros((len(sample_ids), 8, 1), int)
        )

    def declared_schedule(n, n_slices, n_active):
        if (n_slices, n_active) != (18, 8):
            raise ValueError("synthetic schedule dimensions are explicitly 18/8")
        generator = np.random.default_rng(817)
        conv, acq = np.empty((n, 8), np.int64), np.empty((n, 8), np.int64)
        current = np.arange(8)
        for index in range(n):
            conv[index] = current
            current = generator.choice(np.setdiff1d(np.arange(18), current), 8, replace=False)
            acq[index] = current
        return SlicePlan(
            conv,
            acq,
            np.arange(n, dtype=np.int64) - 1,
            np.ones(n, bool),
            "SYNTHETIC uniform8-of10 seed817; NOT source or RTL controller",
            {},
        )

    def make(*, n_samples, config_changes=None):
        cfg = Config(
            sadc_thresholds_v=tuple((-3 + 6 * np.arange(513) / 512).tolist()),
            sadc_endpoint_policy="closed",
            adc2_n_bits=16,
            adc2_v_min=-0.05,
            adc2_v_max=0.45,
            adc2_codebook="uniform_midrise",
            adc2_endpoint_policy="closed",
            ra_v_clip=0.5,
        )
        if config_changes:
            cfg = replace(cfg, **config_changes)
        chip = SplitChip(
            C_main=np.tile(1e-15 * 2.0 ** np.arange(9), (18, 1)),
            C_sub=np.full((18, 1), 2e-15),
            C_bridge=np.full(18, 2e-15),
            c_p_sub=np.zeros(18),
            c_p_main=np.zeros(18),
            C_feedback_true=128e-15,
        )
        book = NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512)
        mapper = Mapper(np.arange(512, dtype=np.int64), nominal_codebook=book)
        pool = PhysicalSlicePool(
            cfg,
            chip=chip,
            nominal_codebook=book,
            mask_binding=binary_masks,
            acquisition_binding=all_signal,
            schedule_binding=declared_schedule,
        )
        controls = {
            "pool": pool,
            "mapper": mapper,
            "sample_ids": np.arange(n_samples, dtype=np.int64),
            "dem_states": np.zeros(n_samples, dtype=np.int64),
            "sampled_top_v": 0.0,
            "sampled_subnode_v": 0.0,
            "reference_high_v": 3.0,
            "reference_low_v": -3.0,
            "nominal_reference_high_v": 3.0,
            "nominal_reference_low_v": -3.0,
            "static_charge_domain": True,
        }
        return cfg, controls

    return make
