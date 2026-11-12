"""显式物理数据与数字binding驱动的唯一pipeline实现。

每笔用真实采集组保存主/sub自由电荷，随后同一笔转换组消费它；跨周期组与slot重排都按SlicePlan核对。
残差输入等效r=−(Qfree_eff+QDAC)/Cnorm；Cnorm是全Signal连接的归一化电容，
实际Signal连接系数另由采集mask给出。理想RA输出为(Cnorm/Cf)*(r+显式RA输入误差)。
浮置top的参考响应另乘−Ceff/Cnorm；有限A0的噪声增益为1+Ceff/Cf，不能由Cnorm/Cf代替。
制造值仅用于plant、资格检查和事后评分，不进入数字学习feature。
"""

from __future__ import annotations

import numpy as np

from adi_model.behavioral.adc2 import ADC2
from adi_model.behavioral.ra import ResidueAmplifier
from adi_model.behavioral.sadc import build_first_stage_quantizer
from adi_model.behavioral.sampler import capture, finite_record
from adi_model.behavioral.sampling_charge import digital_connection_terms
from adi_model.calibration.reconstruction import DigitalState, initialize_state, reconstruct
from adi_model.calibration.weight_calibration import (
    CalibrationSpec,
    DigitalObservation,
    apply_frozen_calibration,
)
from adi_model.control.dem import (
    REF_HIGH,
    REF_LOW,
    SIGNAL,
    SamplingConnections,
    SplitSwitchCommand,
    _integer_array,
)
from adi_model.control.dither import Fig19DitherProgram, complete_flash3_sar6_prefix_table
from adi_model.control.dither_port import DitherPort
from adi_model.simulation.config import Config, ConfigError
from adi_model.simulation.sim import SimResult
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.dac_arch import NominalCodebook
from adi_model.topology.mapper import MappedCodes, Mapper


def _reference_record(value, n: int, name: str):
    return None if value is None else finite_record(value, (n,), name)


def _reference_at(value, index: int):
    return None if value is None else float(value[index])


def _binding_name(value) -> str:
    if value is None:
        return "UNBOUND"
    return f"{getattr(value, '__module__', type(value).__module__)}.{getattr(value, '__qualname__', type(value).__qualname__)}"


def _same_book(left: NominalCodebook, right: NominalCodebook) -> bool:
    return (
        left.code_min == right.code_min
        and left.code_max == right.code_max
        and np.array_equal(left.voltage_v, right.voltage_v)
    )


