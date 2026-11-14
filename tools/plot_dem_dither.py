#!/usr/bin/env python3
"""D3/D4 性能图，按 ADC 论文惯例：
  图1 误差频谱（dB vs 归一化频率，多条曲线叠加）
  图2 重构误差 vs 输入码（对应论文 INL 图，校准前后叠加）
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from algo_chain import make_w, eval_sine, convert_mm, LSB20, VFS

plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti TC', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams.update({'font.size': 8, 'axes.linewidth': .8,
                     'xtick.direction': 'in', 'ytick.direction': 'in'})

w_nom, w_dev = make_w(0.0), make_w(0.01)
N, FIN = 1024, 127


def spec(w_dev_, w_L, dem, dith):
    """返回 (归一化频率轴, 误差 PSD dB re LSB20, 最大杂散 dB, RMS LSB)。"""
    es = []
    rng = 0xACE1; A, C, I = list(range(8)), list(range(8, 16)), [16, 17]
    for i in range(N):
        x = 1.5 * math.sin(2 * math.pi * FIN * i / N)
        h = (i % 8) if dem else 0
        v = ((i // 8) % 8) if dem else 0
        word = ((i * 7) % 64) if dith else 0
        r = convert_mm(x, dict(h=h, v=v, dither=word, acq_seq=i & 15, coin=i & 1),
                       list(A), w_dev_, w_L)
        if r is None or r['over']:
            es.append(0.0)
        else:
            es.append(r['xh'] - x)
        avail = C + I
        for _ in range(2):
            rng ^= (rng << 13) & 0xFFFFFFFF; rng ^= rng >> 17; rng ^= (rng << 5) & 0xFFFFFFFF
            if _ == 0: r1 = rng
            else: r2 = rng
        i1 = ((r1 & 0xFFFF) * 10) >> 16
        j0 = ((r2 >> 16) * 9) >> 16
        i2 = min(j0 + (1 if j0 >= i1 else 0), 9)
        drop = {avail[i1], avail[i2]}
        A, C, I = [z for z in avail if z not in drop], list(A), [z for z in avail if z in drop]
    e = np.array(es) * np.hanning(N)
    E = np.abs(np.fft.rfft(e)) / (N / 4)
    P = 20 * np.log10(np.maximum(E / LSB20, 1e-3))
    f = np.arange(len(P)) / N
    spur = P[3:].max() if len(P) > 3 else 0
    rms = np.sqrt(np.mean(np.array(es) ** 2)) / LSB20
    return f, P, spur, rms


fig = plt.figure(figsize=(10, 3.6))
ax1 = fig.add_subplot(1, 2, 1)
ax2 = fig.add_subplot(1, 2, 2)

# ---- 图1：误差频谱，DEM 开/关（校准关）
for dem, c, lab in ((0, '#c0392b', 'DEM off'), (1, '#2980b9', 'DEM on')):
    f, P, sp, rms = spec(w_dev, w_nom, dem, 1)
    ax1.plot(f, P, lw=.65, color=c, label=f'{lab}  (峰值 {sp:.0f} dB, RMS {rms:.0f} L)')
ax1.set_xlim(0, .5); ax1.set_ylim(0, 60)
ax1.set_xlabel('归一化频率  $f/f_s$')
ax1.set_ylabel('误差谱  [dB re 1 LSB$_{20}$]')
ax1.set_title('(a) 误差频谱：DEM 开/关（1% 失配，校准关；DEM 后为宽带噪声，无离散杂散）', fontsize=7.5)
ax1.legend(fontsize=6.5, loc='upper right', frameon=False)
ax1.grid(alpha=.25, lw=.5)

# ---- 图2：重构误差 vs 输入码，校准前后
for wl, c, lab in ((w_nom, '#e67e22', '未校准'), (w_dev, '#27ae60', '已校准')):
    codes, es = [], []
    for i in range(256):
        xx = -VFS + 6.0 * i / 256
        r = convert_mm(xx, dict(h=i % 8, v=(i // 8) % 8, dither=(i * 7) % 64,
                                acq_seq=i & 15, coin=i & 1), list(range(8)), w_dev, wl)
        if r is None or r['over']:
            continue
        codes.append((xx + VFS) / 6.0 * 512)
        es.append((r['xh'] - xx) / LSB20)
    ax2.plot(codes, es, '.', ms=1.6, color=c, label=lab)
ax2.set_xlabel('粗码  $k$')
ax2.set_ylabel('重构误差  [LSB$_{20}$]')
ax2.set_title('(b) 重构误差 vs 粗码：校准前后', fontsize=8.5)
ax2.legend(fontsize=7, frameon=False)
ax2.grid(alpha=.25, lw=.5)

fig.tight_layout()
out = 'docs/figures/dem_dither_perf.png'
os.makedirs('docs/figures', exist_ok=True)
fig.savefig(out, dpi=170)
print('saved', out)
