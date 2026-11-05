"""保留 2026-09-11 起多轮外部审阅的历史行为合同。

本文件侧重修复是否进入当时的结果生成入口，包括 run_sim_split、run_pipeline、
audit_provenance 及 run_all 的结果判定。历史审阅曾区分普通断言与严格预期失败；
是否存在相应标记应以现存装饰器为准；测试状态以执行记录为准。
R 编号是本文件的稳定标识，不等同各轮审阅者的编号。

第二次审阅（v7.0.0）对应 docs/review_response_2026-09-11.md：
R1→原 R1，跨周期样本归属；R2→原 R2，物理池进入主链；R3→原 R2/R2b，
来源记录识别实际拟合值；R4→原 R6，配置作用于入口；R5→原 R5，数值失败影响
退出状态；R6→原 R7，记录包含拐角以下闪烁功率。原文 R3 的 7+2=9 措辞、
R4 的 KTC 理想数字观测边界、R8 的 ADR 0005 文档问题在该裁定文档跟踪，
不在此另建行为断言。

第三次审阅（v7.0.1）对应 docs/review_response_2026-09-11b.md：
R7→§2.1，检查配置自检表；R8→§2.4，覆盖值的来源约束；R9→§2.5，物理池
因果关系（位于 R2）；R10→§3.1，dem_mode 与调度机制；R11→§2.2/§3.3，
已知限制账本；§2.3 的非法配置入口拒绝位于 R4。这里的 §3.3 是裁定文档的
KTC 带宽讨论；历史“第三轮审阅 §3.3”则指审阅者 §3 第 3 项的源字符串检查，
对应裁定文档 §2.5，两者编号不同。

第四次审阅（v7.0.2）对应 docs/review_response_2026-09-11c.md：
R12→K1，f_max 约束节点；R13→K2，电容对样本的因果归属（位于 R2）；
R14→K3，dem_mode 到达各入口（位于 R10）；R15→K4，布尔结果类型与必需结果集。

第五次审阅（v7.0.4）对应 docs/review_response_2026-09-11d.md：
R16→§4.2，旧耦合旋转的 512 标签／64 物理排列；R17→§5.2，共享源阻抗下
总时间常数的适用边界；R18→§11，观察噪声参与时的总残差最优系数。
这些历史夹具及数值不构成当前架构需求，尤其不能以旧排列空间代替论文三维 DEM。
"""

from __future__ import annotations

import inspect
import math
import pathlib

import numpy as np
import pytest

from adi_model import (
    LEGAL_VALUES,
    Config,
    ConfigError,
    SplitDAC,
    audit_provenance,
    build_split_chip,
    resolve_ra_noise,
    run_sim_split,
    sine_input,
)
from adi_model.acceptance import (
    ACCEPTANCE_CONTAINERS,
    KNOWN_LIMITS,
    MIN_RECORDS,
    REQUIRED_RECORDS,
    acceptance_records,
    gate,
    hard_failures,
)
from adi_model.noise_phase import kappa_optimal, sigma_res_analytic
from adi_model.pipeline import run_pipeline
from adi_model.provenance import GradingError, SourceGrade, annotate_config
from adi_model.ra import flicker_series
from adi_model.scheduler import Scheduler, ShuffledScheduler, make_scheduler
from adi_model.slice_pool import PhysicalSlicePool

N_SMALL = 2048


def _coherent(fs: float, n: int, cycles: int = 97) -> float:
    """计算在 fs 采样率、n 点记录中恰落 FFT 频点的输入频率。

    Args:
        fs: 采样率，单位 Hz。
        n: 记录长度，单位为样本数。
        cycles: 记录内的整数信号周期数。

    Returns:
        相干输入频率，单位 Hz。
    """
    return fs * cycles / n


def _lean_cfg(**over: object) -> Config:
    """构造历史合同使用的短记录配置，关闭相应慢机制。

    Args:
        **over: 施加到该历史基准配置的字段覆盖。

    Returns:
        供此文件旧接口测试使用的 Config；不是当前物理模型的默认参数声明。
    """
    base: dict[str, object] = {
        "dac_arch": "split",
        "dither_mode": "off",
        "ktc_enable": False,
        "mismatch_enable": False,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_mismatch_enable": False,
        "sadc_offset": 0.0,
        "sadc_rdac_gain_mismatch": 0.0,
    }
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


# ===========================================================================
# R1：转换切片必须持有该样本实际采集的电荷。
# ===========================================================================
class TestR1SampleOwnership:
    """检查组间互斥与跨周期电荷归属两项独立条件。

    conv[n] 与 acq[n] 不相交只说明同周期不争用；物理样本还要求
    conv[n]=acq[n-1]。历史 v6.1 的调度只满足前者，约 55% 转换使用了错误电容。
    因此两项条件分别断言，不能互相代替。
    """

    def test_default_scheduler_preserves_sample_ownership(self):
        """检查当时默认乒乓入口转换的组等于其前周期采集组。"""
        cfg = Config()
        n = 4096
        conv, acq = Scheduler(cfg).reserve_dual(n, np.random.default_rng(20260911))
        overlap = max(len(set(conv[i]) & set(acq[i])) for i in range(n))
        assert overlap == 0, f"within-cycle collision: {overlap} slices"

        full = sum(1 for i in range(1, n) if set(conv[i]) == set(acq[i - 1]))
        assert full == n - 1, (
            f"only {full}/{n - 1} conversions used the previously acquired group; "
            "within-cycle disjointness is necessary but not sufficient"
        )

    def test_shuffled_scheduler_preserves_sample_ownership(self):
        """检查随机调度同样保持样本归属。

        历史平均交集约 3.55/8，接近两次独立 8-of-18 抽取的期望 8*8/18≈3.556，
        用于区分独立重抽与因果传递，不能归因为个别随机种子。
        """
        cfg = Config()
        n = 8192
        conv, acq = ShuffledScheduler(cfg, np.random.default_rng(20260911)).reserve_dual(n)
        full = sum(1 for i in range(1, n) if set(conv[i]) == set(acq[i - 1]))
        assert full == n - 1, f"sample ownership held for only {full}/{n - 1} transitions"


