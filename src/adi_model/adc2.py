"""adc2.py -- 后端 ADC2：量化、余量裁剪与溢出统计。

============================================================================
物理模型与文献出处
============================================================================
论文 [00] 结构：两级 SAR，第一级（SADC + RDAC 跟随器 + RA）解出高位，
ADC2 解放大后的残差。本模块只模拟 ADC2 的**静态量化**：

    code = floor((v - vmin) / Δ2)，输出等效输入电压 = vmin + (code+0.5)·Δ2

* ADC2 是**单极性**口径：名义残差 r ∈ [0, Δ1]，RA 放大后 ∈ [0, G0·Δ1] = [0, 1.5 V]，
  配置范围 [adc2_v_min, adc2_v_max] = [-0.15, +1.65] V 提供上下各 ~10% 余量。
  余量的消费者：SADC/RDAC 匹配误差 × G（stage19 验收②）、RA 输出噪声、
  dither 残项。
* **观察器校正量不进 ADC2 量程**（v6.1 遗留缺陷的修复，见
  docs/adr/0006-observer-correction-is-digital.md）：
  KTC 观察支路的校正项 κ·v_N = G_R·(η_n n_R − η_x Δx) 由**数字端**计算并
  扣除，因此 `quantize_with_correction` 先量化 RA 输出、再减校正量。
  旧实现写成 `quantize(vra - κ·v_N)`，把校正量塞进 ADC2 的模拟量程：
  斜率项 κ·G_N·η_x·Δx 在近 Nyquist、满幅时达 G_R·η_x·(2π f_S/2 · Δt · v_FS)
  ≈ 1.18 V（RA 满摆幅 1.5 V 的 79 %），近 Nyquist 时直接把 10 % 余量吃穿，
  单音 5 MHz 下即已 10.3 % 溢出、残差 RMS 1.19 mV（审计后的回归实测）。
  校正量是频率相关量，模拟口径的余量无法用固定百分比覆盖 —— 这是**结构性**
  错误，不是余量不够大。
* 量化噪声 Δ2/√12 折算到输入 = 0.6·LSB20（config.validate 判据 1：
  Δ2/G0 ≤ LSB20，后端分辨能力必须不劣于 20b 目标）。
* 本类只定义后端静态量化。生产 split 通路的 ADC2 采样电压来自
  conversion.ConversionEngine 的实际宽/窄带跟踪；RA 输出可与该电压不同。
  独立 ADC2 热噪声、阈值失配和器件级电容尚未给出，不能声称可忽略。


单位契约：输入/输出均为 [V]（RA 输出口径，非输入折算）。

参数来源分级：
    adc2_n_bits / adc2_v_min / adc2_v_max  [推导]（PPT 未给 ADC2 细节；
    三者由 Config.paper_consistent() 从 G0·Δ1 派生，使 Δ2/G0 = 0.6·LSB20。
    见 docs/adr/0003-stage-1-resolution.md —— 改第一级读数时它们必须跟着变，
    手改单值会让残差溢出，validate() 会报 FAIL）

契约与不变量：
    * 非有限输入被拒绝；原始整数码在任何数字观察器校正之前产生；
    * 溢出标志 over 是**逐样本布尔**，统计口径见 metrics；溢出样本的输出
      被钳位在量程端点 bin —— 物理上等价于"读数为饱和码"，不是丢弃。
"""

from __future__ import annotations

import numpy as np

from .config import Config


