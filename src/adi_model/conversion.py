"""显式参考事件、RA信号响应与ADC2孔径的共享状态。

所有物理初态、时间、R/C/BW和节点换算均由调用方提供；不由数字16tick、目标DR或原文曲线反推。
该模块求解给定RC/放大器模型，不含[14]的比较器越阈/阈值积分、Cboost或battery电路。
参考程序只消费实际mask计算的端口电荷，不在此重造coarse/sid/dither连接。

ReferenceProgram.sensitivity位于明确的浮置top节点。
step要求调用方显式给出reference_to_residue_scale，不能把浮置top当输入等效残差或RA虚地。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from numbers import Real
from typing import TYPE_CHECKING, TypeGuard, cast

import numpy as np

from .reference_charge import ReferenceProgram

if TYPE_CHECKING:
    from .config import Config


def _finite(value: object) -> TypeGuard[float]:
    # NumPy 的标量重载未列出 Real。cast 保留输入对象与 ufunc 的检查行为。
    return (
        isinstance(value, Real)
        and not isinstance(value, bool | np.bool_)
        and bool(np.isfinite(cast(float, value)))
    )


def _positive(value: object) -> TypeGuard[float]:
    return _finite(value) and value > 0


@dataclass(frozen=True)
class ConversionParameters:
    """可不完整的信号响应合同；None不能自动代表理想器件。

    quantizer_time_s为孔径至RA开始的明确时间，ra_duration_s为唯一RA区间。
    reference_accurate_start_s单独给出参考恢复模型的切换时刻；不能默认等于RA起点。
    事件时刻是明确的固定合同，实际program必须匹配它。adc2_narrow_start_s是RA开始后的时间，
    不采用默认比例。

    ra_response_model/adc2_response_model显式选择ideal或所需有限带宽模型。
    ra_dc_gain必须给正开环增益或字符串ideal；ra_slew_v_s必须给正上限或unlimited。
    只有rtol/atol_v是纯数值求解默认，不是物理值。
    """

    quantizer_time_s: float | None = None
    ra_duration_s: float | None = None
    rdac_event_times_s: tuple[float, ...] | None = None
    reference_accurate_start_s: float | None = None
    fine_reference_tau_s: float | None = None
    ra_response_model: str | None = None
    ra_bandwidth_hz: float | None = None
    ra_dc_gain: float | str | None = None
    ra_slew_v_s: float | str | None = None
    adc2_response_model: str | None = None
    adc2_wide_bandwidth_hz: float | None = None
    adc2_narrow_bandwidth_hz: float | None = None
    adc2_narrow_start_s: float | None = None
    rtol: float = 1e-9
    atol_v: float = 1e-11

    def violations(self) -> list[str]:
        """校验已给条件及选中模型的依赖；不生成缺失物理值。"""
        bad = []
        for name in ("rtol", "atol_v"):
            if getattr(self, name) is None:
                bad.append(f"conversion.{name} is required by the numerical solver")
        for name, unit in (
            ("quantizer_time_s", "s"),
            ("ra_duration_s", "s"),
            ("fine_reference_tau_s", "s"),
            ("ra_bandwidth_hz", "Hz"),
            ("adc2_wide_bandwidth_hz", "Hz"),
            ("adc2_narrow_bandwidth_hz", "Hz"),
            ("adc2_narrow_start_s", "s"),
            ("rtol", "unitless"),
            ("atol_v", "V"),
        ):
            value = getattr(self, name)
            if value is not None and not _positive(value):
                bad.append(f"conversion.{name} [{unit}] must be finite and positive")
        if self.reference_accurate_start_s is not None and (
            not _finite(self.reference_accurate_start_s) or self.reference_accurate_start_s < 0
        ):
            bad.append("conversion.reference_accurate_start_s [s] must be finite and nonnegative")
        if self.ra_response_model not in (None, "ideal", "single_pole"):
            bad.append("conversion.ra_response_model must select ideal or single_pole")
        if self.adc2_response_model not in (None, "ideal", "single_pole", "wide_narrow"):
            bad.append(
                "conversion.adc2_response_model must select ideal, single_pole or wide_narrow"
            )
        for name, ideal in (("ra_dc_gain", "ideal"), ("ra_slew_v_s", "unlimited")):
            value = getattr(self, name)
            is_ideal = isinstance(value, str) and value == ideal
            if value is not None and not is_ideal and not _positive(value):
                bad.append(f"conversion.{name} must be positive or explicitly {ideal}")
        if self.ra_response_model == "single_pole" and self.ra_bandwidth_hz is None:
            bad.append("conversion.ra_bandwidth_hz [Hz] is required by single_pole RA")
        if self.ra_bandwidth_hz is not None and self.ra_response_model != "single_pole":
            bad.append("conversion.ra_bandwidth_hz requires explicit single_pole RA")
        if _positive(self.ra_slew_v_s) and self.ra_response_model != "single_pole":
            bad.append("finite RA slew requires explicit single_pole RA")
        if (
            self.adc2_response_model in ("single_pole", "wide_narrow")
            and self.adc2_wide_bandwidth_hz is None
        ):
            bad.append("conversion.adc2_wide_bandwidth_hz [Hz] is required by finite ADC2 response")
        if self.adc2_response_model == "wide_narrow":
            for name, unit in (("adc2_narrow_bandwidth_hz", "Hz"), ("adc2_narrow_start_s", "s")):
                if getattr(self, name) is None:
                    bad.append(f"conversion.{name} [{unit}] is required by wide_narrow ADC2")
            if (
                _positive(self.adc2_narrow_start_s)
                and _positive(self.ra_duration_s)
                and self.adc2_narrow_start_s >= self.ra_duration_s
            ):
                bad.append("ADC2 narrow switch must precede the explicit aperture")
        elif self.adc2_narrow_bandwidth_hz is not None or self.adc2_narrow_start_s is not None:
            bad.append("ADC2 narrow controls require explicit wide_narrow response")
        if self.adc2_wide_bandwidth_hz is not None and self.adc2_response_model not in (
            "single_pole",
            "wide_narrow",
        ):
            bad.append("ADC2 bandwidth requires an explicit finite response model")
        if self.rdac_event_times_s is not None:
            events = np.asarray(self.rdac_event_times_s)
            if (
                events.ndim != 1
                or events.size == 0
                or not np.issubdtype(events.dtype, np.number)
                or np.iscomplexobj(events)
                or np.any(~np.isfinite(events))
                or events[0] < 0
                or np.any(np.diff(events) < 0)
            ):
                bad.append(
                    "conversion.rdac_event_times_s [s] needs explicit ordered nonnegative events"
                )
        return bad

    @property
    def dynamic(self) -> bool:
        """是否实际请求有限信号响应；profile或明确ideal不自动启用。"""
        return (
            self.ra_response_model == "single_pole"
            or self.adc2_response_model in ("single_pole", "wide_narrow")
            or self.ra_bandwidth_hz is not None
            or self.adc2_wide_bandwidth_hz is not None
            or self.adc2_narrow_bandwidth_hz is not None
            or _positive(self.ra_dc_gain)
            or _positive(self.ra_slew_v_s)
        )

    def require_runtime(self, *, fs: float, reference_active: bool) -> None:
        """在实际电气消费者运行前拒绝缺少的节点/相位合同。"""
        bad = self.violations()
        for name, unit in (("quantizer_time_s", "s"), ("ra_duration_s", "s")):
            if getattr(self, name) is None:
                bad.append(f"conversion.{name} [{unit}] is required by ConversionEngine")
        for name in ("ra_response_model", "adc2_response_model", "ra_dc_gain", "ra_slew_v_s"):
            if getattr(self, name) is None:
                bad.append(f"conversion.{name} needs an explicit physical value or ideal choice")
        if not _positive(fs):
            bad.append("fs [Hz] must be finite and positive")
        elif _positive(self.quantizer_time_s) and _positive(self.ra_duration_s):
            aperture = self.quantizer_time_s + self.ra_duration_s
            if aperture > (1 + 1e-12) / fs:
                bad.append("explicit quantizer and RA/ADC2 intervals exceed one sample period")
            if (
                self.reference_accurate_start_s is not None
                and _finite(self.reference_accurate_start_s)
                and self.reference_accurate_start_s > aperture
            ):
                bad.append(
                    "accurate-reference switch must lie inside the declared conversion interval"
                )
            if self.rdac_event_times_s is not None:
                events = np.asarray(self.rdac_event_times_s)
                if (
                    events.ndim == 1
                    and events.size
                    and np.issubdtype(events.dtype, np.number)
                    and not np.iscomplexobj(events)
                    and np.all(np.isfinite(events))
                    and events[-1] > self.quantizer_time_s
                ):
                    bad.append(
                        "reference load events after RA entry are not implemented by this plant"
                    )
        if reference_active:
            for name, unit in (
                ("rdac_event_times_s", "s"),
                ("reference_accurate_start_s", "s"),
                ("fine_reference_tau_s", "s"),
            ):
                if getattr(self, name) is None:
                    bad.append(
                        f"conversion.{name} [{unit}] is required by active reference dynamics"
                    )
        if bad:
            raise ValueError("; ".join(bad))


@lru_cache(maxsize=256)
def _cascade_matrix(a: float, b: float, c: float, dt: float):
    from scipy.linalg import expm

    # 状态依次为衰减参考驱动、RA 减直流目标、ADC2 减直流目标；矩阵指数处理重复极点，
    # 避免除零特判以及近似相等指数相减造成的精度损失。
    return expm(np.array([[-a, 0, 0], [b, -b, 0], [0, c, -c]]) * dt)


def response_interval(
    y0: float,
    z0: float,
    target_v: float,
    reference_drive_v: float,
    tau_ref_s: float | None,
    dt_s: float,
    *,
    ra_bw_hz: float | None,
    adc_bw_hz: float | None,
    clip_v: float,
    slew_v_s: float | None = None,
    rtol: float = 1e-9,
    atol_v: float = 1e-11,
) -> tuple[float, float, bool]:
    """推进给定target+reference_drive*exp(-t/tau)的明确物理区间。

    y为RA输出[V]，z为ADC2跟踪节点[V]。None带宽是调用方明确选择的无状态follower；
    相应未用初值可为NaN，不放进矩阵或ODE，也不默填一个物理零节点。
    只有reference_drive严格为零时才可省略参考时间常数。rtol/atol_v仅控制数值求解，不是器件参数。

    Args:
        y0: 有限带宽RA的初值[V]；无状态RA不读取它。
        z0: 有限带宽ADC2的初值[V]；无状态ADC2不读取它。
        target_v: RA输出端静态目标[V]。
        reference_drive_v: 区间起点的RA输出等效参考扰动[V]。
        tau_ref_s: 活跃参考扰动恢复时间[s]，有限且正。
        dt_s: 明确区间长度[s]，有限且正。
        ra_bw_hz: 闭环RA信号带宽[Hz]或显式None表示follower。
        adc_bw_hz: ADC2跟踪带宽[Hz]或显式None表示follower。
        clip_v: RA输出摆幅[V]，有限且正。
        slew_v_s: RA斜率上限[V/s]；None显式选择不限制。
        rtol: 正相对数值容差。
        atol_v: 正绝对数值容差[V]。

    Returns:
        (RA输出V,ADC2跟踪V,RA饱和标志)。线性分支用矩阵指数；有限斜率/摆幅分支只对实际活跃状态做ODE。
        此函数不计算噪声传递。

    Raises:
        ValueError: 缺失活跃状态、非法参数或不支持的idealRA有限slew组合。
        RuntimeError: ODE未收敛或出现非有限结果。
    """
    ra_stateful, adc_stateful = ra_bw_hz is not None, adc_bw_hz is not None
    if (
        not _positive(dt_s)
        or not _positive(clip_v)
        or not _positive(rtol)
        or not _positive(atol_v)
        or not _finite(target_v)
        or not _finite(reference_drive_v)
        or (tau_ref_s is None and reference_drive_v != 0)
        or (tau_ref_s is not None and not _positive(tau_ref_s))
        or (ra_stateful and (not _positive(ra_bw_hz) or not _finite(y0)))
        or (adc_stateful and (not _positive(adc_bw_hz) or not _finite(z0)))
        or (slew_v_s is not None and (not _positive(slew_v_s) or not ra_stateful))
    ):
        raise ValueError(
            "response interval requires finite active states and explicit positive time/bandwidth/swing"
        )
    a = 0.0 if tau_ref_s is None else 1.0 / tau_ref_s
    b = 2 * np.pi * cast(float, ra_bw_hz) if ra_stateful else 0.0
    c = 2 * np.pi * cast(float, adc_bw_hz) if adc_stateful else 0.0
    ref_end = reference_drive_v * np.exp(-a * dt_s)
    max_drive = max(abs(target_v), abs(target_v + reference_drive_v))
    if ra_stateful:
        max_drive = max(max_drive, abs(y0))
    slope_bound = b * (max_drive + abs(y0)) if ra_stateful else 0.0
    linear = max_drive < clip_v and (slew_v_s is None or slope_bound <= slew_v_s)
    if linear:
        if not ra_stateful and not adc_stateful:
            y = z = target_v + ref_end
        elif not ra_stateful:
            h = _cascade_matrix(a, c, 0.0, dt_s)[:2, :2]
            state = h @ [reference_drive_v, z0 - target_v]
            y, z = target_v + ref_end, target_v + state[1]
        elif not adc_stateful:
            h = _cascade_matrix(a, b, 0.0, dt_s)[:2, :2]
            state = h @ [reference_drive_v, y0 - target_v]
            y = z = target_v + state[1]
        else:
            state = _cascade_matrix(a, b, c, dt_s) @ [
                reference_drive_v,
                y0 - target_v,
                z0 - target_v,
            ]
            y, z = target_v + state[1], target_v + state[2]
        return float(y), float(z), False

    # reference_drive在本区间单调指数恢复，idealRA的峰值由两端决定。
    if not ra_stateful and not adc_stateful:
        y = float(np.clip(target_v + ref_end, -clip_v, clip_v))
        saturated = max(abs(target_v + reference_drive_v), abs(target_v + ref_end)) >= clip_v
        return y, y, saturated

    from scipy.integrate import solve_ivp

    initial = []
    if ra_stateful:
        initial.append(float(np.clip(y0, -clip_v, clip_v)))
    if adc_stateful:
        initial.append(float(z0))

    def rhs(s, state):
        target = target_v + reference_drive_v * np.exp(-a * dt_s * s)
        physical_y = np.clip(state[0] if ra_stateful else target, -clip_v, clip_v)
        derivative = []
        if ra_stateful:
            dy = b * (target - physical_y)
            if slew_v_s is not None:
                dy = np.clip(dy, -slew_v_s, slew_v_s)
            if (physical_y >= clip_v and dy > 0) or (physical_y <= -clip_v and dy < 0):
                dy = 0.0
            derivative.append(dy)
        if adc_stateful:
            z = state[1 if ra_stateful else 0]
            derivative.append(c * (physical_y - z))
        return np.asarray(derivative) * dt_s

    sol = solve_ivp(
        rhs, (0.0, 1.0), initial, method="DOP853", rtol=rtol, atol=atol_v, max_step=1 / 16
    )
    if not sol.success or np.any(~np.isfinite(sol.y)):
        raise RuntimeError(f"joint conversion integration failed: {sol.message}")
    if ra_stateful:
        y = float(np.clip(sol.y[0, -1], -clip_v, clip_v))
        saturated = abs(y0) >= clip_v or np.max(np.abs(sol.y[0])) >= clip_v - 2 * atol_v
    else:
        y = float(np.clip(target_v + ref_end, -clip_v, clip_v))
        saturated = max(abs(target_v + reference_drive_v), abs(target_v + ref_end)) >= clip_v
    z = float(sol.y[1 if ra_stateful else 0, -1]) if adc_stateful else y
    return y, z, bool(saturated)


@dataclass(frozen=True)
class ConversionResult:
    """电气信号观测；事件为(N,E,2)，phase维度按phase_names明确给出。

    不活跃的浮节点/带宽阶段不伪造零值；未执行样本保持NaN。噪声不在此积分，
    也不能把信号带宽等同于开关噪声传递。
    """

    ra_v: np.ndarray
    adc2_v: np.ndarray
    ra_sat: np.ndarray
    reference_error_v: np.ndarray
    reference_charge_c: np.ndarray
    reference_peak_v: np.ndarray
    event_times_s: np.ndarray
    adc2_times_s: np.ndarray
    dac_reference_error_v: np.ndarray
    reference_event_error_v: np.ndarray
    phase_times_s: np.ndarray
    phase_names: tuple[str, ...]
    phase_ra_v: np.ndarray
    phase_adc2_v: np.ndarray
    phase_reference_v: np.ndarray


class ConversionEngine:
    """按真实样本时刻推进共享电气状态，不生成物理初态。

    初态定义在第一次step的sample_time_s。RA/ADC2只有所选状态模型活跃时才需要对应初值；
    参考关闭时不强索无用rail状态。区间外节点保持，而非自动AZ清零；本plant没有实现模拟reset开关。
    """

    def __init__(
        self,
        cfg: Config,
        n_samples: int,
        *,
        initial_reference_error_v=None,
        initial_ra_output_v=None,
        initial_adc2_input_v=None,
    ):
        """拒绝缺参后分配观测；不从DR、profile或数字tick补物理量。

        Args:
            cfg: 含明确conversion和RA摆幅的配置。
            n_samples: 正整数记录长度。
            initial_reference_error_v: 活跃参考high/low轨误差初值(2,)[V]。
            initial_ra_output_v: 单极点RA状态初值[V]；显式ideal可不提供。
            initial_adc2_input_v: 有限ADC2跟踪状态初值[V]；ideal可不提供。

        Raises:
            ValueError: 缺少活跃参数/状态或非法维度。
        """
        if type(n_samples) is not int or n_samples < 1:
            raise ValueError("ConversionEngine.n_samples must be a positive integer")
        p = cfg.conversion
        if not isinstance(p, ConversionParameters):
            raise ValueError("conversion parameters are required by ConversionEngine")
        self.reference_active = cfg.dyn_ref_settling
        if type(self.reference_active) is not bool:
            raise ValueError("dyn_ref_settling must be a boolean")
        p.require_runtime(fs=cfg.fs, reference_active=self.reference_active)
        if not _positive(cfg.ra_v_clip):
            raise ValueError("ra_v_clip [RA-output V] is required by ConversionEngine")
        if self.reference_active:
            for name, unit in (("dyn_c_decouple", "F"), ("dyn_tau_ref", "s")):
                if not _positive(getattr(cfg, name, None)):
                    raise ValueError(f"{name} [{unit}] is required by active reference dynamics")
            if np.iscomplexobj(initial_reference_error_v):
                raise ValueError("initial_reference_error_v must be real [V]")
            rail = np.asarray(initial_reference_error_v, dtype=float)
            if rail.shape != (2,) or np.any(~np.isfinite(rail)):
                raise ValueError(
                    "initial_reference_error_v [V] must explicitly provide two finite rail states"
                )
            self.rail = rail.copy()
        else:
            self.rail = np.zeros(2)  # inactive diagnostic, not an assumed physical rail
        if p.ra_response_model == "single_pole" and not _finite(initial_ra_output_v):
            raise ValueError("initial_ra_output_v [V] is required by finite RA response")
        if p.adc2_response_model in ("single_pole", "wide_narrow") and not _finite(
            initial_adc2_input_v
        ):
            raise ValueError("initial_adc2_input_v [V] is required by finite ADC2 response")
        for name, value in (
            ("initial_ra_output_v", initial_ra_output_v),
            ("initial_adc2_input_v", initial_adc2_input_v),
        ):
            if value is not None and not _finite(value):
                raise ValueError(f"{name} must be finite [V]")
        self.y = float(initial_ra_output_v) if initial_ra_output_v is not None else float("nan")
        self.z = float(initial_adc2_input_v) if initial_adc2_input_v is not None else float("nan")
        self.cfg, self.parameters = cfg, p
        # require_runtime 已校验所选模型消费的时间与物理参数。
        self.aperture = cast(float, p.quantizer_time_s) + cast(float, p.ra_duration_s)
        events = (
            np.asarray(p.rdac_event_times_s, dtype=float) if self.reference_active else np.empty(0)
        )
        marks = [("ra_entry", cast(float, p.quantizer_time_s)), ("adc2_aperture", self.aperture)]
        if self.reference_active:
            marks.append(("reference_accurate", cast(float, p.reference_accurate_start_s)))
        if p.adc2_response_model == "wide_narrow":
            marks.append(
                (
                    "adc2_narrow",
                    cast(float, p.quantizer_time_s) + cast(float, p.adc2_narrow_start_s),
                )
            )
        marks.sort(key=lambda mark: mark[1])
        phases, event_count = len(marks), len(events)
        self.result = ConversionResult(
            ra_v=np.full(n_samples, np.nan),
            adc2_v=np.full(n_samples, np.nan),
            ra_sat=np.zeros(n_samples, dtype=bool),
            reference_error_v=np.full((n_samples, 2), np.nan),
            reference_charge_c=np.zeros((n_samples, event_count, 2)),
            reference_peak_v=np.zeros(n_samples),
            event_times_s=events.copy(),
            adc2_times_s=np.full(n_samples, np.nan),
            dac_reference_error_v=np.full(n_samples, np.nan),
            reference_event_error_v=np.full((n_samples, event_count, 2), np.nan),
            phase_times_s=np.array([t for _, t in marks]),
            phase_names=tuple(name for name, _ in marks),
            phase_ra_v=np.full((n_samples, phases), np.nan),
            phase_adc2_v=np.full((n_samples, phases), np.nan),
            phase_reference_v=np.full((n_samples, phases, 2), np.nan),
        )
        self.next_sample = 0
        self._previous_aperture_s: float | None = None

    def step(
        self,
        i: int,
        *,
        program: ReferenceProgram | None,
        sample_time_s: float,
        residue_v: float,
        gain: float,
        reference_to_residue_scale: float | None,
        feedback_noise_gain: float | None,
    ) -> float:
        """推进一笔，返回应从名义残差扣除的DAC输入等效参考误差[V]。

        Args:
            i: 按顺序推进的记录索引。
            program: 活跃参考必须给实际mask求得的ReferenceProgram；关闭给None。
            sample_time_s: 真实原始孔径时刻[s]，不以i/fs生成。
            residue_v: 已按声明码域形成的名义输入等效RA残差[V]。
            gain: 名义残差到RA输出的正增益，无量纲。
            reference_to_residue_scale: 浮置top到residue的有符号尺度；
                例如r=−C_eff/C_signal*V_top时显式给−C_eff/C_signal。参考关闭可None。
            feedback_noise_gain: 反馈网络的无量纲噪声增益；有限开环增益时必须显式给出。
                相同电容网络下为1+C_eff/C_f，不能用信号增益C_signal/C_f替代；
                理想开环增益时可显式给None。

        Returns:
            正向DAC电压误差correction；residue_actual=residue_v-correction。

        Raises:
            ValueError: 非顺序调用、缺失状态/电参、错事件时刻或不支持的共享区间重叠。
        """
        if type(i) is not int or i != self.next_sample or i >= len(self.result.ra_v):
            raise ValueError("conversion samples must advance consecutively")
        if not _finite(sample_time_s) or not _finite(residue_v) or not _positive(gain):
            raise ValueError(
                "sample time [s], residue [V] and positive gain must be explicit finite values"
            )
        cfg, p, r = self.cfg, self.parameters, self.result
        if feedback_noise_gain is not None and not _positive(feedback_noise_gain):
            raise ValueError("feedback_noise_gain [unitless] must be finite and positive")
        if p.ra_dc_gain != "ideal" and feedback_noise_gain is None:
            raise ValueError("finite RA open-loop gain requires explicit feedback_noise_gain")
        if self.reference_active:
            if not isinstance(program, ReferenceProgram):
                raise ValueError(
                    "active reference dynamics requires an explicit physical ReferenceProgram"
                )
            if not _finite(reference_to_residue_scale) or reference_to_residue_scale == 0:
                raise ValueError(
                    "reference_to_residue_scale [signed unitless] is required for node conversion"
                )
            if (
                not np.array_equal(program.times_s, r.event_times_s)
                or program.charge_c.shape != (len(r.event_times_s), 2)
                or program.sensitivity.shape != program.charge_c.shape
                or np.any(~np.isfinite(program.charge_c))
                or np.any(~np.isfinite(program.sensitivity))
            ):
                raise ValueError(
                    "reference program must match the explicit event-time/rail contract"
                )
        elif program is not None or reference_to_residue_scale is not None:
            raise ValueError(
                "inactive reference dynamics cannot consume a hidden reference program/scale"
            )
        if self._previous_aperture_s is not None:
            gap = sample_time_s - self._previous_aperture_s
            if gap < -1e-12 / cfg.fs:
                raise ValueError(
                    "overlapping shared conversion intervals require a different explicit event plant"
                )
            if self.reference_active:
                self.rail *= np.exp(-max(0.0, gap) / cast(float, p.fine_reference_tau_s))
        # 节点P=−Vout/A0的反馈项由C_eff和C_f决定；C_signal只控制输入
        # 信号电荷。含主/子节点寄生时，两种电容一般不相等。
        actual_gain = (
            gain
            if p.ra_dc_gain == "ideal"
            else gain
            * (
                cast(float, p.ra_dc_gain)
                / (cast(float, p.ra_dc_gain) + cast(float, feedback_noise_gain))
            )
        )
        target = actual_gain * residue_v
        sensitivity = np.zeros(2)
        event_index = 0
        points = np.unique(np.r_[0.0, r.event_times_s, r.phase_times_s])
        previous_time = 0.0
        for t in points:
            dt = t - previous_time
            if dt > 0:
                tau = None
                if self.reference_active:
                    tau = (
                        p.fine_reference_tau_s
                        if previous_time >= cast(float, p.reference_accurate_start_s)
                        else cfg.dyn_tau_ref
                    )
                if previous_time >= cast(float, p.quantizer_time_s):
                    drive = (
                        actual_gain
                        * cast(float, reference_to_residue_scale)
                        * np.dot(sensitivity, self.rail)
                        if self.reference_active
                        else 0.0
                    )
                    narrow = p.adc2_response_model == "wide_narrow" and previous_time >= cast(
                        float, p.quantizer_time_s
                    ) + cast(float, p.adc2_narrow_start_s)
                    adc_bw = p.adc2_narrow_bandwidth_hz if narrow else p.adc2_wide_bandwidth_hz
                    self.y, self.z, clipped = response_interval(
                        self.y,
                        self.z,
                        target,
                        drive,
                        tau,
                        dt,
                        ra_bw_hz=p.ra_bandwidth_hz,
                        adc_bw_hz=adc_bw,
                        clip_v=cast(float, cfg.ra_v_clip),
                        slew_v_s=None
                        if p.ra_slew_v_s == "unlimited"
                        else cast(float, p.ra_slew_v_s),
                        rtol=p.rtol,
                        atol_v=p.atol_v,
                    )
                    r.ra_sat[i] |= clipped
                if self.reference_active:
                    self.rail *= np.exp(-dt / cast(float, tau))
            while event_index < len(r.event_times_s) and r.event_times_s[event_index] == t:
                # 事件表对应已通过入口校验的活跃参考程序。
                q = cast(ReferenceProgram, program).charge_c[event_index]
                self.rail -= q / cast(float, cfg.dyn_c_decouple)
                sensitivity = cast(ReferenceProgram, program).sensitivity[event_index]
                r.reference_charge_c[i, event_index] = q
                r.reference_event_error_v[i, event_index] = self.rail
                event_index += 1
            if t == p.quantizer_time_s:
                if p.ra_response_model == "ideal":
                    drive = (
                        actual_gain
                        * cast(float, reference_to_residue_scale)
                        * np.dot(sensitivity, self.rail)
                        if self.reference_active
                        else 0.0
                    )
                    r.ra_sat[i] |= abs(target + drive) >= cast(float, cfg.ra_v_clip)
                    self.y = float(
                        np.clip(target + drive, -cast(float, cfg.ra_v_clip), cfg.ra_v_clip)
                    )
                if p.adc2_response_model == "ideal":
                    self.z = self.y
            r.reference_peak_v[i] = max(r.reference_peak_v[i], float(np.max(np.abs(self.rail))))
            for phase in np.flatnonzero(r.phase_times_s == t):
                r.phase_ra_v[i, phase], r.phase_adc2_v[i, phase], r.phase_reference_v[i, phase] = (
                    self.y,
                    self.z,
                    self.rail,
                )
            previous_time = float(t)
        correction = (
            -cast(float, reference_to_residue_scale) * float(np.dot(sensitivity, self.rail))
            if self.reference_active
            else 0.0
        )
        r.ra_v[i], r.adc2_v[i], r.reference_error_v[i] = self.y, self.z, self.rail
        r.dac_reference_error_v[i] = correction
        r.adc2_times_s[i] = sample_time_s + self.aperture
        self._previous_aperture_s = r.adc2_times_s[i]
        self.next_sample += 1
        return correction
