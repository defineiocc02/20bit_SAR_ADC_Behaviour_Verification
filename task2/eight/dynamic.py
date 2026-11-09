# =============================================================================
# 文件名     : dynamic.py
# 模块名     : task2.eight.dynamic
# 版本       : v1.0
# 功能       : 原文时序约束下的整周期采集、输入串扰和切换参考Python模型。
# 设计说明   :
#   [时序] 输入孔径为nT，该笔ADC2孔径为(n+1)T，两者与下一输入共享安静时刻。
#   [因果] 转换边沿排队到下一跟踪窗处理，不反向污染已完成的输入采样。
#   [参考] 大转换电荷由粗预充节点承担；RA期间才连接精确REF_IN。
#   [参数] 不猜耦合电容／参考储能；开启对应路径必须显式绑定来源和负载映射。
# 变更记录   : 替换半周期／单参考节点草案，删除独立随机τ离散默认。
# =============================================================================

"""结构与原文对齐、量值依赖显式模拟绑定的动态转换。"""

from __future__ import annotations

import numpy as np

from adi_model.capacitors import Capacitors, ExperimentConfig
from adi_model.reference_mux import CouplingBinding, ReferenceBinding, SwitchedReference
from adi_model.transients import CoupledNode, DynamicConfig, RampDrive, SineDrive
from task2.eight.model import ChargePool, conversion_state, decode

WIRE_SHAPES = {"select": (18,), "sample": (18, 5), "main": (18, 17), "sub": (18, 5), "word": (6,)}