# ===========================================================================
# R2：物理池与结果生成路径的因果连接。
# ===========================================================================
class TestR2PhysicalPoolOnMainPath:
    """用定向电容扰动检查物理池与实际结果链的因果连接。

    仅搜索 PhysicalSlicePool 字符串可被无效 import 或注释满足；仅检查结果属性
    存在也可被常量占位满足。这里要求扰动某切片后，仅实际使用该切片的样本内部
    电容、DAC 权重或残差增益变化，其他样本保持不变。

    两个组件正对照用于确认切片选择信息存在且物理池本身具有该局部因果关系；
    这些检查描述测试目的，不声明现存入口已经具备所有历史接口。
    """

    def test_the_selection_information_exists_and_is_causal(self):
        """正对照一：检查每样本转换／采集编号已显式记录，并满足跨周期归属。"""
        cfg = _lean_cfg()
        sched = Scheduler(cfg)
        conv, acq = sched.reserve_dual(64, np.random.default_rng(0))
        conv_a, acq_a = np.asarray(conv), np.asarray(acq)
        assert conv_a.shape == (64, 8), f"conversion groups are not per-sample: {conv_a.shape}"
        assert acq_a.shape == (64, 8), f"acquisition groups are not per-sample: {acq_a.shape}"
        assert all(
            set(conv_a[i]) == set(acq_a[i - 1]) for i in range(1, 64)
        ), "precondition failed: even the default scheduler does not preserve ownership"

    def test_the_pool_ties_a_slice_to_exactly_the_samples_that_use_it(self):
        """正对照二：扰动一个切片，仅包含该切片的转换样本电容变化。

        同时检查受影响组与未受影响组，排除对全部样本施加统一变化的假归属。
        """
        cfg = _lean_cfg()
        pool = PhysicalSlicePool(cfg, np.random.default_rng(2))
        # 周期 0 尚未完成采集，不作为有效物理转换；
        # 按旧池规划器的有效性标记排除该启动周期。
        conv = pool.plan(48, np.random.default_rng(3)).conv[1:]
        before = pool.signal_capacitance(conv)

        victim = 0
        pool.unit_caps[victim] *= 1.10
        pool.c_slice_total[victim] = pool.unit_caps[victim].sum()
        after = pool.signal_capacitance(conv)

        uses = np.array([victim in group for group in conv])
        assert (
            uses.any() and not uses.all()
        ), "degenerate control: slice 0 either converts every sample or none"
        assert np.all(before[uses] != after[uses]), "a user of slice 0 did not move"
        assert np.all(
            before[~uses] == after[~uses]
        ), "a non-user of slice 0 moved — the coupling is not per-slice"

    def test_the_dac_error_does_not_see_the_selection(self):
        """保留旧主链缺失转换组参数的检查。

        当时 SplitDAC.evaluate_physical 只接收 k_eq、sid，无法依据实际 8/18 组决定
        误差。本断言记录该历史连接状态，不能作为当前接口必须保留该缺陷的要求。
        """
        from adi_model.dac_arch import SplitDAC

        params = list(inspect.signature(SplitDAC.evaluate_physical).parameters)
        assert params == ["self", "k_eq", "sid"], (
            "evaluate_physical gained a parameter — if it is now the conversion "
            "group, the slice selection reaches the DAC error and this test "
            "should assert that it does"
        )

    def test_perturbing_the_capacitors_behind_a_sample_moves_that_sample(self):
        """检查某样本所用电容的扰动确实作用于该样本内部量。

        定向修改实际转换组后，关联样本的电荷、DAC 权重或残差增益应变化，未使用该
        电容的样本不变。最终输出可能因电荷关系或数字重构抵消而不变，所以检查目标
        是内部物理量及其样本归属，而非强制最终码变化。
        """
        cfg = _lean_cfg()
        x = sine_input(0.5 * cfg.v_fs, _coherent(cfg.fs, N_SMALL))
        res = run_sim_split(cfg, x, N_SMALL)

        # 步骤一：先取得各样本对应的电容编号。
        # 缺少编号时无法定向施加下一步扰动，
        # 因此用命名断言报告缺失连接，避免仅抛 AttributeError。
        trace = {
            name: getattr(res, name, None)
            for name in ("conv_slice_ids", "acq_slice_ids", "held_sample", "pool")
        }
        missing = [name for name, value in trace.items() if value is None]
        assert not missing, (
            "SimResult cannot attribute an output to physical capacitors: "
            f"{missing} absent. With no per-sample slice trace there is no way to "
            "name the 8-of-18 capacitors that converted sample i, so e_dac and "
            "c_active cannot be tied to the capacitor set that produced them, and "
            "a perturbation cannot be aimed."
        )

        # 步骤二：将调用方提供的物理池接入入口，再定向扰动；
        # 被扰动电容应只改变实际使用它们的样本 e_dac，
        # 其他转换组的样本应保持不变，
        # 以同时约束敏感性和样本归属。
        pool = trace["pool"]
        conv = np.asarray(trace["conv_slice_ids"])
        victim = int(conv[1, 0])
        before = np.array(res.e_dac)
        pool.unit_caps[victim] *= 1.10
        pool.c_slice_total[victim] = pool.unit_caps[victim].sum()
        rerun = run_sim_split(cfg, x, N_SMALL, **{"pool": pool})
        after = np.array(rerun.e_dac)

        uses = np.array([victim in group for group in conv])
        assert np.any(before[uses] != after[uses]), (
            "the capacitors that converted sample i were perturbed and e_dac did "
            "not move — the pool is not on the path that produces the result"
        )
        assert np.all(before[~uses] == after[~uses]), (
            "a sample that did not use the perturbed slices moved — the coupling "
            "is not per-sample"
        )


