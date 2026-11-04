#!/usr/bin/env python3
"""Compare production joint-response kernel to an independent ODE at 40 MS/s."""

import json
import sys
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / "src"))
from adi_model.conversion import response_interval  # noqa: E402

out = Path("results/ra_response_40m")
out.mkdir(exist_ok=False)
fs = 40e6
duration = 0.4 / fs
t_switch = 0.65 * duration
ra_bw = 120e6
wide = 150e6
narrow = 15e6
tau = 3e-9
ref0 = 0.005
target = 0.25
n = 400
t = np.linspace(0, duration, n + 1)
dt = t[1] - t[0]
paths = {}
for label, use_narrow in [("wide_only", False), ("wide_then_narrow", True)]:
    y = z = 0.0
    ys = [y]
    zs = [z]
    for i in range(n):
        bw = narrow if use_narrow and t[i] >= t_switch else wide
        y, z, _ = response_interval(
            y,
            z,
            target,
            ref0 * np.exp(-t[i] / tau),
            tau,
            dt,
            ra_bw_hz=ra_bw,
            adc_bw_hz=bw,
            clip_v=1.0,
        )
        ys.append(y)
        zs.append(z)

    def ode(t_ns, state, use_narrow=use_narrow):
        """Independent two-state continuous-time response."""
        time = t_ns * 1e-9
        band = narrow if use_narrow and time >= t_switch else wide
        return [
            2 * np.pi * ra_bw * (target + ref0 * np.exp(-time / tau) - state[0]) * 1e-9,
            2 * np.pi * band * (state[0] - state[1]) * 1e-9,
        ]

    oracle = solve_ivp(
        ode,
        [0, duration * 1e9],
        [0, 0],
        t_eval=t * 1e9,
        method="DOP853",
        rtol=1e-11,
        atol=1e-13,
        max_step=0.02,
    )
    if not oracle.success:
        raise RuntimeError(oracle.message)
    err = np.max(np.abs(np.array([ys, zs]) - oracle.y))
    paths[label] = {
        "ra_v": np.array(ys),
        "adc2_v": np.array(zs),
        "oracle_ra_v": oracle.y[0],
        "oracle_adc2_v": oracle.y[1],
        "max_difference_v": float(err),
        "adc2_final_v": float(zs[-1]),
        "input_referred_final_error_v": float((zs[-1] - target) / 32),
    }
np.savez_compressed(
    out / "trace.npz",
    time_s=t,
    **{
        f"{k}_{field}": v
        for k, row in paths.items()
        for field, v in row.items()
        if isinstance(v, np.ndarray)
    },
)
meta = {
    "fs_hz": fs,
    "period_ns": 25.0,
    "assumed_ra_interval_ns": duration * 1e9,
    "assumed_switch_ns": t_switch * 1e9,
    "assumed_ra_bw_MHz": ra_bw / 1e6,
    "assumed_adc2_wide_MHz": wide / 1e6,
    "assumed_adc2_narrow_MHz": narrow / 1e6,
    "assumed_ref_tau_ns": tau * 1e9,
    "assumed_ref_drive_mV": ref0 * 1e3,
    "target_v": target,
    "gain_assumption": 32,
    "cases": {
        k: {kk: vv for kk, vv in row.items() if not isinstance(vv, np.ndarray)}
        for k, row in paths.items()
    },
    "scope": "signal transient only; numeric bandwidths, 10 ns interval, switch fraction and initial reference error are assumptions; no noise transfer simulation or 1.3 dB prediction",
}
(out / "metrics.json").write_text(json.dumps(meta, indent=2) + "\n")
print(json.dumps(meta), flush=True)
