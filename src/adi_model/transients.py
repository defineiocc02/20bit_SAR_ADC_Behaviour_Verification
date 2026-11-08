# =============================================================================
# 文件名     : transients.py
# 模块名     : adi_model.transients
# 版本       : v1.0
# 功能       : 输入有限建立、数字耦合与参考／RA连续响应的状态和解析核。
# 设计说明   :
#   [依据] ISSCC讲稿32页列出三种限制，33页指出RA期间参考仍在建立。
#   [边界] 以下单极点、耦合电容和时间参数是显式工程模型，不是原芯片网表。
#   [时序] 所有时间为秒，电容为F，状态电压为V；事件必须按时间递增。
#   [状态] 状态由调用方持有，分块处理不得偷偷复位；关闭路径返回理想响应。
# 变更记录   : 增加三条动态路径；不修改静态模型的默认行为。
# =============================================================================

"""有单位、可独立验证的连续响应核。"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite

import numpy as np


# =============================================================================
# 1. 配置与连续输入
# =============================================================================
@dataclass(frozen=True)
class DynamicConfig:
    """原文约束下的周期配置；默认全关，不再猜未披露的绝对误差幅度。

    Attributes:
        period_s: 原始采样周期，默认100 ns，对应用户10 MS/s目标。
        acquisition: 启用RDAC与SADC输入跟踪。
        crosstalk: 启用输入端边沿投影；必须另外提供CouplingBinding。
        reference: 启用粗预充／精确参考交接；必须另外提供ReferenceBinding及负载。
        dac_bandwidth_multiple: RDAC单极点的标称带宽／Nyquist比，默认15取自讲稿19页。
            将此比例用于10 MS/s是缩放基准，不是180 nm实测带宽。
        driver_tau_s: 公共外部驱动单极点，默认0表示理想；与RDAC极点分开。
        command_fraction: 本周期最终转换事件位置，默认6/16，为工程节拍分配。
        ra_start_fraction: 精确参考切入与RA开始，默认8/16，为工程节拍分配。
        quiet_guard_s: 上一临界孔径之后再翻采集控制的延迟；默认0取右侧事件极限。
        input_initial_v: 首次跟踪前的保持电压V，默认0；全周期标称模型应抹去其记忆。

    没有独立的随机τ离散默认。固定R假设下的C比例来自实际制造池，或显式模拟绑定。
    ADC2采样严格在下一输入孔径；RA时长由这个等式确定，不能再独立填75 ns终点。
    """

    acquisition: bool = False
    crosstalk: bool = False
    reference: bool = False
    period_s: float = 100e-9
    dac_bandwidth_multiple: float = 15.0
    driver_tau_s: float = 0.0
    command_fraction: float = 6 / 16
    ra_start_fraction: float = 8 / 16
    quiet_guard_s: float = 0.0
    input_initial_v: float = 0.0

    def __post_init__(self):
        """校验绝对时间和相位次序；不允许半周期采集混入此原文缩放配置。"""
        values = [v for v in vars(self).values() if not isinstance(v, bool)]
        if not all(isfinite(v) for v in values):
            raise ValueError("dynamic parameters must be finite")
        if self.period_s <= 0 or self.dac_bandwidth_multiple <= 0 or self.driver_tau_s < 0:
            raise ValueError("invalid period or bandwidth")
        if not 0 < self.command_fraction < self.ra_start_fraction < 1:
            raise ValueError("conversion must precede RA, which ends at the next aperture")
        if not 0 <= self.quiet_guard_s < self.command_s:
            raise ValueError("acquisition control must precede the conversion event")

    @property
    def acquisition_s(self):
        """整周期可跟踪；有显式保护延迟时扣除该非重叠时间。"""
        return self.period_s - self.quiet_guard_s

    @property
    def input_tau_s(self):
        """单极点缩放基准τ=1/(2π BW_DAC)，不是器件提取结果。"""
        return self.period_s / (np.pi * self.dac_bandwidth_multiple)

    @property
    def command_s(self):
        """最终RDAC命令事件距当前孔径的时间，单位s。"""
        return self.command_fraction * self.period_s

    @property
    def ra_start_s(self):
        """粗预充结束、切精确参考的事件时间，单位s。"""
        return self.ra_start_fraction * self.period_s

    @property
    def ra_s(self):
        """RA一直响应到下一输入孔径，即ADC2的共享安静采样时刻。"""
        return self.period_s - self.ra_start_s

    @property
    def enabled(self):
        """只由三条路径的实际开关决定是否离开旧静态快路径。"""
        return self.acquisition or self.crosstalk or self.reference


@dataclass(frozen=True)
class SineDrive:
    """连续正弦源，不将孔径处正弦采样值冒充整个采集窗内的常数。

    peak_v、offset_v单位V，frequency_hz单位Hz，phase_rad单位rad。
    particular给出固定τ在正弦驱动下的精确特解；电压相关τ在每个窗冻结。
    """

    peak_v: float
    frequency_hz: float
    offset_v: float = 0.0
    phase_rad: float = 0.0

    def __post_init__(self):
        """拒绝非有限源和负幅度／频率，避免NaN绕过后级有效性检查。"""
        if (
            not all(isfinite(v) for v in vars(self).values())
            or min(self.peak_v, self.frequency_hz) < 0
        ):
            raise ValueError("invalid continuous sine source")

    def value(self, time_s):
        """返回指定时间的输入电压，标量或数组均可，单位V。"""
        return self.offset_v + self.peak_v * np.sin(
            2 * np.pi * self.frequency_hz * time_s + self.phase_rad
        )

    def track(self, initial_v, end_s, duration_s, tau_s):
        """解析积分整个采集窗；初值形状须能与τ广播，返回孔径电压V。"""
        tau = np.asarray(tau_s, dtype=float)
        if np.any(tau < 0):
            raise ValueError("input tau must not be negative")
        omega = 2 * np.pi * self.frequency_hz
        h = 1 / (1 + 1j * omega * tau)
        end = self.offset_v + np.imag(
            self.peak_v * np.exp(1j * (omega * end_s + self.phase_rad)) * h
        )
        start = self.offset_v + np.imag(
            self.peak_v * np.exp(1j * (omega * (end_s - duration_s) + self.phase_rad)) * h
        )
        decay = np.exp(-duration_s / np.maximum(tau, np.finfo(float).tiny))
        return np.where(tau == 0, self.value(end_s), end + (initial_v - start) * decay)


@dataclass(frozen=True)
class RampDrive:
    """连续线性源：offset_v + slope_v_per_s*t，不在模型内隐藏回绕。"""

    offset_v: float
    slope_v_per_s: float

    def __post_init__(self):
        """斜率可以为负，但源参数必须有限；负斜坡也保留真实方向。"""
        if not all(isfinite(v) for v in vars(self).values()):
            raise ValueError("invalid continuous ramp source")

    def value(self, time_s):
        """返回连续斜坡电压V；重复斜坡由调用方明确安排边界和预热。"""
        return self.offset_v + self.slope_v_per_s * time_s

    def track(self, initial_v, end_s, duration_s, tau_s):
        """线性输入的一阶ODE精确解，保留初始电压及有限跟踪时间。"""
        tau = np.asarray(tau_s, dtype=float)
        if np.any(tau < 0):
            raise ValueError("input tau must not be negative")
        end = self.value(end_s) - self.slope_v_per_s * tau
        start = self.value(end_s - duration_s) - self.slope_v_per_s * tau
        decay = np.exp(-duration_s / np.maximum(tau, np.finfo(float).tiny))
        return np.where(tau == 0, self.value(end_s), end + (initial_v - start) * decay)


@dataclass(frozen=True)
class ChoppedDrive:
    """在采集窗起点换相的连续源适配器，支持后续与CAAZ同笔配对。

    base提供连续源；period_s为原始采样周期，acquisition_s为孔径前跟踪时间。
    保持电容初值不随输入开关自动翻转，因而保留换相后的真实建立压力。
    这里只定义输入极性，不模拟CBIAS自零、保持泄漏或完整CAAZ模拟宏。
    """

    base: SineDrive | RampDrive
    period_s: float
    acquisition_s: float

    def __post_init__(self):
        """换相边界必须早于孔径且位于当前周期内。"""
        if (
            not all(isfinite(x) for x in (self.acquisition_s, self.period_s))
            or not 0 < self.acquisition_s <= self.period_s
        ):
            raise ValueError("invalid chopping acquisition interval")

    def polarity(self, time_s):
        """返回采集窗起点已稳定的±1极性，不在孔径才突变。"""
        index = (
            np.ceil((np.asarray(time_s) + self.acquisition_s) / self.period_s - 1e-12).astype(
                np.int64
            )
            - 1
        )
        return 1 - 2 * (index % 2)

    def value(self, time_s):
        """返回换相后的连续源电压V。"""
        return self.polarity(time_s) * self.base.value(time_s)

    def track(self, initial_v, end_s, duration_s, tau_s):
        """在当前固定极性窗口解析跟踪，电容初值沿用其物理电压。"""
        if duration_s > self.acquisition_s:
            raise ValueError("tracking interval spans an unspecified chopping transition")
        sign = self.polarity(end_s)
        return sign * self.base.track(sign * initial_v, end_s, duration_s, tau_s)


# =============================================================================
# 2. 具有跨周期记忆的受扰节点和参考／RA响应
# =============================================================================
class CoupledNode:
    """数字边沿电荷注入节点；电压跳变为Q/C，随后按单极点恢复。

    系数必须逐线传入，正负表示差分耦合方向。真实芯片应由寄生矩阵替换它们。
    状态为采样节点电压V；tick按绝对时间推进，晚发生的边沿不能影响早先样本。
    """

    def __init__(self, capacitance_f, tau_s):
        """以正电容F和非负τ[s]创建零误差节点；首事件时间可以为负。"""
        if not all(isfinite(v) for v in (capacitance_f, tau_s)) or capacitance_f <= 0 or tau_s < 0:
            raise ValueError("invalid victim node")
        self.capacitance_f, self.tau_s = capacitance_f, tau_s
        self.voltage_v, self.time_s = 0.0, None

    def advance(self, time_s):
        """传播到指定时间；拒绝倒序事件，τ=0在事件之后立即恢复。"""
        if not isfinite(time_s):
            raise ValueError("event time must be finite")
        if self.time_s is not None:
            dt = time_s - self.time_s
            if dt < -1e-18:
                raise ValueError("digital event time moved backwards")
            if dt > 0:
                self.voltage_v *= exp(-dt / self.tau_s) if self.tau_s else 0.0
        self.time_s = time_s
        return self.voltage_v

    def inject(self, time_s, charge_c):
        """先恢复再注入有符号电荷C；无翻转时Q=0，不额外生成随机噪声。"""
        if not isfinite(charge_c):
            raise ValueError("injected charge must be finite")
        self.advance(time_s)
        self.voltage_v += charge_c / self.capacitance_f
        return self.voltage_v


def rail_recovery(error_v, duration_s, tau_s):
    """两轨参考误差的精确指数传播，输入输出V；无状态全局变量。"""
    if duration_s < 0 or tau_s < 0:
        raise ValueError("recovery duration/tau must not be negative")
    return np.asarray(error_v) * (exp(-duration_s / tau_s) if tau_s else 0.0)


def ra_endpoint(ideal_v, initial_v, reference_error_v, duration_s, ref_tau_s, ra_tau_s):
    """RA跟踪一个仍在指数恢复的参考误差，返回输入等效终点电压V。

    解析解对应 dy/dt=(ideal+A*exp(-t/τref)-y)/τra。
    两τ相等采用连续极限，不能用接近零的分母造成数值爆炸。
    这不是“先把参考完全建立再放大”，也不代表gmR+OTA真实多极点传递函数。
    """
    if min(duration_s, ref_tau_s, ra_tau_s) < 0:
        raise ValueError("RA times must not be negative")
    if duration_s == 0:
        return initial_v
    if ra_tau_s == 0:
        return ideal_v + reference_error_v * (exp(-duration_s / ref_tau_s) if ref_tau_s else 0.0)
    ea = exp(-duration_s / ra_tau_s)
    if ref_tau_s == 0:
        kernel = 0.0
    elif abs(ref_tau_s - ra_tau_s) <= 1e-7 * max(ref_tau_s, ra_tau_s):
        kernel = duration_s / ra_tau_s * ea
    else:
        kernel = ref_tau_s / (ref_tau_s - ra_tau_s) * (exp(-duration_s / ref_tau_s) - ea)
    return ideal_v + (initial_v - ideal_v) * ea + reference_error_v * kernel