# ===========================================================================
# R3：来源记录识别实际使用的拟合值。
# ===========================================================================
class TestR3ProvenanceSeesTheActiveFit:
    """检查历史 None 解析为拟合值时，来源记录仍识别实际启用的拟合。"""

    def test_default_config_raises_the_fit_into_fitted_in_use(self):
        """检查历史 resolve_ra_noise 解析的噪声值被列入实际使用的拟合参数。"""
        cfg = Config()
        assert resolve_ra_noise(cfg) > 0.0, "precondition: the resolver is active"
        report = audit_provenance(cfg)
        assert (
            "ra_out_noise_rms" in report["fitted_in_use"]
        ), "the active RA-noise fit is missing from fitted_in_use"
        assert report["resolved_sentinels"], "the resolved value must be reported"

    def test_overriding_a_disclosed_field_is_reported(self):
        """检查覆盖值不能继续沿用原披露数值的权威性。

        历史 fs 的来源为 [00] 摘要 40 MS/s；手动改为 80e6 时，报告须识别该覆盖。
        """
        assert "fs" not in audit_provenance(Config())["overridden"]
        report = audit_provenance(Config(fs=80e6))
        assert "fs" in report["overridden"], (
            "fs=80e6 still carries a source that says 40 MS/s, and the report "
            "did not flag the override"
        )


# ===========================================================================
# R4：配置字段在声明的消费入口生效。
# ===========================================================================
class TestR4ConfigTakesEffectAtTheRunner:
    """检查选项在实际运行入口生效，而不只在实现该选项的辅助方法中生效。"""

    @pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
    def test_ra_gain_model_fixed_reaches_both_runners(self, runner):
        """检查历史 fixed 增益选项到达两条运行链。

        旧入口内联计算电容比并显式传入增益，曾绕过 gain_vector 的 fixed 分支。
        夹具设置 gain_error，使关闭失配时两种模式仍可区分。
        """
        cfg_charge = _lean_cfg(ra_gain_model="charge")
        cfg_fixed = _lean_cfg(ra_gain_model="fixed", gain_error=0.01)
        inp = sine_input(0.5 * cfg_charge.v_fs, _coherent(cfg_charge.fs, N_SMALL))

        r_charge = runner(cfg_charge, inp, N_SMALL, rng=np.random.default_rng(7))
        r_fixed = runner(cfg_fixed, inp, N_SMALL, rng=np.random.default_rng(7))

        assert r_fixed.g_vec.mean() == pytest.approx(cfg_fixed.g_actual, rel=1e-12)
        assert not np.array_equal(r_charge.out, r_fixed.out), (
            f"{runner.__name__} ignores ra_gain_model — the fixed-gain control "
            "does not reach the output"
        )

    def test_unknown_ra_gain_model_is_rejected(self):
        """检查未知增益模式被拒绝，不静默使用电容比路径。

        校验表报告失败与入口拒绝执行分别检查，避免非法配置仍生成貌似有效的结果。
        """
        key = "RA 增益口径 ra_gain_model ∈ {charge, fixed}"
        checks = _lean_cfg(ra_gain_model="Fixd").validate(verbose=False)
        assert key in checks
        assert checks[key][2] is False, "an unknown gain model passed validation"
        assert _lean_cfg(ra_gain_model="charge").validate(verbose=False)[key][2] is True

    @pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
    def test_an_illegal_config_is_refused_at_the_entry(self, runner):
        """检查非法配置在入口被拒绝，不采用报告失败后继续运行的行为。"""
        cfg = _lean_cfg(ra_gain_model="Fixd")
        inp = sine_input(0.5 * cfg.v_fs, _coherent(cfg.fs, N_SMALL))
        with pytest.raises(ConfigError) as ei:
            runner(cfg, inp, 64)
        assert "ra_gain_model" in str(
            ei.value
        ), f"{runner.__name__} refused the config but did not say which field was wrong"

    def test_an_illegal_enum_is_refused_for_every_declared_field(self):
        """检查 LEGAL_VALUES 中每个字段的枚举约束，而不只检查已有校验记录的一项。"""
        for field_name, allowed in LEGAL_VALUES.items():
            bad = _lean_cfg(**{field_name: "__no_such_value__"})
            violations = bad.legality_violations()
            assert any(field_name in v for v in violations), (
                f"{field_name} accepts an undeclared value silently; "
                f"declared options are {allowed}"
            )


