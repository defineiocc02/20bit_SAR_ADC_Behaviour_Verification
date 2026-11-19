# =============================================================================
# 文件名     : algo_chain.py
# 模块名     : 静态电荷研究的器件夹具
# 功能       : 提供冻结制造条件与独立电荷求和。直接执行时委托公共数字 IP 入口。
# 设计说明   : 原脚本中的 11 次 guard 和浮点出码已退役；各实验仍按原始种子
#              制造该批未归一化的物理电容，供 run_static_linearity 交叉核对。
# =============================================================================
"""静态线性度的独立前向器件数据与可追溯抽样。"""

from adi_model.dem_dither import SUB
from adi_model.digital_conversion import ADC2_MIN as YMIN, ADC2_SPAN as SPAN, COARSE_STEP as DELTA, LSB20, VFS, sar_conversion

CF = 16.0
N2 = 8192


def sadc(voltage):
    """复用生产转移规则；返回最终粗码和实际发生的写入。"""
    coarse, decisions = sar_conversion(voltage, 0.0, 0)
    return coarse, [k for k in decisions if k >= 0]


def make_w(sigma=0.0, seed=2026):
    """18×20 器件权重（Cu）。sigma=0 即名义。

    桥误差按合同 §3.2 由实际 Cb 与子电容共同求出 beta_s，
    不独立扰动每个子权重。
    """
    import random
    rng = random.Random(seed)
    w = [[0.0] * 20 for _ in range(18)]
    for s in range(18):
        gs = 1.0 + rng.gauss(0, sigma)                  # 片级增益失配
        for j in range(16):
            w[s][j] = 4.0 * gs * (1 + rng.gauss(0, sigma))
        cb = (8.0 / 7.0) * (1 + rng.gauss(0, sigma))    # 桥电容
        csub = [c * (1 + rng.gauss(0, sigma)) for c in SUB]
        beta = cb / (cb + sum(csub))
        for t in range(4):
            w[s][16 + t] = csub[t] * beta
    return w


def fwd_dac(w_dev, ids, m, bits):
    """前向：从物理电容网络直接算 RDAC 输出与采样扰动量。不调用重构公式。"""
    q_main = 0.0
    q_sw = 0.0
    q_pre = 0.0
    for s_ in range(8):
        sid = ids[s_]
        for j in range(16):
            q_main += w_dev[sid][j]              # 全部主单元都采样 Vin
        for j in range(20):                      # 开关电荷含子阵列
            if m[s_][j]:
                q_sw += w_dev[sid][j]
        for t in range(4):
            if bits[s_][t]:
                q_pre += w_dev[sid][16 + t]
    return q_main, q_sw, q_pre


if __name__ == "__main__":
    from adi_model.cli import main_simulate

    raise SystemExit(main_simulate())
