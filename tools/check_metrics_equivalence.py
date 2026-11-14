"""Equivalence probe for `metrics` vs `metrics_lsb20`.

The two modules are near-verbatim copies. Before collapsing one into a thin
re-export, this probe hashes the observable behaviour of every public entry point
on fixed inputs, so the refactor can be proven behaviour-preserving rather than
assumed.

    python tools/check_metrics_equivalence.py --json before.json
    # ... refactor ...
    python tools/check_metrics_equivalence.py --json after.json --compare before.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path

import numpy as np

FS = 40e6
N = 4096


def _digest(value) -> str:
    """Hash a scalar/dict/array result so any drift shows up."""
    if isinstance(value, dict):
        payload = json.dumps({k: _canon(v) for k, v in sorted(value.items())}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()
    array = np.ascontiguousarray(np.asarray(value, dtype=float))
    return hashlib.sha256(array.tobytes()).hexdigest()


def _canon(value):
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, np.floating):
        return format(float(value), ".17g")
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, np.ndarray):
        return _digest(value)
    return value


def probe(module_name: str) -> dict:
    """Exercise every public entry point with fixed inputs."""
    mod = importlib.import_module(module_name)
    rng = np.random.default_rng(20261112)
    t = np.arange(N) / FS
    tone_bin = 205
    fin = tone_bin * FS / N
    tone = 0.99 * np.sin(2 * np.pi * fin * t)
    noisy = tone + rng.normal(0, 1e-5, N)
    levels = np.linspace(-2.9, 2.9, 64)
    mu = 1e-4 * np.sin(np.linspace(0, 3.1, 64))

    out: dict[str, str] = {}
    out["power_spectral_density"] = _digest(mod.power_spectral_density(noisy, FS))
    out["spectrum_dbfs"] = _digest(mod.spectrum_dbfs(noisy, FS, 3.0))
    out["sine_fit_metrics"] = _digest(mod.sine_fit_metrics(noisy, FS, fin))
    out["inl_from_mean_error"] = _digest(mod.inl_from_mean_error(levels, mu, 6.0 / 2**20))
    psd = mod.power_spectral_density(noisy, FS)
    out["integrate_noise_band"] = _digest(mod.integrate_noise_band(psd, 1e3, 1e6))
    out["reference_precision_bits"] = _digest(
        np.asarray([mod.reference_precision_bits(1e-4, 3.0), mod.reference_precision_bits(1e-6, 3.0)])
    )
    out["static_test"] = _digest(
        mod.static_test(lambda level, count: np.full(count, level) + 1e-5 * rng.standard_normal(count),
                        levels, 3)
    )
    # `summarize` consumes a full SimResult (`effective_config`), not a bare
    # record, so it is exercised by the pipeline tests instead of here.
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", required=True,
                        help="把探测到的摘要写到这个路径（是**输出**，不是输入）")
    parser.add_argument("--compare", default=None,
                        help="与已有的摘要文件比对，而不是新写一份")
    parser.add_argument("--force", action="store_true",
                        help="允许覆写已存在的输出文件")
    args = parser.parse_args()

    document = {
        "metrics": probe("adi_model.metrics.metrics"),
        "metrics_lsb20": probe("adi_model.metrics.metrics_lsb20"),
    }
    # ⚠️ `--json` 是**输出路径**。2026-11-13 曾误把它当成输入、
    # 传了归档的 p4_ablation.json，导致该数据文件被覆写（已从 git 恢复）。
    # 现在若目标文件已存在且不在 --compare 模式，拒绝覆写除非显式 --force。
    target = Path(args.json)
    if target.exists() and args.compare is None and not args.force:
        print(
            f"refusing to overwrite existing file: {target}\n"
            "  --json is the OUTPUT path; pass --compare to compare instead, "
            "or --force to overwrite."
        )
        return 2
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"{'entry point':<28}{'metrics':<18}{'metrics_lsb20':<18}match")
    ok = True
    for name, digest in document["metrics"].items():
        other = document["metrics_lsb20"][name]
        same = digest == other
        ok = ok and same
        print(f"{name:<28}{digest[:16]:<18}{other[:16]:<18}{'yes' if same else 'NO'}")

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text())
        drifted = [
            f"{side}.{name}"
            for side in ("metrics", "metrics_lsb20")
            for name in baseline[side]
            if baseline[side][name] != document[side][name]
        ]
        if drifted:
            print("\nDRIFT vs baseline:", drifted)
            return 1
        print("\nno drift vs baseline")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
