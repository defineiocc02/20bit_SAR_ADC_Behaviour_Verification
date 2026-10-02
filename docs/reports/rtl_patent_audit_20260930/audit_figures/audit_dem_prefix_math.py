"""Verify a proposed 2D DEM prefix query; no RTL or PPA acceptance is implied."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))
from adi_model.dem import SplitSwitchGeometry, split_switch_command  # noqa: E402


def digest(path: Path) -> str:
    """Bind the mathematical witness to its actual source inputs."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cyclic(prefix: np.ndarray, start: np.ndarray, count: np.ndarray) -> np.ndarray:
    """Exact inclusive-zero prefix query on eight physical entries."""
    end = start + count
    return np.where(
        end <= 8,
        prefix[np.minimum(end, 8)] - prefix[start],
        prefix[8] - prefix[start] + prefix[np.maximum(end - 8, 0)],
    )


def main() -> None:
    """Check all legal DEM states/counts with independent forward masks."""
    states = np.arange(512, dtype=np.int64)
    geometry = SplitSwitchGeometry(63, 8, 8, True, True, False)
    # Forward rotated-and-filtered physical order from the independent model.
    command = split_switch_command(geometry, np.zeros(512), states)
    order = command.main_order
    sub_order = command.sub_order
    rh, ch, sh = (states // 8 % 8)[:, None], (states % 8)[:, None], (states // 64 % 8)[:, None]
    counts = np.arange(64)[None, :]
    missing = ((7 - rh) % 8) * 8 + ((7 - ch) % 8)
    length = counts + (counts > missing)
    full_rows, columns = length // 8, length % 8
    partial_row = (rh + full_rows) % 8
    rng = np.random.default_rng(20260930)
    patterns = [
        np.zeros(71, dtype=np.int64),
        np.ones(71, dtype=np.int64),
        np.full(71, (1 << 47) - 1, dtype=np.int64),
        np.arange(1, 72, dtype=np.int64),
    ]
    patterns += list(np.eye(71, dtype=np.int64))
    patterns += [rng.integers(1, 1 << 47, size=71, dtype=np.int64) for _ in range(16)]
    main_checks = sub_checks = 0
    for weights in patterns:
        grid = np.zeros(64, dtype=np.int64)
        grid[:63] = weights[:63]
        grid = grid.reshape(8, 8)
        column_prefix = np.concatenate(
            [np.zeros((8, 1), dtype=np.int64), grid.cumsum(axis=1)], axis=1
        )
        row_prefix = np.concatenate([[0], grid.sum(axis=1).cumsum()])
        end = ch + columns
        part = np.where(
            end <= 8,
            column_prefix[partial_row, np.minimum(end, 8)] - column_prefix[partial_row, ch],
            column_prefix[partial_row, 8]
            - column_prefix[partial_row, ch]
            + column_prefix[partial_row, np.maximum(end - 8, 0)],
        )
        proposed = cyclic(row_prefix, np.broadcast_to(rh, full_rows.shape), full_rows) + part
        oracle = np.concatenate(
            [np.zeros((512, 1), dtype=np.int64), weights[:63][order].cumsum(axis=1)], axis=1
        )
        np.testing.assert_array_equal(proposed, oracle)
        sub_prefix = np.concatenate([[0], weights[63:].cumsum()])
        sub_counts = np.arange(8)[None, :]
        sub_query = cyclic(
            sub_prefix, np.broadcast_to(sh, (512, 8)), np.broadcast_to(sub_counts, (512, 8))
        )
        sub_oracle = np.concatenate(
            [np.zeros((512, 1), dtype=np.int64), weights[63:][sub_order].cumsum(axis=1)], axis=1
        )[:, :8]
        np.testing.assert_array_equal(sub_query, sub_oracle)
        main_checks += proposed.size
        sub_checks += sub_query.size
    # A tempting 63-entry linear ring must fail: 2D rotation wraps each row.
    actual = np.zeros(64, dtype=np.int64)
    actual[order[1, :8]] = 1
    wrong = np.zeros(64, dtype=np.int64)
    wrong[(np.arange(8) + 1) % 63] = 1
    assert not np.array_equal(actual, wrong)
    fig, axes = plt.subplots(1, 3, figsize=(9.4, 3.2))
    for ax, selected, title in zip(
        axes,
        [actual, wrong, actual != wrong],
        [
            "A  True 2D DEM: sid=1, n=8",
            "B  Wrong 63-entry linear ring",
            "C  Two different physical cells",
        ],
        strict=True,
    ):
        data = np.asarray(selected, dtype=float).reshape(8, 8)
        data[7, 7] = 2
        ax.imshow(data, vmin=0, vmax=2, cmap=ListedColormap(["#eef0f2", "#237f82", "#bdbdbd"]))
        ax.set_xticks(range(8))
        ax.set_yticks(range(8))
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Physical column")
        ax.set_ylabel("Physical row")
    fig.tight_layout()
    figs = REPO / "docs/reports/rtl_patent_audit_20260930/figures"
    fig.savefig(figs / "dem_prefix_math.pdf", metadata={"CreationDate": None, "ModDate": None})
    fig.savefig(figs / "dem_prefix_math.png", dpi=190)
    plt.close(fig)
    record = {
        "scope": "PROPOSED_INTEGER_SELECTOR_EQUIVALENCE_NOT_RTL_OR_SYNTHESIS",
        "status": "PASS",
        "seed": 20260930,
        "patterns": len(patterns),
        "basis_vectors": 71,
        "states": 512,
        "main_counts": 64,
        "sub_counts": 8,
        "main_checks": main_checks,
        "sub_checks": sub_checks,
        "one_dimensional_ring_negative_control": "REJECTED_AT_SID1_COUNT8",
        "conservative_extra_prefix_bits_per_slice": 4024,
        "extra_prefix_bits_all_slices": 72432,
        "current_weight_store_state_bits": 62316,
        "naive_retained_raw_plus_prefix_state_bits": 134748,
        "input_sha256": {
            name: digest(REPO / name)
            for name in [
                "src/adi_model/dem.py",
                "rtl/core/dem_addr_gen.sv",
                "rtl/params/rtl_params.vh",
            ]
        },
        "generator_sha256": digest(Path(__file__)),
        "pdf_sha256": digest(figs / "dem_prefix_math.pdf"),
        "png_sha256": digest(figs / "dem_prefix_math.png"),
    }
    (Path(__file__).parent / "dem_prefix_math.result.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
