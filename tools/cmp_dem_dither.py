#!/usr/bin/env python3
"""D3 DEM / D4 Split Dither 的 Python-RTL 逐位比对（严格版）。

与旧版的区别：
  * 要求精确读到 133376 条 DEM、512 条 Dither，缺一即 FAIL
  * 检查组合唯一性（无重复、无缺失）
  * P 行同时校验 U 与 f（旧版跳过）
  * 空日志 / 截断日志 / 仿真失败一律 FAIL

用法：python3 tools/cmp_dem_dither.py <log>

为什么按文件路径加载 dem_dither 而不用 `from adi_model.dem_dither import ...`
--------------------------------------------------------------------------
`src/adi_model/__init__.py` 会连带导入 calibration（依赖 scipy）与
simulation.config（用到 `typing.TypeGuard`，需 Python>=3.10）。
而 `dem_dither.py` 本身是零依赖纯函数。走包导入会把一个纯算术比对
绑死在整个包的依赖树上，结果是：
  * 没有 scipy 的环境直接 ImportError；
  * 系统自带 python3.9（macOS CommandLineTools）在 TypeGuard 处炸掉。
本脚本的职责只是"比对两个纯函数"，不该受这些约束，因此改为按路径直载。
"""
import sys, os, importlib.util

sys.path.insert(0, os.path.dirname(__file__))

_HERE = os.path.dirname(os.path.abspath(__file__))
_DD_PATH = os.path.join(_HERE, '..', 'src', 'adi_model', 'dem_dither.py')
_spec = importlib.util.spec_from_file_location('_dem_dither_standalone', _DD_PATH)
_dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dd)
post_mask, bridge_swap, pre = _dd.post_mask, _dd.bridge_swap, _dd.pre

N_DEM = 521 * 8 * 8 * 2 * 2      # 133376
N_DIT = 64 * 8                   # 512


def hb(h, n):
    v = int(h, 16)
    return [(v >> i) & 1 for i in range(n)]


def hb4(h):
    v = int(h, 16)
    return [(v >> (3 - i)) & 1 for i in range(4)]


def main(path):
    if not os.path.exists(path):
        print(f"FAIL: 日志不存在 {path}")
        return 2
    dem_seen, dit_seen = set(), set()
    eb = ep = 0
    bad_lines = 0

    for ln in open(path, errors='replace'):
        p = ln.split()
        if not p:
            continue
        try:
            if p[0] == 'M':
                if len(p) != 13:
                    bad_lines += 1; continue
                k, h, v, sel = (int(p[i]) for i in range(1, 5))
                key = (k, h, v, sel)
                if key in dem_seen:
                    print(f"FAIL: DEM 重复 {key}"); return 2
                dem_seen.add(key)
                co, half = sel >> 1, sel & 1
                exp = bridge_swap(post_mask(k, h, v), h, v, half << 3, co)
                if [hb(x, 20) for x in p[5:13]] != exp:
                    eb += 1
                    if eb <= 3:
                        print(f"M 失配 k={k} h={h} v={v} coin={co} half={half}")
            elif p[0] == 'P':
                if len(p) != 13:
                    bad_lines += 1; continue
                w, v, U, f = (int(p[i]) for i in range(1, 5))
                key = (w, v)
                if key in dit_seen:
                    print(f"FAIL: Dither 重复 {key}"); return 2
                dit_seen.add(key)
                bits, T, Ur, Ld = pre(w, v)
                if [hb4(x) for x in p[5:13]] != bits:
                    ep += 1
                    if ep <= 3: print(f"P mask 失配 word={w} v={v}")
                if U != Ur or f != (4*((w>>2)&1) + 2*((w>>1)&1) + (w&1)):
                    ep += 1
                    if ep <= 6: print(f"P U/f 失配 word={w} U={U}/{Ur} f={f}")
        except (ValueError, IndexError):
            bad_lines += 1

    print(f"DEM    {len(dem_seen)}/{N_DEM} 组  失配 {eb}")
    print(f"Dither {len(dit_seen)}/{N_DIT} 组  失配 {ep}")
    if bad_lines:
        print(f"坏行 {bad_lines}")
    ok = (len(dem_seen) == N_DEM and len(dit_seen) == N_DIT
          and eb == 0 and ep == 0 and bad_lines == 0)
    if not ok:
        if len(dem_seen) != N_DEM:
            print(f"FAIL: DEM 覆盖不足（{len(dem_seen)} != {N_DEM}）")
        if len(dit_seen) != N_DIT:
            print(f"FAIL: Dither 覆盖不足（{len(dit_seen)} != {N_DIT}）")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'mout.txt'))
