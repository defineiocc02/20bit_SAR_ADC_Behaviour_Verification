#!/usr/bin/env python3
"""Generate original, data-derived comparisons (no paper/PPT artwork copied)."""

import argparse
import json
from itertools import pairwise
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=ROOT / "figures")
args = parser.parse_args()
R = ROOT / "data"
OUT_DIR = args.out
OUT_DIR.mkdir(parents=True, exist_ok=True)
plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 8,
        "axes.labelsize": 8,
        "axes.titlesize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 6.5,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 0.8,
    }
)
BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
BLACK = "#111111"


def db(x):
    """Convert linear power to a floored decibel value."""
    return 10 * np.log10(np.maximum(x, 1e-30))


def save(fig, name):
    """Export a figure in bitmap and editable vector formats."""
    fig.savefig(OUT_DIR / (name + ".png"), dpi=300, facecolor="white")
    fig.savefig(OUT_DIR / (name + ".svg"), facecolor="white")
    svg = OUT_DIR / (name + ".svg")
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    fig.savefig(OUT_DIR / (name + ".pdf"), facecolor="white")
    plt.close(fig)


def decorate(ax):
    """Apply the shared axis styling."""
    ax.grid(True, which="major", color="0.88", lw=0.45)
    ax.tick_params(direction="out", length=2.5, width=0.6)


def fft_power(v):
    """Return one-sided power per bin normalized to a 3 V peak sine."""
    n = len(v)
    f = np.fft.rfftfreq(n, 1 / 40e6) / 1e6
    p = np.abs(np.fft.rfft(v - np.mean(v)) / n) ** 2
    p[1:-1] *= 2
    p /= 3**2 / 2
    return f, p


# PPT 21-22: retain unaveraged bins; the fundamental exceeds the plotted y range.
t = R / "timing_normal_seed42"
zero = np.load(t / "permute_zero.npz")
per = np.load(t / "permute_10ps.npz")
rot = np.load(t / "rotate_10ps.npz")
meta = json.loads((t / "metrics.json").read_text())
row = {v["case"]: v for v in meta["cases"]}
fs = 40e6
n = row["permute_10ps"]["n"]
fin = row["permute_10ps"]["fin_hz"]
freq = np.fft.rfftfreq(n, 1 / fs) / 1e6
ks = round((fs / 2 - fin) * n / fs)
fspur = freq[ks]
fig, axs = plt.subplots(1, 2, figsize=(5.75, 2.72), layout="constrained", sharey=True)
axs[0].plot(freq, db(zero["spectrum_power"]), color=ORANGE, label="Zero skew", alpha=0.75, lw=0.5)
axs[0].plot(freq, db(per["spectrum_power"]), color=BLUE, label="10 ps RMS skew", alpha=0.72, lw=0.5)
axs[0].set_title("(a) Random 8-of-18 scheduling")
axs[0].legend(loc="lower left", frameon=False)
axs[1].plot(freq, db(per["spectrum_power"]), color=BLUE, label="Random 8-of-18", alpha=0.67, lw=0.5)
axs[1].plot(
    freq, db(rot["spectrum_power"]), color=ORANGE, label="Fixed rotation", alpha=0.65, lw=0.5
)
axs[1].axvline(fspur, color=BLACK, ls=":", lw=0.8)
axs[1].scatter(
    [fspur, fspur],
    [db(per["spectrum_power"][ks]), db(rot["spectrum_power"][ks])],
    color=[BLUE, ORANGE],
    s=15,
    zorder=10,
)
axs[1].annotate(
    f"{db(rot['spectrum_power'][ks]):.1f} / {db(per['spectrum_power'][ks]):.1f} dBFS/bin",
    xy=(fspur, db(rot["spectrum_power"][ks])),
    xytext=(2, -83),
    arrowprops={"arrowstyle": "->", "lw": 0.6},
    fontsize=7,
)
axs[1].text(fspur + 0.2, -169, r"$f_s/2-f_{in}$", fontsize=7, rotation=90, va="bottom")
axs[1].set_title("(b) 10 ps: scheduling comparison")
axs[1].legend(loc="lower left", frameon=False)
for ax in axs:
    ax.set(xlim=(0, 20), ylim=(-175, -75), xlabel="Frequency (MHz)")
    decorate(ax)
