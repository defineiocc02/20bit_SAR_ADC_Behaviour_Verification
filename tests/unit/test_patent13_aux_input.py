"""专利 [13] US 10,541,702 B1 -- 辅助输入与电荷核算的机制级测试。

覆盖：
1. **建立标度律**：辅助供电让 R_f 允许放大 (C_f+C_pg)/C_f 倍，
   带宽上限同比例压缩，驱动噪声降 sqrt 倍（公式恒等式）；
2. **信息流**：寄生支路的时间常数在辅助模式下不经过 R_f；
3. **电荷核算**：驱动器每样本电荷减少的份额 = C_pg·ΔV；
4. **gate_boost**：r_on 的信号调制结构性归零（FIG.4/5 的机制主张）；
5. **入口拒绝**：非法模式显式报错，不静默退化。
"""

import numpy as np
import pytest

from adi_model.aux_input import AuxInputStage, build_stage
from adi_model.config import Config


@pytest.fixture()
def stage() -> AuxInputStage:
    cfg = Config()
    return build_stage(cfg, c_parasitic_ratio=0.02, r_filter=100.0)


class TestSettlingScaling:
    def test_r_filter_max_ratio_identity(self, stage):
        """R_f 允许放大倍数 = (C_f + C_pg)/C_f（[13] 机制的直接推论）。"""
        expected = (stage.c_filter + stage.c_parasitic) / stage.c_filter
        assert stage.r_filter_max_ratio == pytest.approx(expected)

    def test_aux_lowers_required_bandwidth(self, stage):
        bw_off = stage.required_filter_bw("off", eps=0.01)
        bw_aux = stage.required_filter_bw("dedicated_pin", eps=0.01)
        assert bw_aux < bw_off
        # 比例恒等：带宽压缩比 = C_f/(C_f+C_pg)
        assert bw_off / bw_aux == pytest.approx(stage.r_filter_max_ratio)

    def test_opamp_midpoint_matches_dedicated_pin(self, stage):
        """FIG.2 与 FIG.3 的差别只在物理实现；低阻供电的核算口径相同。"""
        assert stage.required_filter_bw("opamp_midpoint") == pytest.approx(
            stage.required_filter_bw("dedicated_pin")
        )

    def test_noise_scales_with_sqrt_of_bw_ratio(self, stage):
        assert stage.driver_noise_ratio() == pytest.approx(np.sqrt(stage.bw_ratio), rel=1e-12)
        # en_density 不参与比值（约掉）
        assert stage.driver_noise_ratio(en_density=7.0) == pytest.approx(
            stage.driver_noise_ratio(en_density=1.0)
        )

    def test_tighter_eps_requires_more_bandwidth(self, stage):
        """建立目标越紧（ε 越小），所需最低带宽越高（ln(1/ε) 单调）。"""
        assert stage.required_filter_bw("off", eps=0.001) > stage.required_filter_bw("off", eps=0.1)

    def test_gate_boost_keeps_off_bw_constraint(self, stage):
        """自举省的是 r_on 调制，不是寄生电荷——建立约束与 off 相同。"""
        assert stage.required_filter_bw("gate_boost") == pytest.approx(
            stage.required_filter_bw("off")
        )


class TestInformationFlow:
    def test_parasitic_tau_bypasses_r_filter_with_aux(self, stage):
        """辅助模式下寄生支路时间常数 = R_aux·C_pg，不经过 R_f。"""
        assert stage.parasitic_tau("off") == pytest.approx(
            stage.r_filter * (stage.c_filter + stage.c_parasitic)
        )
        assert stage.parasitic_tau("dedicated_pin") == pytest.approx(
            stage.r_aux * stage.c_parasitic
        )
        # 核心主张：辅助通路的寄生建立比经 R_f 快得多
        assert stage.parasitic_tau("dedicated_pin") < 0.01 * stage.parasitic_tau("off")

    def test_signal_tau_unchanged(self, stage):
        """信号电荷始终走 R_f·C_f——辅助只接管寄生，不改信号通路。"""
        for _mode in ("off", "dedicated_pin", "opamp_midpoint", "gate_boost"):
            assert stage.signal_tau() == pytest.approx(stage.r_filter * stage.c_filter)


class TestChargeAccounting:
    def test_driver_charge_reduction(self, stage):
        acc = stage.driver_charge_per_sample(dv=1.0)
        assert acc["off_coulomb"] == pytest.approx((stage.c_filter + stage.c_parasitic) * 1.0)
        assert acc["aux_coulomb"] == pytest.approx(stage.c_filter * 1.0)
        assert acc["ratio"] == pytest.approx(stage.c_filter / (stage.c_filter + stage.c_parasitic))

    def test_negative_dv_refused(self, stage):
        with pytest.raises(ValueError, match="不能为负"):
            stage.driver_charge_per_sample(dv=-0.1)


class TestGateBoost:
    def test_ron_modulation_is_structurally_zero(self, stage):
        """FIG.4/5：V_GS 恒定 -> r_on 与信号无关（调制因子 = 0）。"""
        boosted = AuxInputStage(**{**stage.__dict__, "mode": "gate_boost"})
        assert boosted.ron_modulation_factor() == 0.0
        for mode in ("off", "dedicated_pin", "opamp_midpoint"):
            st = AuxInputStage(**{**stage.__dict__, "mode": mode})
            assert st.ron_modulation_factor() == 1.0

    def test_boost_voltage_is_the_disclosed_example(self, stage):
        assert stage.boost_voltage == pytest.approx(3.3)  # [13] FIG.4：3.3 V 例


class TestValidation:
    def test_illegal_mode_refused(self):
        with pytest.raises(ValueError, match="不是已实现取值"):
            build_stage(Config(), mode="wireless").validated()

    def test_nonpositive_params_refused(self):
        cfg = Config()
        st = build_stage(cfg)
        bad = AuxInputStage(**{**st.__dict__, "c_parasitic": -1.0})
        with pytest.raises(ValueError, match="必须为正"):
            bad.validated()

    def test_bad_eps_refused(self, stage):
        with pytest.raises(ValueError, match="必须在"):
            stage.required_filter_bw(eps=0.0)
