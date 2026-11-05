"""显式ADC2静态量化核：RA输出节点[V]到整数码及bin-center[V]。

当前实现的码制为uniform_midrise：code=floor((v-vmin)/delta)，decode=vmin+(code+0.5)*delta。
位数、窗口和端点约定全部由调用者提供，不从目标DR、G0或第一级位数反推。有限越界输入夹码并保留标志，
不丢样本。本核没有热噪声、阈值随机失配、采集带宽或器件级电容模型。
"""

from __future__ import annotations

import numpy as np

from .config import Config, ConfigError


class ADC2:
    """无记忆后端量化器；只检查本块实际消费的五个配置字段。"""

    def __init__(self, cfg: Config):
        """冻结显式静态量化合同，不要求其他块的电阻、电容或阈值。

        Args:
            cfg: 显式adc2_n_bits、adc2_v_min/max[V]、adc2_codebook和endpoint。

        Raises:
            ConfigError: 本块输入缺失或非法，或请求未实现码制。

        Side effects: 保存量化参数；不修改Config、不生成随机状态。
        """
        bad = cfg.adc2_violations()
        if bad:
            raise ConfigError("ADC2 cannot run:\n  - " + "\n  - ".join(bad))
        assert cfg.adc2_n_bits is not None
        assert cfg.adc2_v_min is not None and cfg.adc2_v_max is not None
        assert cfg.adc2_endpoint_policy is not None
        self.vmin = float(cfg.adc2_v_min)
        self.vmax = float(cfg.adc2_v_max)
        self.n_code = 2**cfg.adc2_n_bits
        self.step = (self.vmax - self.vmin) / self.n_code
        self.endpoint_policy = cfg.adc2_endpoint_policy

    def quantize_codes(self, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """量化有限输入并独立返回逐样本窗口标志。

        Args:
            v: 任意形状的RA输出节点电压[V]。

        Returns:
            同形状(int64原始码,bool溢出)。closed包含两端；
            half_open为[vmin,vmax)。exact vmax两种约定都夹到最后码，标志不同。

        Raises:
            ValueError: 非有限电压，不能解释为有效转换。

        Side effects: 无；输入不改写，饱和样本保留。
        """
        values = np.asarray(v, dtype=float)
        if np.any(~np.isfinite(values)):
            raise ValueError("ADC2 acquisition voltage must be finite [V]")
        over = (values < self.vmin) | (
            (values > self.vmax) if self.endpoint_policy == "closed" else (values >= self.vmax)
        )
        clipped = np.clip(values, self.vmin, self.vmax)
        code = np.floor((clipped - self.vmin) / self.step)
        return np.clip(code, 0, self.n_code - 1).astype(np.int64), over

    def decode_codes(self, code: np.ndarray) -> np.ndarray:
        """将合法原始整数码还原为名义bin-center电压[V]。

        Args:
            code: 任意形状整数数组，值域0..n_code-1。

        Returns:
            同形状RA输出节点电压[V]。

        Raises:
            ValueError: 非整数、负值或超范围码；不静默夹错误原始数据。

        Side effects: 无。
        """
        code = np.asarray(code)
        if (
            not np.issubdtype(code.dtype, np.integer)
            or np.any(code < 0)
            or np.any(code >= self.n_code)
        ):
            raise ValueError("ADC2 codes must be integers within the backend code range")
        return self.vmin + (code.astype(np.float64) + 0.5) * self.step

    def quantize(self, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回bin-center电压[V]与原始模拟窗口标志。

        Args:
            v: 任意形状的有限RA输出节点电压[V]。

        Returns:
            (解码电压,溢出)；二者均与输入同形状。

        Side effects: 无，不包含数字观察器或外部校正替身。
        """
        code, over = self.quantize_codes(v)
        return self.decode_codes(code), over