# ===========================================================================
# R5：数值 FAIL 进入命令退出状态。
# ===========================================================================
class TestR5HardGateBindsToExitCode:
    """检查数值结果判定进入命令退出状态，确定性不能代替正确性。"""

    def test_pass_records_and_summaries_are_collected(self):
        """检查 run_all 使用的逐项记录与摘要两种结果容器均被收集。"""
        results = {
            "pipeline": {"PASS": False},
            "flicker": {"PASS": True},
            "s12_summary": {"a": True, "b": False},
        }
        assert hard_failures(results) == ["pipeline.PASS", "s12_summary.b"]

    def test_non_acceptance_dicts_are_ignored(self):
        """检查反例数据行及功能开关值不会被误判为验收失败。"""
        results = {
            "calibration_control": {"uncalibrated_sndr_db": -6.0, "dem_enable": False},
            "s7_dem_off": {"SNDR_dB": 90.0},
        }
        assert hard_failures(results) == []

    def test_run_all_binds_the_rule_to_a_nonzero_exit(self):
        """静态检查旧 run_all 入口连接判定规则与非零退出。

        该脚本导入即运行实验，因此此处不导入脚本；规则本身由其他行为测试检查，
        这里只约束入口中的文本连接。
        """
        src = pathlib.Path("tools/run_all.py").read_text(encoding="utf-8")
        assert "gate(R)" in src, "run_all does not evaluate the hard gate"
        assert (
            "sys.exit(0 if _GATE.ok() else 1)" in src
        ), "run_all reports FAIL but never binds it to the exit code"


# ===========================================================================
# R6：短记录中的拐角以下闪烁功率。
# ===========================================================================
class TestR6FlickerDriftBackfill:
    """检查短记录无法分辨的低频功率没有被当作不存在。"""

    def test_short_record_contains_the_unresolved_low_frequency_power(self):
        """检查假设低截止为 0.1 Hz 的短记录不应全零。

        历史 f_min=fs/n=1220.7 Hz 高于拐角，离散谱为空；低于 f_min 的功率是不可分辨，
        旧漂移模型的目标是表示这部分功率。
        """
        x = flicker_series(
            32768,
            40e6,
            40.0,
            1e-6,
            np.random.default_rng(0),
            t_obs=10.0,
            include_drift=True,
        )
        assert np.count_nonzero(x) > 0, "the drift back-fill produced all zeros"


# ===========================================================================
# R7：总判定收集配置自检结果。
# ===========================================================================
class TestR7GateCoversConfigChecks:
    """检查配置自检表中的失败同样进入总结果判定。

    旧收集器只读 results[name]["PASS"] 和 results[name+"_summary"]，
    遗漏 validate 和 validate_ktc 的逐项 PASS；本组保留该容器遗漏反例。
    """

    def test_a_failing_config_check_is_collected(self):
        """检查配置自检表中的 FAIL 被收集为失败。"""
        results = {
            "validate": {"invalid_config": {"PASS": False}},
            "validate_ktc": {"bw": {"PASS": False}},
        }
        assert hard_failures(results) == ["validate.invalid_config", "validate_ktc.bw"]

    def test_the_explicit_lists_are_the_ones_run_all_actually_writes(self):
        """检查显式容器列表与旧 run_all 实际写入的模式一致。"""
        assert set(ACCEPTANCE_CONTAINERS) <= {"validate", "validate_ktc"}
        src = pathlib.Path("tools/run_all.py").read_text(encoding="utf-8")
        for container in ACCEPTANCE_CONTAINERS:
            assert (
                f'R["{container}"]' in src
            ), f"the rule collects {container!r} but run_all.py never writes it"

    def test_collecting_a_container_must_not_swallow_switch_values(self):
        """扩大结果容器覆盖时仍区分功能开关 False 与验收失败。"""
        results = {
            "validate": {"margin_v": {"PASS": True, "actual": None}},
            "calibration_control": {"dem_enable": False, "uncal_vs_cal_db": -6.0},
        }
        assert hard_failures(results) == []


