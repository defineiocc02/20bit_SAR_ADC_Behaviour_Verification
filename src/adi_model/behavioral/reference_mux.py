# =============================================================================
# 文件名     : reference_mux.py
# 模块名     : adi_model.behavioral.reference_mux
# 版本       : v1.0
# 功能       : 粗预充与精确REF_IN分离、在RA期间继续恢复的线性参考子模型。
# 设计说明   :
#   [依据] ISSCC论文1页、US10826519B1图1/2披露预充后切准参考的功能关系。
#   [边界] 这是显式R/C绑定的两节点等效，不冒充完整缓冲器或gmR＋OTA电路。
#   [电荷] SAR负载只加到内部预充节点；精确源仅在交接后提供剩余恢复电荷。
#   [参数] 不提供1 nF等猜测默认；所有C、R、预充响应和RA响应由调用方明确绑定。
# 变更记录   : 修复旧草案把全部转换电荷算给精确参考的问题。
# =============================================================================

"""有守恒校验的粗预充／精确参考交接核。
    **当前状态（2026-11-13 核实）：**
    * `src/` 内**没有模块 import 它**——主链用的是 `conversion.py` 的
      简化 τ 恢复模型（`dyn_ref_settling`），不是这里的完整两节点网络；
    * **有单测覆盖**：`tests/unit/test_reference_mux.py`（12 个用例），
      逐条验证本文档声称的物理不变量：电荷守恒、切换连续性、
      "SAR 电荷只进内部节点"、以及**矩阵指数解 vs 独立 RK4 积分**的交叉检查；
    * 它保留的原因是：A7（含动态参考的完整链路）因缺少
      `dyn_c_decouple`/`dyn_tau_ref` 的显式依据而 NOT_RUN，
      这里是一份**可核查的替代模型**，将来若有参数依据可直接接入。

    ⇒ 所以本模块**不是死代码**，但它也**不是主链依赖**。
    不要在 README 里把它算作"已在用"。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite

import numpy as np
from scipy.linalg import expm


@dataclass(frozen=True)
class ReferenceBinding:
    """必须显式绑定的线性参考参数，不能仅凭讲稿32页估出。

    Attributes:
        internal_cap_f: 内部参考节点等效储能C，含需建模的负载，单位F。
        coarse_tau_s: 粗预充驱动的等效恢复时间常数s；0表示理想预充。
        fine_pin_cap_f: 精确REF_IN引脚等效储能F，与内部节点C分开。
        fine_source_r_ohm: 精确源到引脚的等效电阻Ω。
        mux_r_ohm: 精确引脚到内部参考节点的开关／连线电阻Ω。
        ra_tau_s: 输入等效RA单极点s；0表示只观察参考终点。
        reference_span_v: 此参考端口的实际差分跨度V，不等同ADC输入10 V跨度。
        coarse_offset_v: 粗预充目标相对精确目标的两轨电压缺额V。
        source: 参数来源说明，如模拟提取记录或明确命名的数学单元例。
        auxiliary_domain: 必须明确为independent或external_trace。
            independent假定辅助注入有独立理想参考；否则必须外供采样误差序列。
        ra_initialization: previous_output沿用上笔RA输出；zero_reset明确假定每笔归零；
            external_trace要求逐笔提供RA起始电压V，不由模型推断电路复位方式。

    参数绑定是计算前提，不是自动验证来源真实或工艺验收通过。
    电路可能有更多节点或非线性，需以实际模拟响应替换这个等效核。
    """

    internal_cap_f: float
    coarse_tau_s: float
    fine_pin_cap_f: float
    fine_source_r_ohm: float
    mux_r_ohm: float
    ra_tau_s: float
    reference_span_v: float
    coarse_offset_v: tuple[float, float]
    source: str
    auxiliary_domain: str
    ra_initialization: str

    def __post_init__(self):
        """检查量纲范围与未绑定来源；不允许用零电阻造成奇异网络。"""
        positive = (
            self.internal_cap_f,
            self.fine_pin_cap_f,
            self.fine_source_r_ohm,
            self.mux_r_ohm,
            self.reference_span_v,
        )
        if not all(isfinite(v) and v > 0 for v in positive):
            raise ValueError("reference C/R/span must be finite and positive")
        if not all(isfinite(v) and v >= 0 for v in (self.coarse_tau_s, self.ra_tau_s)):
            raise ValueError("reference time constants must be finite and nonnegative")
        if len(self.coarse_offset_v) != 2 or not all(isfinite(v) for v in self.coarse_offset_v):
            raise ValueError("coarse offset must contain two finite voltages")
        if self.ra_initialization not in {"previous_output", "zero_reset", "external_trace"}:
            raise ValueError("RA initial-state policy must be explicitly bound")
        if not self.source.strip() or self.auxiliary_domain not in {
            "independent",
            "external_trace",
        }:
            raise ValueError("reference source and auxiliary domain must be explicitly bound")


class SwitchedReference:
    """两轨内部节点与精确引脚的跨周期状态，电压为相对理想参考的缺额V。

    每周期先接粗驱动，按外部事件序列抽取SAR电荷，ra_start时切到REF_IN。
    在精确连接阶段，内部C经Rmux接引脚C，引脚再经Rsource接理想源。
    RA在该网络仍恢复时响应，最终在下一输入孔径采样。
    """

    def __init__(self, binding: ReferenceBinding):
        """开始于零缺额；恢复历史由同一实例保存，不按数据块重置。"""
        self.binding = binding
        self.internal = np.zeros(2)
        self.pin = np.zeros(2)
        self.previous_output_v = 0.0

    def _coarse(self, duration_s):
        """粗驱动供内部节点；精确源只恢复自己的引脚，不承接SAR负载。"""
        b = self.binding
        a = exp(-duration_s / b.coarse_tau_s) if b.coarse_tau_s else 0.0
        target = np.asarray(b.coarse_offset_v)
        self.internal = target + (self.internal - target) * a
        self.pin *= exp(-duration_s / (b.fine_source_r_ohm * b.fine_pin_cap_f))

    def cycle(self, charge_events, config, residue_v, sensitivity, *, initial_ra_v=None):
        """执行一周期并返回RA终点及电荷观测，输入单位C、V和V/V。

        charge_events为(offset_s,两轨电荷C)序列，必须由实际位试探负载提供；不能用特征数冒充。
        保留位结果逐步发展的时间，不能把整笔电荷强行移到最后再估计参考误差。
        sensitivity是内部参考缺额到输入等效残差的有符号两轨Jacobian。
        正负轨方向已经包含在sensitivity中，不在此处再取绝对值。
        内部／引脚节点在切换时保持电压连续，有限Rmux下没有虚假的瞬时跳变。
        """
        b = self.binding
        if b.ra_initialization == "external_trace":
            if initial_ra_v is None or not isfinite(initial_ra_v):
                raise ValueError("external RA initial voltage must be bound")
            ra_initial = initial_ra_v
        else:
            ra_initial = self.previous_output_v if b.ra_initialization == "previous_output" else 0.0
        s = np.asarray(sensitivity, dtype=float)
        if s.shape != (2,) or not np.all(np.isfinite(s)):
            raise ValueError("reference sensitivity requires two finite rail values")
        cycle_initial_internal = self.internal.copy()
        cycle_initial_pin = self.pin.copy()
        q = np.zeros(2)
        previous_time = 0.0
        for time_s, charge in charge_events:
            charge = np.asarray(charge, dtype=float)
            if (
                not isfinite(time_s)
                or not previous_time <= time_s < config.ra_start_s
                or charge.shape != (2,)
                or not np.all(np.isfinite(charge))
            ):
                raise ValueError("reference charge events must be ordered inside the coarse phase")
            self._coarse(time_s - previous_time)
            self.internal += charge / b.internal_cap_f
            q += charge
            previous_time = time_s
        self._coarse(config.ra_start_s - previous_time)
        initial_internal, initial_pin = self.internal.copy(), self.pin.copy()
        # 四个参考状态加RA状态与常数1，直接求线性ODE的矩阵指数。
        # 同时求解避免“先建立参考再理想放大”的顺序错误，也无需猜单一τref。
        size = 6 if b.ra_tau_s else 4
        matrix = np.zeros((size, size))
        g, gs = 1 / b.mux_r_ohm, 1 / b.fine_source_r_ohm
        for rail in range(2):
            i, j = 2 * rail, 2 * rail + 1
            matrix[i, i], matrix[i, j] = -g / b.internal_cap_f, g / b.internal_cap_f
            matrix[j, i] = g / b.fine_pin_cap_f
            matrix[j, j] = -(g + gs) / b.fine_pin_cap_f
        if b.ra_tau_s:
            matrix[4, 0], matrix[4, 2] = s / b.ra_tau_s
            matrix[4, 4], matrix[4, 5] = -1 / b.ra_tau_s, residue_v / b.ra_tau_s
            initial = np.r_[
                self.internal[0], self.pin[0], self.internal[1], self.pin[1], ra_initial, 1.0
            ]
        else:
            initial = np.r_[self.internal[0], self.pin[0], self.internal[1], self.pin[1]]
        final = expm(matrix * config.ra_s) @ initial
        self.internal, self.pin = final[[0, 2]], final[[1, 3]]
        output = float(final[4]) if b.ra_tau_s else float(residue_v + s @ self.internal)
        self.previous_output_v = output
        # 由两节点电荷守恒取得精确源供电量，独立ODE测试另对电流积分交叉检查。
        fine_charge = b.internal_cap_f * (initial_internal - self.internal) + b.fine_pin_cap_f * (
            initial_pin - self.pin
        )
        return output, {
            "internal_start_v": initial_internal,
            "pin_start_v": initial_pin,
            "internal_end_v": self.internal.copy(),
            "pin_end_v": self.pin.copy(),
            "coarse_demand_c": q,
            "coarse_supplied_charge_c": q
            + b.internal_cap_f * (cycle_initial_internal - initial_internal),
            "fine_recovery_charge_c": b.fine_pin_cap_f * (cycle_initial_pin - initial_pin),
            "fine_source_charge_c": fine_charge,
        }


@dataclass(frozen=True)
class CouplingBinding:
    """外部绑定的输入端采样误差投影，不提供猜测的0.02 fF默认。

    coefficients以物理线组为键、有符号差分等效F数组为值；capacitance_f与tau_s
    定义已投影到输入采样误差的脉冲核。它不是完整门级串扰或ADC2端串扰网络。
    source必须交代参数来自寄生、模拟波形还是明确的单位数学例。
    """

    coefficients: dict
    capacitance_f: float
    tau_s: float
    vdd_v: float
    source: str

    def __post_init__(self):
        """只校验参数合法，不凭一个来源字符串升级为已提取／已验收。"""
        if not all(isfinite(x) for x in (self.capacitance_f, self.tau_s, self.vdd_v)):
            raise ValueError("coupling parameters must be finite")
        if self.capacitance_f <= 0 or self.tau_s < 0 or self.vdd_v <= 0 or not self.source.strip():
            raise ValueError("coupling physical scale and source must be bound")