axs[0].set_ylabel("Magnitude (dBFS/bin)")
save(fig, "01_timing_skew")

# PPT 38: output spectra with independently trained/frozen coefficients.
a = R / "ac105_noise"
capture = np.load(a / "validation.npz")
met = json.loads((a / "metrics.json").read_text())
f, pb = fft_power(capture["before_v"])
_, pa = fft_power(capture["after_v"])
fig, axs = plt.subplots(1, 2, figsize=(5.75, 2.8), layout="constrained")
for ax, p, c, label in [(axs[0], pb, ORANGE, "Before fit"), (axs[1], pa, BLUE, "Frozen weights")]:
    ax.plot(f, db(p), color=c, lw=0.42, alpha=0.8)
    ax.set(xlim=(0, 20), ylim=(-175, 0), xlabel="Frequency (MHz)")
    decorate(ax)
    ax.set_title(label)
axs[0].set_ylabel("Magnitude (dBFS/bin)")
for ax, key in zip(axs, ["before_integer", "after_integer"], strict=False):
    m = met[key]
    ax.text(
        0.98,
        0.95,
        f"SNR {m['SNR_dB_10_harmonics_excluded']:.2f} dB\nSNDR {m['SNDR_dB']:.2f} dB",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=7,
    )
save(fig, "02_ac_calibration")

# Sparse held-out voltage sweep (explicitly not a code density or INL test).
s = np.load(R / "static_transfer/sweep.npz")
m = json.loads((R / "static_transfer/metrics.json").read_text())
fig, axs = plt.subplots(1, 2, figsize=(5.75, 2.65), layout="constrained", sharex=True)
for ax, name, colour, lim, title in [
    (axs[0], "before_error_lsb", ORANGE, 60, "Before fit"),
    (axs[1], "after_error_lsb", BLUE, 1.5, "Frozen weights"),
]:
    y = s[name]
    ax.scatter(s["x_v"], y, s=0.4, alpha=0.32, color=colour, rasterized=True)
    ax.axhline(0, color="0.3", lw=0.5)
    ax.set(xlim=(-3, 3), ylim=(-lim, lim), xlabel="Input voltage (V)")
    ax.set_title(title)
    decorate(ax)
    ax.text(
        0.97,
        0.95,
        f"std {np.std(y):.2f} LSB",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=7,
    )
axs[0].set_ylabel("Reconstruction error (20-bit LSB)")
save(fig, "03_static_error")

# PPT 36-37: 1 kHz single-cycle ADC smoke and independent sparse noise-state probe.
c = np.load(R / "ac1k_noise/validation.npz")
f, pb = fft_power(c["before_v"])
_, pa = fft_power(c["after_v"])
noise = np.load(R / "lowfreq_40hz_16/spectrum.npz")
freq = noise["freq_hz"]
power = noise["total_v2_per_hz"]
edges = np.geomspace(0.1, 10000, 72)
xx = []
yy = []
for lo, hi in pairwise(edges):
    mask = (freq >= lo) & (freq < hi)
    if mask.any():
        xx.append(np.sqrt(lo * hi))
        yy.append(np.sqrt(np.mean(power[mask])) * 1e9)
