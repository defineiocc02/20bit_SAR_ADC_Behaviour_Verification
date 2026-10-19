"""AD4080 数字校正的定点参考模型（与 RTL 逐拍一致）。

与 ad4080_rtl/rtl/sys_error_corr.sv 和 cal_two_point.sv 的定点运算**逐一对应**：
- 20-bit 二进制补码（手册 Rev.B p.20 Table 7）
- GAIN 斜率分母 511（0x001/0x3FF 各偏 0x200 恰好 511 码、±1.5594%）
- K_GAIN=512，SHIFT_G=24；INV_DX_K=40961，SHIFT_K=20；INV_DX=1280，SHIFT_O=30
- 测试点 x1=-419430（10%）、x2=+419430（90%），DX=838860

用法：python3 ad4080_cal_model.py
"""
import random

IN_W = 20
MAXC = (1 << (IN_W - 1)) - 1        # +524287
MINC = -(1 << (IN_W - 1))           # -524288

X1 = -419430
X2 = +419430
DX = X2 - X1                        # 838860

K_GAIN = 512
SHIFT_G = 24
INV_DX_K = 40961                    # round(1/(DX * 0.015594/511) * 2^20)
SHIFT_K = 20
INV_DX = 1280                       # round(2^30 / DX)
SHIFT_O = 30


def clip(v):
    return MINC if v < MINC else MAXC if v > MAXC else v


def sys_corr(x, off, gain):
    """与 sys_error_corr.sv 完全一致：delta = (x*gd*512 + 2^23) >>> 24。"""
    gd = gain - 512
    delta = (x * gd * K_GAIN + (1 << (SHIFT_G - 1))) >> SHIFT_G
    y = x + delta + off
    return clip(y), (y < MINC), (y > MAXC), delta


def cal_two(y1, y2):
    """与 cal_two_point.sv 完全一致：常数乘法代替除法。"""
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
    return round(m * x + c)


def test_case(name, m, c):
    y1 = clip(ideal_y(X1, m, c))
    y2 = clip(ideal_y(X2, m, c))
    g, o = cal_two(y1, y2)
    errs = []
    for x in [MINC, MINC + 1, -300000, -1, 0, 1, 1000, 300000, MAXC - 1, MAXC]:
        yt = clip(ideal_y(x, m, c))
        yc, _, _, _ = sys_corr(x, o, g)
        errs.append(yc - yt)
    print(f"{name:26s} y1={y1:8d} y2={y2:8d} -> GAIN={g:4d} OFF={o:5d} "
          f"maxerr={max(abs(e) for e in errs):4d} LSB")


def main():
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

    # 满偏端点必须精确
    for g, want_m in [(0x001, 0.984406), (0x200, 1.0), (0x3FF, 1.015594)]:
        x = 100000
        y, _, _, _ = sys_corr(x, 0, g)
        exp = round(x * want_m)
        print(f"gain=0x{g:03X} m={want_m:.6f}  y={y} exp={exp} err={y-exp}")


if __name__ == "__main__":
    main()
