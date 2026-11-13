# =============================================================================
# 文件名     : reference_buffer.py
# 模块名     : adi_model.control.reference_buffer
# 版本       : v1.0
# 功能       : 按 US10826519B1 FIG.5 的拓扑语义实现参考缓冲器的可核对判据。
#
# 设计说明   :
#   * 依据是 [14] 的 FIG.5（Sheet 5 of 16，已入库核对，图面清晰可读）：
#       MN1 栅 = Vref（外部参考）；MN2 栅 = Vref_internal（缓冲输出）
#       MP1/MP2 为 VDD1 上的电流镜 ⇒ 强制两支路电流相等
#       MP3 的栅由 INV 驱动，输出 Vref_internal
#       MN3 以 Vbias 作尾电流源接 GND
#       Threshold_adjust 与 Trial<N:1> 进入右侧调节框（图上未展开）
#   * 由此得到的**语义**（而不是猜测的具体数值）：
#       ① 这是**闭环单位增益**缓冲器 —— Vref_internal 反馈到 MN2，
#          因此闭环增益 ≈ 1，输出跟随 Vref；
#       ② 电流镜使两支路电流相等 ⇒ 输出电流受尾电流 MN3 限制，
#          这决定了它能给多少参考电荷（"低功耗参考"标题的含义）；
#       ③ Threshold_adjust / Trial<N:1> 说明比较阈值可调，且试验序列会
#          改动参考 —— 即参考不是绝对静止的。
#   * 本模块只做**判据与账目**，不改变 `behavioral/reference.py` 的建立模型：
#     后者算"多快建立"，这里算"结构与容量是否自洽"。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：从 FIG.5 提取三条不变式（闭环增益、尾电流上限、参考可变）。
# =============================================================================

