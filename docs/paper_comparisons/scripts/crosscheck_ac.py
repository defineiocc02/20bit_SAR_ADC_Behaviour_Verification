"""Cross-check saved ADC FFT metrics against ADCToolbox."""

import argparse
import json
from pathlib import Path

import numpy as np
from adctoolbox.spectrum import compute_spectrum

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
base = Path(__file__).resolve().parents[1] / "data"

out = {}
for name in ["ac105_nonoise", "ac105_noise", "ac1k_noise"]:
    p = base / name
    m = json.loads((p / "metrics.json").read_text())
    z = np.load(p / "validation.npz")
    fs = m["validation"]["fs_hz"]
    rows = {}
    for what in ["before", "after"]:
        y = z[what + "_v"]
        t = compute_spectrum(
            y,
            fs=fs,
            max_scale_range=(-3, 3),
            win_type="boxcar",
            side_bin=0,
            max_harmonic=10,
            nf_method=3,
        )
        mt = t["metrics"]
        ind = m[what + "_integer"]
        row = {
            "adctoolbox_sndr_db": float(mt["sndr_dbc"]),
            "independent_sndr_db": ind["SNDR_dB"],
            "adctoolbox_snr_db": float(mt["snr_dbc"]),
            "independent_snr_db": ind["SNR_dB_10_harmonics_excluded"],
            "adctoolbox_sfdr_db": float(mt["sfdr_dbc"]),
            "independent_sfdr_db": ind["SFDR_dBc"],
            "adctoolbox_fundamental_bin": int(t["plot_data"]["fundamental_bin"]),
            "independent_fundamental_bin": ind["fundamental_bin"],
        }
        rows[what] = row
    out[name] = rows
args.out.write_text(json.dumps(out, indent=2, allow_nan=False) + "\n")
for n, rows in out.items():
    for k, r in rows.items():
        print(
            n,
            k,
            "SNDR diff",
            r["adctoolbox_sndr_db"] - r["independent_sndr_db"],
            "SNR diff",
            r["adctoolbox_snr_db"] - r["independent_snr_db"],
        )
