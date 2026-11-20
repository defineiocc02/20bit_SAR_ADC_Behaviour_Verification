#!/usr/bin/env python3
"""证明 X = floor(N*I/2^36) 可用 32 位有符号表示（截断无损）。

为什么需要这个证明
------------------
recon_v21 为了把 96 位除法降成 32 位，对 X 做了 $signed(X_wide[31:0]) 截断。
这一步只有在 |X| < 2^31 时才无损。早前用"角点抽样最大值 353,632,160"
作为依据 —— **那是抽样，不是全域界**，而且和真实上界差了 5 倍。
本脚本从合法域出发做不等式推导，给出真正的全域界。

假设（每条都注明来源）
--------------------
A1 raw ∈ [0, 8191]          13 位 ADC2 码，接口位宽保证
A2 alpha ∈ [2^29, 2^31]     方案 §9.2 profile；RTL 的 bad_alpha 按此域报 err
A3 |b| <= 2^26              b_q24 是 signed 27 位 Q24
A4 G ∈ [16*2^30, 64*2^30]   方案 §9.2 profile
A5 每个 w_sub <= 4*2^30     **合同未给此上界**，本证明取"单列满量程 4Cu"作
                            保守假设。若实际权重表可超过 4Cu，本证明失效，
                            必须更新 A5 后重跑。
"""
import sys

CU = 1 << 30

# ---- 假设 ----
RAW_MIN, RAW_MAX = 0, 8191
ALPHA_MIN, ALPHA_MAX = 1 << 29, 1 << 31
B_MAX = 1 << 26
G_MIN, G_MAX = 16 * CU, 64 * CU
WSUB_MAX = 4 * CU
N_SLICE = 8
N_SUBBIT = 4


def main():
    print("=" * 68)
    print("X 位宽全域证明")
    print("=" * 68)
    print("假设：")
    print(f"  A1 raw   ∈ [{RAW_MIN}, {RAW_MAX}]")
    print(f"  A2 alpha ∈ [2^29, 2^31] = [{ALPHA_MIN}, {ALPHA_MAX}]")
    print(f"  A3 |b|   <= 2^26 = {B_MAX}")
    print(f"  A4 G     ∈ [{G_MIN}, {G_MAX}]")
    print(f"  A5 w_sub <= 4*2^30（合同未给，本证明的保守假设）")
    print()

    # Y = -3145728 + 1536*raw + 768
    y_lo = -3145728 + 1536 * RAW_MIN + 768
    y_hi = -3145728 + 1536 * RAW_MAX + 768
    print(f"Y ∈ [{y_lo}, {y_hi}]")

    # |Y - b| 的上界
    yb = max(abs(y_lo - B_MAX), abs(y_hi + B_MAX))
    print(f"|Y-b| <= {yb}")

    # Z = RND((Y-b)*2^30, alpha)，alpha 最小时放大倍数最大（2 倍），再加半 LSB
    z_max = (yb * (1 << 30) + (ALPHA_MIN - 1)) // ALPHA_MIN + 1
    print(f"|Z| <= {z_max}  ({z_max.bit_length()} 位)")

    # Wm / Wp 上界
    w_max = N_SLICE * N_SUBBIT * WSUB_MAX
    print(f"Wm, Wp <= {N_SLICE}*{N_SUBBIT}*4*2^30 = {w_max} = {w_max // CU} Cu")

    # N = Z*64 + 6*Wm - 3*G - 6*Wp
    n_max = z_max * 64 + 6 * w_max + 3 * G_MAX + 6 * w_max
    print(f"|N| <= {n_max}  ({n_max.bit_length()} 位)")

    # I = RND(2^60, G)，G 最小时最大
    i_max = (1 << 60) // G_MIN + 1
    print(f"I <= {i_max}  ({i_max.bit_length()} 位)")

    # X = floor(N*I / 2^36)
    ni_max = n_max * i_max
    x_max = ni_max >> 36
    print(f"|N*I| <= {ni_max}  ({ni_max.bit_length()} 位)")
    print(f"|X| <= {x_max} = 0x{x_max:X}  ({x_max.bit_length()} 位)")
    print()

    ok = x_max < (1 << 31)
    print(f"2^31 = {1 << 31}")
    print(f"|X| < 2^31 ? {ok}")
    print(f"余量：{((1 << 31) - 1) - x_max}  ({100.0 * x_max / (1 << 31):.2f}% 用掉)")
    print()

    # 记录"角点法"的对比，说明为什么不能用它
    print("对照：早前用有限角点得到的 353,632,160 —— 比真实上界小 "
          f"{x_max / 353632160:.1f} 倍，")
    print("      不能作为位宽依据。本脚本给的是全域不等式界。")
    print()
    if ok:
        print("RESULT: XWIDTH_PROVEN  |X| < 2^31 在上述假设下成立，32 位截断无损")
        return 0
    print("RESULT: XWIDTH_FAIL  X 超出 32 位有符号范围，位宽优化不成立")
    return 1


if __name__ == "__main__":
    sys.exit(main())
