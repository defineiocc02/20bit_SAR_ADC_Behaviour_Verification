"""显式SADC阈值的静态数学核。

输入与阈值均为SADC采样节点[V]；N+1个阈值定义N个整数码仓。这只是searchsorted量化，
不包含逐位SAR时序、采样网络或随机失配生成。生产目标需要显式513个实际阈值；
不再把未知RDAC几何/桥接比或旧7+2读数当成SADC阈值来源。
热噪声、动态响应及dither绑定由各自真实输入接口负责。
"""

from __future__ import annotations

import numpy as np

from .config import Config, ConfigError


class SADC:
    """无记忆静态量化器，支持显式有限递增阈值的通用数学测试。"""

    def __init__(self, thresholds: np.ndarray, *, endpoint_policy: str):
        """冻结实际节点阈值，不生成偏移、增益或失配分布。

        Args:
            thresholds: (N+1,)有限严格递增实际阈值[SADC节点V]。
            endpoint_policy: 必须显式closed或half_open；后者上端点越界。

        Raises:
            ConfigError: 阈值形状/有限性/单调性或端点约定非法。

        Side effects: 拷贝并只读保存阈值，不修改输入、不消耗随机源。

        Source: 通用静态量化数学；N=512仅为当前目标的决策区间数。
        """
        try:
            if np.iscomplexobj(thresholds):
                raise ValueError("complex thresholds are not physical node voltages")
            values = np.array(thresholds, dtype=float, copy=True)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConfigError("SADC thresholds must be real finite voltages [V]") from exc
        if (
            values.ndim != 1
            or values.size < 2
            or not np.all(np.isfinite(values))
            or np.any(values[1:] <= values[:-1])
        ):
            raise ConfigError("SADC thresholds must be finite and strictly increasing [V]")
        if endpoint_policy not in ("closed", "half_open"):
            raise ConfigError("SADC endpoint_policy must be closed or half_open")
        values.setflags(write=False)
        self.thresholds = values
        self.n_code = values.size - 1
        self.endpoint_policy = endpoint_policy

    def convert_codes(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """转换并保留输入范围标志，所有有限越界样本均夹码返回。

        Args:
            x: 任意形状SADC输入节点电压[V]。

        Returns:
            同形状(int64粗码,bool越界)。内部阈值归入上侧码仓；closed包含首末端点，
            half_open只包含下端点，末端点均返回最后码。

        Raises:
            ValueError: 输入含NaN或无穷，不能作为转换。

        Side effects: 无；阈值及输入不改写。
        """
        values = np.asarray(x, dtype=float)
        if np.any(~np.isfinite(values)):
            raise ValueError("SADC input voltage must be finite [V]")
        over = (values < self.thresholds[0]) | (
            (values > self.thresholds[-1])
            if self.endpoint_policy == "closed"
            else (values >= self.thresholds[-1])
        )
        code = np.searchsorted(self.thresholds, values, side="right") - 1
        return np.clip(code, 0, self.n_code - 1).astype(np.int64), over

    def convert(self, x: np.ndarray) -> np.ndarray:
        """只取夹码粗码；需要范围诊断的调用者必须用convert_codes。

        Args:
            x: 任意形状SADC输入节点电压[V]。

        Returns:
            同形状int64粗码，范围0..n_code-1。

        Side effects: 无；未删除越界样本。
        """
        code, _over = self.convert_codes(x)
        return code


def build_first_stage_quantizer(cfg: Config) -> SADC:
    """从显式目标阈值构造9bit SADC，不消费其他块物理输入。

    Args:
        cfg: sadc_thresholds_v必须为513个实际节点阈值，端点约定必须显式。

    Returns:
        含512个码仓的独立SADC。

    Raises:
        ConfigError: 缺阈值、端点、错误决策位数或非法阈值。

    Side effects: 无；不从RDAC物理真值或名义端点产生阈值。
    """
    bad = cfg.sadc_violations()
    if type(cfg.b1) is not int or cfg.b1 != 9:
        bad.append("b1 must be 9 for the source-target SADC factory")
    if bad:
        raise ConfigError("SADC cannot run:\n  - " + "\n  - ".join(bad))
    assert cfg.sadc_thresholds_v is not None and cfg.sadc_endpoint_policy is not None
    return SADC(np.asarray(cfg.sadc_thresholds_v), endpoint_policy=cfg.sadc_endpoint_policy)