# ===========================================================================
# R8：覆盖值不能沿用原披露值的来源权威性。
# ===========================================================================
class TestR8OverrideIsEnforced:
    """检查来源等级跟随实际值，而不是只跟随字段名。

    v7.0.1 曾报告覆盖标记，却仍允许 fs=80e6 通过 DISCLOSED 的 require 并返回 8e7；
    该历史反例用于检查手动覆盖不能继承 40 MS/s 披露的权威性。
    """

    def test_an_overridden_value_is_not_quotable_as_disclosed(self):
        ann = annotate_config(Config(fs=80e6))
        assert ann["fs"].overridden is True, "the override is not recorded on the Graded"
        with pytest.raises(GradingError) as ei:
            ann["fs"].require(SourceGrade.DISCLOSED)
        assert "differs from the stock default" in str(
            ei.value
        ), "refused, but without saying why the declared source no longer applies"

    def test_the_unmodified_field_is_still_quotable(self):
        """正对照：未经覆盖且满足来源要求的字段仍可引用。"""
        assert Config().fs == 40e6
        ann = annotate_config(Config())
        assert ann["fs"].overridden is False
        assert ann["fs"].require(SourceGrade.DISCLOSED) == 40e6

    def test_effective_grade_demotes_only_overridden_disclosed_or_derived(self):
        ann_ovr = annotate_config(Config(fs=80e6))
        ann_ok = annotate_config(Config())
        assert ann_ovr["fs"].grade is SourceGrade.DISCLOSED
        assert ann_ovr["fs"].effective_grade is SourceGrade.ASSUMED
        assert ann_ok["fs"].effective_grade is SourceGrade.DISCLOSED
        # 非披露/派生字段的等级不因覆盖而改变（例如 pdk_sigma_est_ppm 本就是假设）
        assumed = annotate_config(Config(pdk_sigma_est_ppm=2000.0))["pdk_sigma_est_ppm"]
        assert assumed.grade is SourceGrade.ASSUMED
        assert assumed.effective_grade is SourceGrade.ASSUMED

    def test_map_preserves_the_override_flag(self):
        """检查派生量保留上游覆盖标记，不通过映射恢复来源权威性。"""
        ann = annotate_config(Config(fs=80e6))
        derived = ann["fs"].map(lambda v: v / 1e6)
        assert derived.overridden is True
        with pytest.raises(GradingError):
            derived.require(SourceGrade.DISCLOSED)


# ===========================================================================
# R10：历史调度字段消费情况；第二轮未消费，第三轮仅一入口生效。
# ===========================================================================
class TestR10DemModeIsWired:
    """保留历史 dem_mode 在各入口选择调度机制的检查。

    第二轮审阅记录该字段未被消费；v7.0.2 后三入口均经 make_scheduler，但当时
    run_pipeline 调用被覆写的 reserve_dual，run_sim 和 run_sim_split 仅调用未覆写的
    reserve，所以选择相同类不等于执行相同机制。因果随机调度还须满足
    conv[n]=acq[n-1]；它会改变历史 stage19 数值，不能只按开关名称推断已实现。
    """

    def test_dem_mode_selects_the_scheduler_class(self):
        """检查旧 rotate 选择基础类、permute 选择随机调度类。"""
        rng = np.random.default_rng(0)
        rotate = make_scheduler(_lean_cfg(dem_mode="rotate"), rng)
        permute = make_scheduler(_lean_cfg(dem_mode="permute"), rng)
        assert type(rotate) is Scheduler, f"rotate gave {type(rotate).__name__}"
        assert type(permute) is ShuffledScheduler, f"permute gave {type(permute).__name__}"

    def test_an_explicit_scheduler_that_contradicts_the_config_is_refused(self):
        """检查显式调度器与配置声明冲突时被拒绝。"""
        cfg = _lean_cfg(dem_mode="permute")
        with pytest.raises(ConfigError, match="dem_mode"):
            run_sim_split(cfg, sine_input(0.5 * cfg.v_fs, 1e6), 64, scheduler=Scheduler(cfg))

    def test_dem_mode_changes_the_pipeline_result(self):
        """检查实际消费逐周期组的旧流水线入口能区分两种模式。

        该旧路径只有启用 dyn_input_settling 才将组选择用于循环；不消费组时结果相同
        不能证明调度开关正确，所以夹具显式选择可观察的路径。
        """
        inp = sine_input(0.5 * _lean_cfg().v_fs, _coherent(_lean_cfg().fs, N_SMALL))
        common = {"dyn_input_settling": True, "slice_bw_spread": 0.3}
        out_a = run_pipeline(
            _lean_cfg(dem_mode="rotate", **common),
            inp,
            N_SMALL,
            rng=np.random.default_rng(11),
        ).out
        out_b = run_pipeline(
            _lean_cfg(dem_mode="permute", **common),
            inp,
            N_SMALL,
            rng=np.random.default_rng(11),
        ).out
        assert not np.array_equal(
            np.asarray(out_a), np.asarray(out_b)
        ), "dem_mode no longer reaches the pipeline — the binding was removed"

    def test_dem_mode_changes_the_result_at_every_entry(self):
        """检查历史各入口对同一调度开关具有一致含义。"""
        base = _lean_cfg()
        x = sine_input(0.5 * base.v_fs, _coherent(base.fs, N_SMALL))
        common = {"mismatch_enable": True, "dyn_input_settling": True}
        out_a = run_sim_split(
            _lean_cfg(dem_mode="rotate", **common), x, N_SMALL, rng=np.random.default_rng(11)
        ).out
        out_b = run_sim_split(
            _lean_cfg(dem_mode="permute", **common), x, N_SMALL, rng=np.random.default_rng(11)
        ).out
        assert not np.array_equal(np.asarray(out_a), np.asarray(out_b)), (
            "run_sim_split ignores dem_mode: ShuffledScheduler inherits "
            "reserve() from Scheduler, so the selected class does not change the "
            "slice groups this entry actually uses"
        )


