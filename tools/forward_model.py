"""独立前向模型：从电容连接直接算 RDAC 输出，不复用 D5 的重构公式。

与 digital_chain 的 dac_q24 实现路径完全独立：
  - 本文件按"每个单元电容接 VREF+ 还是 VREF-、是否被选中"逐单元累加电荷
  - 再除以总采样权重 G 得到归一化输出
  - 最后按 ADC2 的真实 LSB 量化余差

数字 DUT 只拿到 adc2 / dither / 码，拿不到这里的真实权重。
"""

from __future__ import annotations

import numpy as np

V_FS = 3.0            # 单端满量程
Q24 = 1 << 24
ADC2_FS = 4096
ADC2_LSB = 0.45 / ADC2_FS      # ADC2 的 LSB（V）
RA_GAIN = 32.0                 # 残差放大倍数


def dac_from_connection(w, mask, ids, cols, sub_cols, vref=3.0):
    """按物理连接逐单元累加：选中(mask=1)接 -VREF，未选中接 +VREF。

    w/sid/unit 为真实权重；返回归一化输出 Vout/VREF ∈ [-1,1]。
    仅使用"哪些单元被选中"与"单元权重"，不出现 K/G 之类的重构中间量。
    """
    num = 0.0   # Σ (±VREF · w)
    den = 0.0   # Σ (w · a)，a 只对采样列计 1
    for slot, sid in enumerate(ids):
        for j in range(cols):
            wj = w[(sid, j)]
            den += wj
            num += (-vref if mask[slot][j] else vref) * wj
        for i, j in enumerate(sub_cols):
            if mask[slot][j]:
                num += -2.0 * vref * w[(sid, j)]
    return num / den


def quantize_adc2(residue_v):
    """单边窗口：RA 输出 0..0.45 V 线性映射到 ADC2 0..4095。

    合法输入折算余差 = 0 .. 14.06 mV。超出返回 None（非法/饱和），
    由调用方报告，不静默夹紧后继续用于精度验收。
    """
    ra = residue_v * RA_GAIN
    if ra < 0.0 or ra > 0.45:
        return None
    return int(round(ra / ADC2_LSB))