# =============================================================================
# 1. 绑定和跨块状态
# =============================================================================
class DynamicCapture:
    """时序对齐的条件行为模型；不是凭未披露参数预测原芯片性能。

    Args:
        config: 周期、RDAC标称带宽及三路径开关。
        caps: 固定制造池，不能在每笔重新抽样。
        experiment: 静态电荷模型范围与采样率。
        drive: 连续正弦或斜坡。外部驱动若有限带宽，单独使用driver_tau_s。
        coupling: 可选输入采样误差投影，开启串扰时必须绑定。
        reference: 可选粗预充／精确参考R/C绑定，开启参考时必须绑定。
        reference_charge: 函数(index, context)->(周期内时间s,两轨SAR电荷C)事件序列，参考开启时必填。
            context含输入、物理片号及实际转换／注入位图；不能用特征数替代物理电荷。
        auxiliary_sample_error: external_trace模式所需函数，返回该笔采样时已存下的
            输入等效辅助参考误差V。独立理想辅助参考模式明确为0，不擅自给精确源加负载。
        input_tau_multipliers: 可选18片实测倍率；未给出时只按已存在制造C比例和固定R假设计算。
        external_edges: 可选函数(index)->(offset_s,charge_c)事件序列，offset属于该笔输入
            采集窗所在的上一周期[0,T)。电荷是外部额外投影，不得重复内置位图的耦合。
        acquisition_response: 可选函数(index,context)->(八槽电压V,SADC电压V)，替代
            单极点采集。context给孔径、开始时刻、物理片号及保持初值；串扰随后叠加。
        ra_initial_response: external_trace模式的逐笔RA初始电压V函数(index,context)。

    Raises:
        ValueError: 周期不一致、缺失必需绑定或参数形状／范围非法。

    默认不加入额外±2%R或τ离散，不添加任意电压相关时间常数。
    保持初值沿用上一笔采样值，是边界假设；全周期15×Nyquist应使初值记忆极小。
    SADC与ADC2参考仍理想，子阵列拓扑沿用现有工程网络，不宣称原芯片全电路复现。
    串扰绑定仅覆盖输入端投影，不能宣称ADC2端／整棵时钟树串扰已被计算。
    """

    def __init__(
        self,
        config: DynamicConfig,
        caps: Capacitors,
        experiment: ExperimentConfig,
        drive,
        *,
        coupling: CouplingBinding | None = None,
        reference: ReferenceBinding | None = None,
        reference_charge=None,
        auxiliary_sample_error=None,
        input_tau_multipliers=None,
        external_edges=None,
        acquisition_response=None,
        ra_initial_response=None,
    ):
        """冻结制造／绑定参数，未绑定路径不能静默开启。"""
        if not np.isclose(config.period_s, 1 / experiment.fs_hz, rtol=1e-12, atol=0):
            raise ValueError("dynamic period must match sample rate")
        if config.crosstalk and coupling is None:
            raise ValueError("UNBOUND_CROSSTALK: provide physical input projection")
        if config.reference and (reference is None or reference_charge is None):
            raise ValueError(
                "UNBOUND_REFERENCE: provide coarse/fine network and actual charge mapping"
            )
        if (
            config.reference
            and reference.auxiliary_domain == "external_trace"
            and auxiliary_sample_error is None
        ):
            raise ValueError("UNBOUND_AUXILIARY: external sampled reference error is required")
        if not callable(getattr(drive, "value", None)) or not callable(
            getattr(drive, "track", None)
        ):
            raise ValueError("continuous drive must provide value and track interfaces")
        if (
            config.reference
            and reference.ra_initialization == "external_trace"
            and ra_initial_response is None
        ):
            raise ValueError("UNBOUND_RA_INITIAL: provide the analog initial-state response")
        self.config, self.experiment = config, experiment
        self.drive = drive
        self.physical = ChargePool.from_caps(caps, experiment)
        # [假设] 静态权重只提供电容比，不提供开关R；这里明确固定R。
        # 该倍率不是工艺Ron提取，也不能当作论文的0.6 ps孔径失配。
        # 外供倍率时覆盖全部18片，避免只覆盖被选八片后丢失物理归属。
        # 子节点的完整耦合极点仍需外部acquisition_response替代本近似。
        self.tau_multiplier = caps.main.sum(axis=1) / (32 * experiment.unit_cap_f)
        if input_tau_multipliers is not None:
            a = np.asarray(input_tau_multipliers, dtype=float)
            if a.shape != (18,) or not np.all(np.isfinite(a)) or np.any(a <= 0):
                raise ValueError("input_tau_multipliers require 18 finite positive values")
            self.tau_multiplier = a.copy()
        self.tau_multiplier.setflags(write=False)
        # 公共驱动稳态传递单独计算，不能把公共滤波器误差随机分配给18片。
        self.acquisition_response = acquisition_response
        self.ra_initial_response = ra_initial_response
        if config.driver_tau_s == 0:
            self.filtered_drive = drive
        elif isinstance(drive, SineDrive):
            w = 2 * np.pi * drive.frequency_hz
            h = 1 / (1 + 1j * w * config.driver_tau_s)
            self.filtered_drive = SineDrive(
                drive.peak_v * abs(h),
                drive.frequency_hz,
                drive.offset_v,
                drive.phase_rad + np.angle(h),
            )
        elif isinstance(drive, RampDrive):
            self.filtered_drive = RampDrive(
                drive.offset_v - drive.slope_v_per_s * config.driver_tau_s, drive.slope_v_per_s
            )
        else:
            raise ValueError("external drive must include its driver response; set driver_tau_s=0")
        self.external_edges = external_edges
        self.coupling = coupling
        self.coefficients = {}
        for name, shape in WIRE_SHAPES.items():
            if config.crosstalk:
                if set(coupling.coefficients) != set(WIRE_SHAPES):
                    raise ValueError("coupling must bind exactly the declared physical wire groups")
                a = np.asarray(coupling.coefficients[name], dtype=float)
                if a.shape != shape or not np.all(np.isfinite(a)):
                    raise ValueError("invalid coupling matrix shape or value")
                self.coefficients[name] = a.copy()
                self.coefficients[name].setflags(write=False)
            else:
                self.coefficients[name] = np.zeros(shape)
        self.bits = {k: np.zeros(v, dtype=np.int8) for k, v in WIRE_SHAPES.items()}
        self.victim = CoupledNode(
            coupling.capacitance_f if coupling else 1.0, coupling.tau_s if coupling else 0.0
        )
        self.pending_edge = None
        self.held = np.full(18, config.input_initial_v)
        # 两个交织SADC各有初态；第n笔复用n−2笔量化器，不是n−1笔。
        self.sadc_held = np.full(2, config.input_initial_v)
        self.reference = SwitchedReference(reference) if config.reference else None
        self.reference_binding = reference
        self.reference_charge = reference_charge
        self.auxiliary_sample_error = auxiliary_sample_error
        self.sample_index = 0
        self.last_trace = {}
        beta = caps.bridge / (caps.bridge + caps.sub.sum(axis=1))
        self.dummy_weight = (
            beta
            * caps.sub[:, -1]
            / (8 * 32 * experiment.unit_cap_f)
            * 2
            * experiment.full_scale_peak_v
        )

    def _edge(self, name, ids, value):
        """按物理位差分取有符号电荷；不会因槽序变化凭空产生物理翻转。"""
        delta = value.astype(np.int8) - self.bits[name][ids]
        self.bits[name][ids] = value
        charge = np.sum(self.coefficients[name][ids] * delta) * (
            self.coupling.vdd_v if self.coupling else 0.0
        )
        return float(charge), int(np.count_nonzero(delta))

    # =============================================================================
    # 2. 输入孔径、当前转换、下一ADC2孔径
    # =============================================================================
    def capture(
        self, input_v, word, plan, phase, thresholds, step_v, table, cfg, noise=None, *, strict=True
    ):
        """返回同笔转换状态、粗码、输入等效残差及ADC2原码。

        输入时间为全局index*T；ADC2观测属于该笔但发生在(index+1)*T。
        参考状态可解析前推到下一孔径；输入串扰节点只处理已到达孔径之前的事件。
        同一实例可连续分块，不能每块复位；模拟无效原码为-1并保存有效掩码。
        strict异常前该块已推进，不应在原实例重复提交。

        Args:
            input_v: (N,)输入电压V，必须与连续源在全局孔径的值一致。
            word: (N,)六位扰动字；使用原静态decode的合法性检查。
            plan: (N,8)有序物理片号，单笔不得重复，范围0至17。
            phase: 两个(N,)整数数组，水平相位0至14、垂直相位0至15。
            thresholds: 有序SADC阈值V，沿用静态码本的调用约定。
            step_v: 扰动基本步长V，沿用静态电荷网络的定义。
            table: 已构建的主／子阵列候选映射，与制造池拓扑必须一致。
            cfg: ADC2位数和输入折算半窗口V，本模型不推导物理RA增益。
            noise: 可选静态路径噪声序列；不能作为串扰或参考建立的替代。
            strict: 为真时任意前端或ADC2拒绝均抛错；为假时保留-1哨兵。

        Returns:
            同笔状态、(N,)粗码、(N,)输入折算残差V、(N,)ADC2整数原码。

        Raises:
            ValueError: 源／时间不一致、控制非法或strict下存在无效样本。
        """
        c = self.config
        x = np.asarray(input_v, dtype=float)
        plan = np.asarray(plan)
        word = np.asarray(word)
        if x.ndim != 1 or not np.all(np.isfinite(x)):
            raise ValueError("input must be a finite vector")
        n = len(x)
        if plan.shape != (n, 8) or word.shape != (n,):
            raise ValueError("invalid sample/control shapes")
        if (
            plan.dtype.kind not in "iu"
            or np.any((plan < 0) | (plan >= 18))
            or np.any(np.diff(np.sort(plan, axis=1), axis=1) == 0)
        ):
            raise ValueError("invalid physical slice IDs")
        for a, limit in zip(phase, (15, 16), strict=True):
            a = np.asarray(a)
            if a.shape != x.shape or a.dtype.kind not in "iu" or np.any((a < 0) | (a >= limit)):
                raise ValueError("invalid DEM phase")
        times = (self.sample_index + np.arange(n)) * c.period_s
        if not np.allclose(x, self.drive.value(times), rtol=0, atol=1e-12):
            raise ValueError("continuous source/time mismatch")
        if not c.enabled and strict:
            from task2.eight.experiment import _capture

            out = _capture(
                x, word, plan, phase, thresholds, step_v, table, self.physical, cfg, noise
            )
            self.sample_index += n
            self.last_trace = {
                "input_sample_time_s": times,
                "adc2_sample_time_s": times + c.period_s,
                "valid": np.ones(n, dtype=bool),
            }
            return out
        terms = decode(word)
        codes = np.zeros(n, dtype=np.int64)
        command = np.zeros(n, dtype=np.int64)
        residual = np.zeros(n)
        adc = np.full(n, -1, dtype=np.int64)
        names = [
            "input_error_v",
            "crosstalk_sample_v",
            "late_edge_charge_c",
            "digital_edges",
            "reference_error_v",
            "reference_sample_error_v",
            "ideal_residue_v",
            "coarse_charge_p_c",
            "coarse_charge_n_c",
            "fine_charge_p_c",
            "fine_charge_n_c",
            "reference_start_p_v",
            "reference_start_n_v",
            "reference_end_p_v",
            "reference_end_n_v",
        ]
        trace = {k: np.zeros(n) for k in names}
        trace.update(
            input_sample_time_s=times,
            adc2_sample_time_s=times + c.period_s,
            next_input_sample_time_s=times + c.period_s,
            valid=np.zeros(n, dtype=bool),
            frontend_valid=np.zeros(n, dtype=bool),
        )
        peak = self.experiment.full_scale_peak_v
        active_noise = noise is not None and not noise.is_quiet
        for row, t in enumerate(times):
            ids = plan[row]
            sadc_index = (self.sample_index + row) & 1
            # 本采集组在上一临界孔径之后开始跟踪；当前转换边沿尚未发生。
            select = np.zeros(18, dtype=np.int8)
            select[ids] = 1
            sample = ((terms.sample_sub_high[row, :, None] >> np.arange(5)) & 1).astype(np.int8)
            q1, e1 = self._edge("select", np.arange(18), select)
            q2, e2 = self._edge("sample", ids, sample)
            q3, e3 = self._edge(
                "word", np.arange(6), ((int(word[row]) >> np.arange(6)) & 1).astype(np.int8)
            )
            event = t - c.period_s + c.quiet_guard_s
            events = [(event, q1 + q2 + q3 if c.crosstalk else 0.0)]
            if self.pending_edge is not None:
                events.append(self.pending_edge)
            if c.crosstalk and self.external_edges is not None:
                for offset, charge in self.external_edges(self.sample_index + row):
                    if not 0 <= offset < c.period_s:
                        raise ValueError(
                            "external edge must be inside the previous sampling interval"
                        )
                    events.append((t - c.period_s + offset, charge))
            for event_time, charge in sorted(events):
                self.victim.inject(event_time, charge)
            coupled = self.victim.advance(t) if c.crosstalk else 0.0
            if c.acquisition and self.acquisition_response is not None:
                sampled, sadc = self.acquisition_response(
                    self.sample_index + row,
                    {
                        "time_s": t,
                        "start_s": t - c.acquisition_s,
                        "slice_ids": ids.copy(),
                        "initial_v": self.held[ids].copy(),
                        "sadc_initial_v": float(self.sadc_held[sadc_index]),
                        "target_v": x[row],
                    },
                )
                sampled = np.asarray(sampled, dtype=float)
                if (
                    sampled.shape != (8,)
                    or not np.all(np.isfinite(sampled))
                    or not np.isfinite(sadc)
                ):
                    raise ValueError("invalid bound acquisition response")
            else:
                sampled = (
                    self.filtered_drive.track(
                        self.held[ids], t, c.acquisition_s, c.input_tau_s * self.tau_multiplier[ids]
                    )
                    if c.acquisition
                    else np.full(8, x[row])
                )
                sadc = (
                    float(
                        self.filtered_drive.track(
                            self.sadc_held[sadc_index], t, c.acquisition_s, c.input_tau_s
                        )
                    )
                    if c.acquisition
                    else x[row]
                )
            ktc = noise.sampling_v[row] if active_noise else 0.0
            sampled = sampled + coupled + ktc
            sadc += coupled + ktc
            self.held[ids] = sampled
            self.sadc_held[sadc_index] = sadc
            sensed = sadc + (noise.comparator_v[row] if active_noise else 0.0)
            raw = int(
                np.searchsorted(
                    thresholds, sensed - terms.lower_eighth[row] * step_v / 8, side="right"
                )
                - 1
            )
            k = raw - int(terms.upper[row])
            okay = -peak <= x[row] < peak and 0 <= raw < 512 and 0 <= k < 512
            codes[row] = raw
            command[row] = k if okay else 0
            pos = int(phase[1][row]) * 8 + int(phase[0][row]) % 8 if table.bridge else 0
            main = ((table.main[command[row], pos, :, None] >> np.arange(17)) & 1).astype(np.int8)
            sub = ((table.sub[command[row], pos, :, None] >> np.arange(5)) & 1).astype(np.int8)
            w = self.physical.weight[ids]
            g = self.physical.gain[ids]
            r = float(
                np.sum(g * (sampled + peak))
                - np.sum(w * np.concatenate([main, sub[:, :4] + sample[:, :4]], axis=1))
            )
            trace["input_error_v"][row] = np.sum(g * (sampled - x[row]))
            trace["crosstalk_sample_v"][row] = coupled
            trace["ideal_residue_v"][row] = r
            if okay:
                qm, em = self._edge("main", ids, main)
                qs, es = self._edge("sub", ids, sub)
            else:
                qm = qs = 0.0
                em = es = 0
            # 排队而不现在注入，下一输入孔径才处理这个未来边沿。
            self.pending_edge = (t + c.command_s, qm + qs if c.crosstalk else 0.0)
            trace["late_edge_charge_c"][row] = qm + qs if c.crosstalk else 0.0
            trace["digital_edges"][row] = e1 + e2 + e3 + em + es
            if c.reference:
                context = {
                    "input_v": x[row],
                    "held_v": sampled.copy(),
                    "slice_ids": ids.copy(),
                    "main_bits": main.copy(),
                    "sub_bits": sub.copy(),
                    "sample_bits": sample.copy(),
                    "command": int(command[row]),
                    "word": int(word[row]),
                }
                q = self.reference_charge(self.sample_index + row, context) if okay else []
                aux = (
                    0.0
                    if self.reference_binding.auxiliary_domain == "independent"
                    else float(self.auxiliary_sample_error(self.sample_index + row, context))
                )
                if not np.isfinite(aux):
                    raise ValueError("invalid bound auxiliary error")
                conversion = np.concatenate([main, sub[:, :4]], axis=1)
                span = self.reference_binding.reference_span_v
                sensitivity = (
                    np.array(
                        [
                            np.sum(w * conversion),
                            -np.sum(w * (1 - conversion)) - np.sum(self.dummy_weight[ids]),
                        ]
                    )
                    / span
                )
                before = r
                r, obs = self.reference.cycle(
                    q,
                    c,
                    r + aux,
                    sensitivity,
                    initial_ra_v=self.ra_initial_response(self.sample_index + row, context)
                    if self.ra_initial_response is not None
                    else None,
                )
                trace["reference_error_v"][row] = r - before
                trace["reference_sample_error_v"][row] = aux
                for prefix, field in [
                    ("reference_start", "internal_start_v"),
                    ("reference_end", "internal_end_v"),
                    ("coarse_charge", "coarse_demand_c"),
                    ("fine_charge", "fine_source_charge_c"),
                ]:
                    trace[prefix + "_p_" + ("c" if "charge" in prefix else "v")][row] = obs[field][
                        0
                    ]
                    trace[prefix + "_n_" + ("c" if "charge" in prefix else "v")][row] = obs[field][
                        1
                    ]
            if active_noise:
                r += noise.offset_v + noise.conversion_v[row]
            residual[row] = r
            valid = okay and np.isfinite(r) and -cfg.adc2_half_range_v <= r < cfg.adc2_half_range_v
            trace["frontend_valid"][row] = okay
            trace["valid"][row] = valid
            if valid:
                adc[row] = min(
                    (1 << cfg.adc2_bits) - 1,
                    int(
                        np.floor(
                            (r + cfg.adc2_half_range_v)
                            * (1 << cfg.adc2_bits)
                            / (2 * cfg.adc2_half_range_v)
                        )
                    ),
                )
        self.sample_index += n
        self.last_trace = trace
        if strict and not trace["valid"].all():
            raise ValueError(
                f"dynamic capture rejected {np.count_nonzero(~trace['valid'])} samples"
            )
        return (
            conversion_state(command, plan, phase, terms, thresholds, step_v, table),
            codes,
            residual,
            adc,
        )
