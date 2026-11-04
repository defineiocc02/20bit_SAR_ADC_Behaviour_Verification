#!/usr/bin/env python3
"""Held-out static ramp using frozen training coefficients and the same physical pool.

Sparse 16k-point sweep: sampled reconstruction error, NOT code-density INL/DNL.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / "src"))

from adi_model import Config  # noqa: E402
from adi_model.fixed_point import FixedPointReconstructor  # noqa: E402
from adi_model.pipeline import run_pipeline  # noqa: E402
from adi_model.slice_pool import PhysicalSlicePool  # noqa: E402
from adi_model.weight_calibration import DigitalObservation, FrozenCalibration  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
out = args.out
out.mkdir(parents=True, exist_ok=False)
root = Path(__file__).resolve().parents[1] / "data/ac105_nonoise"
cfg = Config.from_dict(json.loads((root / "config.json").read_text())["validation"])
fit = FrozenCalibration.from_dict(json.loads((root / "coefficients.json").read_text()))
pool = PhysicalSlicePool(cfg, np.random.default_rng(cfg.seed + 8101))
n = 16384
amplitude = 2.85


def ramp(t):
    """Return the independent voltage ramp for the held-out sweep."""
    return -amplitude + 2 * amplitude * np.clip(np.asarray(t) * cfg.fs / (n - 1), 0, 1)


r = run_pipeline(cfg, ramp, n, pool=pool, rng=np.random.default_rng(cfg.seed + 19002))
d = DigitalObservation.from_result(r)
before = FixedPointReconstructor.from_result(r).reconstruct(d)
after = FixedPointReconstructor.from_frozen_calibration(d.spec, fit).reconstruct(d)
x = np.asarray(r.sample.x1_clean)
lsb = 2 * cfg.v_fs / (2**cfg.n_bits_target)
eb = (before.voltage - x) / lsb
ea = (after.voltage - x) / lsb
np.savez_compressed(
    out / "sweep.npz",
    x_v=x,
    before_error_lsb=eb,
    after_error_lsb=ea,
    before_code=before.code,
    after_code=after.code,
    overflow=d.overflow,
)
metrics = {
    "fs_hz": cfg.fs,
    "n": n,
    "range_v": [-amplitude, amplitude],
    "lsb_v": lsb,
    "physical_pool_fabrication_seed": cfg.seed + 8101,
    "calibration": "frozen coefficients trained on independent sine/dither; loaded from ac105_nonoise",
    "noise_enabled": False,
    "before_error_std_lsb": float(np.std(eb)),
    "after_error_std_lsb": float(np.std(ea)),
    "before_error_max_abs_lsb": float(np.max(np.abs(eb))),
    "after_error_max_abs_lsb": float(np.max(np.abs(ea))),
    "overflow_count": int(np.count_nonzero(d.overflow)),
    "before_clipped": int(np.count_nonzero(before.clipped_low | before.clipped_high)),
    "after_clipped": int(np.count_nonzero(after.clipped_low | after.clipped_high)),
    "scope": "Sparse reconstruction-error sweep only; not a code-density test and not INL/DNL; no full ADC2 capacitor nonlinearity or temperature model.",
}
(out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
print(json.dumps(metrics, indent=2), flush=True)
