"""共享残差放大器的显式节点模型。

输入 residue 为已保存残差的输入等效电压[V]；物理网络提供每笔正增益G=C_signal/C_feedback。
input_error_v 在同一放大器输入等效节点相加，随后共同乘 G；
普通逐笔 AZ 的输入等效误差由此进入原有输出限幅及后级量化之前；CAAZ 扩展不在此路径。

普通输出白噪声仅支持调用方显式选择的 gaussian_white 过程与输出 RMS[V]。
没有目标 DR/NSD 反推、AZ dB 倍率、自动清除 1/f 或固定增益回退。
ISSCC2024 讲稿 PDF11/34/35 支持共享 RA、名义增益和宽窄带操作；
本模块不实现 GMR/OTA、CBIAS、电路噪声传递或晶体管级建立。有限建立由独立模型负责。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from adi_model.config import Config


def _finite_scalar(value, name: str, *, positive: bool) -> float:
    """检查调用边界的显式标量，拒绝缺失、布尔、非有限及非法符号。"""
    if value is None:
        raise ValueError(f"{name} is required by ResidueAmplifier")
    if isinstance(value, bool | np.bool_) or np.ndim(value) != 0 or np.iscomplexobj(value):
        raise ValueError(f"{name} must be a finite real scalar")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite real scalar") from error
    if not np.isfinite(number) or (number <= 0 if positive else number < 0):
        relation = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be finite and {relation}")
    return number


class ResidueAmplifier:
    """放大显式残差/输入误差，加入明确的输出白噪声，并检查输出摆幅。

    cfg.ra_v_clip 是 RA 输出节点的正对称限值[V]，必须显式提供。输出噪声启用时，
    cfg.ra_out_noise_rms 与 cfg.ra_noise_distribution 必填；gaussian_white 仅定义所选数值过程，
    不表示器件噪声分布已经测量。
    """

    def __init__(self, cfg: Config):
        """冻结显式输出摆幅及启用噪声参数，不补预算或未知物理值。"""
        self.cfg = cfg
        self.clip_v = _finite_scalar(
            getattr(cfg, "ra_v_clip", None), "ra_v_clip [RA-output V]", positive=True
        )
        enabled = getattr(cfg, "ra_enable_noise", None)
        if type(enabled) is not bool:
            raise ValueError("ra_enable_noise must be an explicit bool")
        if enabled:
            self.noise_out = _finite_scalar(
                getattr(cfg, "ra_out_noise_rms", None),
                "ra_out_noise_rms [RA-output V RMS]",
                positive=False,
            )
            distribution = getattr(cfg, "ra_noise_distribution", None)
            if distribution != "gaussian_white":
                raise ValueError(
                    "active RA output noise requires explicit "
                    "ra_noise_distribution='gaussian_white'; other processes are not implemented"
                )
        else:
            self.noise_out = 0.0

    def gain_vector(self, c_active: np.ndarray, c_feedback_true: float) -> np.ndarray:
        """由实际物理网络计算 G=C_signal/C_feedback，不读取名义/拟合增益。

        c_active 为每笔活动网络的输入等效信号电容[F]，不含备用切片；
        c_feedback_true 为该物理网络的正反馈电容[F]。调用方负责来源和节点定义。
        """
        feedback = _finite_scalar(c_feedback_true, "c_feedback_true [F]", positive=True)
        if np.iscomplexobj(c_active):
            raise ValueError("c_active [F] must be finite and positive")
        capacitance = np.asarray(c_active, dtype=float)
        if np.any(~np.isfinite(capacitance)) or np.any(capacitance <= 0):
            raise ValueError("c_active [F] must be finite and positive")
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            try:
                gain = capacitance / feedback
            except FloatingPointError as error:
                raise ValueError("physical capacitance ratio is not representable") from error
        if np.any(~np.isfinite(gain)) or np.any(gain <= 0):
            raise ValueError("physical capacitance ratio must be finite and positive")
        return gain

    def evaluate(
        self,
        residue: np.ndarray,
        rng: np.random.Generator,
        g: np.ndarray | float,
        *,
        input_error_v: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """计算 G*(residue+input_error_v)+n_out 后限幅。

        Args:
            residue: (N,) 放大器残差输入等效电压 [V]，有限且非空。
            rng: 显式启用且 RMS 非零的输出噪声使用的 Generator。
            g: 正的显式物理增益，可为标量或与 residue 同形的 (N,) 数组，不接受 None 回退。
            input_error_v: 可选同形 (N,) 放大器输入等效误差 [V]，在乘 G 前相加。

        Returns:
            限幅后的 RA 输出 [V] 与逐笔达到或超过配置摆幅的布尔标志，所有样本保留。
            n_out 仅按显式启用的过程与输出节点 RMS 抽样。
        """
        if np.iscomplexobj(residue):
            raise ValueError("RA residue [input V] must be a finite one-dimensional record")
        residue = np.asarray(residue, dtype=float)
        if residue.ndim != 1 or not residue.size or np.any(~np.isfinite(residue)):
            raise ValueError("RA residue [input V] must be a finite one-dimensional record")
        if g is None or np.iscomplexobj(g) or np.asarray(g).dtype == np.bool_:
            raise ValueError("g requires the explicit finite positive physical gain")
        gain = np.asarray(g, dtype=float)
        if gain.shape not in ((), residue.shape) or np.any(~np.isfinite(gain)) or np.any(gain <= 0):
            raise ValueError("g must be a positive scalar or a same-shape physical gain record")
        if input_error_v is None:
            stored = residue
        else:
            if np.iscomplexobj(input_error_v):
                raise ValueError("input_error_v [RA-input V] must be finite and match residue")
            error = np.asarray(input_error_v, dtype=float)
            if error.shape != residue.shape or np.any(~np.isfinite(error)):
                raise ValueError("input_error_v [RA-input V] must be finite and match residue")
            with np.errstate(over="raise", invalid="raise"):
                try:
                    stored = residue + error
                except FloatingPointError as failure:
                    raise ValueError("RA input sum is not representable") from failure
        with np.errstate(over="raise", invalid="raise"):
            try:
                output = gain * stored
                if self.noise_out > 0:
                    if not isinstance(rng, np.random.Generator):
                        raise ValueError("active RA output noise requires an explicit RNG")
                    output = output + rng.normal(0.0, self.noise_out, residue.size)
            except FloatingPointError as failure:
                raise ValueError("RA output is not representable") from failure
        if np.any(~np.isfinite(output)):
            raise ValueError("RA output is not representable")
        saturation = np.abs(output) >= self.clip_v
        return np.clip(output, -self.clip_v, self.clip_v), saturation
