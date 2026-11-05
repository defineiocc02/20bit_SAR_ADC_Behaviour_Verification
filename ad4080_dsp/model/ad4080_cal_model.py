"""AD4080 风格数字校正与固定横坐标两点拟合的定点参考。

计算对应 ad4080_dsp/rtl/sys_error_corr.sv 与 cal_two_point.sv 的工程
数值规则：20 bit 补码（手册 Rev.B p.20、Table 7），GAIN 以 0x200 为中心。
0x001/0x3FF 各偏 511 码，对应原定标 ±1.5594%；以下系数是本工程实现参数，
不代表已独立证明器件内部实现。

增益运算使用 K_GAIN=512、SHIFT_G=24；两点拟合使用 INV_DX_K=40961、
SHIFT_K=20、INV_DX=1280、SHIFT_O=30。固定点 X1=-419430（10%）、
X2=+419430（90%），DX=838860，所有样本与偏移均以 ADC 码计。

cal_two 拟合固定 x 到输入 y 的正向仿射关系；若 y 是已知参考输入对应的
原始测量码，所得系数仍是正向传递系数，不能直接当作逆向校正系数。
用法：``python3 ad4080_cal_model.py``。
"""

import random

IN_W = 20
MAXC = (1 << (IN_W - 1)) - 1  # +524287
MINC = -(1 << (IN_W - 1))  # -524288

X1 = -419430
X2 = +419430
DX = X2 - X1  # 838860

K_GAIN = 512
SHIFT_G = 24
INV_DX_K = 40961  # round(1/(DX * 0.015594/511) * 2^20)
SHIFT_K = 20
INV_DX = 1280  # round(2^30 / DX)
SHIFT_O = 30


def clip(v):
    """将数值饱和到 20 bit 补码范围 [MINC, MAXC]，即 [-524288, 524287]。

    调用方提供可比较的标量，本函数不检查类型，也不将范围内数值强制转为整数。
    """
    return MINC if v < MINC else MAXC if v > MAXC else v


def sys_corr(x, off, gain):
    """按固定增益算式计算校正码与饱和标志。

    增量为 ``(x * (gain-512) * 512 + 2**23) >> 24``，随后加 off 并饱和。
    整数加半步再右移，半步向正无穷舍入；这与本工程 RTL 的默认运算对应，
    不是器件内部数值实现的证明。参数类型和输入合法范围由调用方保证。

    Args:
        x: 20 bit 补码样本，单位为 ADC 码。
        off: 12 bit 补码偏移，单位为 ADC 码。
        gain: 10 bit 直二进制增益码，512 表示单位增益。

    Returns:
        四元组：饱和后码、低端饱和布尔值、高端饱和布尔值和增益增量。
        标志按饱和前结果判断，增量单位仍为 ADC 码。
    """
    gd = gain - 512
    delta = (x * gd * K_GAIN + (1 << (SHIFT_G - 1))) >> SHIFT_G
    y = x + delta + off
    return clip(y), (y < MINC), (y > MAXC), delta


def cal_two(y1, y2):
    """拟合固定 X1/X2 到 y1/y2 的正向仿射关系。

    采用与默认 RTL 对应的预计算倒数和常数乘法，加半步右移后分别钳位。
    不求逆向校正，也不检查输入单调性或端点是否来自实际测量。

    Args:
        y1: 固定 X1=-419430 对应的补码响应标量，单位为 ADC 码。
        y2: 固定 X2=+419430 对应的补码响应标量，单位为 ADC 码。

    Returns:
        ``(gain, off)``，gain 钳位到 0..1023，off 钳位到 -2048..2047。
    """
    dy = y2 - y1
    num_g = dy - DX
    gd = (num_g * INV_DX_K + (1 << (SHIFT_K - 1))) >> SHIFT_K
    gain = 512 + gd
    gain = 0 if gain < 0 else 1023 if gain > 1023 else gain
    offnum = y1 * DX - dy * X1
    oc = (offnum * INV_DX + (1 << (SHIFT_O - 1))) >> SHIFT_O
    off = -2048 if oc < -2048 else 2047 if oc > 2047 else oc
    return gain, off


def ideal_y(x, m, c):
    """返回 ``round(m * x + c)`` 的整数 ADC 码，舍入遵循 Python round。"""
    return round(m * x + c)


def test_case(name, m, c):
    """在指定增益/偏移下比较十个固定边界码点，打印最大绝对误差。

    参考响应和拟合端点均先饱和。该比较验证正向定点近似，不证明逆向校准效果。

    Args:
        name: 输出中使用的案例名称。
        m: 目标正向响应的无量纲增益。
        c: 目标正向响应的偏移，单位为 ADC 码。
    """
    y1 = clip(ideal_y(X1, m, c))
    y2 = clip(ideal_y(X2, m, c))
    g, o = cal_two(y1, y2)
    errs = []
    for x in [MINC, MINC + 1, -300000, -1, 0, 1, 1000, 300000, MAXC - 1, MAXC]:
        yt = clip(ideal_y(x, m, c))
        yc, _, _, _ = sys_corr(x, o, g)
        errs.append(yc - yt)
    print(
        f"{name:26s} y1={y1:8d} y2={y2:8d} -> GAIN={g:4d} OFF={o:5d} "
        f"maxerr={max(abs(e) for e in errs):4d} LSB"
    )


def main():
    """打印典型与固定种子随机案例的正向近似误差，不设置通过阈值。

    随机部分使用 2000 个增益/偏移组合，每组比较五个随机码；最后打印
    0x001/0x200/0x3FF 增益端点与期望斜率的差值，不据此自动判定精度达标。
    """
    print("== 定点参考模型（补码，K_GAIN=512，分母 511） ==")
    test_case("ideal", 1.0, 0.0)
    test_case("gain +1%", 1.01, 0.0)
    test_case("gain -1%", 0.99, 0.0)
    test_case("gain +1.5594% (满偏)", 1.015594, 0.0)
    test_case("gain -1.5594% (满偏)", 0.984406, 0.0)
    test_case("offset +100", 1.0, 100.0)
    test_case("offset -100", 1.0, -100.0)
    test_case("gain +0.5% off +50", 1.005, 50.0)
    test_case("gain -0.5% off -50", 0.995, -50.0)

    random.seed(1)
    worst = 0
    for _ in range(2000):
        m = 1 + random.uniform(-0.01, 0.01)
        c = random.uniform(-200, 200)
        y1 = clip(ideal_y(X1, m, c))
        y2 = clip(ideal_y(X2, m, c))
        g, o = cal_two(y1, y2)
        for x in [random.randrange(MINC, MAXC + 1) for _ in range(5)]:
            yt = clip(ideal_y(x, m, c))
            yc, _, _, _ = sys_corr(x, o, g)
            worst = max(worst, abs(yc - yt))
    print("random max error (LSB):", worst)

    # 打印满偏端点与期望斜率的差值；此处没有自动通过/失败断言。
    for g, want_m in [(0x001, 0.984406), (0x200, 1.0), (0x3FF, 1.015594)]:
        x = 100000
        y, _, _, _ = sys_corr(x, 0, g)
        exp = round(x * want_m)
        print(f"gain=0x{g:03X} m={want_m:.6f}  y={y} exp={exp} err={y-exp}")


if __name__ == "__main__":
    main()