xx = np.array(xx)
yy = np.array(yy)
target = 8.8 * np.sqrt(1 + 40 / xx)
target[xx > 40] = 8.8
fig, axs = plt.subplots(1, 2, figsize=(5.75, 2.75), layout="constrained")
axs[0].semilogx(f[1:] * 1e6, db(pb[1:]), color=ORANGE, lw=0.4, alpha=0.65, label="Before fit")
axs[0].semilogx(f[1:] * 1e6, db(pa[1:]), color=BLUE, lw=0.4, alpha=0.65, label="Frozen weights")
axs[0].set(
    xlim=(1000, 20e6), ylim=(-190, 0), xlabel="Frequency (Hz)", ylabel="Magnitude (dBFS/bin)"
)
axs[0].set_title("(a) 1 kHz, one input cycle")
axs[0].legend(loc="lower left", frameon=False)
axs[1].loglog(xx, yy, color=BLUE, lw=1, label="16 noise-state runs")
axs[1].loglog(xx, target, color=BLACK, lw=0.8, ls="--", label="Input density model")
axs[1].axvline(40, color="0.35", ls=":", lw=0.7)
axs[1].text(46, 100, "40 Hz input", fontsize=7)
axs[1].set(
    xlim=(0.1, 10000),
    ylim=(5, 500),
    xlabel="Frequency (Hz)",
    ylabel=r"Input density (nV/$\sqrt{\mathrm{Hz}}$)",
)
axs[1].set_title("(b) Sparse noise-state probe")
axs[1].legend(loc="upper right", frameon=False)
for ax in axs:
    decorate(ax)
save(fig, "04_low_frequency")

# PPT 35: assumed dynamic parameters, model against an independent ODE.
d = np.load(R / "ra_response_40m/trace.npz")
time = d["time_s"] * 1e9
fig, axs = plt.subplots(1, 2, figsize=(5.75, 2.7), layout="constrained")
for key, c, label in [
    ("wide_only", ORANGE, "Wide bandwidth"),
    ("wide_then_narrow", BLUE, "Wide, then narrow"),
]:
    axs[0].plot(time, d[key + "_adc2_v"] * 1e3, color=c, label=label)
    axs[0].plot(
        time[::40], d[key + "_oracle_adc2_v"][::40] * 1e3, "o", color=c, mfc="white", ms=2.5, lw=0
    )
    axs[1].plot(time, (d[key + "_adc2_v"] - 0.25) * 1e6 / 32, color=c, label=label)
axs[0].axhline(250, color=BLACK, ls="--", lw=0.6, label="Target")
axs[0].set(ylabel="ADC2 input (mV)", ylim=(-5, 270))
axs[0].legend(loc="lower right", frameon=False)
axs[1].axhline(0, color=BLACK, ls="--", lw=0.6)
axs[1].set(ylabel=r"Input-referred error ($\mu$V)", ylim=(-8000, 400))
axs[1].set_yscale("symlog", linthresh=10)
for ax in axs:
    ax.axvline(6.5, color="0.4", ls=":", lw=0.7)
    ax.set(xlim=(0, 10), xlabel="Time (ns)")
    decorate(ax)
axs[0].set_title("(a) Signal response")
axs[1].set_title("(b) Settling error (symlog)")
save(fig, "05_ra_settling")

# PPT 26: do not pretend that trial/reject and resolved-bit events are identical.
e = json.loads((R / "structural_trace_40m/event_comparison.json").read_text())
py = e["python_trial_reject_codes"]
rt = e["rtl_codes"]
fig, axs = plt.subplots(1, 2, figsize=(5.75, 2.6), layout="constrained", sharey=True)
axs[0].step(np.arange(len(py)), py, where="post", color=ORANGE, label="19 trial/reject states")
axs[1].step(np.arange(len(rt)), rt, where="post", color=BLUE, label="7 resolved states")
for ax, title in zip(
    axs, ["Python load-event assumption", "Structural RTL observation"], strict=False
):
    ax.set(xlabel="Event index", ylim=(-10, 280))
    ax.set_title(title)
    ax.legend(loc="upper right", frameon=False)
    decorate(ax)
axs[0].set_ylabel("RDAC code")
save(fig, "06_reference_events")
print("FIGURES_WRITTEN", OUT_DIR)