def _validate_request(
    cfg,
    pool,
    mapper,
    n_samples,
    *,
    dither_program,
    dither_port,
    static_charge_domain,
) -> int:
    """Reject an unusable request before any state is touched.

    This is the whole validation prologue that used to sit inline at the top of
    `execute_pipeline`. It is pure: it reads its arguments, computes nothing that
    is consumed later except the normalised sample count, and either raises
    `ConfigError` or returns.

    Keeping it separate matters for two reasons. First, the error messages are the
    contract a caller actually sees, so they deserve to be readable on their own.
    Second, `execute_pipeline` had 691 lines mostly because 98 of them were these
    guards; moving them out is a pure structural change and is proven by the
    behaviour fingerprint (`tools/behavior_fingerprint.py --check`).

    Parameters
    ----------
    cfg
        Explicit run contract. `cfg.check_legal` is applied to the pool geometry.
    pool, mapper
        Must be an explicitly bound `PhysicalSlicePool` and a 512-row `Mapper`.
    n_samples
        Requested raw count; normalised to `int` here.
    dither_program, dither_port
        Mutually exclusive dither topologies (code-shift v1 / charge-port v2).
    static_charge_domain
        Whether the caller requests static-training qualification. `True` does not
        bypass the engine-side physical checks that follow.

    Returns
    -------
    int
        `n_samples` as a plain int.

    Raises
    ------
    ConfigError
        Any binding, topology or feature combination the engine does not support.
        Every message states which binding is wrong; none is a silent fallback.
    """
    if not isinstance(pool, PhysicalSlicePool) or not isinstance(mapper, Mapper):
        raise ConfigError("run_pipeline requires an explicit PhysicalSlicePool and Mapper")
    cfg.check_legal(geometry=pool.geometry)
    if dither_program is not None and dither_port is not None:
        raise ConfigError(
            "dither_program (code-shift topology) and dither_port (independent "
            "charge-port topology) are mutually exclusive; pick one and state it"
        )
    dither_active = dither_program is not None or dither_port is not None
    if bool(cfg.dither_enable) != dither_active:
        raise ConfigError(
            "dither_enable requires an explicit Fig19 program or charge port, and vice versa"
        )
    if dither_program is not None and not isinstance(dither_program, Fig19DitherProgram):
        raise ConfigError("dither_program must be Fig19DitherProgram")
    if dither_port is not None and not isinstance(dither_port, DitherPort):
        raise ConfigError("dither_port must be DitherPort")
    if isinstance(n_samples, bool) or not isinstance(n_samples, int | np.integer) or n_samples < 1:
        raise ConfigError("n_samples must be a positive integer")
    n_samples = int(n_samples)
    if type(static_charge_domain) is not bool:
        raise ConfigError("static_charge_domain must be bool and is independently qualified")
    for name in ("nominal_codebook", "mask_binding", "acquisition_binding", "schedule_binding"):
        if getattr(pool, name, None) is None:
            raise ConfigError(f"physical pipeline requires explicitly bound pool.{name}")
    assert pool.nominal_codebook is not None
    if not _same_book(pool.nominal_codebook, mapper.nominal_codebook):
        raise ConfigError("pool and coarse Mapper nominal codebooks conflict")
    if len(mapper.rdac_code_by_coarse) != 512:
        raise ConfigError("source-target Mapper needs 512 explicitly supplied coarse rows")
    if dither_program is not None:
        if dither_program.words.shape != (n_samples,) or dither_program.spec.v_fs != cfg.v_fs:
            raise ConfigError("Fig19 program raw length/Vfs conflicts with the actual record")
        if (
            cfg.dem_enable
            or cfg.dyn_input_settling
            or cfg.dyn_ref_settling
            or cfg.dyn_crosstalk
            or cfg.conversion is not None
            or cfg.enable_sampling_noise
            or (cfg.ra_enable_noise and cfg.ra_out_noise_rms != 0)
            or cfg.driver_noise_rms not in (None, 0)
            or cfg.sampling_tau_mismatch not in (None, 0)
        ):
            raise ConfigError(
                "Fig19 static v1 requires explicit zero-noise, no dynamics/response approximation "
                "and no DEM combination; requested features are not silently disabled"
            )
        # v1 的"等距码本/恒等译码"守卫。
        # 2026-11-13：原式把码本钉成 -v_fs + k*LSB（v(511)=2.988），而 Fig.11 的实测码本
        # 端点闭合到 ±v_fs（v(511)=+3.000），两者在最后一位差 1 LSB、在中间处处只差
        # 恒定的斜率差。论文 [00] 明确 "±3V differential input range"，端点必须到 +3V，
        # 因此判据从"等于等距参考"放宽为"步长均匀且端点闭合"，并保留原来的恒等译码与
        # 前缀表要求（那两条才是 v1 真正依赖的东西）。
        book = np.asarray(mapper.nominal_codebook.voltage_v, dtype=float)
        book_steps = np.diff(book)
        expected_book = -cfg.v_fs + np.arange(512) * dither_program.spec.coarse_step_v
        book_is_uniform = bool(
            book.size == 512
            and np.all(book_steps > 0)
            and np.ptp(book_steps) <= 32 * np.finfo(float).eps * cfg.v_fs
        )
        book_matches_reference = np.allclose(
            book,
            expected_book,
            rtol=0,
            atol=32 * np.finfo(float).eps * cfg.v_fs,
        )
        book_endpoints_close = bool(
            abs(book[0] + cfg.v_fs) <= 32 * np.finfo(float).eps * cfg.v_fs
            and abs(book[-1] - cfg.v_fs) <= 32 * np.finfo(float).eps * cfg.v_fs
        )
        if (
            mapper.nominal_codebook.code_min != 0
            or mapper.nominal_codebook.code_max != 511
            or not np.array_equal(mapper.rdac_code_by_coarse, np.arange(512))
            or not (book_matches_reference or (book_is_uniform and book_endpoints_close))
            or mapper.event_codes_by_coarse is None
            or not np.array_equal(mapper.event_codes_by_coarse, complete_flash3_sar6_prefix_table())
        ):
            raise ConfigError(
                "Fig19 v1 needs the explicit uniform512 codebook/identity decoder and complete "
                "Flash3/SAR6 seven-event table; no prefix or codebook fallback"
            )
    if dither_port is not None:
        # 独立电荷口 v2：RDAC 码域完全不参与，因此**不要求**均匀码本；
        # 但 must 有本 run 的逐笔字，且不得与参考/RA/ADC2 动态路径混用。
        port_program = dither_port.require_words()
        if port_program.words.shape != (n_samples,) or dither_port.spec.v_fs != cfg.v_fs:
            raise ConfigError("dither port raw length/Vfs conflicts with the actual record")
        if (
            cfg.dyn_input_settling
            or cfg.dyn_ref_settling
            or cfg.dyn_crosstalk
            or cfg.conversion is not None
        ):
            raise ConfigError(
                "dither charge-port v2 is a static-connection model; dynamic reference/RA/ADC2 "
                "response and continuous acquisition are outside it"
            )
        # 电荷口不偏移 RDAC 码，因此不需要逐事件前缀表；这里只要求
        # 码本本身可求值（由 pool/mapper 一致性检查覆盖）。
    if cfg.dyn_input_settling:
        raise ConfigError(
            "continuous acquisition requires a complete SADC/RDAC phase and initial-node binding; "
            "this static connection runner does not supply it or shorten the full-Ts RDAC window"
        )
    if cfg.dyn_crosstalk:
        raise ConfigError(
            "crosstalk needs an explicit edge/coupling circuit binding; legacy code-distance proxy removed"
        )
    if cfg.input_network is not None and (
        cfg.input_network.pretrack_mode != "off"
        or cfg.input_network.auxiliary_cap_f not in (None, 0)
    ):
        raise ConfigError("pretracking/AUX requires a complete active acquisition-state binding")
    return int(n_samples)