"""参考缓冲器（FIG.5）的结构判据与电荷账目。

单位：电压 V，电流 A，电荷 C，电容 F。

    ⚠️ **本模块不参与主链仿真**：`src/` 内没有任何地方 import 它。
    用途是把专利结构约束写成可核对、可测试的判据；是否接入主链属设计选择，
    需人决定（原件未给出本芯片是否采用该机制）。当前唯一使用者是
    `tests/unit/test_reference_buffer.py`。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError


# =============================================================================
# 1. 闭环跟踪（① 单位增益）
# =============================================================================
def closed_loop_output(*, vref_v: float, gain: float = 1.0, offset_v: float = 0.0) -> float:
    """FIG.5 的闭环输出：Vref_internal = gain·Vref + offset。

    Args:
        vref_v: 外部参考电压 [V]。
        gain: 闭环增益（无量纲）。FIG.5 是单位增益反馈，应取 1.0；
            给其它值只用于做灵敏度对照，不表示原件如此。
        offset_v: 闭环失调 [V]。

    Returns:
        缓冲器输出 [V]。

    Raises:
        ConfigError: 参数非有限，或 gain 不在 (0, 2] 之外（超出即不是"跟随"）。

    Side effects: 无。
    """
    if not np.isfinite(vref_v) or not np.isfinite(offset_v):
        raise ConfigError("vref_v and offset_v must be finite")
    if not np.isfinite(gain) or not 0.0 < gain <= 2.0:
        raise ConfigError("closed-loop gain must be in (0, 2]; FIG.5 is unity-gain feedback")
    return float(gain) * float(vref_v) + float(offset_v)


def tracking_error_v(*, vref_v: float, **kwargs) -> float:
    """输出相对 Vref 的跟随误差 [V]（单位增益时应等于 offset_v）。"""
    return closed_loop_output(vref_v=vref_v, **kwargs) - float(vref_v)


# =============================================================================
# 2. 尾电流决定的可搬运电荷上限（②）
# =============================================================================
@dataclass(frozen=True)
class ReferenceChargeBudget:
    """一段时间内缓冲器能给的最大参考电荷。

    Attributes:
        tail_current_a: MN3 的尾电流 [A]。
        available_c: 时间窗口内可搬运的电荷上界 [C]。
        required_c: 实际负载需要的电荷 [C]。
    """

    tail_current_a: float
    available_c: float
    required_c: float

    @property
    def margin_c(self) -> float:
        """余量 [C]；为负表示参考会被拉塌。"""
        return self.available_c - self.required_c

    @property
    def sufficient(self) -> bool:
        """是否够用（严格判定，不给"差不多"的余量）。"""
        return self.available_c >= self.required_c


def reference_charge_budget(
    *, tail_current_a: float, window_s: float, load_cap_f: float, swing_v: float
) -> ReferenceChargeBudget:
    """按 FIG.5 的电流镜结构核算参考电荷是否够用。

    FIG.5 里 MP1/MP2 是电流镜、MN3 是尾电流源，因此缓冲器输出的**总电流**
    受尾电流限制。它一次能给负载的电荷上限就是 `I_tail × 时间窗口`。

    Args:
        tail_current_a: 尾电流 [A]，>0。
        window_s: 允许的充放电时间 [s]，>0。
        load_cap_f: 负载等效电容 [F]，≥0。
        swing_v: 负载需要摆动的电压 [V]，≥0。

    Returns:
        ReferenceChargeBudget。

    Raises:
        ConfigError: 任一参数非有限或越界。

    Side effects: 无；纯乘法，可复算。
    """
    for name, value in (
        ("tail_current_a", tail_current_a),
        ("window_s", window_s),
        ("load_cap_f", load_cap_f),
        ("swing_v", swing_v),
    ):
        if not np.isfinite(value):
            raise ConfigError(f"{name} must be finite")
    if tail_current_a <= 0:
        raise ConfigError("tail_current_a must be positive [A]")
    if window_s <= 0:
        raise ConfigError("window_s must be positive [s]")
    if load_cap_f < 0 or swing_v < 0:
        raise ConfigError("load_cap_f and swing_v must be nonnegative")
    return ReferenceChargeBudget(
        tail_current_a=float(tail_current_a),
        available_c=float(tail_current_a) * float(window_s),
        required_c=float(load_cap_f) * float(swing_v),
    )


# =============================================================================
# 3. 参考不是绝对静止（③ Threshold_adjust / Trial<N:1>）
# =============================================================================
def reference_is_static(*, threshold_adjust_enabled: bool, trial_count: int) -> bool:
    """判断"能否把参考当作静止常数"。

    FIG.5 右侧的调节框有 `Threshold_adjust` 与 `Trial<N:1>` 两个输入，
    说明比较阈值可调、试验序列会改动参考。因此：

    * `Threshold_adjust` 使能 ⇒ 参考不可当作静止（随时可能被调）；
    * `Trial<N:1>` 的 N>1 ⇒ 一次转换里有多次试验，参考在转换内会动。

    这条判据的用途：如果调用方假设"参考整笔恒定"，本函数应返回 False，
    提醒他该假设与原件结构冲突。

    Args:
        threshold_adjust_enabled: 阈值调节是否使能。
        trial_count: 试验次数 N（≥1）。

    Returns:
        True 表示可以把参考当静止常数。

    Raises:
        ConfigError: trial_count 非正整数。

    Side effects: 无。
    """
    if isinstance(trial_count, bool) or not isinstance(trial_count, int | np.integer):
        raise ConfigError("trial_count must be an integer")
    if trial_count < 1:
        raise ConfigError("trial_count must be at least 1")
    if not isinstance(threshold_adjust_enabled, bool):
        raise ConfigError("threshold_adjust_enabled must be an explicit bool")
    return (not threshold_adjust_enabled) and int(trial_count) == 1


# =============================================================================
# 4. 共享 RA 的"共模误差"判据（PPT 第 11 页 "Matched offset and gain error"）
# =============================================================================
def shared_amplifier_error_is_common_mode(*, per_phase_error_v: np.ndarray) -> bool:
    """判据：共享 RA/ADC2 时，各相的失调与增益误差必须是**共模**的。

    依据：PPT 第 11 页逐字 ——
        "Shared residue amplifier / 40% of ADC power consumption /
         **Matched offset and gain error** / Shared ADC2"

    RA 与 ADC2 被 RDAC1/RDAC2 两相**共享**，因此它带来的失调与增益误差
    对两相是**同一个**误差。这与"每相各自独立的失配"在效果上完全不同：

    * 共模误差：整体平移/缩放传递函数，**不产生交织杂散**；
    * 独立误差：两相传递函数不同 ⇒ 会产生交织杂散与相关杂散。

    Args:
        per_phase_error_v: (P,) 各相的误差（失调或增益误差折到输入端，任意单位）。

    Returns:
        True 表示可以视为共模（各相最大差为 0）。

    Raises:
        ConfigError: 非一维、为空或含非有限值。

    Side effects: 无。

    注意：本判据检验的是**模型是否按"共享"建模**。若模型让每相各有独立 RA
    失调，这里会返回 False —— 那是与原件结构不符，不是精度问题。
    """
    values = np.asarray(per_phase_error_v, dtype=float)
    if values.ndim != 1 or values.size < 1:
        raise ConfigError("per_phase_error_v must be a nonempty one-dimensional array")
    if np.any(~np.isfinite(values)):
        raise ConfigError("per-phase errors must be finite")
    return bool(np.ptp(values) <= 32 * np.finfo(float).eps * max(1.0, float(np.abs(values).max())))