# ===========================================================================
# R11：保留参考结果与总判定规则。
# ===========================================================================
class TestR11KnownLimits:
    """检查保留的 results.json 与结果判定、已知限制账本相互一致。

    本组目标包括参考输出受同一规则约束，以及每项限制都有原因且没有过期。
    这些是待执行的断言目的，不是当前参考产物通过的声明。
    """

    @staticmethod
    def _reference_results() -> dict:
        """读取旧 run_all.py 写出的保留参考结果。"""
        path = pathlib.Path("tools/results/results.json")
        assert path.exists(), "the reference results.json is missing"
        import json

        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_shipped_results_pass_the_gate(self):
        verdict = gate(self._reference_results())
        assert (
            verdict.failures == []
        ), f"the shipped results.json fails the gate: {verdict.failures}"
        assert (
            verdict.stale_exemptions == []
        ), f"the known-limits ledger is stale: {verdict.stale_exemptions}"
        assert verdict.ok(), "the gate rejects the shipped reference output"

    def test_the_ledger_is_either_empty_or_explained_in_full(self):
        """检查限制账本为空或每项均有完整说明，不要求为测试保留虚假限制。

        历史 v7.0.2 的唯一 KTC f_max 项来自节点口径错误，不能将它当成电路极限。
        账本本身仍由合成反例检查，以防失败被无理由豁免。
        """
        for name, reason in KNOWN_LIMITS.items():
            assert len(reason.strip()) > 40, f"exemption {name!r} has no real justification"

    def test_an_exemption_moves_a_failure_out_of_failures_and_is_reported(self):
        """用合成输入检查豁免的移动与报告，不要求生产数据必须存在真实限制。"""
        results = {"pipeline": {"PASS": False}}
        assert "pipeline.PASS" in gate(results).failures, "precondition: it fails"

        registry = {"pipeline.PASS": "Synthetic entry: long enough to clear the reason floor."}
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("adi_model.acceptance.KNOWN_LIMITS", registry)
            verdict = gate(results)
        assert verdict.failures == [], "an exempted failure still counted as a failure"
        assert verdict.exempted == ["pipeline.PASS"], "the exemption was not surfaced"
        assert (
            verdict.missing_required
        ), "precondition: the synthetic input omits every required record"
        assert not verdict.ok(), (
            "ok() must still be false — it is the conjunction of no unregistered "
            "failure, no stale exemption, and no missing required record; the "
            "absent required records are not excused by an unrelated exemption"
        )

    def test_a_stale_exemption_is_an_error_not_a_silent_no_op(self):
        """检查不再失败的豁免项被报告为过期错误。"""
        registry = {"pipeline.PASS": "Synthetic entry: long enough to clear the reason floor."}
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("adi_model.acceptance.KNOWN_LIMITS", registry)
            verdict = gate({"pipeline": {"PASS": True}})
        assert verdict.stale_exemptions == ["pipeline.PASS"]
        assert not verdict.ok(), "a stale exemption was accepted"

    def test_an_exemption_without_a_reason_is_refused(self):
        """检查空理由的豁免被拒绝。"""
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("adi_model.acceptance.KNOWN_LIMITS", {"pipeline.PASS": "   "})
            with pytest.raises(ValueError, match="no reason"):
                gate({"pipeline": {"PASS": False}})

    def test_coverage_floor_is_met_without_being_vacuous(self):
        """检查结果数量下限能发现整类记录缺失。"""
        actual = len(acceptance_records(self._reference_results()))
        assert actual == MIN_RECORDS, (
            f"MIN_RECORDS={MIN_RECORDS} but the reference output has {actual} records; "
            "update the floor deliberately, in the same commit that changes the records"
        )


# ===========================================================================
# R12：在约束对应节点计算旧 KTC 带宽界。
# ===========================================================================
class TestR12KtcMaxUsesTheObservedNode:
    """K1：保留 KTC 观察节点与后级量化窗口的历史边界比较。

    v7.0.2 曾用 f_max=(adc2_v_max-G*Delta1)/(2*pi*v_fs*G0*Delta_t)，
    在旧配置得到 2.5465 MHz，并与 5 MHz 要求比较。ADR 0006 的校正作用于数字域
    quantize(v_ra)-kappa*v_N，校正项不占 ADC2 窗口；旧观察通路约束应使用其
    ra_v_clip 与 ktc_gain_n，得到 (ra_v_clip/ktc_gain_n)/(2*pi*v_fs*Delta_t)
    为 134.45 MHz。本组保留同一配置下两个节点口径的算术对照，不将旧观察模型
    作为当前必备架构。
    """

    NAME = "KTC 观测通路摆幅上限 f_max (满幅)"

    def test_the_bound_is_the_observation_path_swing_over_the_observed_step(self):
        cfg = Config(ktc_enable=True)
        checks = cfg.validate(verbose=False)
        assert self.NAME in checks, f"the bound is missing from validate(): {sorted(checks)}"
        measured, limit, passed, unit = checks[self.NAME]
        expected = (cfg.ra_v_clip / cfg.ktc_gain_n) / (2 * math.pi * cfg.v_fs * cfg.ktc_dt())
        assert measured == pytest.approx(expected, rel=1e-12), (
            "the bound is no longer computed from the observation-path swing — "
            f"measured {measured!r}, observation-node expression {expected!r}"
        )
        assert unit == "Hz"
        assert passed and measured >= limit, (
            f"the observation-node bound fails at {measured / 1e6:.4f} MHz "
            f"(limit {limit / 1e6:.1f} MHz) — if this is intended, it belongs in "
            "acceptance.KNOWN_LIMITS with a reason and an exit condition"
        )

    def test_the_second_stage_node_would_fail_and_is_not_the_criterion(self):
        """保留错误后级节点的反例，区分约束数值通过与观察节点选择正确。"""
        cfg = Config(ktc_enable=True)
        g = cfg.g_actual
        adc2_margin = min(cfg.adc2_v_max - g * cfg.delta1, -cfg.adc2_v_min)
        wrong_node = adc2_margin / (2 * math.pi * cfg.v_fs * cfg.g0 * cfg.ktc_dt())
        correct_node = (cfg.ra_v_clip / cfg.ktc_gain_n) / (2 * math.pi * cfg.v_fs * cfg.ktc_dt())
        assert wrong_node < 5e6, (
            f"the counterfactual no longer fails ({wrong_node / 1e6:.4f} MHz) — the "
            "parameters moved, so the two nodes are no longer distinguishable and "
            "this test can no longer tell them apart"
        )
        assert (
            correct_node > 10 * wrong_node
        ), "the two nodes are too close to be told apart; the pin is vacuous"
        assert self.NAME in cfg.validate(verbose=False)