def _prepare_conversion(
    *,
    cfg,
    pool,
    n_samples: int,
    dither_program,
    dither_port,
    dem_states,
    sample_ids,
    sampled_top_v,
    sampled_subnode_v,
    ra_input_error_v,
    rng,
):
    """校验 DEM/ID/采样记录与 dither 拓扑，返回后续转换需要的派生量。

    这是从 `execute_pipeline`（683 行）抽出的**纯校验 + 派生**段：
    只读参数、只抛 `ConfigError`，**不写 pool/rng/state**（抽取前已逐行核对，
    确认该段无任何副作用）。

    Args:
        cfg/pool/plan/n_samples: 运行上下文与已规划的片池。
        dither_program/dither_port: 两种 dither 拓扑，至多一个非 None。
        dem_states/sample_ids: 本批的实际 DEM 状态与采样序号。
        sampled_top_v/sampled_subnode_v: 显式采样节点电压，不默填 0。
        ra_input_error_v: 可选 RA 输入等效误差。
        rng: 可选 Generator；`None` 且需要随机性时本函数会创建。

    Returns:
        (dither_active, acquisition_ids, sid, ids, top, subnode, injected,
         feedback, coefficients, c_effective, random_active, rng)

        `rng` 必须用返回的那个：函数内部可能在 `rng is None and random_active`
        时新建，调用方的原局部变量会过期。

    Raises:
        ConfigError: 任一校验不通过；文案与抽取前逐字相同。

    Side effects: 无（不碰 pool/state）。仅在需要时创建新的随机数 Generator。
    """
    dither_active = dither_program is not None or dither_port is not None
    plan = pool.plan(n_samples)
    acquisition_ids = np.vstack((plan.conv[0:1], plan.acq[:-1]))
    if dither_program is not None:
        used = np.unique(acquisition_ids)
        if any(
            getattr(pool, name) is not None and np.any(getattr(pool, name)[used] != 0)
            for name in ("t_skew", "v_os")
        ):
            raise ConfigError("Fig19 static v1 has no active-slice offset/skew phase binding")
    sid = _integer_array(dem_states, "actual DEM states", ndim=1)
    if sid.shape != (n_samples,) or np.any(sid < 0):
        raise ConfigError("DEM states must explicitly identify every raw with nonnegative integers")
    if not cfg.dem_enable and np.any(sid != sid[0]):
        raise ConfigError("DEM disabled requires a held actual state, not a varying sequence")
    ids = _integer_array(sample_ids, "sample IDs", ndim=1)
    if ids.shape != (n_samples,):
        raise ConfigError("sample IDs must explicitly identify all raw records")
    if len(ids) > 1 and np.any((np.diff(ids) & 0xFFFF_FFFF) != 1):
        raise ConfigError(
            "this waveform producer requires continuous raw IDs; do not hide missing periods"
        )
    top = finite_record(sampled_top_v, (n_samples, cfg.n_active), "sampled top node [V]")
    subnode = finite_record(sampled_subnode_v, top.shape, "sampled sub node [V]")
    injected = (
        None
        if ra_input_error_v is None
        else finite_record(ra_input_error_v, (n_samples,), "RA input error [V]", scalar=False)
    )
    if dither_active and (
        np.any(top != 0) or (injected is not None and np.any(injected != 0))
    ):
        raise ConfigError(
            "Fig19 dither requires explicit zero top reset and no additional RA disturbance"
        )
    feedback = pool.chip.require_feedback()
    pool.chip.require_main_parasitic()
    coefficients = pool.split_coefficients(plan.conv)
    c_effective = coefficients["c_eff"]
    if not isinstance(c_effective, np.ndarray):
        raise ConfigError("actual main parasitic capacitance is required for sampled-node charge")
    random_active = (
        cfg.ra_enable_noise or cfg.enable_sampling_noise or cfg.driver_noise_rms is not None
    )
    if rng is not None and not isinstance(rng, np.random.Generator):
        raise ConfigError("rng must be a Generator")
    if rng is None and random_active:
        rng = np.random.default_rng(cfg.seed)
    return (
        plan,
        dither_active,
        acquisition_ids,
        sid,
        ids,
        top,
        subnode,
        injected,
        feedback,
        coefficients,
        c_effective,
        random_active,
        rng,
    )



