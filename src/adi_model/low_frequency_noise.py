"""显式有限频带 PSD 的平稳 Gaussian 数值状态。

调用方必须明确给出单边 S(f)=S0*fc/f 的参考节点、频带、分布和 RNG。
该数值工具不把整 ADC 的 NSD/corner 分配给某一器件，不从记录时长推导物理低频截止，
也不实现温度漂移或 AZ 电路。选择 gaussian 分布属于调用方的模型定义，
不是由 PSD 或论文 headline 唯一导出的器件统计。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._arrays import readonly as _readonly


@dataclass(frozen=True)
class BandLimitedFlicker:
    """可在同一物理时间轴复用的有限频带随机状态。"""

    frequency_hz: np.ndarray
    cosine_v: np.ndarray
    sine_v: np.ndarray
    white_density_v2_hz: float
    corner_hz: float
    low_hz: float
    high_hz: float
    reference_node: str
    distribution: str

    @classmethod
    def create(
        cls,
        white_density_v2_hz,
        corner_hz,
        low_hz,
        rng,
        *,
        high_hz,
        modes,
        reference_node,
        distribution,
    ):
        """按显式过程/频带抽取对数频率求积状态。

        S0 的单位为指定节点 V²/Hz；fc/fl/fh 为 Hz，满足0<fl<fh<=fc。modes 是数值求积分辨率，
        不是物理器件参数。所有输入均须明确给出；
        不自动使用1/观察时长、corner作为高截止或默认随机种子。
        """
        if not isinstance(reference_node, str) or not reference_node.strip():
            raise ValueError("reference_node is required for the supplied voltage PSD")
        if distribution != "gaussian":
            raise ValueError("explicit distribution='gaussian' is required by this generator")
        if not isinstance(rng, np.random.Generator):
            raise ValueError("an explicit RNG is required by the flicker generator")
        values = [white_density_v2_hz, corner_hz, low_hz, high_hz]
        if any(
            value is None
            or isinstance(value, bool | np.bool_)
            or np.ndim(value) != 0
            or np.iscomplexobj(value)
            for value in values
        ):
            raise ValueError("explicit finite S0, corner_hz, low_hz and high_hz are required")
        try:
            density, corner, low, high = (float(value) for value in values)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "explicit finite S0, corner_hz, low_hz and high_hz are required"
            ) from error
        if (
            not np.all(np.isfinite([density, corner, low, high]))
            or density < 0
            or not 0 < low < high <= corner
            or type(modes) is not int
            or modes < 4
        ):
            raise ValueError("flicker needs finite S0>=0, 0<low<high<=corner and modes>=4")
        band_log = np.log(high) - np.log(low)
        band_power = density * corner * band_log
        if not np.isfinite(band_power):
            raise ValueError("the supplied flicker band power is not representable")
        width = band_log / modes
        frequency = np.exp(np.log(low) + width * (np.arange(modes) + rng.random(modes)))
        sigma = np.sqrt(band_power / modes)
        return cls(
            _readonly(frequency),
            _readonly(rng.normal(0, sigma, modes)),
            _readonly(rng.normal(0, sigma, modes)),
            density,
            corner,
            low,
            high,
            reference_node.strip(),
            distribution,
        )

    @property
    def expected_variance_v2(self) -> float:
        """返回显式频带的连续积分[V²]，不将它当作器件实测方差。"""
        return float(
            self.white_density_v2_hz * self.corner_hz * (np.log(self.high_hz) - np.log(self.low_hz))
        )

    def at_times(self, time_s) -> np.ndarray:
        """在调用方给定的绝对时间[s]查询同一状态；不改变物理时钟。"""
        if np.iscomplexobj(time_s):
            raise ValueError("physical timestamps must be finite real seconds")
        time = np.asarray(time_s, dtype=float)
        if np.any(~np.isfinite(time)):
            raise ValueError("physical timestamps must be finite real seconds")
        output = np.empty(time.size)
        for start in range(0, time.size, 4096):
            with np.errstate(over="raise", invalid="raise"):
                try:
                    angle = 2 * np.pi * time.ravel()[start : start + 4096, None] * self.frequency_hz
                    output[start : start + 4096] = (
                        np.cos(angle) @ self.cosine_v + np.sin(angle) @ self.sine_v
                    )
                except FloatingPointError as error:
                    raise ValueError("PSD observation is not representable") from error
        if np.any(~np.isfinite(output)):
            raise ValueError("PSD observation is not representable")
        return output.reshape(time.shape)

    def at_adc_indices(self, sample_indices, sample_rate_hz: float) -> np.ndarray:
        """以显式采样索引/时钟查询；低速观察的抗混叠条件由调用方给定。"""
        index = np.asarray(sample_indices)
        if (
            not np.issubdtype(index.dtype, np.integer)
            or sample_rate_hz is None
            or isinstance(sample_rate_hz, bool | np.bool_)
            or not isinstance(sample_rate_hz, int | float | np.integer | np.floating)
            or np.ndim(sample_rate_hz) != 0
            or np.iscomplexobj(sample_rate_hz)
            or not np.isfinite(sample_rate_hz)
            or sample_rate_hz <= 0
        ):
            raise ValueError("ADC queries need integer indices and a positive physical clock")
        return self.at_times(index / sample_rate_hz)
