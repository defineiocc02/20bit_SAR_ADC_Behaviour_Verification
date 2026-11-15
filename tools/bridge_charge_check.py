#!/usr/bin/env python3
"""Binary-to-Unary bridging 的名义电荷守恒验证。

背景
----
ISSCC 2024 §9.8 PPT 第 31 页画了"DEM 侧一元权重"与"Dither 侧二进制权重"
之间的边界混合（绿箭头，标 P=50%）。但图上箭头两端**权重不等**（4 对 8），
合同 `docs/ISSCC2024_ONCHIP_CONTRACT_v2.md` §6.1 明确写：

    "PPT31 给出了边界混合，但没有开关真值表。
     禁止交换不等权的完整 8 单位与 4 单位后声称守恒。"

合同因此选择了一种**严格等名义电荷**的 [E] 实现：
交换「选中主半单元（物理 4Cu）」与「全部 8 片子阵列 4Cu 支路」。
按合同 §3.2 的权威权重表，这两者恰好等权：

    主半单元             w = 1/4
    8 条 4Cu 子支路      w = 8 × (1/32) = 1/4

本脚本穷举验证 `dem_dither.bridge_swap()` 的名义电荷守恒。

为什么权重口径必须用 §3.2 的表（踩坑记录）
------------------------------------------
第一次验证时我直接用"物理 Cu 数相加"（主侧每半单元算 4Cu，
子侧每片按 4/2/1/1 Cu 算），结果 33,280/66,688 组"不守恒"，看起来像严重 bug。
错误在于：合同说子支路是"**经 beta=1/8 后**合计 4Cu"，β 是桥电容比，
子阵列必须先乘 β 才能与主侧同量纲。把 β 当 1 就必然差 8 倍。

用 §3.2 的名义 w 之后立刻归零 —— 这说明**同一个 mask，用错权重口径
会得出完全相反的结论**，所以这份验证必须把口径写死。

用法：
    python3 tools/bridge_charge_check.py
退出码 0 = 全部守恒；非 0 = 有不守恒（并打印首个反例）。
"""
from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DD = os.path.join(os.path.dirname(_HERE), 'src', 'adi_model', 'dem_dither.py')
_spec = importlib.util.spec_from_file_location('_dem_dither_bc', _DD)
_dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dd)

# 合同 §3.2 的权威名义权重 w = Ceff/Cf
W_MAIN = 1.0 / 4.0                        # 一元宏单元的每个 4Cu 半单元
W_SUB = (1.0 / 32.0, 1.0 / 64.0, 1.0 / 128.0, 1.0 / 128.0)   # 4Cu, 2Cu, 1Cu, 1Cu(guard)

K_MAX = 520          # 合同表达范围（含 guard）
H_N = 8
V_N = 8
ACQ_SEQ = (0, 8)     # acq_seq[3] 决定 half；低位在本实现里不参与 bridge


def nominal_weight(m: list[list[int]]) -> float:
    """8×20 mask -> 名义权重和（单位：Ceff/Cf）。"""
    main = sum(m[s][j] for s in range(8) for j in range(16)) * W_MAIN
    sub = sum(m[s][16 + t] * W_SUB[t] for s in range(8) for t in range(4))
    return main + sub


def main() -> int:
    tested = 0
    bad = 0
    first = None

    for k in range(K_MAX + 1):
        for h in range(H_N):
            for v in range(V_N):
                m = _dd.post_mask(k, h, v)
                for acq in ACQ_SEQ:
                    swapped = _dd.bridge_swap(m, h, v, acq, 1)
                    tested += 1
                    if abs(nominal_weight(m) - nominal_weight(swapped)) > 1e-12:
                        bad += 1
                        if first is None:
                            first = (k, h, v, acq,
                                     nominal_weight(m), nominal_weight(swapped))

    print("Binary-to-Unary bridging 名义电荷守恒验证")
    print("-" * 58)
    print(f"权重口径   : 合同 §3.2（主 1/4，子 1/32,1/64,1/128,1/128）")
    print(f"穷举范围   : k=0..{K_MAX} × {H_N} h × {V_N} v × {len(ACQ_SEQ)} acq")
    print(f"测试组合   : {tested}")
    print(f"不守恒     : {bad}")
    if first:
        k, h, v, acq, w0, w1 = first
        print(f"首个反例   : k={k} h={h} v={v} acq={acq}  {w0} -> {w1}")
    print("-" * 58)
    print(f"关键等式   : 主半单元 1/4 == 8 × (1/32) = 1/4")
    print("RESULT:", "PASS" if bad == 0 else "FAIL")
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
