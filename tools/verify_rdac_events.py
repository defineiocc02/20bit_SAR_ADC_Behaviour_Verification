#!/usr/bin/env python3
"""Cross-check live structural RDAC pins against Python event and timing contracts."""

from __future__ import annotations

import argparse
import csv
from itertools import groupby
from pathlib import Path

import numpy as np

from adi_model import Config
from adi_model.mapper import dem_state_sequence
from adi_model.pipeline import run_pipeline
from adi_model.reference_charge import resolved_loading_codes
from adi_model.timing import rtl_dither_codes


def verify(path: Path) -> int:
    """Validate all events without dropping overflow or duplicate decision states."""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 3 * 512 * 7:
        raise ValueError("RDAC trace does not contain all 10752 expected events")
    cfg = Config.rtl_structural(dem_enable=True)
    expected_sid = dem_state_sequence(513, cfg, np.arange(513) % 2)[1:]
    expected_dither = rtl_dither_codes(512, cfg.conversion.rtl_dither_seed)
    groups = groupby(rows, key=lambda row: (int(row["mode"]), int(row["sample_id"])))
    checked = 0
    seen = set()
    for (mode, sample_id), values in groups:
        group = list(values)
        index = sample_id - 2
        if mode not in (0, 1, 2) or not 0 <= index < 512 or len(group) != 7:
            raise ValueError("missing, duplicated or out-of-range RDAC event group")
        if (mode, sample_id) in seen:
            raise ValueError("duplicated RDAC sample group")
        seen.add((mode, sample_id))
        phases = [int(row["phase"]) for row in group]
        if phases != list(range(3, 10)):
            raise ValueError("RDAC events do not occupy phases 3 through 9")
        if [int(row["target"]) for row in group] != [index] * 7:
            raise ValueError("input target is not aligned with sample ID")
        dither = 0 if mode == 0 else int(expected_dither[index])
        if [int(row["dither"]) for row in group] != [dither] * 7:
            raise ValueError("dither sequence or sample association differs")
        coarse = min(max(index + (dither if mode == 2 else 0), 0), 511)
        if [int(row["coarse"]) for row in group] != [coarse] * 7:
            raise ValueError("coarse decision differs from the expected quantizer input")
        adjustment = dither if mode == 1 else (-dither if mode == 2 else 0)
        expected = np.clip(resolved_loading_codes(coarse, 9, 1, adjustment), 0, 511)
        actual = [int(row["command"]) for row in group]
        if not np.array_equal(actual, expected):
            raise ValueError("RTL RDAC events differ from Python resolved commands")
        if [int(row["sid"]) for row in group] != [int(expected_sid[index])] * 7:
            raise ValueError("DEM state differs from the primed Python sequence")
        elapsed = np.array([float(row["elapsed_ns"]) for row in group])
        if not np.allclose(elapsed, np.arange(3, 10) * 25 / 16, atol=1e-7, rtol=0):
            raise ValueError("RDAC event times differ from the 40 MS/s structural profile")
        checked += 1
    if checked != 3 * 512:
        raise ValueError("RDAC trace does not contain exactly 1536 sample groups")
    # Run the whole Python pipeline against the same target stream.  The
    # event-level checks above independently cover every intermediate phase;
    # this check also covers sample ownership and the actual model entry point.
    for mode, mode_name in enumerate(("off", "sampling", "quantizer")):
        final_rows = [row for row in rows if int(row["mode"]) == mode and row["phase"] == "9"]
        target = np.array([int(row["target"]) for row in final_rows])
        cfg = Config.rtl_structural(
            dem_enable=True,
            dither_mode=mode_name,
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_rdac_gain_mismatch=0,
        )
        x = (-3 + (target + 0.3) * 6 / 512) / (cfg.dither_alpha if mode_name == "sampling" else 1)

        def known_input(time_s, *, values=x, sample_rate=cfg.fs):
            index = np.rint(np.asarray(time_s) * sample_rate).astype(np.int64)
            return values[np.clip(index, 0, len(values) - 1)]

        model = run_pipeline(cfg, known_input, len(target))
        expected = {
            "sample_id": model.sample_id,
            "sid": model.sid,
            "coarse": model.coarse,
            "command": np.clip(model.k, 0, 511),
        }
        if mode_name == "sampling":
            expected["dither"] = model.sample.dither_bank_code
        elif mode_name == "quantizer":
            expected["dither"] = np.rint(model.sample.dither / cfg.nominal_rdac_step)
        for field, actual in expected.items():
            if not np.array_equal(actual, [int(row[field]) for row in final_rows]):
                raise ValueError(f"{mode_name}: Python pipeline and RTL {field} differ")
    return len(rows)


def main() -> None:
    """Read the trace written by ``rdac_event_contract_tb``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    print(f"RDAC_PYTHON_RTL_EVENTS_MATCH events={verify(args.trace)}")


if __name__ == "__main__":
    main()