def execute_pipeline(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    pool: PhysicalSlicePool,
    mapper: Mapper,
    sample_ids,
    dem_states,
    sampled_top_v,
    sampled_subnode_v,
    reference_high_v,
    reference_low_v,
    nominal_reference_high_v,
    nominal_reference_low_v,
    static_charge_domain: bool,
    state: DigitalState | None = None,
    rng=None,
    ra_input_error_v=None,
    rdac_noise_cap_f=None,
    sampling_control=None,
    initial_reference_error_v=None,
    initial_ra_output_v=None,
    initial_adc2_input_v=None,
    dither_program: Fig19DitherProgram | None = None,
    dither_port: DitherPort | None = None,
) -> SimResult:
    """执行一段显式priming的记录，不修改Config或制造电容。

    Args:
        cfg: 冻结schema的显式运行合同，不执行profile/架构重写。
        input_fn: 单孔径输入[V]，时间[s]；每记录时间原点为0。
        n_samples: 正整数raw数，所有无效/饱和事务均保留。
        pool: 显式chip、nominal_codebook与实际mask/acquisition/schedule binding。
        mapper: 512粗码到RDAC整数码的明确表；参考动态另需明确逐事件表。
        sample_ids: (N,)实际连续uint32身份，保留回绕，不从ID制造启动相位。
        dem_states: (N,)实际整数状态；DEM关闭时必须保持同一状态。
        sampled_top_v: 显式标量或(N,A)采样主节点V，列按本笔实际acquisition组顺序；
            首笔外部预采集使用conv[0]顺序。
        sampled_subnode_v: 同顺序的显式采样sub节点V，不默填零。
        reference_high_v: 实际全局高参考驱动基准V，标量或(N,)，未连接轨可None；
            动态轨误差另由Engine状态叠加，不将基准称为已包含droop的采集节点。
        reference_low_v: 实际全局低参考驱动基准V，标量或(N,)，不从v_fs推导。
        nominal_reference_high_v: 独立数字名义高轨V，标量或(N,)。
        nominal_reference_low_v: 独立数字名义低轨V，只有名义轨进入学习term。
        static_charge_domain: 请求静态学习资格；引擎还须检查物理/动态条件，
            True不是绕过资格检查的声明。
        state: 可选已有数字状态/冻结权重；None仅用来源名义g0作float诊断。
        rng: 可选显式噪声Generator；活跃噪声无此对象时使用明确cfg.seed。
        ra_input_error_v: 可选(N,)RA输入等效[V]，不注入数字学习项。
        rdac_noise_cap_f: 采样噪声启用时必填(N,)显式噪声等效电容[F]。
        sampling_control: 原样传给数字采集binding的明确控制数据，无物理truth。
        initial_reference_error_v: 活跃动态参考所需显式初始两轨误差V。
        initial_ra_output_v: 活跃动态RA所需显式初始输出V。
        initial_adc2_input_v: 活跃动态ADC2所需显式初始输入V，不默填0。
        dither_program: 同一raw的Fig19静态等效Q32控制。SADC采x-L、RDAC存x-T，
            各resolved命令减U；数字公共项为-T，不再额外纠正U。

    Returns:
        SimResult；未装载冻结权重时out只为名义标量诊断，整数输出另调用to_codes。

    Raises:
        ConfigError: 缺绑定、控制冲突、未支持电路域或缺活跃物理参数。

    Side effects: 重置并消费pool采集资格；消费启用噪声RNG，不学习系数。
    """
    # 全部绑定/拓扑校验在 _validate_request 内完成；这里只取回规范化后的笔数。
    n_samples = _validate_request(
        cfg,
        pool,
        mapper,
        n_samples,
        dither_program=dither_program,
        dither_port=dither_port,
        static_charge_domain=static_charge_domain,
    )
    (
        plan,
        dither_active,
        acquisition_ids,
        sid,
        ids,
        top,
        subnode,
        injected,
        feedback,
        coefficients,
        c_effective,
        random_active,
        rng,
    ) = _prepare_conversion(
        cfg=cfg,
        pool=pool,
        n_samples=n_samples,
        dither_program=dither_program,
        dither_port=dither_port,
        dem_states=dem_states,
        sample_ids=sample_ids,
        sampled_top_v=sampled_top_v,
        sampled_subnode_v=sampled_subnode_v,
        ra_input_error_v=ra_input_error_v,
        rng=rng,
    )
    # 两种 dither 拓扑在这里分叉：
    #   v1  code-shift：SADC 看 x-L、RDAC 采 x-T、码 k=c-U；
    #   v2  charge-port：SADC 只看 x-L，RDAC 仍采真实 x，upper 段改由
    #       残差节点的独立电荷口注入（下面 q_residue 处），码保持 c。
    sample = capture(
        cfg,
        input_fn,
        n_samples,
        sample_ids=ids,
        rng=rng,
        rdac_noise_cap_f=rdac_noise_cap_f,
        dither_program=dither_program,
        # 采样节点只平移 **lower 段**（含 0..1 的小数 + 0/2 的整步）。
        # 2026-11-13：原实现传 total_v（= L + U），把 upper 段也算进采样节点，
        # 于是 dither 摆幅达 8 LSB9，而 RA 输入端只允许 clip/G ≈ 1.38 LSB9，
        # 残差被顶进限幅（ra_sat 大面积 True）。
        # 论文口径（[00]）："dither range is enhanced by 2b when the result is transferred
        # from the quantizer to the RDAC" —— 量化器侧只承担小幅度，RDAC 侧才放大；
        # 因此 U 段只出现在 RDAC 节点与数字公共项，不再进采样节点。
        dither_sample_v=(
            None if dither_port is None else dither_port.require_words().lower_v
        ),
    )
    sadc = build_first_stage_quantizer(cfg)
    backend = ADC2(cfg)
    # 2026-11-13 修正：**不要**加窗口中心偏置。
    # 判码规则是 thr[k] = vd0(k)，因此 x ∈ [vd0(k), vd0(k+1)) ⇒ 残差 = x - vd0(k)
    # 落在**单边** [0, 1 LSB9)，而不是双极 ±0.5 LSB9。
    # 早前误以为残差是双极、于是把"残差 0"摆到窗口中点 0.1875，
    # 结果 ADC2 输入变成 [0.1875, 0.5625]，超出上界 0.4125 —— 这就是
    # 定点路径饱和、A0 出现 -5.79% 增益误差与 -0.172V 偏移的直接原因。
    # 正确映射：adc2_input = G*residue，范围 [0, 0.375] ⊂ [-0.0375, 0.4125] ✓
    adc2_centre = 0.0
    ra = ResidueAmplifier(cfg)
    state = initialize_state(cfg) if state is None else state
    if not isinstance(state, DigitalState):
        raise ConfigError(
            "state must be DigitalState; physical truth is not a digital coefficient source"
        )
    coarse, sadc_over = sadc.convert_codes(sample.x_sadc)
    mapped = mapper.encode(coarse)
    dither_trace = None
    if dither_program is not None:
        assert mapped.event_codes is not None
        dither_trace = dither_program.commands(
            coarse,
            mapped.event_codes,
            code_min=mapper.nominal_codebook.code_min,
            code_max=mapper.nominal_codebook.code_max,
        )
        mapped = MappedCodes(dither_trace.rdac_code, dither_trace.rdac_event_codes)
    assert pool.acquisition_binding is not None
    sampled = pool.acquisition_binding(
        pool.geometry, ids.copy(), acquisition_ids.copy(), sampling_control
    )
    if not isinstance(sampled, SamplingConnections):
        raise ConfigError("acquisition binding must return actual SamplingConnections")
    sampled = sampled.validated(pool.geometry, n_samples)
    if dither_program is not None and (
        np.any(sampled.main != SIGNAL) or np.any(sampled.sub != SIGNAL)
    ):
        raise ConfigError(
            "Fig19 static v1 common-voltage equivalence requires actual allSIGNAL acquisition"
        )
    if cfg.dyn_ref_settling and (np.any(sampled.main != SIGNAL) or np.any(sampled.sub != SIGNAL)):
        raise ConfigError(
            "reference-connected acquisition requires a shared sampling/reference event binding; dynamic reference currently requires allSIGNAL acquisition"
        )
    # ==== 224 减法器：RDAC 用码 k = c - U（原件 FIG.19 / [10] Sheet 20）====
    # SADC 判决给出 c；U=D6,D4 是"提前量"，从 c 里减掉才是 RDAC 实际要选的码。
    # 数字重构仍用 c（vd0 取 c 的名义电压），U 的一部分由 300/222 两条模拟路径抵消，
    # 差值由重构式的 dither 扣回项补足。缺这一步时 dither 的 U 只改了模拟侧、
    # 码侧不动，输出就会随 U 漂移（实测 64 字 p2p 达 74 mV）。
    if dither_program is not None:
        hold_upper = np.asarray(dither_program.upper_code, dtype=np.int64)
    elif dither_port is not None:
        hold_upper = np.asarray(dither_port.require_words().upper_code, dtype=np.int64)
    else:
        hold_upper = np.zeros(n_samples, dtype=np.int64)
    rdac_codes = np.asarray(mapped.codes, dtype=np.int64) - hold_upper
    if np.any((rdac_codes < 0) | (rdac_codes > 511)):
        raise ConfigError(
            "dither upper segment would push the RDAC code outside the 9-bit domain; "
            "the patent keeps k=c-U legal by limiting U to early coarse codes"
        )
    command = pool.select(rdac_codes, sid, slice_ids=plan.conv)
    high_used = (
        np.any(sampled.main == REF_HIGH, axis=(1, 2))
        | np.any(sampled.sub == REF_HIGH, axis=(1, 2))
        | np.any(command.main_on, axis=(1, 2))
        | np.any(command.sub_on, axis=(1, 2))
    )
    low_used = (
        np.any(sampled.main == REF_LOW, axis=(1, 2))
        | np.any(sampled.sub == REF_LOW, axis=(1, 2))
        | np.any(~command.main_on, axis=(1, 2))
        | np.any(~command.sub_on, axis=(1, 2))
    )
    # A reference-event plant consumes its explicit two-rail driver contract;
    # static endpoint arithmetic only consumes rails actually connected here.
    high_needed = bool(np.any(high_used) or cfg.dyn_ref_settling)
    low_needed = bool(np.any(low_used) or cfg.dyn_ref_settling)
    high = (
        _reference_record(reference_high_v, n_samples, "actual high reference [V]")
        if high_needed
        else None
    )
    low = (
        _reference_record(reference_low_v, n_samples, "actual low reference [V]")
        if low_needed
        else None
    )
    nominal_high = (
        _reference_record(nominal_reference_high_v, n_samples, "nominal high reference [V]")
        if np.any(high_used)
        else None
    )
    nominal_low = (
        _reference_record(nominal_reference_low_v, n_samples, "nominal low reference [V]")
        if np.any(low_used)
        else None
    )
    used_physical_ids = np.unique(acquisition_ids)
    reasons = []
    if not static_charge_domain:
        reasons.append("caller did not request static training qualification")
    for name in ("t_skew", "v_os"):
        value = getattr(pool, name)
        if value is not None and np.any(value[used_physical_ids] != 0):
            reasons.append(f"active physical {name} is outside the static training equation")
    if injected is not None and np.any(injected != 0):
        reasons.append("external RA-input disturbance is not an observed static-training term")
    if np.any(top != 0):
        reasons.append("nonzero sampled top charge includes unmodeled parasitic/reset terms")
    for name, actual, nominal, used in (
        ("high", high, nominal_high, high_used),
        ("low", low, nominal_low, low_used),
    ):
        if (
            actual is not None
            and nominal is not None
            and not np.array_equal(actual[used], nominal[used])
        ):
            reasons.append(f"actual {name} rail differs from the declared digital reference")
    joint = None
    if cfg.dyn_ref_settling or cfg.conversion is not None:
        if cfg.conversion is None:
            raise ConfigError("active conversion requires explicit ConversionParameters")
        active_state_dynamics = cfg.dyn_ref_settling or cfg.conversion.dynamic
        if active_state_dynamics:
            reasons.append(
                "nonideal reference/RA/ADC2 response is outside static unit-weight training"
            )
        if active_state_dynamics and (
            (cfg.ra_enable_noise and cfg.ra_out_noise_rms != 0) or cfg.enable_sampling_noise
        ):
            raise ConfigError(
                "sample-domain white-noise composition with this nonideal conversion is not implemented; no noise transfer is inferred from signal response"
            )
        if cfg.dyn_ref_settling and mapped.event_codes is None:
            raise ConfigError(
                "reference recovery requires explicit per-coarse RDAC event-code records"
            )
        from adi_model.behavioral.conversion import ConversionEngine

        joint = ConversionEngine(
            cfg,
            n_samples,
            initial_reference_error_v=initial_reference_error_v,
            initial_ra_output_v=initial_ra_output_v,
            initial_adc2_input_v=initial_adc2_input_v,
        )
    elif any(
        value is not None
        for value in (initial_reference_error_v, initial_ra_output_v, initial_adc2_input_v)
    ):
        raise ConfigError(
            "dynamic initial states were supplied without an active stateful consumer"
        )
    pool.reset_electrical_state()
    shape = (n_samples, cfg.n_active)
    # 独立电荷口的 upper 段台账 [V]；模拟注入已通过采样节点 T 平移实现，
    # 此处只用于报告"upper 段折合多大"，不重复注入。
    upper_v = (
        np.zeros(n_samples) if dither_port is None else dither_port.upper_v(dither_port.require_words().words)
    )
    recorded_main = np.empty((n_samples, cfg.n_active, pool.geometry.n_main), dtype=np.int64)
    recorded_sub = np.empty((n_samples, cfg.n_active, pool.geometry.n_sub), dtype=np.int64)
    node_charge = np.empty((*shape, 2))
    held = np.empty(shape, dtype=np.int64)
    q_free = np.empty(n_samples)
    q_residue = np.empty(n_samples)
    c_input = np.empty(n_samples)
    c_normalize = np.empty(n_samples)
    gains = np.empty(n_samples)
    vd_true = np.empty(n_samples)
    residue = np.empty(n_samples)
    vra = np.empty(n_samples)
    adc2_input = np.empty(n_samples)
    ra_sat: np.ndarray = np.empty(n_samples, dtype=bool)
    for index, converting in enumerate(plan.conv):
        group = acquisition_ids[index]
        values = np.full(cfg.n_active, sample.rdac_input_v[index])
        if pool.t_skew is not None and np.any(pool.t_skew[group] != 0):
            values = (
                finite_record(
                    input_fn(sample.aperture_s[index] + pool.t_skew[group]),
                    (cfg.n_active,),
                    "per-slice skewed input [V]",
                )
                + sample.driver_noise_v[index]
            )
        if pool.v_os is not None:
            values += pool.v_os[group]
        connection = SamplingConnections(
            sampled.main[index : index + 1], sampled.sub[index : index + 1]
        )
        packet = pool.acquire(
            group,
            values,
            sample_id=int(ids[index]),
            sampled_top_v=top[index],
            sampled_subnode_v=subnode[index],
            connections=connection,
            reference_high_v=_reference_at(high, index),
            reference_low_v=_reference_at(low, index),
        )
        order = np.array([int(np.flatnonzero(group == physical)[0]) for physical in converting])
        recorded_main[index] = sampled.main[index, order]
        recorded_sub[index] = sampled.sub[index, order]
        node_charge[index] = pool.held_node_charge(converting, sample_id=int(ids[index]))
        held[index] = pool.held_sample[converting]
        q_free[index] = pool.held_effective_node_charge(converting, sample_id=int(ids[index]))
        c_input[index] = packet.signal_capacitance_f[0]
        c_normalize[index] = packet.normalization_capacitance_f[0]
        gains[index] = ra.gain_vector(c_normalize[index : index + 1], feedback)[0]
        conversion_mask = SplitSwitchCommand(
            command.main_on[index : index + 1], command.sub_on[index : index + 1]
        )
        drive = pool.charge_from_masks(
            converting[None, :],
            conversion_mask,
            reference_high_v=_reference_at(high, index),
            reference_low_v=_reference_at(low, index),
        )
        vd_true[index] = drive.input_equivalent_v[0]
        # ==== 两个口径必须换算后再相减（2026-11-13，本轮最后一个不一致点）====
        # q_free（采样期自由电荷）按**全部物理电容**归一（C_samp：17 列 + 子阵列 5 列）；
        # Q_D（转换期参考驱动电荷）只含**参与码的单元**（C_conv：16 列 + 子阵列 4 列）。
        # 二者相差约 3%，直接把 (-q_free - Q_D)/C_conv 相减会得到一个
        # 与输入成正比的伪残差（实测 slope 0.0306 → 零输入外全饱和）。
        # 正确做法：把 q_free 折算到转换口径（乘 C_conv/C_samp）再相减，
        # 等价于"残差 = x - v_d"，两个量在同一分母上。
        c_convert = float(np.asarray(drive.signal_capacitance_f).reshape(-1)[0])
        c_samp = float(c_normalize[index])
        if c_samp <= 0 or c_convert <= 0:
            raise ConfigError("sampling/conversion capacitances must be positive")
        # 记录字段也用转换侧口径，外部可据此核对 Q_D / C_signal == vd_true。
        c_input[index] = c_convert
        q_residue[index] = (
            -q_free[index] * (c_convert / c_samp)
            - drive.charge_c[0]
            + c_convert * sample.rdac_noise_v[index]
        )
        residue[index] = q_residue[index] / c_convert
        if joint is None:
            error = None if injected is None else injected[index : index + 1]
            output, saturated = ra.evaluate(
                residue[index : index + 1], rng, gains[index : index + 1], input_error_v=error
            )
            # RA 是零中心的线性放大器；ADC2 窗口中心在 0.5 LSB9*G，因此
            # 不加中点：残差是单边 [0,1] LSB9（见上方说明）。vra 仍记 RA 原始输出。
            vra[index] = output[0]
            adc2_input[index] = output[0]
            ra_sat[index] = saturated[0]
        else:
            assert cfg.conversion is not None
            program = None
            if cfg.dyn_ref_settling:
                from adi_model.behavioral.reference_charge import physical_reference_program

                assert mapped.event_codes is not None and cfg.conversion is not None
                event_codes = mapped.event_codes[index]
                events = pool.select(
                    event_codes,
                    np.full(len(event_codes), sid[index], dtype=np.int64),
                    slice_ids=np.broadcast_to(converting, (len(event_codes), cfg.n_active)),
                )
                if not np.array_equal(
                    events.main_on[-1], conversion_mask.main_on[0]
                ) or not np.array_equal(events.sub_on[-1], conversion_mask.sub_on[0]):
                    raise ConfigError(
                        "reference event binding does not end at the actual final conversion mask"
                    )
                # 掩码按**端口宽度**给（子阵列 5 位），而器件只装配 4 列
                # （FIG.11 第 5 个 C 是虚线占位，见 p1_gaps §11）。
                # physical_reference_program 要求掩码与电容数组同形，
                # 因此在这里把 sub_on 截到实际列数 —— 与 dac_arch 的处理一致。
                _sub_on = np.asarray(events.sub_on)
                _n_sub_cols = pool.chip.C_sub.shape[-1]
                if _sub_on.shape[-1] != _n_sub_cols:
                    _sub_on = _sub_on[..., :_n_sub_cols]
                program = physical_reference_program(
                    slice_ids=converting,
                    main_caps_f=pool.chip.C_main[converting],
                    sub_caps_f=pool.chip.C_sub[converting],
                    bridge_caps_f=pool.chip.C_bridge[converting],
                    main_parasitic_f=pool.chip.require_main_parasitic()[converting],
                    sub_parasitic_f=pool.chip.c_p_sub[converting],
                    main_on=events.main_on,
                    sub_on=_sub_on,
                    times_s=cfg.conversion.rdac_event_times_s,
                    sampled_main_bottom_v=packet.main_bottom_v[0, order],
                    sampled_sub_bottom_v=packet.sub_bottom_v[0, order],
                    sampled_top_v=top[index, order],
                    sampled_subnode_v=subnode[index, order],
                    ref_high_v=_reference_at(high, index),
                    ref_low_v=_reference_at(low, index),
                )
            reference_correction = joint.step(
                index,
                program=program,
                residue_v=residue[index] + (0.0 if injected is None else injected[index]),
                gain=gains[index],
                reference_to_residue_scale=(-c_effective[index] / c_normalize[index])
                if cfg.dyn_ref_settling
                else None,
                sample_time_s=float(sample.aperture_s[index]),
                feedback_noise_gain=(1.0 + c_effective[index] / feedback)
                if cfg.conversion.ra_dc_gain != "ideal"
                else None,
            )
            residue[index] -= reference_correction
            q_residue[index] = residue[index] * c_normalize[index]
            vd_true[index] += reference_correction
            vra[index] = joint.result.ra_v[index]
            # ADC2 窗口是 [vmin, vmax] = [中心 ± 0.6 LSB9·G]，中心 = 0.5 LSB9·G
            # （paper_literal: [-0.0375, 0.4125]，G32、LSB9=11.72mV）。
            # RA 是纯线性放大器、零输出为 0，因此"残差 0"必须落在窗口中点：
            # ADC2 输入 = G*residue + 中点。缺这一项时负残差全部溢出（实测 x=±1/±3 V 全 over）。
            adc2_input[index] = joint.result.adc2_v[index]
            ra_sat[index] = joint.result.ra_sat[index]
            if not cfg.dyn_ref_settling and not cfg.conversion.dynamic:
                error = None if injected is None else injected[index : index + 1]
                point_output, point_sat = ra.evaluate(
                    residue[index : index + 1], rng, gains[index : index + 1], input_error_v=error
                )
                vra[index] = point_output[0]
                adc2_input[index] = point_output[0]
                ra_sat[index] |= point_sat[0]
        pool.release(converting, sample_id=int(ids[index]))
    adc2_code, adc2_over = backend.quantize_codes(adc2_input)
    # ADC2 码解码成节点电压后先减去中点，才回到"以 RA 零点为参考"的残差电压；
    # 否则重构式 fine/G 会带一个恒定的中点偏置。
    fine = backend.decode_codes(adc2_code)
    # 名义电压必须取**RDAC 实际使用的码 k = c - U**：残差是在 k 的电荷基线上建立的，
    # 取 vd0(c) 会与残差不同源（实测 64 字 p2p 达 114 mV）。
    # 这与原件 FIG.19 的 224 减法器一致：减法之后的码才是 DAC 的状态。
    vd0 = mapper.nominal_codebook.evaluate(rdac_codes)
    if np.any(ra_sat) or np.any(adc2_over):
        reasons.append("RA/ADC2 clipping violates the linear training equation")
    actual_sampling = SamplingConnections(recorded_main, recorded_sub)
    signal_mask, term = digital_connection_terms(
        pool.geometry,
        actual_sampling,
        command,
        nominal_reference_high_v=None
        if nominal_high is None
        else nominal_high[:, None] * np.ones((1, cfg.n_active)),
        nominal_reference_low_v=None
        if nominal_low is None
        else nominal_low[:, None] * np.ones((1, cfg.n_active)),
    )
    # 数字已知公共项只含 **L**：U 的作用已经通过 k = c - U 落在物理链里
    # （vd0(k) + residue 恰等于 x），若这里再扣 T，U 会被扣两次。
    # 依据 [10] 摘要页 (b)："the dither being subtracted from a digital output result
    # of the subsequent analog to digital converter stage" —— 后级只承担剩余部分。
    if dither_program is not None:
        term = term - dither_program.lower_v[:, None, None]
    elif dither_port is not None:
        term = term - dither_port.require_words().lower_v[:, None, None]
    diagnostic_reasons = []
    if cfg.dyn_ref_settling or (
        cfg.conversion is not None
        and (
            cfg.conversion.ra_response_model == "single_pole"
            or cfg.conversion.adc2_response_model in ("single_pole", "wide_narrow")
        )
    ):
        diagnostic_reasons.append(
            "stateful reference/RA/ADC2 response is outside this scalar diagnostic subset"
        )
    if np.any(top != 0):
        diagnostic_reasons.append(
            "nonzero sampled top terms have no explicit nominal scalar correction"
        )
    if not np.all(signal_mask):
        diagnostic_reasons.append(
            "sampling signal_mask is not allSIGNAL; no scalar alpha is inferred"
        )
    try:
        diagnostic_gain = float(finite_record(state.estimated_gain, (), "nominal scalar gain [1]"))
        if diagnostic_gain <= 0:
            raise ConfigError("nominal scalar gain must be positive")
    except ConfigError:
        diagnostic_reasons.append("a finite positive nominal scalar gain is required")
        diagnostic_gain = None
    # Actual C/G/reference errors and clipping remain visible in the nominal
    # output. They are never used to normalize it or select error-free records.
    diagnostic_available = not diagnostic_reasons
    if diagnostic_available:
        assert diagnostic_gain is not None
        # 重构只补 **L**：
        #   - U 段的作用已经由 k = c - U 在物理链里完成（vd0(k) + residue 恰等于 x，
        #     实测两个 word 都是 0.400712），再补 T 就等于把 U 重复加一次；
        #   - L 段按 [10] 摘要页 (b) "subtracted from a digital output result of the
        #     subsequent ADC stage"，在后级数字输出里扣除。
        # 因此 correction = -L，重建式为 out = vd0(k) + fine/G + L……等等：
        # reconstruct() 的定义是 out = (vd - correction)/alpha，代入 correction=-L
        # 得到 out = vd0(k) + fine/G + L，即把采样侧注入的 L 加回来。
        if dither_program is not None:
            correction = -dither_program.lower_v
        elif dither_port is not None:
            correction = -dither_port.require_words().lower_v
        else:
            correction = np.zeros(n_samples)
        out = reconstruct(vd0, fine, diagnostic_gain, correction, 1.0)
    else:
        out = np.full(n_samples, np.nan)
    bad = sadc_over | ra_sat | adc2_over | ~plan.valid
    observation = DigitalObservation(
        CalibrationSpec.from_config(cfg, geometry=pool.geometry),
        plan.conv,
        adc2_code,
        command.main_on,
        command.sub_on,
        signal_mask,
        term,
        bad,
        ids,
        static_charge_domain and not reasons,
    )
    dither_metadata = None
    if dither_port is not None:
        port_program = dither_port.require_words()
        dither_metadata = {
            **dither_port.spec.to_dict(),
            "model": "FIG19_CHARGE_PORT_V2",
            "topology": "RDAC code stays c; upper segment injected at the residue node",
            "stored_rdac_input": "sample.rdac_input_v = input_v; no code-domain shift",
            "sadc_input": "sample.x_sadc = input_v - L",
            "digital_known_common_q": "-upper_v only; lower is a decision-point shift",
            "upper_code_max": int(port_program.upper_code.max()),
            "lower_v_max": float(port_program.lower_v.max()),
            "upper_v_max": float(upper_v.max()),
            "not_modeled": [
                "physical dither capacitor/switch network and its own mismatch",
                "dither DAC reference settling",
                "in-loop dither scheduling (words are supplied per raw, not generated in-loop)",
            ],
        }
    elif dither_program is not None:
        assert dither_trace is not None
        dither_metadata = {
            **dither_program.spec.to_dict(),
            "prefix_events": int(dither_trace.rdac_event_codes.shape[1]),
            "event_flag_scope": "resolved RDAC integer range; no per-event analog settling claim",
            "stored_rdac_input": "sample.rdac_input_v = input_v - T; actual pool.acquire",
            "sadc_input": "sample.x_sadc = input_v - L in this zero-noise v1",
            "digital_known_common_q": "-total_q; no second upper correction",
        }
    result = SimResult(
        cfg=cfg,
        pool=pool,
        state=state,
        sample=sample,
        sample_id=ids.copy(),
        conv_slice_ids=plan.conv.copy(),
        acq_slice_ids=acquisition_ids.copy(),
        next_acq_slice_ids=plan.acq.copy(),
        held_sample_ids=held,
        plan_valid=plan.valid.copy(),
        coarse=coarse,
        sadc_over=sadc_over,
        k=rdac_codes,  # 224 减法器之后的实际 RDAC 码 k = c - U
        sid=sid,
        main_masks=command.main_on,
        sub_masks=command.sub_on,
        sampling_main_connections=recorded_main,
        sampling_sub_connections=recorded_sub,
        sampled_node_charge_c=node_charge,
        sampled_effective_charge_c=q_free,
        residue_charge_c=q_residue,
        signal_capacitance_f=c_input,
        normalization_capacitance_f=c_normalize,
        g_vec=gains,
        vd0=vd0,
        vd_true=vd_true,
        residue=residue,
        vra=vra,
        adc2_input_voltage=adc2_input,
        adc2_code=adc2_code,
        fine=fine,
        ra_sat=ra_sat,
        adc2_over=adc2_over,
        out=out,
        err=out - sample.input_clean_v,
        x_ref=sample.input_clean_v,
        static_domain_reasons=tuple(dict.fromkeys(reasons)),
        nominal_diagnostic_available=diagnostic_available,
        nominal_diagnostic_reasons=tuple(diagnostic_reasons),
        binding_metadata={
            "mask": _binding_name(pool.mask_binding),
            "acquisition": _binding_name(pool.acquisition_binding),
            "schedule": _binding_name(pool.schedule_binding),
            "schedule_strategy": plan.strategy,
            "source_alignment": "EXPLICIT_USER_BINDINGS; not independently proved source mapping",
            "acquisition_window": "full raw period; point-domain endpoint for this runner",
            "continuous_acquisition_status": "NOT_IMPLEMENTED without complete phase/initial-node binding",
            "event_code_table_applied": bool(cfg.dyn_ref_settling),
            "inactive_runtime_inputs": (
                (
                    ["initial_reference_error_v"]
                    if initial_reference_error_v is not None and not cfg.dyn_ref_settling
                    else []
                )
                + (["reference_high_v"] if reference_high_v is not None and not high_needed else [])
                + (["reference_low_v"] if reference_low_v is not None and not low_needed else [])
                + (
                    ["nominal_reference_high_v"]
                    if nominal_reference_high_v is not None and not np.any(high_used)
                    else []
                )
                + (
                    ["nominal_reference_low_v"]
                    if nominal_reference_low_v is not None and not np.any(low_used)
                    else []
                )
            ),
            "unused_slice_parameter_ids": {
                name: np.setdiff1d(
                    np.flatnonzero(getattr(pool, name) != 0), used_physical_ids
                ).tolist()
                for name in ("t_skew", "v_os")
                if getattr(pool, name) is not None
            },
            "RA_error_node": "RA input-equivalent V before actual gain/clipping/ADC2",
            "dither": dither_metadata,
        },
        digital_observation=observation,
        dither_program=dither_program
        if dither_program is not None
        else (None if dither_port is None else dither_port.require_words()),
        dither_trace=dither_trace,
        conversion_trace=None if joint is None else joint.result,
    )
    if state.weight_calibration is not None:
        apply_frozen_calibration(result, state.weight_calibration)
    return result
