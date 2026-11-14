#!/usr/bin/env python3
"""D3 DEM / D4 Dither 性能对比图（数据来自 algo_chain 的 Python 模型）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from algo_chain import make_w, eval_mm, eval_sine, convert_mm, LSB20, VFS

plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti TC', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.size'] = 9
w_nom, w_dev = make_w(0.0), make_w(0.01)

fig, ax = plt.subplots(1, 3, figsize=(14, 4.2))

# ---- A: 校准前后（G4 三场景）
cases = [("名义器件\n名义系数", w_nom, w_nom),
         ("失配器件\n名义系数", w_dev, w_nom),
         ("失配器件\n正确系数", w_dev, w_dev)]
mx, rm = [], []
for _, wd, wl in cases:
    s = eval_mm(wd, wl)
    mx.append(s['e_flt'] / LSB20); rm.append(s['rms'] / LSB20)
x = np.arange(3); bw = 0.36
ax[0].bar(x - bw/2, mx, bw, label='max', color='#c0392b')
ax[0].bar(x + bw/2, rm, bw, label='RMS', color='#2980b9')
ax[0].set_yscale('log'); ax[0].set_xticks(x)
ax[0].set_xticklabels([c[0] for c in cases], fontsize=8)
ax[0].set_ylabel('误差 [LSB$_{20}$]')
ax[0].set_title('A  校准前后（1% 电容失配，256 点）')
ax[0].legend(fontsize=8); ax[0].grid(axis='y', alpha=.3)
for i, v in enumerate(mx):
    ax[0].text(i - bw/2, v*1.3, f'{v:.2f}', ha='center', fontsize=7)

# ---- B: DEM / Dither 组合 × 校准
combos = [(0, 0), (0, 1), (1, 0), (1, 1)]
rms_off, rms_on = [], []
for dem, dith in combos:
    r = eval_sine(w_dev, w_nom, dem=bool(dem), dith=bool(dith))
    rms_off.append(r['rms'] / LSB20)
    r2 = eval_sine(w_dev, w_dev, dem=bool(dem), dith=bool(dith))
    rms_on.append(r2['rms'] / LSB20)
x = np.arange(4)
ax[1].bar(x - bw/2, rms_off, bw, label='校准关', color='#e67e22')
ax[1].bar(x + bw/2, rms_on, bw, label='校准开', color='#27ae60')
ax[1].set_yscale('log'); ax[1].set_xticks(x)
ax[1].set_xticklabels([f'DEM={d}\nDith={t}' for d, t in combos], fontsize=8)
ax[1].set_ylabel('RMS 误差 [LSB$_{20}$]')
ax[1].set_title('B  DEM / Dither × 校准（1024 点正弦）')
ax[1].legend(fontsize=8); ax[1].grid(axis='y', alpha=.3);

# ---- C: 误差 vs 输入码
for tag, wl, c in (("未校准", w_nom, '#e67e22'), ("已校准", w_dev, '#27ae60')):
    xs, es = [], []
    for i in range(256):
        xx = -VFS + 6.0 * i / 256
        r = convert_mm(xx, dict(h=i % 8, v=(i // 8) % 8, dither=(i*7) % 64,
                                acq_seq=i & 15, coin=i & 1),
                       list(range(8)), w_dev, wl)
        if r is None or r['over']:
            continue
        xs.append(xx); es.append((r['xh'] - xx) / LSB20)
    ax[2].plot(xs, es, lw=.8, label=tag, color=c)
ax[2].set_xlabel('输入 [V]'); ax[2].set_ylabel('误差 [LSB$_{20}$]')
ax[2].set_title('C  误差随输入分布')
ax[2].legend(fontsize=8); ax[2].grid(alpha=.3)

fig.suptitle('D3 DEM / D4 Split Dither —— 工程模型性能（Python，1% 失配 seed=2026，非硬件实测）',
             fontsize=10)
fig.tight_layout(rect=[0, 0, 1, 0.95])
out = 'docs/figures/dem_dither_perf.png'
os.makedirs('docs/figures', exist_ok=True)
fig.savefig(out, dpi=140)
print('saved', out)
print('A max:', [f'{v:.2f}' for v in mx], 'rms:', [f'{v:.2f}' for v in rm])
print('B rms off:', [f'{v:.1f}' for v in rms_off], 'on:', [f'{v:.2f}' for v in rms_on])