class ADC2:
    """后端 flash/SAR 量化器的行为模型（静态、无记忆）。

    Attributes:
        step:   量化步长 Δ2 = (vmax - vmin)/2^n_bits [V]。
        vmin/vmax: 量程边界 [V]，来自 config（单极性残差口径）。
        n_code: 输出码数 = 2^adc2_n_bits。
    """

    def __init__(self, cfg: Config):
        """按配置构造第二级量化器：步长、输入窗口与限幅门限。

        Args:
            cfg: 模型配置（Config）。提供 delta2、adc2_v_min/v_max、
                 adc2_n_bits——三者由 Config.paper_consistent() 从
                 G0·Δ1 派生（来源分级 [推导]）。改第一级读数时须同步，
                 否则残差溢出、validate() 报 FAIL。
        Side effects: 固化 step / vmin / vmax / n_code。
        """
        self.cfg = cfg
        self.step = cfg.delta2
        self.vmin = cfg.adc2_v_min
        self.vmax = cfg.adc2_v_max
        self.n_code = 2**cfg.adc2_n_bits

    def quantize_codes(self, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return raw integer ADC2 codes and analog-window overflow flags.

        Args:
            v: Finite ADC2 acquisition voltage [V], arbitrary shape.

        Returns:
            (int64 code, bool overflow) of the same shape. The specified input
            operating range includes both rails; the upper rail maps to the
            last code. Out-of-window finite inputs saturate, with flags retained.

        Raises:
            ValueError: NaN or infinity, which cannot represent a conversion.
        """
        v = np.asarray(v, dtype=float)
        if np.any(~np.isfinite(v)):
            raise ValueError("ADC2 acquisition voltage must be finite")
        over = (v < self.vmin) | (v > self.vmax)
        vc = np.clip(v, self.vmin, self.vmax)
        code = np.floor((vc - self.vmin) / self.step)
        return np.clip(code, 0, self.n_code - 1).astype(np.int64), over

    def decode_codes(self, code: np.ndarray) -> np.ndarray:
        """Decode legal raw integer codes to nominal ADC2 bin-center voltages [V].

        Raises:
            ValueError: Noninteger or out-of-range code data. A malformed code
                buffer is not silently clipped during digital reconstruction.
        """
        code = np.asarray(code)
        if (
            not np.issubdtype(code.dtype, np.integer)
            or np.any(code < 0)
            or np.any(code >= self.n_code)
        ):
            raise ValueError("ADC2 codes must be integers within the backend code range")
        return self.vmin + (code + 0.5) * self.step

    def quantize(self, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """量化并统计溢出。

        Args:
            v: RA 输出口径的输入电压 [V]，任意形状。
        Returns:
            (vq, over)
            vq:   量化后的等效输入电压 [V]——落在所在 bin 的**中点**
                  （flash ADC 的标准行为模型；码值本身不返回，
                  因为下游只需要电压量级的残差）。
            over: 逐样本布尔，True = 输入超出 [vmin, vmax]（被钳位）。
        Side effects: 无（本类无状态）。
        """
        code, over = self.quantize_codes(v)
        return self.decode_codes(code), over

    def quantize_with_correction(
        self, v: np.ndarray, correction: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """先量化 RA 输出，再在数字域扣除观察器校正量。

        数字端重构式（reconstruction.reconstruct）里的 v₂ 是
        "RA 输出口径"的电压，因此校正量与量化结果在同一口径相加即可：

            v₂ = quantize(vra) − κ·v_N

        与旧写法 ``quantize(vra − κ·v_N)`` 的差别只有 ADC2 的量化噪声
        （两者相差一次 Δ2 的重化），但量程占用完全不同：本写法下
        ADC2 只承担 RA 输出本身，κ·v_N 无论多大都不会把它顶出
        [vmin, vmax] —— 这正是 docs/adr/0006 记录的修复点。

        Args:
            v:          RA 输出口径的输入电压 [V]。**不是**已减去校正量的
                        中间量 —— 上游不得再自行做 κ·v_N 的模拟相减。
            correction: κ·v_N [V]，与 v 同形状；数字端已知量。
        Returns:
            (v2, over)：v2 为校正后的 RA 口径残差 [V]；over 只反映 v
            本身是否越界（即 ADC2 真实看到的量）。
        Side effects: 无。
        """
        vq, over = self.quantize(v)
        return vq - np.asarray(correction, dtype=float), over
