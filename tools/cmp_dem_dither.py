#!/usr/bin/env python3
"""D3 DEM / D4 Split Dither 的 Python-RTL 逐位比对。

用法（需先跑 VCS 生成 mout.txt）：
    # 1) RTL 产生向量
    vcs -full64 -sverilog -timescale=1ns/1ps -o simv_m \
        rtl/dem_mask_v3.sv rtl/dither_pre.sv tb/tb_dem_dither.sv
    ./simv_m > mout.txt
    # 2) 比对
    PYTHONPATH=src:tools python3 tools/cmp_dem_dither.py mout.txt
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
from algo_chain import post_mask, bridge_swap, pre


def hb(h, n):
    """按 j 索引展开（bit0 -> j=0）。"""
    v = int(h, 16)
    return [(v >> i) & 1 for i in range(n)]


def hb4(h):
    """4 位子阵列按 [b2,b1,b0,bal] 展开。"""
    v = int(h, 16)
    return [(v >> (3 - i)) & 1 for i in range(4)]


def main(path):
    eb = ep = nm = np_ = 0
    for ln in open(path):
        p = ln.split()
        if not p:
            continue
        if p[0] == 'M':
            k, h, v, sel = (int(p[i]) for i in range(1, 5))
            co, half = sel >> 1, sel & 1
            exp = bridge_swap(post_mask(k, h, v), h, v, half << 3, co)
            got = [hb(x, 20) for x in p[5:13]]
            nm += 1
            if got != exp:
                eb += 1
                if eb <= 3:
                    print(f"M k={k} h={h} v={v} coin={co} half={half}")
        elif p[0] == 'P':
            w, v = int(p[1]), int(p[2])
            bits, T, U, Ld = pre(w, v)
            got = [hb4(x) for x in p[5:13]]
            np_ += 1
            if got != bits:
                ep += 1
                if ep <= 3:
                    print(f"P word={w} v={v}")
    print(f"DEM   {nm} 组 失配 {eb}")
    print(f"Dither {np_} 组 失配 {ep}")
    ok = (eb == 0 and ep == 0)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'mout.txt'))