# ===========================================================================
# R15：拒绝需要猜测真值的非布尔结果。
# ===========================================================================
class TestR15GateRefusesNonBooleanPass:
    """K4：检查布尔结果类型，防止 bool("False") 被读作 True。

    旧 bool(raw) 强制转换会丢失类型，故检查字符串、整数、浮点及 None 均被拒绝。
    v7.0.4 允许 numpy.bool_，因为内存结果可直接来自 NumPy 比较；原 v7.0.3 的
    s10_summary.C_F→增益(电荷一致) 曾以 np.True_ 触发该问题。允许该类型仍须同时
    保留真、假两种值，不能重新引入一般真值转换。
    """

    def test_a_stringified_false_is_refused_not_read_as_true(self):
        with pytest.raises(ValueError, match="not a bool"):
            acceptance_records({"validate": {"x": {"PASS": "False"}}})

    def test_every_non_boolean_type_is_refused(self):
        """检查非布尔类型被拒绝，特别覆盖易被 bool(0)／bool(1) 转换的整数。"""
        for raw in ("True", "False", 0, 1, 0.0, 1.0, None, [], {}):
            with pytest.raises(ValueError, match="not a bool"):
                acceptance_records({"pipeline": {"PASS": raw}})

    def test_a_real_boolean_is_returned_unchanged(self):
        """正对照：实际布尔值保持原值。"""
        assert acceptance_records({"pipeline": {"PASS": False}}) == {"pipeline.PASS": False}
        assert acceptance_records({"pipeline": {"PASS": True}}) == {"pipeline.PASS": True}

    def test_a_numpy_boolean_is_a_verdict_not_an_impostor(self):
        """检查 np.bool_ 被保值归一化，False 不会转成 True。"""
        np_bool = type(np.True_)
        assert acceptance_records({"pipeline": {"PASS": np.True_}}) == {"pipeline.PASS": True}
        assert acceptance_records({"pipeline": {"PASS": np_bool(False)}}) == {
            "pipeline.PASS": False
        }
        # NumPy 浮点仍被拒绝；一般真值不能代替布尔结果。
        with pytest.raises(ValueError, match="not a bool"):
            acceptance_records({"pipeline": {"PASS": np.float64(1.0)}})

    def test_the_required_set_is_enforced_by_id_not_by_count(self):
        """检查必需记录以标识符约束，不能删一条再补无关记录满足数量下限。

        MIN_RECORDS 约束总数；REQUIRED_RECORDS 约束具体结论对应的记录。
        """
        verdict = gate({"pipeline": {"PASS": True}})
        assert verdict.missing_required, "the required set is not enforced at all"
        assert not verdict.ok(), "a result missing a required record was accepted"
        assert REQUIRED_RECORDS - set(acceptance_records({"pipeline": {"PASS": True}})) == set(
            verdict.missing_required
        )


