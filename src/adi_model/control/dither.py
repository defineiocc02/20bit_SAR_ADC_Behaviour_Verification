"""Fig19 的有限静态等效端口，不是原芯片完整 dither 电容/开关网络。

US10505561 Fig19 的六位字分为 lower 与 upper。此工程选择负的模拟等效注入：
SADC 看 x-L，RDAC 保存 x-T，实际 RDAC 码 k=c-U，且 T=L+U*Delta。
原 SADC 视图 VD(c)+r+L 与实际 mask 视图 VD(k)+r+T 等价，不能再纠正一次 U。

L/T 从同一组显式 Q32 整数转 V；比例、负极性及目标端口等效均标为工程假设。
独立采样噪声、动态开关、完整 3D DEM 和 dither 电容自身失配不在此 v1 模型内。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model._arrays import readonly
from adi_model.control.dem import _integer_array
from adi_model.simulation.config import ConfigError, _finite_real

FIG19_FRACTIONAL_WEIGHTS_Q = (9586981, 4793490, 2396745)


@dataclass(frozen=True)
class Fig19StaticSpec:
    """显式名义电压、固定点单位、低位权及等效极性。

    Args:
        v_fs: 差分等效输入峰值 [V]，不是晶体管端口电压。
        step_q: 一个粗码步长的归一化 Q32 整数；9bit/±Vfs 时为 2**24。
        voltage_fraction_bits: 本 v1 共享接口明确为 32。
        fractional_lower_weights_q: Fig19七slice示例4:2:1除7，经ties-to-even量化的
            D3/D2/D1三项Q32权；本v1只接受(9586981,4793490,2396745)，不扩任意比例。
        equivalent_polarity: 显式选择 subtract；不把原图加号直接当物理电荷符号。
        provenance: 调用方记录的比例/目标绑定依据；不升级为原芯片披露。

    Side effects: 不制造器件、不生成随机字、不拟合系数。
    """

    v_fs: float
    step_q: int
    voltage_fraction_bits: int
    fractional_lower_weights_q: tuple[int, int, int]
    equivalent_polarity: str
    provenance: str

    def __post_init__(self):
        """在端口量派生前检查单位、比例与明确的等效极性。"""
        if not _finite_real(self.v_fs) or self.v_fs <= 0:
            raise ConfigError("Fig19 v_fs must be finite positive differential-equivalent V")
        if type(self.voltage_fraction_bits) is not int or self.voltage_fraction_bits != 32:
            raise ConfigError("Fig19 static v1 explicitly supports normalized Q32")
        if type(self.step_q) is not int or self.step_q != 2**24:
            raise ConfigError("Fig19 9bit full-scale step must be 2**24 in normalized Q32")
        try:
            weights = tuple(self.fractional_lower_weights_q)
        except TypeError as error:
            raise ConfigError("Fig19 requires three explicit fractional Q32 weights") from error
        if (
            len(weights) != 3
            or any(type(value) is not int or value <= 0 for value in weights)
            or sum(weights) != self.step_q
        ):
            raise ConfigError("Fig19 D3/D2/D1 explicit positive Q32 weights must sum to step_q")
        if weights != FIG19_FRACTIONAL_WEIGHTS_Q:
            raise ConfigError(
                "Fig19 v1 requires the explicit Q32 rounding of source-example 4:2:1/7"
            )
        if self.equivalent_polarity != "subtract":
            raise ConfigError("Fig19 static v1 requires explicit ASSUMED subtract polarity")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ConfigError("Fig19 static binding requires a nonempty provenance statement")
        object.__setattr__(self, "v_fs", float(self.v_fs))
        object.__setattr__(self, "fractional_lower_weights_q", weights)

    @property
    def coarse_step_v(self) -> float:
        """明确粗码步长 [V/code]。"""
        return self.v_fs * (self.step_q / 2**self.voltage_fraction_bits)

    def to_dict(self) -> dict:
        """导出工程绑定与单位，不宣称完整原芯片实现。"""
        return {
            "model": "FIG19_STATIC_EQUIVALENT_V1",
            "source": "US10505561 Fig19/PDF22 and description/PDF33; ISSCC2024 slides17-18",
            "source_reproduction": False,
            "v_fs_v": float(self.v_fs),
            "step_q": self.step_q,
            "voltage_fraction_bits": self.voltage_fraction_bits,
            "voltage_unit": "normalized V/Vfs signed Q32",
            "fractional_lower_weights_q": list(self.fractional_lower_weights_q),
            "fractional_ratio_scope": "Fig19 seven-slice example 4:2:1/7; Q32 rounding is an engineering choice",
            "equivalent_polarity": "ASSUMED_SUBTRACTIVE",
            "target_binding_grade": "ASSUMED_COMMON_VOLTAGE_PORT_EQUIVALENCE",
            "provenance": self.provenance,
            "original_code_view": "VD(c)+r+L",
            "actual_mask_view": "VD(k)+r+T; k=c-U; no second U correction",
            "unmodeled": [
                "complete source capacitor/switch network",
                "dither DAC mismatch and switching-noise covariance",
                "continuous acquisition/reference dynamics",
                "3D DEM combination",
            ],
        }


@dataclass(frozen=True)
class Fig19CommandTrace:
    """同笔最终码与完整 resolved-prefix 范围标志；不包含模拟真值。

    event_range_overflow 为 (N,E)；只检查实际整数 RDAC 命令域，不冒称逐事件模拟建立。
    重复 prefix 仍是独立事件。坏事件随异常保留，不夹码、不删样本。
    """

    coarse: np.ndarray
    rdac_code: np.ndarray
    prefix_before_upper: np.ndarray
    rdac_event_codes: np.ndarray
    final_range_overflow: np.ndarray
    event_range_overflow: np.ndarray

    def __post_init__(self):
        """冻结控制与逐事件标志，不在异常路径丢弃非法事务。"""
        for name in self.__dataclass_fields__:
            value = np.asarray(getattr(self, name))
            object.__setattr__(self, name, readonly(value, value.dtype))


class Fig19DitherRangeError(ConfigError):
    """非法实际 RDAC 事件；trace 保留全部原始码与越界标志。"""

    def __init__(self, trace: Fig19CommandTrace):
        """保留完整拒绝证据，并给出首个非法样本/事件位置。"""
        self.trace = trace
        bad = np.argwhere(trace.event_range_overflow)
        first = bad[0].tolist() if len(bad) else None
        super().__init__(
            "Fig19 adjusted RDAC command is outside the explicit code range; "
            f"first invalid [sample,event]={first}; no clipping or event dropping"
        )


@dataclass(frozen=True)
class Fig19DitherProgram:
    """逐 raw 的六位字及同源 Q32 lower/total 注入。

    使用 from_words 明确生成；word 排列为 {D6,D5,D4,D3,D2,D1}。
    U=4*D6+D4，Lq=2*D5*step_q+sum(D3/D2/D1*weight_q)，Tq=Lq+U*step_q。
    SADC/RDAC 模拟端分别使用 -L/-T，数字已知公共项使用 -T。
    """

    spec: Fig19StaticSpec
    words: np.ndarray
    lower_q: np.ndarray
    total_q: np.ndarray
    upper_code: np.ndarray

    def __post_init__(self):
        """独立校验实际六位字与全部派生量，防止端口/数字控制不一致。"""
        if not isinstance(self.spec, Fig19StaticSpec):
            raise ConfigError("Fig19 program requires an explicit Fig19StaticSpec")
        words = _integer_array(self.words, "Fig19 six-bit words", ndim=1)
        if words.size == 0 or np.any((words < 0) | (words > 63)):
            raise ConfigError("Fig19 words must be a nonempty integer vector in [0,63]")
        bits = (words[:, None] >> np.arange(5, -1, -1)) & 1
        upper = 4 * bits[:, 0] + bits[:, 2]
        lower = 2 * bits[:, 1] * self.spec.step_q + (
            bits[:, 3:] @ np.asarray(self.spec.fractional_lower_weights_q, dtype=np.int64)
        )
        total = lower + upper * self.spec.step_q
        for name, supplied, expected in (
            ("lower_q", self.lower_q, lower),
            ("total_q", self.total_q, total),
            ("upper_code", self.upper_code, upper),
        ):
            actual = _integer_array(supplied, "Fig19 " + name, ndim=1)
            if not np.array_equal(actual, expected):
                raise ConfigError(f"Fig19 {name} conflicts with its actual six-bit word")
            object.__setattr__(self, name, readonly(actual, np.int64))
        object.__setattr__(self, "words", readonly(words, np.int64))

    @classmethod
    def from_words(cls, words, *, spec: Fig19StaticSpec) -> Fig19DitherProgram:
        """按显式规格产生同源端口量；不生成隐藏随机字。

        Args:
            words: (N,) 无符号语义六位整数控制，不接受浮点取整或 bool。
            spec: 固定点单位、名义幅度、低位权与假设的明确绑定。

        Returns:
            不可变 program，含同一份 word 派生的 Lq/Tq/U。

        Raises:
            ConfigError: 字类型、码域、规格或派生量非法。
        """
        if not isinstance(spec, Fig19StaticSpec):
            raise ConfigError("Fig19 program requires Fig19StaticSpec")
        raw = _integer_array(words, "Fig19 six-bit words", ndim=1)
        if raw.size == 0 or np.any((raw < 0) | (raw > 63)):
            raise ConfigError("Fig19 words must be a nonempty integer vector in [0,63]")
        bits = (raw[:, None] >> np.arange(5, -1, -1)) & 1
        upper = 4 * bits[:, 0] + bits[:, 2]
        lower = 2 * bits[:, 1] * spec.step_q + bits[:, 3:] @ np.asarray(
            spec.fractional_lower_weights_q, dtype=np.int64
        )
        return cls(spec, raw, lower, lower + upper * spec.step_q, upper)

    @property
    def lower_v(self) -> np.ndarray:
        """SADC lower 幅度 [V]；实际等效注入为其负值。"""
        return readonly((self.lower_q / 2**self.spec.voltage_fraction_bits) * self.spec.v_fs)

    @property
    def total_v(self) -> np.ndarray:
        """RDAC total 幅度 [V]；实际等效注入及数字公共项为其负值。"""
        return readonly((self.total_q / 2**self.spec.voltage_fraction_bits) * self.spec.v_fs)

    @property
    def sadc_input_shift_q(self) -> np.ndarray:
        """加到真实 SADC 采样输入的归一化 Q32 控制，即 -Lq。"""
        return readonly(-self.lower_q, np.int64)

    @property
    def rdac_input_shift_q(self) -> np.ndarray:
        """加到真实 RDAC 采样底板的归一化 Q32 控制，即 -Tq。"""
        return readonly(-self.total_q, np.int64)

    @property
    def known_common_q(self) -> np.ndarray:
        """数字已知公共项，精确等于实际冻结的 RDAC 采样偏移码。"""
        return self.rdac_input_shift_q

    def commands(
        self, coarse, prefix_before_upper, *, code_min: int, code_max: int
    ) -> Fig19CommandTrace:
        """对实际 c/prefix 统一减 U，保留每个事件并拒绝非法范围。

        prefix 必须来自明确数字控制表，以该笔 c 结束；不从最终码猜失效/缺失的比较器事件。
        此函数不夹码；异常 trace 保存全部输入、调整后码及逐事件范围标志。
        """
        c = _integer_array(coarse, "Fig19 actual SADC codes", ndim=1)
        prefix = _integer_array(prefix_before_upper, "Fig19 resolved prefixes", ndim=2)
        if (
            type(code_min) is not int
            or type(code_max) is not int
            or (code_min, code_max) != (0, 511)
        ):
            raise ConfigError("Fig19 static v1 requires the explicit 512-code domain [0,511]")
        if np.any((c < 0) | (c > 511)) or np.any((prefix < 0) | (prefix > 511)):
            raise ConfigError("Fig19 raw c/prefix must be actual nine-bit resolved codes")
        if (
            c.shape != self.words.shape
            or prefix.shape[0] != len(c)
            or prefix.shape[1] == 0
            or not np.array_equal(prefix[:, -1], c)
        ):
            raise ConfigError("Fig19 actual prefix rows must match words and end at raw coarse c")
        k = c - self.upper_code
        events = prefix - self.upper_code[:, None]
        trace = Fig19CommandTrace(
            c,
            k,
            prefix,
            events,
            (k < code_min) | (k > code_max),
            (events < code_min) | (events > code_max),
        )
        if np.any(trace.final_range_overflow) or np.any(trace.event_range_overflow):
            raise Fig19DitherRangeError(trace)
        return trace


def complete_flash3_sar6_prefix_table() -> np.ndarray:
    """明确绑定当前 core 完整比较路径，返回只读 (512,7) resolved-prefix 表。

    当前 RTL P_BITS=9/P_SEED_BITS=3，七次 resolved 事件对应 phase2..8 的消费，重复码保留。
    此表只描述该完整 valid 序列；不推模拟时间、不替代 comparator valid/deadline 检查。
    与 sim/fixtures/fig19_ideal_ports/rdac_prefix_table.json 独立核对，不能用于其他 ADC 码制。
    """
    coarse = np.arange(512, dtype=np.int64)[:, None]
    clear = np.asarray([63, 31, 15, 7, 3, 1, 0], dtype=np.int64)
    return readonly(coarse & ~clear, np.int64)
