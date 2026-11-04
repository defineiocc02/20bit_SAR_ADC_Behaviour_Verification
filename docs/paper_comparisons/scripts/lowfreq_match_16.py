#!/usr/bin/env python3
"""40 Hz sparse-clock noise-state probe; not a full-rate ADC conversion stream."""

import json
import sys
import time
from itertools import pairwise
from pathlib import Path

import numpy as np
from scipy.signal import welch

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / "src"))
from adi_model.low_frequency_noise import BandLimitedFlicker  # noqa: E402

out = Path("results/lowfreq_40hz_16")
out.mkdir(exist_ok=False)
fs = 40e6
hop = 2000
probe_fs = fs / hop
duration = 64
samples = int(duration * probe_fs)
idx = np.arange(samples, dtype=np.int64) * hop
s0 = (8.8e-9) ** 2
fc = 40.0
fl = 0.1
modes = 128
nperseg = 262144
nrep = 16
p_total = p_flick = None
start = time.monotonic()
for seed in range(nrep):
    rng = np.random.default_rng(24001 + seed)
    state = BandLimitedFlicker.create(s0, fc, fl, rng, modes=modes)
    flick = state.at_adc_indices(idx, fs)
    white = rng.normal(0, np.sqrt(s0 * probe_fs / 2), samples)
    f, p1 = welch(
        flick,
        fs=probe_fs,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )
    _, p2 = welch(
        flick + white,
        fs=probe_fs,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )
    p_flick = p1 if p_flick is None else p_flick + p1
    p_total = p2 if p_total is None else p_total + p2
    print("noise_realization", seed + 1, "/", nrep, flush=True)
p_flick /= nrep
p_total /= nrep
mask = (f >= 2) & (f <= 30)
slope = float(np.polyfit(np.log10(f[mask]), 10 * np.log10(p_flick[mask]), 1)[0])
edges = np.geomspace(0.1, 40, 35)
centers = []
band_power = []
for lo, hi in pairwise(edges):
    q = (f >= lo) & (f < hi)
    if q.any():
        centers.append(np.sqrt(lo * hi))
        band_power.append(float(np.mean(p_flick[q])))
centers = np.array(centers)
band_power = np.array(band_power)
valid = (centers >= 2) & (centers <= 30)
binned_slope = float(np.polyfit(np.log10(centers[valid]), 10 * np.log10(band_power[valid]), 1)[0])
np.savez_compressed(
    out / "spectrum.npz", freq_hz=f, flicker_v2_per_hz=p_flick, total_v2_per_hz=p_total
)
meta = {
    "physical_fs_hz": fs,
    "sparse_probe_fs_hz": probe_fs,
    "physical_sample_stride": hop,
    "duration_s": duration,
    "n_sparse": samples,
    "nperseg": nperseg,
    "window": "hann",
    "realizations": nrep,
    "modes": modes,
    "corner_input_hz": fc,
    "low_cutoff_assumption_hz": fl,
    "white_density_input_v_sqrt_hz": 8.8e-9,
    "flicker_raw_bin_log_slope_2_to_30_db_dec": slope,
    "flicker_logband_mean_power_slope_db_dec": binned_slope,
    "elapsed_s": time.monotonic() - start,
    "scope": "isolated stochastic noise state with explicitly ideal anti-alias white observation; NOT 1.28M contiguous conversions or full 40MS/s ADC simulation; corner and white floor are inputs from slide, not predictions",
}
(out / "metrics.json").write_text(json.dumps(meta, indent=2) + "\n")
print("LOWFREQ_COMPLETE slope", slope, "seconds", meta["elapsed_s"], flush=True)