# ===========================================================================
# R16–R18：第五次外部审阅（v7.0.4）的排列空间、时间常数拓扑
# 及包含观察噪声的最优校正系数。
# ===========================================================================
class TestR16DemPermutationSpace:
    """§4.2：检查旧 512 个数字标签只形成 64 个联合物理旋转。

    旧主阵列偏移为 (sid*331) mod 64，子阵列为 (sid*173) mod 8；两个乘子分别
    与各自模数互素，但共用同一 sid，使联合周期为 lcm(64,8)=64，而非乘积 512。
    原无随机种子枚举记录为 64 种联合排列，每个主旋转只配一个子旋转。

    此历史计数用于防止误称三维 DEM。论文中的 8/18 选择、横向／纵向重排及
    二进制到一元桥接不能由该耦合旋转的标签数量推出。
    """

    def test_the_joint_permutation_space_is_64_not_512(self):
        from adi_model.dac_arch import _A_MAIN, _A_SUB
        from adi_model.mapper import N_DEM_STATES

        cfg = _lean_cfg()
        n_m, n_s = cfg.dac_n_main, cfg.dac_n_sub
        assert N_DEM_STATES == 512, "mapper state count changed — re-derive this pin"
        assert (n_m, n_s) == (64, 8), "array sizes changed — re-derive this pin"
        joint = {((s * _A_MAIN) % n_m, (s * _A_SUB) % n_s) for s in range(N_DEM_STATES)}
        assert len(joint) == 64, (
            f"expected the 512 sid labels to collapse to 64 distinct joint "
            f"rotations (lcm(64,8)), found {len(joint)} — if this is now larger, "
            "the permutation driver changed and the DEM scope statements in "
            "docs/model_scope.md must be revisited"
        )

    def test_each_main_rotation_is_paired_with_exactly_one_sub_rotation(self):
        from adi_model.dac_arch import _A_MAIN, _A_SUB

        cfg = _lean_cfg()
        n_m, n_s = cfg.dac_n_main, cfg.dac_n_sub
        pairs: dict[int, set[int]] = {}
        for s in range(512):
            pairs.setdefault((s * _A_MAIN) % n_m, set()).add((s * _A_SUB) % n_s)
        assert all(len(v) == 1 for v in pairs.values()), (
            "the two rotations decoupled — the selection of the main rotation no "
            "longer determines the sub rotation; update the scope statements"
        )
        assert len({next(iter(v)) for v in pairs.values()}) == n_s, (
            "each sub rotation should appear exactly 64/8 = 8 times across the " "64 main rotations"
        )

    def test_the_dac_realises_the_same_space_through_its_public_interface(self):
        """通过旧 SplitDAC 公共接口检查排列空间，并显式开启该夹具默认关闭的 DEM。"""
        cfg = Config(dem_enable=True, dac_arch="split")
        dac = SplitDAC(cfg, build_split_chip(cfg))
        orders = {tuple(dac.full_order(sid).tolist()) for sid in range(512)}
        assert len(orders) == 64, (
            f"SplitDAC.full_order yields {len(orders)} distinct physical orders "
            "over the 512 states — expected 64"
        )


class TestR17CommonModeTau:
    """§5.2：检查共享源阻抗使总时间常数不能统一按切片数缩小。

    星形网络由 R_s 驱动 N 个 R_on、C_slice 支路，共模时间常数为
    R_s*C_total+R_on*C_slice；只有支路项随分片缩小，共享源阻抗项不缩小。
    旧审阅夹具 N=8、C_total=20.5 pF、R_s=30 Ω、R_on=20 Ω，得到总模型 1.025 ns、
    共模 0.666 ns（仅缩小 1.54 倍），全按切片缩小则为 0.128 ns。
    """

    def test_the_star_network_common_mode_time_constant(self):
        n, rs, ron = 8, 30.0, 20.0
        c_total = 20.5e-12
        c_slice = c_total / n
        tau_aggregate = (rs + ron) * c_total
        tau_common = (n * rs + ron) * c_slice  # = rs·c_total + ron·c_slice
        tau_all_per_slice = (rs + ron) * c_slice
        assert tau_aggregate == pytest.approx(1.025e-9)
        assert tau_common == pytest.approx(0.666e-9, rel=1e-3)
        assert tau_all_per_slice == pytest.approx(0.128e-9, rel=1e-3)
        # 对照原“约 8 倍”说法遗漏的共享源阻抗影响。
        assert tau_aggregate / tau_common < 2.0, (
            "with these source/switch numbers the aggregate bound is within 2× "
            "of the star-network common mode, not 8× — the conservative claim "
            "is qualified, as pipeline.py's header now states"
        )
        # 核对上述限定所依据的代数恒等式。
        assert (n * rs + ron) * c_slice == pytest.approx(rs * c_total + ron * c_slice)


class TestR18KappaOptimalWithObserverNoise:
    """§11：检查最小总残差同时计入观察噪声。

    只最小化采样噪声时系数为 aᵀΣb/(bᵀΣb)；报告总残差还含 kappa²*sigma_eN²，
    因此联合最优为 aᵀΣb/(bᵀΣb+sigma_eN²)。本组保留旧 noise_phase 接口的正反向
    算术检查，sigma_eN=0 对应其历史结果。
    """

    def _state(self) -> dict:
        rng = np.random.default_rng(5)
        a = rng.normal(0.0, 1.0, 6)
        b = rng.normal(0.0, 1.0, 6)
        return {"Sigma_diag": rng.uniform(0.5, 2.0, 6) ** 2, "a": a, "b": b}

    def test_with_observer_noise_it_minimises_the_total_residual(self):
        st = self._state()
        for sigma_eN in (0.0, 0.3, 1.0, 3.0):
            k = kappa_optimal(st, sigma_eN)
            # 最优性：κ* 处的总残差不得大于邻近系数处的总残差。
            grid = np.linspace(0.0, 2.0, 2001)
            totals = np.array([sigma_res_analytic(st, float(x), sigma_eN) for x in grid])
            at_k = sigma_res_analytic(st, k, sigma_eN)
            assert at_k <= totals.min() + 1e-12, (
                f"κ* = {k:.4f} does not minimise the total residual for "
                f"σ_eN = {sigma_eN} — the closed form and sigma_res_analytic "
                "disagree"
            )

    def test_observer_noise_shrinks_the_coefficient_toward_zero(self):
        st = self._state()
        k0 = kappa_optimal(st, 0.0)
        assert 0.0 < kappa_optimal(st, 1.0) < k0 < 1.0, (
            "adding observer noise must pull the optimal coefficient toward 0 "
            "— deepest cancellation is not minimum total noise"
        )

    def test_default_argument_preserves_the_historical_behaviour(self):
        st = self._state()
        assert kappa_optimal(st) == pytest.approx(kappa_optimal(st, 0.0))
